"""Property-based tests: laws that must hold for *every* input, not the
handful somebody thought to type.

The example-based tests next door pin behaviour that was once wrong. These
pin behaviour that can never be right to break — a bbox that survives a
transform chain, a checksum that notices a typo, a canonicalizer that is
stable, a loader that refuses junk instead of crashing on it, and a result
that survives the JSON round-trip another language reads it through.
"""
import json
import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from paperpin.align.canon import canon_value, canonical_map
from paperpin.errors import DocumentError
from paperpin.geometry.transform import (TransformChain, denormalize_bbox,
                                         exif_transpose_transform,
                                         normalize_bbox, pdf_top_to_bottom_origin,
                                         rotate90, scale)
from paperpin.types import Candidate, FieldResult, GroundResult, PageInfo, Status
from paperpin.verify.checksums import ean_check_digit, iban_mod97

# Page-sized numbers: A4 points through phone-photo pixels, nothing absurd.
sizes = st.tuples(st.floats(40, 6000), st.floats(40, 6000))
coords = st.floats(-4000, 8000, allow_nan=False, allow_infinity=False)
text = st.text(max_size=60)


@st.composite
def bboxes(draw):
    x0, x1 = sorted(draw(st.tuples(coords, coords)))
    y0, y1 = sorted(draw(st.tuples(coords, coords)))
    return (x0, y0, x1, y1)


@st.composite
def chains(draw):
    """A transform chain of the kind intake actually builds: EXIF orientation,
    then rotation, then a render scale."""
    size = draw(sizes)
    chain = TransformChain(size)
    if draw(st.booleans()):
        chain.push(exif_transpose_transform(draw(st.integers(1, 8)),
                                            chain.processed_size))
    if draw(st.booleans()):
        chain.push(rotate90(draw(st.integers(0, 3)), chain.processed_size))
    if draw(st.booleans()):
        factor = draw(st.floats(0.05, 20))
        chain.push(scale(factor, factor, chain.processed_size))
    return chain


# ------------------------------------------------------------- geometry ---

@given(chain=chains(), bbox=bboxes())
def test_a_bbox_survives_a_round_trip_through_any_chain(chain, bbox):
    """§6.2's whole promise. It bit the prototype twice, and every published
    box depends on it — an axis-aligned box must come back where it started."""
    there = chain.map_bbox_to_processed(bbox)
    back = chain.map_bbox_to_original(there)

    scale_ = max(chain.original_size) + max(abs(v) for v in bbox)
    assert all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9 * scale_)
               for a, b in zip(back, bbox)), f"{bbox} -> {there} -> {back}"


@given(bbox=bboxes(), size=sizes)
def test_normalize_always_lands_inside_the_unit_square(bbox, size):
    """Consumers multiply these by a raster size. A coordinate outside 0..1
    would draw a box off the page."""
    x0, y0, x1, y1 = normalize_bbox(bbox, size)

    assert 0.0 <= x0 <= x1 <= 1.0
    assert 0.0 <= y0 <= y1 <= 1.0


@st.composite
def page_and_bbox_on_it(draw):
    width, height = draw(sizes)
    x0, x1 = sorted(draw(st.tuples(st.floats(0, width), st.floats(0, width))))
    y0, y1 = sorted(draw(st.tuples(st.floats(0, height), st.floats(0, height))))
    return (x0, y0, x1, y1), (width, height)


@given(case=page_and_bbox_on_it())
def test_denormalize_undoes_normalize_for_boxes_on_the_page(case):
    bbox, size = case

    back = denormalize_bbox(normalize_bbox(bbox, size), size)

    assert all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
               for a, b in zip(back, bbox))


@given(bbox=bboxes(), height=st.floats(40, 6000))
def test_pdf_origin_flip_is_its_own_inverse(bbox, height):
    once = pdf_top_to_bottom_origin(bbox, height)
    twice = pdf_top_to_bottom_origin(once, height)

    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(twice, bbox))


# ---------------------------------------------------------------- canon ---

@given(s=text)
def test_canon_is_idempotent(s):
    """Matching canonicalizes both sides; if a second pass moved the string,
    a value that came back through the pipeline would stop matching itself."""
    once = canon_value(s)

    assert canon_value(once) == once


@given(s=text)
def test_the_canonical_map_indexes_back_into_the_original(s):
    """Every canonical character has to name a real position in the source, or
    a match maps onto the wrong characters and the bbox is wrong."""
    canonical, index = canonical_map(s)

    assert len(canonical) == len(index)
    assert all(0 <= i < len(s) for i in index)
    assert index == sorted(index)


# ------------------------------------------------------------ checksums ---

@given(digits=st.text(alphabet="0123456789", min_size=12, max_size=12),
       position=st.integers(0, 11), bump=st.integers(1, 9))
def test_a_single_wrong_digit_breaks_the_ean_check(digits, position, bump):
    """The point of a check digit. If a typo can survive it, `proof="checksum"`
    is worth nothing."""
    body = digits
    check = next(d for d in range(10) if ean_check_digit(body + str(d)))
    valid = body + str(check)

    typo = list(valid)
    typo[position] = str((int(typo[position]) + bump) % 10)

    assert ean_check_digit(valid) is True
    assert ean_check_digit("".join(typo)) is False


@given(country=st.sampled_from(["SK", "DE", "LV", "CZ"]),
       body=st.text(alphabet="0123456789", min_size=16, max_size=16))
def test_iban_mod97_rejects_a_transposed_pair(country, body):
    """mod-97 exists to catch swapped digits, the commonest transcription
    error on a bank line."""
    remainder = next(c for c in range(2, 99)
                     if iban_mod97(f"{country}{c:02d}{body}"))
    valid = f"{country}{remainder:02d}{body}"
    swapped = [i for i in range(len(body) - 1) if body[i] != body[i + 1]]
    assume(swapped)
    i = swapped[0]
    transposed = (f"{country}{remainder:02d}"
                  + body[:i] + body[i + 1] + body[i] + body[i + 2:])

    assert iban_mod97(valid) is True
    assert iban_mod97(transposed) is False


# ----------------------------------------------------------- robustness ---

@given(blob=st.binary(min_size=1, max_size=3000))
@settings(max_examples=60, deadline=None)
def test_junk_bytes_are_refused_not_crashed_on(blob):
    """Anything can arrive at a document loader. It has to come back as a
    paperpin error a caller can catch, never an arbitrary exception."""
    from paperpin.intake.loader import load_document

    try:
        document = load_document(blob, filename="junk.bin")
    except DocumentError:
        return                      # the documented outcome
    document.close()                # a real image inside random bytes is legal


# ------------------------------------------------------------- contract ---

statuses = st.sampled_from(list(Status))
unit = st.floats(0, 1, allow_nan=False, allow_infinity=False)


@st.composite
def field_results(draw):
    x0, x1 = sorted(draw(st.tuples(unit, unit)))
    y0, y1 = sorted(draw(st.tuples(unit, unit)))
    located = draw(st.booleans())
    return FieldResult(
        name=draw(st.text(min_size=1, max_size=20)),
        value=draw(st.one_of(st.text(max_size=20), st.integers(), st.none())),
        status=draw(statuses),
        confidence=draw(unit),
        page=0 if located else None,
        bbox=(x0, y0, x1, y1) if located else None,
        evidence=draw(st.one_of(st.none(), st.text(max_size=20))),
        candidates=[Candidate(page=0, bbox=(x0, y0, x1, y1), score=draw(unit),
                              evidence=draw(st.text(max_size=10)))]
        if located else [],
        notes=draw(st.lists(st.text(max_size=15), max_size=2)),
    )


@given(fields=st.lists(field_results(), max_size=6))
def test_a_result_survives_the_json_round_trip(fields):
    """`save()` then `GroundResult.from_dict()` is how the CLI, the Lab and
    every other-language consumer read a result back. Statuses and boxes have
    to come out the way they went in."""
    by_name = {f.name: f for f in fields}
    page = PageInfo(index=0, width=595.3, height=841.9, route="textlayer")
    original = GroundResult(fields=by_name, pages=[page], source="x.pdf")

    restored = GroundResult.from_dict(json.loads(original.to_json()))

    assert set(restored.keys()) == set(by_name)
    for name, before in by_name.items():
        after = restored[name]
        assert after.status == before.status
        assert after.bbox == before.bbox
        assert after.page == before.page
        assert after.value == before.value


@given(fields=st.lists(field_results(), max_size=6))
def test_the_summary_always_accounts_for_every_field(fields):
    by_name = {f.name: f for f in fields}
    result = GroundResult(fields=by_name, pages=[], source="x.pdf")

    assert sum(result.counts().values()) == len(by_name)
