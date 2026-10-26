"""Unit tests for palette extraction and distribution."""

from __future__ import annotations

import io

from PIL import Image
import pytest

from custom_components.tv_mgmt.light_sync.color import (
    ExtractionParams,
    analyze_image,
    delta_e,
    distribute_palette,
    extract_palette,
)
from custom_components.tv_mgmt.light_sync.const import (
    PALETTE_MIN_DELTA_E,
    PaletteStrategy,
)

RED = (220, 30, 30)
BLUE = (30, 60, 220)
YELLOW = (250, 200, 20)
GREEN = (20, 200, 80)


def _poster(
    blocks: list[tuple[tuple[int, int, int, int], tuple[int, int, int]]],
) -> bytes:
    img = Image.new("RGB", (200, 300), (8, 8, 8))
    for box, rgb in blocks:
        img.paste(rgb, box)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def four_color_poster() -> bytes:
    """Four flat colour blocks on a black background (yellow largest)."""
    return _poster(
        [
            ((0, 0, 100, 140), RED),
            ((100, 0, 200, 140), BLUE),
            ((0, 140, 100, 300), YELLOW),
            ((100, 140, 200, 210), GREEN),
        ]
    )


def _close(a, b, tol: float = 8.0) -> bool:
    return delta_e(a, b) < tol


def test_palette_finds_distinct_colors(four_color_poster: bytes) -> None:
    """All four blocks are found, black is filtered, no near-duplicates."""
    palette = extract_palette(four_color_poster, ExtractionParams(), 5)
    assert 3 <= len(palette) <= 5
    for expected in (RED, BLUE, YELLOW, GREEN):
        assert any(_close(c, expected) for c in palette), expected
    assert not any(max(c) < 40 for c in palette)  # letterbox black dropped
    for i, a in enumerate(palette):
        for b in palette[i + 1 :]:
            assert delta_e(a, b) >= PALETTE_MIN_DELTA_E


def test_palette_respects_size(four_color_poster: bytes) -> None:
    """Size caps the palette; best (vibrant x coverage) colours come first."""
    palette = extract_palette(four_color_poster, ExtractionParams(), 3)
    assert len(palette) == 3
    # Yellow covers the most area and is highly saturated -> ranked first.
    assert _close(palette[0], YELLOW)


def test_palette_deduplicates_similar_shades() -> None:
    """Two near-identical reds collapse into one palette entry."""
    data = _poster(
        [
            ((0, 0, 100, 300), (200, 20, 30)),
            ((100, 0, 200, 150), (205, 25, 32)),
            ((100, 150, 200, 300), BLUE),
        ]
    )
    palette = extract_palette(data, ExtractionParams(), 5)
    reds = [c for c in palette if _close(c, (200, 20, 30), 15)]
    assert len(reds) == 1
    assert any(_close(c, BLUE) for c in palette)


def test_palette_of_grey_poster_is_empty() -> None:
    """Achromatic artwork gives no palette (falls through to next source)."""
    data = _poster([((0, 0, 200, 300), (128, 128, 128))])
    assert extract_palette(data, ExtractionParams(), 4) == []
    analysis = analyze_image(data, ExtractionParams(), 4)
    assert analysis.color is None
    assert analysis.palette == ()


def test_analyze_image_single_and_palette(four_color_poster: bytes) -> None:
    """Palette size 0 skips the palette; otherwise palette[0] leads."""
    single = analyze_image(four_color_poster, ExtractionParams(), 0)
    assert single.color is not None
    assert single.palette == ()
    multi = analyze_image(four_color_poster, ExtractionParams(), 4)
    assert len(multi.palette) == 4
    assert multi.color == multi.palette[0]


LIGHTS = ["light.a", "light.b", "light.c", "light.d", "light.e"]
P3 = [(1, 0, 0), (2, 0, 0), (3, 0, 0)]


def _slots(result: dict, palette: list) -> list[int]:
    return [palette.index(result[light]) + 1 for light in result]


@pytest.mark.parametrize(
    ("strategy", "count", "expected"),
    [
        (PaletteStrategy.SPREAD, 5, [1, 2, 3, 1, 2]),
        (PaletteStrategy.DOMINANT_FIRST, 5, [1, 2, 3, 2, 3]),
        (PaletteStrategy.MIRRORED, 5, [1, 2, 3, 2, 1]),
        (PaletteStrategy.MIRRORED, 4, [1, 2, 2, 1]),
        (PaletteStrategy.MIRRORED, 2, [1, 1]),
        (PaletteStrategy.SPREAD, 1, [1]),
    ],
)
def test_distribute_palette(strategy, count, expected) -> None:
    """Strategies assign colours in light order."""
    result = distribute_palette(P3, LIGHTS[:count], strategy)
    assert list(result) == LIGHTS[:count]  # order preserved
    assert _slots(result, P3) == expected


def test_distribute_mirrored_wraps_long_strips() -> None:
    """More mirrored positions than colours wrap around the palette."""
    lights = [f"light.{i}" for i in range(8)]
    result = distribute_palette(P3[:2], lights, PaletteStrategy.MIRRORED)
    assert _slots(result, P3[:2]) == [1, 2, 1, 2, 2, 1, 2, 1]


def test_distribute_single_colour_and_empty() -> None:
    """One colour goes everywhere; empty inputs give an empty mapping."""
    one = [(9, 9, 9)]
    for strategy in PaletteStrategy:
        assert set(distribute_palette(one, LIGHTS, strategy).values()) == {(9, 9, 9)}
    assert distribute_palette([], LIGHTS, PaletteStrategy.SPREAD) == {}
    assert distribute_palette(P3, [], PaletteStrategy.SPREAD) == {}
