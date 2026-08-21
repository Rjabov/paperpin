"""Concurrent PDF rasters must not corrupt pdfium.

pdfplumber renders through pypdfium2, and pdfium demands serialized calls:
unlocked concurrent renders produce PdfiumError("Failed to load document")
storms and, rarely, a straight SIGSEGV (reproduced 2026-08-21 from the
Lab's page endpoint, which rasters on FastAPI's threadpool)."""
import subprocess
import sys
from pathlib import Path

import pytest

PDF = Path(__file__).parent.parent / "fixtures" / "corpus" / "inv_sk_right.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="corpus not generated")

STRESS = """
import sys, threading
from paperpin.intake.loader import load_document

errors = []

def one_render():
    try:
        doc = load_document(sys.argv[1])
        try:
            assert doc.pages[0].raster(dpi=90).width > 0
        finally:
            doc.close()
    except Exception as e:
        errors.append(repr(e))

for _ in range(6):
    ts = [threading.Thread(target=one_render) for _ in range(6)]
    for t in ts: t.start()
    for t in ts: t.join()

print(f"FAILURES={len(errors)}")
for e in errors[:3]:
    print(" ", e)
"""


def test_concurrent_pdf_rasters_survive():
    # subprocess so a segfault fails the test instead of killing pytest
    proc = subprocess.run([sys.executable, "-c", STRESS, str(PDF)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"crashed (rc={proc.returncode})\n{proc.stderr[-800:]}"
    assert "FAILURES=0" in proc.stdout, proc.stdout[:800]
