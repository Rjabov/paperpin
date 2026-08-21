"""The demo invoice used in README screenshots, fully synthetic.

A fictional Czech paper supplier invoicing a fictional London studio, in
English, EUR. Dense enough to be a real grounding exercise: wrapped item
descriptions, two VAT rates with a recapitulation table, a variable
symbol, an IBAN. Typography is deliberately ERP-utilitarian: a too-pretty
invoice reads as fake.

No real entity appears here: the IČO checksum is deliberately invalid
(provably no registered company), the IBAN is format-valid but uses an
unassigned bank code, and every name, street and number is invented.
Both properties are asserted at generation time.

Regenerate with:  python fixtures/demo/demo_invoice.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "demo_invoice.pdf"

W, H = A4
M = 46

INK = (0.10, 0.11, 0.10)
GRAY = (0.42, 0.42, 0.40)
FAINT = (0.72, 0.72, 0.70)
GREEN = (0.20, 0.42, 0.30)   # letterhead rule only

SUPPLIER = {
    "wordmark": "HAVEL & KRAUS",
    "sub": "PAPER  s.r.o.",
    "tagline": "fine paper & board since 1993",
    "addr": ["Přístavní 1478/24", "170 00 Praha 7 — Holešovice", "Czech Republic"],
    "ico": "48321099",          # invalid mod-11 checksum, asserted below
    "dic": "CZ48321099",
    "web": "havelkraus-paper.example", "tel": "+420 220 118 402",
}
CUSTOMER = {
    "name": "Bramwell Studio Ltd",
    "addr": ["18 Fournier Walk", "London E1 6QL", "United Kingdom"],
    "reg": "Company No. 14892307",
    "vat": "GB 428 9017 55",
}
META = {
    "number": "2026-0847",
    "vs": "20260847",
    "issue": "04.08.2026",
    "supply": "01.08.2026",
    "due": "18.08.2026",
    "order": "PO-1193",
    "payment": "bank transfer",
}
BANK = {"name": "Vltava Bank a.s., Praha", "swift": "VLTBCZPP", "bank_code": "2222",
        "account": "0000004718230267"}

# (description, qty, unit, unit_price, vat_rate)
ITEMS = [
    ("Offset paper A4 80 g/m², bright white, 500-sheet ream — carton of 5 reams", 24, "carton", 21.90, 21),
    ("Offset paper A3 100 g/m², natural white, 250-sheet ream", 6, "ream", 18.40, 21),
    ("Kraft envelopes C4 self-seal, 90 g/m² ribbed, box of 250", 10, "box", 31.25, 21),
    ("Recycled notepad A5, 60 ruled sheets, FSC recycled mix, pack of 10", 12, "pack", 24.80, 12),
    ("Sketchbook A4 spiral-bound, 190 g/m² acid-free cartridge, 40 sheets", 18, "pcs", 9.35, 12),
    ("Archival document box, lignin-free board, 350 × 260 × 110 mm", 30, "pcs", 12.60, 21),
    ("Tissue paper 17 g/m² unbuffered, sheets 750 × 500 mm, ream of 480", 2, "ream", 96.00, 21),
    ("Freight & handling — DPD Classic, 6 parcels, tracked", 1, "", 54.00, 21),
]


def _iban_cz(bank_code: str, account: str) -> str:
    bban = bank_code + account
    digits = "".join(str(int(c, 36)) for c in bban + "CZ00")
    check = 98 - int(digits) % 97
    iban = f"CZ{check:02d}{bban}"
    assert int("".join(str(int(c, 36)) for c in iban[4:] + iban[:4])) % 97 == 1
    return iban


def _ico_checksum_valid(ico: str) -> bool:
    s = sum(int(d) * w for d, w in zip(ico[:7], range(8, 1, -1)))
    return (11 - s % 11) % 10 == int(ico[7])


def fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


def fonts() -> dict:
    """Arial (or DejaVu) for the ERP body; Futura for the letterhead where
    available. Diacritics need real TTFs; base-14 Helvetica mangles them."""
    candidates = [
        ("/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for body, bold in candidates:
        if Path(body).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont("Body", body))
            pdfmetrics.registerFont(TTFont("BodyB", bold))
            break
    else:
        raise SystemExit("no TTF with Latin Extended found (Arial or DejaVu needed)")
    try:
        pdfmetrics.registerFont(TTFont(
            "Brand", "/System/Library/Fonts/Supplemental/Futura.ttc", subfontIndex=2))
    except Exception:
        return {"r": "Body", "b": "BodyB", "brand": "BodyB"}
    return {"r": "Body", "b": "BodyB", "brand": "Brand"}


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def label(c, x, y, text, size=6.3, color=GRAY, font="Body", track=0.6):
    c.setFont(font, size)
    c.setFillColorRGB(*color)
    t = c.beginText(x, y)
    t.setCharSpace(track)
    t.textOut(text.upper())
    t.setCharSpace(0)  # Tc persists in the content stream past the text object
    c.drawText(t)


def main() -> None:
    assert not _ico_checksum_valid(SUPPLIER["ico"]), "IČO must be fictional (invalid checksum)"
    iban = _iban_cz(BANK["bank_code"], BANK["account"])
    iban_pretty = " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))

    rows = [(d, q, u, p, r, round(q * p, 2)) for d, q, u, p, r in ITEMS]
    bases: dict[int, float] = {}
    for *_, rate, amount in rows:
        bases[rate] = round(bases.get(rate, 0) + amount, 2)
    vats = {rate: round(base * rate / 100, 2) for rate, base in bases.items()}
    subtotal = round(sum(bases.values()), 2)
    vat_total = round(sum(vats.values()), 2)
    total = round(subtotal + vat_total, 2)
    assert (subtotal, vat_total, total) == (2038.40, 386.14, 2424.54)

    f = fonts()
    c = canvas.Canvas(str(OUT), pagesize=A4)
    c.setTitle(f"Invoice {META['number']} — Havel & Kraus Paper s.r.o.")

    # ---- letterhead ----
    y = H - 64
    c.setFillColorRGB(*INK)
    c.setFont(f["brand"], 17.5)
    t = c.beginText(M, y)
    t.setCharSpace(2.6)
    t.textOut(SUPPLIER["wordmark"])
    t.setCharSpace(0)
    c.drawText(t)
    label(c, M + 1, y - 12.5, f'{SUPPLIER["sub"]}   ·   {SUPPLIER["tagline"]}', size=6.8, track=1.1)

    c.setFont(f["r"], 7.3)
    c.setFillColorRGB(*GRAY)
    ry = y + 16
    for line in SUPPLIER["addr"] + [f'{SUPPLIER["tel"]}   ·   {SUPPLIER["web"]}']:
        c.drawRightString(W - M, ry, line)
        ry -= 9.4
    c.setStrokeColorRGB(*GREEN)
    c.setLineWidth(1.4)
    c.line(M, y - 24, W - M, y - 24)

    # ---- title ----
    y -= 52
    c.setFillColorRGB(*INK)
    c.setFont(f["b"], 12.5)
    c.drawString(M, y, "INVOICE — TAX DOCUMENT")
    c.drawRightString(W - M, y, f'No. {META["number"]}')

    # ---- meta grid ----
    y -= 24
    meta_pairs = [
        ("Issue date", META["issue"]), ("Taxable supply", META["supply"]), ("Due date", META["due"]),
        ("Variable symbol", META["vs"]), ("Order ref.", META["order"]), ("Payment method", META["payment"]),
    ]
    col_w = (W - 2 * M) / 3
    for i, (k, v) in enumerate(meta_pairs):
        cx = M + (i % 3) * col_w
        cy = y - (i // 3) * 24
        label(c, cx, cy, k)
        c.setFont(f["r"], 9.4)
        c.setFillColorRGB(*INK)
        c.drawString(cx, cy - 11, v)

    # ---- parties ----
    y -= 66
    label(c, M, y, "Supplier")
    label(c, M + (W - 2 * M) / 2, y, "Customer")
    for cx, name, addr, ids in [
        (M, "Havel & Kraus Paper s.r.o.", SUPPLIER["addr"],
         [f'Reg. No. (IČO): {SUPPLIER["ico"]}', f'VAT ID (DIČ): {SUPPLIER["dic"]}']),
        (M + (W - 2 * M) / 2, CUSTOMER["name"], CUSTOMER["addr"],
         [CUSTOMER["reg"], f'VAT No.: {CUSTOMER["vat"]}']),
    ]:
        yy = y - 13
        c.setFont(f["b"], 9.6)
        c.setFillColorRGB(*INK)
        c.drawString(cx, yy, name)
        c.setFont(f["r"], 9)
        for line in addr:
            yy -= 11
            c.drawString(cx, yy, line)
        c.setFillColorRGB(*GRAY)
        c.setFont(f["r"], 8.4)
        for line in ids:
            yy -= 10.6
            c.drawString(cx, yy, line)

    # ---- items table ----
    y -= 92
    x_num, x_desc, x_qty, x_unit, x_price, x_vat, x_amt = M, M + 20, M + 306, M + 336, M + 398, M + 428, W - M
    desc_w = x_qty - x_desc - 26
    c.setStrokeColorRGB(*INK)
    c.setLineWidth(0.8)
    c.line(M, y, W - M, y)
    hy = y - 11
    label(c, x_num, hy, "#")
    label(c, x_desc, hy, "Description")
    c.setFillColorRGB(*GRAY)
    c.setFont(f["r"], 6.3)
    for xx, txt in [(x_qty, "QTY"), (x_price, "UNIT PRICE"), (x_vat + 14, "VAT"), (x_amt, "AMOUNT EUR")]:
        c.drawRightString(xx, hy, txt)
    label(c, x_unit, hy, "Unit")
    c.setStrokeColorRGB(*FAINT)
    c.setLineWidth(0.5)
    c.line(M, hy - 5, W - M, hy - 5)

    yy = hy - 17
    for i, (desc, qty, unit, price, rate, amount) in enumerate(rows, 1):
        lines = wrap(desc, f["r"], 8.8, desc_w)
        c.setFont(f["r"], 8.8)
        c.setFillColorRGB(*GRAY)
        c.drawRightString(x_num + 8, yy, str(i))
        c.setFillColorRGB(*INK)
        for j, line in enumerate(lines):
            c.drawString(x_desc, yy - j * 10.4, line)
        c.drawRightString(x_qty, yy, str(qty))
        if unit:
            c.drawString(x_unit, yy, unit)
        c.drawRightString(x_price, yy, fmt(price))
        c.drawRightString(x_vat + 14, yy, f"{rate} %")
        c.drawRightString(x_amt, yy, fmt(amount))
        yy -= 10.4 * len(lines) + 6.2
    c.setStrokeColorRGB(*FAINT)
    c.line(M, yy + 3, W - M, yy + 3)

    # ---- VAT recap (left) + totals (right) ----
    yy -= 18
    label(c, M, yy, "VAT recapitulation")
    ry = yy - 13
    c.setFont(f["r"], 8.2)
    c.setFillColorRGB(*GRAY)
    for xx, txt in [(M + 40, "RATE"), (M + 120, "BASE"), (M + 190, "VAT"), (M + 264, "TOTAL")]:
        c.drawRightString(xx, ry, txt)
    ry -= 12
    c.setFillColorRGB(*INK)
    c.setFont(f["r"], 8.8)
    for rate in sorted(bases, reverse=True):
        for xx, val in [(M + 40, f"{rate} %"), (M + 120, fmt(bases[rate])),
                        (M + 190, fmt(vats[rate])), (M + 264, fmt(round(bases[rate] + vats[rate], 2)))]:
            c.drawRightString(xx, ry, val)
        ry -= 12
    tx_label, tx_val = W - M - 190, W - M
    ty = yy - 2
    for k, v in [("Total excl. VAT", fmt(subtotal)), ("VAT total", fmt(vat_total))]:
        c.setFont(f["r"], 9.2)
        c.setFillColorRGB(*GRAY)
        c.drawString(tx_label, ty, k)
        c.setFillColorRGB(*INK)
        c.drawRightString(tx_val, ty, v)
        ty -= 14.5
    c.setStrokeColorRGB(*INK)
    c.setLineWidth(0.8)
    c.line(tx_label, ty + 4.5, tx_val, ty + 4.5)
    ty -= 4
    c.setFont(f["b"], 12)
    c.drawString(tx_label, ty, "TOTAL DUE")
    c.drawRightString(tx_val, ty, f"{fmt(total)} EUR")

    # ---- payment box ----
    ty -= 40
    c.setStrokeColorRGB(*FAINT)
    c.setLineWidth(0.7)
    c.rect(M, ty - 34, W - 2 * M, 46)
    cx = M + 12
    for (k, v), wcol in zip([("Bank", BANK["name"]), ("IBAN", iban_pretty),
                             ("SWIFT / BIC", BANK["swift"]), ("Variable symbol", META["vs"])],
                            [150, 180, 90, 100]):
        label(c, cx, ty - 3, k)
        c.setFont(f["b"] if k == "IBAN" else f["r"], 9)
        c.setFillColorRGB(*INK)
        c.drawString(cx, ty - 15.5, v)
        cx += wcol

    # ---- footer ----
    c.setFont(f["r"], 6.6)
    c.setFillColorRGB(*GRAY)
    c.drawString(M, 52, "Registered in the Commercial Register at the Municipal Court in Prague, Section C, Insert 184921.")
    c.drawString(M, 43, "Invoice issued electronically and is valid without signature or stamp. Goods remain the property of the supplier until paid in full.")
    c.drawRightString(W - M, 52, "1 / 1")

    c.showPage()
    c.save()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
