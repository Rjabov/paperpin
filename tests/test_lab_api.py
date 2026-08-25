"""Lab backend API tests — upload → BYO run → grounded result, no network,
isolated PAPERPIN_HOME."""
import json
import os
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

CORPUS = Path(__file__).parent.parent / "fixtures" / "corpus"
pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="corpus not generated")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERPIN_HOME", str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from lab.server import db
    db.reset_for_tests()
    from fastapi.testclient import TestClient
    from lab.server import app as app_module
    monkeypatch.setattr(app_module, "LAB_TOKEN", "test-token", raising=False)
    with TestClient(app_module.app, base_url="http://localhost") as c:
        c.headers["X-Lab-Token"] = "test-token"
        yield c
    from lab.server import runner
    runner.reset_for_tests()   # stop the workers before closing what they use
    db.reset_for_tests()


def _upload(client, name="inv_sk_right.pdf"):
    with open(CORPUS / name, "rb") as fh:
        r = client.post("/api/documents",
                        files={"file": (name, fh, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def _wait_run(client, run_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] in ("done", "error"):
            return r
        time.sleep(0.2)
    raise TimeoutError("run did not finish")


def test_upload_dedupe_and_pages(client):
    doc = _upload(client)
    assert doc["pages"][0]["route"] == "textlayer"
    again = _upload(client)
    assert again["id"] == doc["id"]  # sha dedupe


def test_upload_garbage_rejected(client):
    r = client.post("/api/documents", files={"file": ("x.bin", b"\x00\x01", "application/octet-stream")})
    assert r.status_code == 422
    r = client.post("/api/documents", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_byo_run_end_to_end(client):
    doc = _upload(client)
    meta = json.loads((CORPUS / "inv_sk_right.json").read_text(encoding="utf-8"))
    extraction = dict(meta["extraction"])
    extraction["fake_field"] = "TOTALLY-INVENTED-99"
    r = client.post("/api/runs", json={"document_id": doc["id"], "model": "byo",
                                       "extraction": extraction})
    assert r.status_code == 200
    run = _wait_run(client, r.json()["run_id"])
    assert run["status"] == "done", run.get("error")
    fields = run["result"]["fields"]
    assert fields["fake_field"]["status"] == "not_found"
    located = sum(1 for f in fields.values()
                  if f["status"] in ("verified", "low_confidence"))
    assert located >= len(meta["extraction"]) - 1


def test_byo_requires_extraction(client):
    doc = _upload(client)
    r = client.post("/api/runs", json={"document_id": doc["id"], "model": "byo"})
    assert r.status_code == 422


def test_page_raster(client):
    doc = _upload(client)
    r = client.get(f"/api/documents/{doc['id']}/pages/0.jpg?width=600")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert len(r.content) > 5000


def test_run_history_and_fields_persisted(client):
    doc = _upload(client)
    meta = json.loads((CORPUS / "inv_sk_right.json").read_text(encoding="utf-8"))
    r = client.post("/api/runs", json={"document_id": doc["id"], "model": "byo",
                                       "extraction": meta["extraction"]})
    _wait_run(client, r.json()["run_id"])
    runs = client.get(f"/api/runs?document_id={doc['id']}").json()
    assert len(runs) == 1 and runs[0]["status"] == "done"


def test_settings_masking(client):
    r = client.post("/api/settings", json={"gemini_api_key": "AIzaFAKEFAKEFAKEFAKE1234"})
    body = r.json()
    assert body["gemini_key_set"] is True
    assert "FAKEFAKEFAKE" not in json.dumps(body)
    assert body["gemini_key_masked"].startswith("AIza")


def test_models_offline_lists_byo(client):
    models = client.get("/api/models").json()
    assert any(m["id"] == "byo" and m["cloud"] is False for m in models)


def test_presets_roundtrip(client):
    r = client.post("/api/presets", json={
        "name": "receipts-sk", "schema_spec": {"total": {"type": "number"}},
        "prompt_text": "Slovak receipts"})
    assert r.status_code == 200
    names = [p["name"] for p in client.get("/api/presets").json()]
    assert "invoice" in names and "receipts-sk" in names


def test_foreign_host_header_is_refused(client):
    # DNS rebinding: a page that resolves its own hostname to 127.0.0.1 is
    # same-origin with the Lab and would otherwise read every document
    r = client.get("/api/documents", headers={"Host": "evil.example.com"})
    assert r.status_code == 421
    r2 = client.post("/api/presets", json={"name": "x"},
                     headers={"Host": "evil.example.com"})
    assert r2.status_code == 421


def test_cross_origin_request_is_refused(client):
    r = client.get("/api/documents",
                   headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_localhost_variants_still_work(client):
    for host in ("localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"):
        r = client.get("/api/documents", headers={"Host": host})
        assert r.status_code == 200, (host, r.status_code)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are decorative on Windows; the 0o600 guarantee is POSIX-only")
def test_lab_files_are_not_world_readable(client, tmp_path):
    import stat

    from lab.server import db
    _upload(client)
    p = db.lab_home() / "lab.sqlite"
    assert p.exists()
    assert stat.S_IMODE(p.stat().st_mode) & 0o077 == 0, "lab db is group/world readable"
    assert stat.S_IMODE(db.lab_home().stat().st_mode) & 0o077 == 0


def test_api_rejects_missing_token(client):
    del client.headers["X-Lab-Token"]
    r = client.get("/api/documents")
    assert r.status_code == 401


def test_api_rejects_wrong_token(client):
    client.headers["X-Lab-Token"] = "nope"
    r = client.get("/api/documents")
    assert r.status_code == 401


def test_api_accepts_query_token(client):
    del client.headers["X-Lab-Token"]
    r = client.get("/api/documents?token=test-token")
    assert r.status_code == 200


def test_static_paths_exempt_from_token(client):
    del client.headers["X-Lab-Token"]
    r = client.get("/")
    assert r.status_code != 401


def test_cmd_lab_prints_tokened_url(monkeypatch, capsys):
    import types
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    from lab.server import app as app_module
    from paperpin.cli import _cmd_lab
    args = types.SimpleNamespace(port=8377, no_browser=True)
    assert _cmd_lab(args) == 0
    out = capsys.readouterr().out
    assert f"?token={app_module.LAB_TOKEN}" in out


def test_run_payload_carries_native_boxes(client):
    doc = _upload(client)
    meta = json.loads((CORPUS / "inv_sk_right.json").read_text(encoding="utf-8"))
    r = client.post("/api/runs", json={"document_id": doc["id"], "model": "byo",
                                       "extraction": meta["extraction"]})
    run_id = r.json()["run_id"]
    run = _wait_run(client, run_id)
    assert run["native"] == {}  # BYO has no model boxes, key still present
    from lab.server import db
    db.execute("INSERT INTO native_boxes(run_id,field_name,page,value,bbox_json)"
               " VALUES(?,?,?,?,?)",
               (run_id, "total", 0, json.dumps("3355.77"),
                json.dumps({"raw": [100, 200, 120, 300],
                            "xyxy": [0.2, 0.1, 0.3, 0.12]})))
    again = client.get(f"/api/runs/{run_id}").json()
    assert again["native"]["total"]["xyxy"] == [0.2, 0.1, 0.3, 0.12]
    assert again["native"]["total"]["value"] == "3355.77"


def test_single_gemini_run_triggers_native_pass(monkeypatch):
    from lab.server import db, runner
    calls = []
    monkeypatch.setattr(runner, "_execute_run", lambda rid: calls.append(("exec", rid)))
    monkeypatch.setattr(runner, "_native_pass",
                        lambda rid, m, d: calls.append(("native", rid, m, d)))
    monkeypatch.setattr(db, "query_one",
                        lambda *a, **k: {"status": "done",
                                         "model": "gemini/gemini-flash-latest",
                                         "document_id": 42})
    runner._run_task(7)
    assert ("exec", 7) in calls
    assert ("native", 7, "gemini/gemini-flash-latest", 42) in calls


def test_single_byo_run_skips_native_pass(monkeypatch):
    from lab.server import db, runner
    calls = []
    monkeypatch.setattr(runner, "_execute_run", lambda rid: calls.append(("exec", rid)))
    monkeypatch.setattr(runner, "_native_pass",
                        lambda rid, m, d: calls.append(("native", rid)))
    monkeypatch.setattr(db, "query_one",
                        lambda *a, **k: {"status": "done", "model": "byo",
                                         "document_id": 42})
    runner._run_task(7)
    assert calls == [("exec", 7)]


# The close/hammer race is run in a child process on purpose: when the bug is
# present the two threads deadlock, and a deadlock inside the test process
# would wedge pytest itself rather than fail a test.
_DEADLOCK_PROBE = '''
import os, sys, threading
sys.path.insert(0, %r)
os.environ["PAPERPIN_HOME"] = %r
from lab.server import db
db.connect()
stop = threading.Event()
def hammer():
    while not stop.is_set():
        try:
            db.query("SELECT * FROM documents")
        except Exception:
            pass          # a reconnect after close is fine; a hang is not
threading.Thread(target=hammer, daemon=True).start()
for _ in range(200):
    db.reset_for_tests()
    db.connect()
stop.set()
print("closed cleanly")
'''


def test_closing_the_connection_waits_for_a_worker_mid_statement(tmp_path):
    """CI hung here on Windows, in fixture teardown, not in any assertion.

    The connection is shared across threads (`check_same_thread=False`) and
    every statement takes `_LOCK`; closing it did not, so a close landing
    mid-statement deadlocked both threads.
    """
    import subprocess
    import sys

    root = str(Path(__file__).parent.parent)
    probe = _DEADLOCK_PROBE % (root, str(tmp_path))

    try:
        done = subprocess.run([sys.executable, "-c", probe],
                              capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        pytest.fail("closing the shared connection deadlocked against a worker "
                    "mid-statement — reset_for_tests() must hold _LOCK")

    assert done.returncode == 0, done.stderr
    assert "closed cleanly" in done.stdout
