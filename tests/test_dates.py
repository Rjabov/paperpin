from paperpin.align.matchers import date_interpretations_in, value_date_set


def hits(text):
    out = set()
    for _s, _e, cands in date_interpretations_in(text):
        out |= cands
    return out


def test_european_dotted():
    assert (2026, 1, 29) in hits("Dátum vyhotovenia: 29.01.2026")


def test_iso():
    assert (2026, 1, 29) in hits("date: 2026-01-29")


def test_slash_ambiguity_both_readings():
    got = hits("03/04/2026")
    assert (2026, 4, 3) in got and (2026, 3, 4) in got


def test_slash_unambiguous_day_first():
    assert hits("25/04/2026") == {(2026, 4, 25)}


def test_month_name_english():
    assert (2026, 1, 20) in hits("January 20, 2026")


def test_month_name_day_first():
    assert (2026, 3, 5) in hits("5 March 2026")


def test_two_digit_year():
    assert (2026, 1, 29) in hits("29.01.26")


def test_invalid_rejected():
    assert not hits("45.13.2026")


def test_llm_iso_matches_document_dotted():
    doc = hits("29.01.2026")
    llm = value_date_set("2026-01-29")
    assert doc & llm


def test_not_a_date_in_number():
    # phone-ish digit runs must not produce dates via lookarounds
    assert not hits("0911455117")


def test_date_glued_to_time_still_matches():
    # real receipts print 'date+time' with no separator: '05-20-2509:56',
    # '13.04.2511:33' — the trailing digit guard must see through hh:mm
    assert (2025, 5, 20) in value_date_set("05-20-25")
    assert date_interpretations_in("C16 170440P278181 05-20-2509:56")
    assert any((2025, 5, 20) in c for _, _, c in
               date_interpretations_in("05-20-2509:56"))
    assert any((2025, 4, 13) in c for _, _, c in
               date_interpretations_in("13.04.2511:33 0527"))


def test_plain_digit_runs_still_not_dates():
    # the guard exists for a reason: digits continuing WITHOUT a time shape
    # must still block the match
    assert not date_interpretations_in("13.04.251133")


def test_czech_and_polish_month_names():
    # the corpus is CZ-heavy; genitive month names print on formal invoices
    assert value_date_set("15. března 2026") == {(2026, 3, 15)}
    assert value_date_set("15 marca 2026") == {(2026, 3, 15)}
    assert value_date_set("1. září 2025") == {(2025, 9, 1)}


def test_latvian_january_genitive():
    # 'januara' was listed twice; the actual LV form 'janvara' never was
    assert value_date_set("19. janvāra 2026") == {(2026, 1, 19)}


def test_month_name_scan_is_linear_on_alpha_runs():
    # B-P2-1 (round-3): unbounded [A-Za-z]+ backtracked quadratically — a
    # 50k-letter run in a PDF cost 31s for one date field
    import time

    from paperpin.align.matchers import value_date_set
    t0 = time.perf_counter()
    value_date_set("A" * 200_000)
    assert time.perf_counter() - t0 < 2.0
