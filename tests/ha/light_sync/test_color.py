"""Unit tests for the pure colour helpers."""

from __future__ import annotations

import io

from PIL import Image
import pytest

from custom_components.tv_mgmt.light_sync.color import (
    AdjustParams,
    ColorExtractionError,
    ExtractionParams,
    adjust_color,
    extract_color,
    iter_genres,
    match_genre_color,
    normalize_title,
    rgb_to_hex,
    rgb_to_hs,
    saturation_of,
)
from custom_components.tv_mgmt.light_sync.const import GENRE_COLORS, ExtractionMethod


def _png(draw) -> bytes:
    img = Image.new("RGB", (200, 300), (0, 0, 0))
    draw(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fill(img: Image.Image, box: tuple[int, int, int, int], rgb) -> None:
    img.paste(rgb, box)


@pytest.fixture
def letterboxed_red_poster() -> bytes:
    """Mostly black (letterbox) with a red picture area and white text."""

    def draw(img):
        _fill(img, (0, 60, 200, 240), (200, 20, 30))
        _fill(img, (40, 200, 160, 220), (255, 255, 255))

    return _png(draw)


@pytest.mark.parametrize(
    "method",
    [ExtractionMethod.DOMINANT, ExtractionMethod.VIBRANT, ExtractionMethod.AVERAGE],
)
def test_extract_ignores_black_and_white(letterboxed_red_poster: bytes, method) -> None:
    """Dark bars and white text are filtered, red wins for every method."""
    r, g, b = extract_color(letterboxed_red_poster, ExtractionParams(method=method))
    assert r > 150
    assert g < 80
    assert b < 80


def test_vibrant_prefers_saturated_minority() -> None:
    """Vibrant picks a smaller but saturated area over a large dull one."""

    def draw(img):
        _fill(img, (0, 0, 200, 220), (110, 100, 95))  # big dull brown-grey
        _fill(img, (0, 220, 200, 300), (0, 120, 255))  # vivid blue band

    data = _png(draw)
    vib = extract_color(data, ExtractionParams(method=ExtractionMethod.VIBRANT))
    assert vib is not None
    assert vib[2] > 200
    assert vib[0] < 60
    dom = extract_color(
        data, ExtractionParams(method=ExtractionMethod.DOMINANT, skip_achromatic=False)
    )
    assert dom is not None
    assert abs(dom[0] - 110) < 20


def test_grey_poster_returns_none() -> None:
    """Achromatic posters fall through when skip_achromatic is set."""
    data = _png(lambda img: _fill(img, (0, 0, 200, 300), (128, 128, 128)))
    assert extract_color(data, ExtractionParams()) is None
    assert extract_color(data, ExtractionParams(skip_achromatic=False)) == (
        128,
        128,
        128,
    )


def test_all_black_poster_does_not_crash() -> None:
    """If filtering removes everything we analyse all pixels instead."""
    data = _png(lambda img: None)
    assert extract_color(data, ExtractionParams()) is None


def test_invalid_image_raises() -> None:
    """Undecodable data raises ColorExtractionError."""
    with pytest.raises(ColorExtractionError):
        extract_color(b"not an image", ExtractionParams())


def test_adjust_color_raises_saturation_and_value() -> None:
    """Dull/dark colours become usable bias-light colours, hue preserved."""
    out = adjust_color((60, 40, 40), AdjustParams(0.5, 1.0, 0.6))
    assert saturation_of(out) >= 0.49
    assert max(out) >= 150
    assert out[0] > out[1]
    assert out[0] > out[2]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("The Dark Knight (2008)", "dark knight"),
        ("  dark   KNIGHT! ", "dark knight"),
        ("Amélie", "amelie"),
        ("A Quiet Place", "quiet place"),
        ("Fast & Furious", "fast and furious"),
        ("The", "the"),
        (None, ""),
    ],
)
def test_normalize_title(raw, expected) -> None:
    """Normalisation is case/accent/article/year insensitive."""
    assert normalize_title(raw) == expected


def test_genre_attribute_wins_over_keywords() -> None:
    """Explicit genre is used before title keyword heuristics."""
    rgb, genre = match_genre_color(["Sci-Fi", "Drama"], ["A Christmas Story"])
    assert genre == "science fiction"
    assert rgb == GENRE_COLORS["science fiction"]


def test_title_keyword_fallback() -> None:
    """Keywords in the title map to a genre."""
    assert match_genre_color([], ["Halloween Kills"])[1] == "horror"
    assert match_genre_color([], ["Some Unknown Film"]) is None


def test_iter_genres_flattens() -> None:
    """Strings, comma lists and nested lists are flattened."""
    assert iter_genres("Action, Comedy") == ["Action", " Comedy"]
    assert iter_genres(["Horror", ["Thriller"]]) == ["Horror", "Thriller"]
    assert iter_genres(None) == []


def test_conversions() -> None:
    """Hex/HS helpers."""
    assert rgb_to_hex((255, 0, 128)) == "#ff0080"
    assert rgb_to_hs((255, 0, 0)) == (0.0, 100.0)
