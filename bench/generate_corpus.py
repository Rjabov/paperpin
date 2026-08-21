"""Synthetic invoice generator (HANDOVER §9.1a).

Parameterized: locale (SK/LV/EN/DE), number format, date format, layout
(totals right/left, boxed payment block), fonts. Because we control the
rendering, every document ships with exact ground-truth boxes for free —
normalized 0..1, top-left origin, matching paperpin's published convention.

Usage:
    python -m bench.generate_corpus --out fixtures/corpus --seed 42
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = A4  # 595.27 x 841.89 pt


def _register_fonts() -> tuple[str, str]:
    """A TTF with full Latin Extended (ľ, č, ā …) — base-14 Helvetica mangles
    them, which would make the corpus lie about diacritics. Windows: Arial;
    Linux/mac: DejaVu; fallback: Helvetica (with a loud warning)."""
    candidates = [
        ("CorpusSans", r"C:\Windows\Fonts\arial.ttf",
         "CorpusSans-Bold", r"C:\Windows\Fonts\arialbd.ttf"),
        ("CorpusSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "CorpusSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("CorpusSans", "/System/Library/Fonts/Supplemental/Arial.ttf",
         "CorpusSans-Bold", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for body_name, body_path, bold_name, bold_path in candidates:
        if Path(body_path).exists() and Path(bold_path).exists():
            pdfmetrics.registerFont(TTFont(body_name, body_path))
            pdfmetrics.registerFont(TTFont(bold_name, bold_path))
            return body_name, bold_name
    print("  ! no TTF with Latin Extended found — falling back to Helvetica "
          "(diacritics will be degraded)")
    return "Helvetica", "Helvetica-Bold"

LOCALES = {
    "sk": {
        "labels": {
            "invoice": "Faktúra č.", "order": "Objednávka číslo:",
            "issue_date": "Dátum vyhotovenia:", "due_date": "Dátum splatnosti:",
            "supplier": "Dodávateľ:", "customer": "Odberateľ:",
            "reg": "IČO:", "vat_id": "IČ DPH:", "iban": "IBAN", "swift": "SWIFT",
            "vs": "Variabilný symbol", "item": "Fakturovaná položka",
            "qty": "Množstvo", "unit_price": "Cena za MJ", "amount": "Spolu",
            "subtotal": "Základ pre DPH", "vat": "DPH", "total": "Celková suma s DPH",
            "grand": "Celková fakturovaná suma:",
        },
        "decimal": ",", "thousands": " ", "date_fmt": "dd.mm.yyyy",
        "country": "SK", "currency": "EUR",
        "names": ["VÍNO Château s.r.o.", "Pekáreň Ružinov s.r.o.", "Kníhkupectvo Máj a.s."],
        "streets": ["Tyršovo nábrežie 12", "Hlavná 47", "Štúrova 8"],
        "cities": ["85101 Bratislava", "04001 Košice", "91101 Trenčín"],
        "items": ["VIAJUR - DUNAJ suché (r.2022)", "Chlieb kváskový 500g",
                  "Kniha — Dejiny Slovenska", "Káva zrnková 1kg", "Olivový olej extra 0,75l"],
    },
    "lv": {
        "labels": {
            "invoice": "Rēķins Nr.", "order": "Pasūtījuma nr.:",
            "issue_date": "Izrakstīšanas datums:", "due_date": "Apmaksas termiņš:",
            "supplier": "Piegādātājs:", "customer": "Saņēmējs:",
            "reg": "Reģ. Nr.:", "vat_id": "PVN Nr.:", "iban": "IBAN", "swift": "SWIFT",
            "vs": "Maksājuma mērķis", "item": "Nosaukums",
            "qty": "Daudzums", "unit_price": "Cena", "amount": "Summa",
            "subtotal": "Summa bez PVN", "vat": "PVN", "total": "Kopā ar PVN",
            "grand": "Kopā apmaksai:",
        },
        "decimal": ",", "thousands": " ", "date_fmt": "dd.mm.yyyy",
        "country": "LV", "currency": "EUR",
        "names": ["Dishboard SIA", "Rīgas Maiznīca SIA", "Baltijas Grāmatas AS"],
        "streets": ["Brīvības iela 155", "Čaka iela 33", "Elizabetes iela 2"],
        "cities": ["LV-1010 Rīga", "LV-3001 Jelgava", "LV-4201 Valmiera"],
        "items": ["Programmatūras abonements PRO", "Rudzu maize 800g",
                  "Grāmata — Latvijas vēsture", "Kafija malta 500g", "Medus 1kg"],
    },
    "en": {
        "labels": {
            "invoice": "Invoice No.", "order": "Order number:",
            "issue_date": "Issue date:", "due_date": "Due date:",
            "supplier": "Supplier:", "customer": "Bill to:",
            "reg": "Company ID:", "vat_id": "VAT ID:", "iban": "IBAN", "swift": "SWIFT",
            "vs": "Payment reference", "item": "Description",
            "qty": "Qty", "unit_price": "Unit price", "amount": "Amount",
            "subtotal": "Subtotal", "vat": "VAT", "total": "Total incl. VAT",
            "grand": "Total due:",
        },
        "decimal": ".", "thousands": ",", "date_fmt": "month dd, yyyy",
        "country": "DE", "currency": "EUR",
        "names": ["Acme Cloud Ltd", "Northwind Print Co", "Bluebird Consulting GmbH"],
        "streets": ["12 King's Road", "Hauptstraße 9", "45 Market Street"],
        "cities": ["London SW3 4RP", "10115 Berlin", "Manchester M1 1AE"],
        "items": ["Cloud subscription — Team plan", "Business cards 500 pcs",
                  "Consulting day rate", "Laptop stand aluminium", "USB-C dock 11-in-1"],
    },
    "de": {
        "labels": {
            "invoice": "Rechnung Nr.", "order": "Bestellnummer:",
            "issue_date": "Rechnungsdatum:", "due_date": "Fällig am:",
            "supplier": "Verkäufer:", "customer": "Käufer:",
            "reg": "Handelsregister:", "vat_id": "USt-IdNr.:", "iban": "IBAN", "swift": "BIC",
            "vs": "Verwendungszweck", "item": "Bezeichnung",
            "qty": "Menge", "unit_price": "Einzelpreis", "amount": "Betrag",
            "subtotal": "Nettobetrag", "vat": "MwSt.", "total": "Gesamtbetrag",
            "grand": "Zu zahlender Betrag:",
        },
        "decimal": ",", "thousands": ".", "date_fmt": "dd.mm.yyyy",
        "country": "DE", "currency": "EUR",
        "names": ["Müller & Söhne GmbH", "Bäckerei Königsbrunn KG", "Schwarzwald IT AG"],
        "streets": ["Gartenstraße 14", "Münchener Str. 82", "Am Waldrand 3"],
        "cities": ["80331 München", "70173 Stuttgart", "79098 Freiburg"],
        "items": ["Wartungsvertrag Jahresgebühr", "Brezeln 100 Stück",
                  "Serverschrank 42HE", "Monitor 27 Zoll 4K", "Netzwerkkabel Cat7 50m"],
    },
}

MONTH_NAMES_EN = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]


@dataclass
class TruthSheet:
    """Collects ground-truth boxes as text is drawn."""
    fields: dict = field(default_factory=dict)

    def record(self, name: str, value, x: float, baseline_y: float,
               text: str, font: str, size: float, page: int = 0) -> None:
        w = stringWidth(text, font, size)
        ascent, descent = 0.75 * size, 0.22 * size
        x0, x1 = x, x + w
        top = PAGE_H - (baseline_y + ascent)
        bottom = PAGE_H - (baseline_y - descent)
        self.fields[name] = {
            "value": value if not isinstance(value, str) else value,
            "bbox": [x0 / PAGE_W, top / PAGE_H, x1 / PAGE_W, bottom / PAGE_H],
            "page": page, "printed": text,
        }


def fmt_number(v: float, loc: dict, decimals: int = 2) -> str:
    s = f"{v:,.{decimals}f}"          # 1,234.56
    s = s.replace(",", "\x00").replace(".", loc["decimal"]).replace("\x00", loc["thousands"])
    return s


def fmt_date(d: tuple[int, int, int], loc: dict) -> str:
    y, m, day = d
    if loc["date_fmt"] == "dd.mm.yyyy":
        return f"{day:02d}.{m:02d}.{y}"
    if loc["date_fmt"] == "yyyy-mm-dd":
        return f"{y}-{m:02d}-{day:02d}"
    return f"{MONTH_NAMES_EN[m - 1]} {day}, {y}"


def _iban(rng: random.Random, country: str) -> str:
    """A structurally valid IBAN with a correct mod-97 check."""
    bban = "".join(rng.choice("0123456789") for _ in range(16 if country != "DE" else 18))
    rearranged = bban + country + "00"
    digits = "".join(str(int(c, 36)) for c in rearranged)
    check = 98 - int(digits) % 97
    return f"{country}{check:02d}{bban}"


def generate_invoice(out_pdf: Path, seed: int, locale: str = "sk",
                     layout: str = "right", boxed_payment: bool = True) -> dict:
    rng = random.Random(seed)
    loc = LOCALES[locale]
    L = loc["labels"]
    truth = TruthSheet()

    c = canvas.Canvas(str(out_pdf), pagesize=A4)
    body, bold = _register_fonts()

    inv_no = f"{rng.randint(2026001, 2026999)}{rng.randint(100, 999)}"
    issue = (2026, rng.randint(1, 8), rng.randint(1, 28))
    due = (2026, min(12, issue[1] + 1), rng.randint(1, 28))
    supplier = rng.choice(loc["names"])
    customer = rng.choice([n for n in loc["names"] if n != supplier])
    sup_reg = str(rng.randint(30000000, 49999999))
    cust_reg = str(rng.randint(30000000, 49999999))
    vat_country = loc["country"]
    if vat_country == "SK":
        sup_vat = f"SK{rng.randint(90909091, 909090909) * 11:010d}"  # divisible by 11
    elif vat_country == "LV":
        sup_vat = f"LV{rng.randint(10**10, 10**11 - 1)}"
    else:
        sup_vat = f"DE{rng.randint(10**8, 10**9 - 1)}"
    iban = _iban(rng, "SK" if vat_country == "SK" else ("LV" if vat_country == "LV" else "DE"))
    iban_spaced = " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))
    swift = rng.choice(["TATRSKBX", "HABALV22", "DEUTDEFF", "UNCRSKBX"])

    n_items = rng.randint(1, 3)
    vat_rate = rng.choice([19, 20, 21, 23])
    items = []
    subtotal = 0.0
    for i in range(n_items):
        qty = rng.choice([1, 2, 5, 12, 24])
        unit = round(rng.uniform(1.5, 240.0), rng.choice([2, 2, 2, 6]))
        amount = round(qty * unit, 2)
        subtotal = round(subtotal + amount, 2)
        items.append({"name": rng.choice(loc["items"]), "qty": qty,
                      "unit_price": unit, "amount": amount,
                      "ean": None if rng.random() < 0.5 else _ean13(rng)})
    vat_amount = round(subtotal * vat_rate / 100, 2)
    total = round(subtotal + vat_amount, 2)

    # ---- header: logo top-left; invoice block right, or under the logo for
    # the "left" layout (never overlapping — that garbles the text layer)
    c.setFont(bold, 15)
    c.drawString(48, 800, supplier.split()[0].upper())
    if layout == "right":
        label_x, label_y = 330, 800
    else:
        label_x, label_y = 48, 772
    c.setFont(body, 11)
    c.drawString(label_x, label_y, L["invoice"])
    c.setFont(bold, 13)
    x = label_x + stringWidth(L["invoice"], body, 11) + 12
    c.drawString(x, label_y, inv_no)
    truth.record("invoice_number", inv_no, x, label_y, inv_no, bold, 13)

    c.setFont(body, 9.5)
    y = label_y - 24
    for label_key, name, d in (("issue_date", "invoice_date", issue),
                               ("due_date", "due_date", due)):
        c.drawString(label_x, y, L[label_key])
        ds = fmt_date(d, loc)
        x = label_x + 110
        c.drawString(x, y, ds)
        truth.record(name, ds, x, y, ds, body, 9.5)
        y -= 15

    # ---- supplier / customer blocks
    block_top = 742 if layout == "right" else 700
    c.setFont(bold, 9.5)
    c.drawString(48, block_top, L["supplier"])
    c.setFont(body, 9.5)
    x0s, ys = 48, block_top - 14
    c.drawString(x0s, ys, supplier)
    truth.record("supplier_name", supplier, x0s, ys, supplier, body, 9.5)
    street, city = rng.choice(loc["streets"]), rng.choice(loc["cities"])
    c.drawString(x0s, ys - 13, street)
    c.drawString(x0s, ys - 26, city)
    c.drawString(x0s, ys - 44, f"{L['reg']} {sup_reg}")
    reg_x = x0s + stringWidth(L["reg"] + " ", body, 9.5)
    truth.record("supplier_reg_number", sup_reg, reg_x, ys - 44, sup_reg, body, 9.5)
    c.drawString(x0s, ys - 57, f"{L['vat_id']} {sup_vat}")
    vat_x = x0s + stringWidth(L["vat_id"] + " ", body, 9.5)
    truth.record("supplier_vat_number", sup_vat, vat_x, ys - 57, sup_vat, body, 9.5)

    c.setFont(bold, 9.5)
    c.drawString(330, block_top, L["customer"])
    c.setFont(body, 9.5)
    c.drawString(330, ys, customer)
    truth.record("customer_name", customer, 330, ys, customer, body, 9.5)
    c.drawString(330, ys - 13, rng.choice(loc["streets"]))
    c.drawString(330, ys - 26, rng.choice(loc["cities"]))
    c.drawString(330, ys - 44, f"{L['reg']} {cust_reg}")
    truth.record("customer_reg_number", cust_reg,
                 330 + stringWidth(L["reg"] + " ", body, 9.5), ys - 44, cust_reg, body, 9.5)

    # ---- payment block (optionally inverse-contrast, E-13)
    py = 610
    if boxed_payment:
        c.setFillColorRGB(0.09, 0.33, 0.65)
        c.rect(48, py - 34, 320, 56, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
    c.setFont(body, 7.5)
    c.drawString(56, py + 8, L["iban"])
    c.setFont(bold, 10.5)
    c.drawString(56, py - 6, iban_spaced)
    truth.record("iban", iban_spaced, 56, py - 6, iban_spaced, bold, 10.5)
    c.setFont(body, 7.5)
    c.drawString(270, py + 8, L["swift"])
    c.setFont(bold, 10.5)
    c.drawString(270, py - 6, swift)
    truth.record("swift", swift, 270, py - 6, swift, bold, 10.5)
    c.setFont(body, 7.5)
    c.drawString(56, py - 20, L["vs"])
    c.setFont(bold, 9.5)
    c.drawString(140, py - 20, inv_no)
    truth.record("variable_symbol", inv_no, 140, py - 20, inv_no, bold, 9.5)
    c.setFillColorRGB(0, 0, 0)

    # ---- items table
    ty = 540
    c.setFont(bold, 8.5)
    col_ean, col_name, col_qty, col_unit, col_amount = 48, 130, 330, 400, 490
    for cx, key in ((col_ean, "EAN" if locale != "en" else "SKU"),
                    (col_name, L["item"]), (col_qty, L["qty"]),
                    (col_unit, L["unit_price"]), (col_amount, L["amount"])):
        c.drawString(cx, ty, key if isinstance(key, str) else key)
    c.line(48, ty - 4, 547, ty - 4)
    c.setFont(body, 9)
    ry = ty - 18
    for i, it in enumerate(items):
        if it["ean"]:
            c.drawString(col_ean, ry, it["ean"])
            if i == 0:
                truth.record("ean", it["ean"], col_ean, ry, it["ean"], body, 9)
        c.drawString(col_name, ry, it["name"])
        if i == 0:
            truth.record("item_name", it["name"], col_name, ry, it["name"], body, 9)
        qty_s = fmt_number(it["qty"], loc, 0)
        c.drawString(col_qty, ry, qty_s)
        if i == 0:
            truth.record("qty", qty_s, col_qty, ry, qty_s, body, 9)
        unit_s = fmt_number(it["unit_price"], loc, 6 if it["unit_price"] * 100 % 1 else 2)
        c.drawString(col_unit, ry, unit_s)
        if i == 0:
            truth.record("unit_price", unit_s, col_unit, ry, unit_s, body, 9)
        amt_s = fmt_number(it["amount"], loc)
        c.drawString(col_amount, ry, amt_s)
        if i == 0:
            truth.record("line_amount", amt_s, col_amount, ry, amt_s, body, 9)
        ry -= 16

    # ---- totals
    tx_label = 330 if layout == "right" else 48
    tx_value = 490 if layout == "right" else 200
    sy = ry - 30
    rows = [(L["subtotal"], fmt_number(subtotal, loc), "subtotal"),
            (f"{L['vat']} {vat_rate} %", fmt_number(vat_amount, loc), "vat_amount"),
            (L["total"], fmt_number(total, loc), "total")]
    c.setFont(body, 9.5)
    for label, val, name in rows:
        c.drawString(tx_label, sy, label)
        c.drawString(tx_value, sy, val)
        truth.record(name, val, tx_value, sy, val, body, 9.5)
        sy -= 15
    truth.record("vat_rate", str(vat_rate),
                 tx_label + stringWidth(L["vat"] + " ", body, 9.5), sy + 30,
                 str(vat_rate), body, 9.5)
    c.setFont(bold, 11)
    sy -= 8
    c.drawString(tx_label, sy, L["grand"])
    grand_s = f"{fmt_number(total, loc)} {loc['currency']}"
    gx = tx_value if layout == "right" else tx_label + 220
    c.drawString(gx, sy, grand_s)
    truth.record("grand_total", grand_s, gx, sy, grand_s, bold, 11)

    c.setFont(body, 8)
    c.drawString(48, 60, f"{supplier} · {street} · {city}")

    c.showPage()
    c.save()

    extraction = {
        "invoice_number": inv_no,
        "invoice_date": fmt_date(issue, loc),
        "due_date": fmt_date(due, loc),
        "supplier_name": supplier,
        "supplier_reg_number": sup_reg,
        "supplier_vat_number": sup_vat,
        "customer_name": customer,
        "customer_reg_number": cust_reg,
        "iban": iban_spaced,
        "swift": swift,
        "variable_symbol": inv_no,
        "item_name": items[0]["name"],
        "qty": fmt_number(items[0]["qty"], loc, 0),
        "unit_price": fmt_number(items[0]["unit_price"], loc,
                                 6 if items[0]["unit_price"] * 100 % 1 else 2),
        "subtotal": fmt_number(subtotal, loc),
        "vat_rate": str(vat_rate),
        "vat_amount": fmt_number(vat_amount, loc),
        "total": fmt_number(total, loc),
        "currency": loc["currency"],
    }
    if items[0]["ean"]:
        extraction["ean"] = items[0]["ean"]
    return {"locale": locale, "layout": layout, "seed": seed,
            "extraction": extraction, "truth": truth.fields}


def _ean13(rng: random.Random) -> str:
    digits = [rng.randint(0, 9) for _ in range(12)]
    total = sum(d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
    digits.append((10 - total % 10) % 10)
    return "".join(map(str, digits))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fixtures/corpus")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--locales", default="sk,lv,en,de")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, locale in enumerate(args.locales.split(",")):
        for j, (layout, boxed) in enumerate((("right", True), ("left", False))):
            name = f"inv_{locale}_{layout}"
            meta = generate_invoice(out / f"{name}.pdf", seed=args.seed + i * 10 + j,
                                    locale=locale, layout=layout, boxed_payment=boxed)
            (out / f"{name}.json").write_text(
                json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
            manifest.append(name)
            print(f"  {name}.pdf")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
