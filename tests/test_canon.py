from paperpin.align.canon import canonical_map, canon_value, fuzzy_windows


def test_accent_folding():
    canon, idx = canonical_map("Celková fakturovaná suma:")
    assert canon == "celkovafakturovanasuma"
    assert len(idx) == len(canon)


def test_offset_map_projects_back():
    s = "Reg.No: 40103567891  VAT:LV40103567891"
    canon, idx = canonical_map(s)
    assert "vatlv40103567891" in canon
    pos = canon.find("lv40103567891")
    start, end = idx[pos], idx[pos + len("lv40103567891") - 1] + 1
    assert s[start:end] == "LV40103567891"


def test_merged_tokens_e11():
    # THE core trick: merged OCR tokens still match canonically
    canon, _ = canonical_map("Reg.No:40103567891VAT:LV40103567891")
    assert canon_value("LV40103567891") in canon


def test_diacritics_slovak_latvian():
    assert canon_value("suché") == "suche"
    assert canon_value("Bohéma") == "bohema"
    assert canon_value("Rēķins") == "rekins"
    assert canon_value("Množstvo") == "mnozstvo"
    assert canon_value("Fällig") == "fallig"


def test_spacing_variants_e24():
    assert canon_value("s.r.o.") == canon_value("s. r. o.") == "sro"


def test_fuzzy_windows_finds_confusable():
    # O misread as 0
    hits = fuzzy_windows("ibansk731100000002629028990", "sk73110o000002629028990", 0.88)
    assert hits


def test_empty_inputs():
    assert canonical_map("")[0] == ""
    assert fuzzy_windows("", "abc", 0.8) == []
    assert fuzzy_windows("abc", "", 0.8) == []


def test_non_latin_scripts_survive_canon():
    # the engine is script-agnostic: Cyrillic/Greek text must not canonize to
    # nothing (Latin folding behavior stays byte-identical)
    assert canon_value("Счёт № 12345") == "счетno12345"  # № NFKD-folds to 'No'
    assert canon_value("Τιμολόγιο 77") == "τιμολογιο77"
    assert canon_value("Faktúra č. 2026") == "fakturac2026"  # Latin unchanged


def test_nonascii_decimal_digits_normalize():
    assert canon_value("٤٢") == "42"   # Arabic-Indic digits


def test_multichar_decompositions_survive_folding():
    # PDF text layers emit ligatures; German sharp s casefolds to two letters
    assert canon_value("ﬁnance") == "finance"
    assert canon_value("Straße") == "strasse"
    assert canon_value("STRASSE") == "strasse"


def test_offset_map_stays_valid_through_expansion():
    canon, idx = canonical_map("aﬁb")
    assert canon == "afib"
    # both emitted chars of the ligature point back at the ligature's position
    assert idx == [0, 1, 1, 2]
