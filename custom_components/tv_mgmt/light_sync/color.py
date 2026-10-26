"""Pure colour helpers for TV Mgmt light sync (ported from Movie Light Sync).

This module deliberately has **no Home Assistant runtime dependencies** (only
constants), so every function here can be unit-tested in isolation and the
CPU-bound Pillow work can be run safely in an executor thread.

Contents:

* :func:`extract_color` - dominant / vibrant / average poster colour.
* :func:`analyze_image` - colour **and** a 3-5 colour palette in one decode.
* :func:`extract_palette` - distinct, vibrancy-ranked palette.
* :func:`distribute_palette` - assign palette colours to ordered lights.
* :func:`adjust_color` - saturation floor/boost and brightness floor.
* :func:`normalize_title` - case/accents/article/year-insensitive title key.
* :func:`match_genre_color` - genre attribute or title-keyword -> colour.
* small conversion helpers (hex, HS).
"""

from __future__ import annotations

from collections.abc import Iterable
import colorsys
from dataclasses import dataclass
import io
import re
import unicodedata

from PIL import Image, UnidentifiedImageError

from .const import (
    ACHROMATIC_SATURATION,
    EXTRACTION_THUMBNAIL_SIZE,
    GENRE_ALIASES,
    GENRE_COLORS,
    PALETTE_KMEANS_ITERATIONS,
    PALETTE_MIN_COVERAGE,
    PALETTE_MIN_DELTA_E,
    PALETTE_QUANTIZE_COLORS,
    QUANTIZE_COLORS,
    TITLE_KEYWORD_GENRES,
    ExtractionMethod,
    PaletteStrategy,
)

RGB = tuple[int, int, int]

# Share of pixels that must survive the dark/light filter; below that the
# filter is ignored (e.g. a mostly black poster with white text).
_MIN_KEPT_RATIO = 0.05

_LEADING_ARTICLES = ("the ", "a ", "an ")
_YEAR_RE = re.compile(r"[\(\[]\s*(19|20)\d{2}\s*[\)\]]")
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")


class ColorExtractionError(Exception):
    """Raised when an image cannot be decoded or analysed."""


@dataclass(frozen=True, slots=True)
class ExtractionParams:
    """Parameters for :func:`extract_color` (hashable -> usable as cache key)."""

    method: ExtractionMethod = ExtractionMethod.VIBRANT
    dark_threshold: int = 32
    light_threshold: int = 235
    skip_achromatic: bool = True


@dataclass(frozen=True, slots=True)
class AdjustParams:
    """Parameters for :func:`adjust_color`."""

    min_saturation: float = 0.35  # 0..1
    saturation_boost: float = 1.3  # multiplier
    min_brightness: float = 0.45  # 0..1 (HSV value)


# --------------------------------------------------------------------------- #
# Conversions
# --------------------------------------------------------------------------- #
def clamp_rgb(rgb: Iterable[float]) -> RGB:
    """Clamp/round an iterable of three numbers into a valid RGB tuple."""
    values = [round(float(v)) for v in rgb]
    if len(values) != 3:
        raise ValueError(f"Expected 3 colour components, got {len(values)}")
    r, g, b = (max(0, min(255, v)) for v in values)
    return (r, g, b)


def rgb_to_hex(rgb: Iterable[float]) -> str:
    """Return ``#rrggbb`` for an RGB colour."""
    r, g, b = clamp_rgb(rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def rgb_to_hs(rgb: Iterable[float]) -> tuple[float, float]:
    """Return a Home Assistant style HS tuple (hue 0-360, sat 0-100)."""
    r, g, b = clamp_rgb(rgb)
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return (round(h * 360, 2), round(s * 100, 2))


def saturation_of(rgb: Iterable[float]) -> float:
    """Return HSV saturation (0..1) of a colour."""
    r, g, b = clamp_rgb(rgb)
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[1]


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def _luma(pixel: RGB) -> float:
    """Rec. 601 luma of a pixel (0..255)."""
    r, g, b = pixel
    return 0.299 * r + 0.587 * g + 0.114 * b


def _filter_pixels(pixels: list[RGB], params: ExtractionParams) -> list[RGB]:
    """Drop near-black and near-white pixels (letterboxing, title text)."""
    dark = params.dark_threshold
    light = params.light_threshold
    if dark <= 0 and light >= 255:
        return pixels
    kept = [
        p
        for p in pixels
        if (dark <= 0 or _luma(p) >= dark) and (light >= 255 or min(p) <= light)
    ]
    if len(kept) < max(1, int(len(pixels) * _MIN_KEPT_RATIO)):
        # Poster is (almost) entirely dark/white - filtering would leave
        # nothing meaningful, so analyse everything instead.
        return pixels
    return kept


def _clusters(
    pixels: list[RGB], colors: int = QUANTIZE_COLORS, kmeans: int = 0
) -> list[tuple[int, RGB]]:
    """Quantise ``pixels`` and return ``(count, rgb)`` clusters.

    Median cut seeds the clusters; ``kmeans`` > 0 refines them with that many
    k-means passes (Pillow's built-in implementation).
    """
    strip = Image.new("RGB", (len(pixels), 1))
    strip.putdata(pixels)
    quantized = strip.quantize(
        colors=colors, method=Image.Quantize.MEDIANCUT, kmeans=kmeans
    )
    palette = quantized.getpalette() or []
    colors_found = quantized.getcolors(maxcolors=colors * 4) or []
    result: list[tuple[int, RGB]] = []
    for count, index in colors_found:
        base = int(index) * 3
        if base + 2 >= len(palette):
            continue
        result.append((count, (palette[base], palette[base + 1], palette[base + 2])))
    result.sort(key=lambda item: item[0], reverse=True)
    return result


def _vibrant_score(count: int, rgb: RGB, total: int) -> float:
    """Score a cluster by frequency, saturation and brightness."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    del h
    share = count / total
    # Saturation dominates; value avoids picking muddy dark colours; a
    # small share floor keeps tiny specks from winning.
    return (share**0.5) * (0.15 + s) ** 2 * (0.25 + v)


def _load_pixels(image_bytes: bytes, params: ExtractionParams) -> list[RGB]:
    """Decode, downscale and filter an image into analysable pixels."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # draft() lets the JPEG decoder downscale while decoding which is
            # dramatically faster for large posters.
            img.draft("RGB", (EXTRACTION_THUMBNAIL_SIZE * 2,) * 2)
            rgb_img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as err:
        raise ColorExtractionError(f"Unable to decode image: {err}") from err

    rgb_img.thumbnail((EXTRACTION_THUMBNAIL_SIZE, EXTRACTION_THUMBNAIL_SIZE))
    # Pillow 12.1+ renamed getdata (deprecated, removed in 14).
    flatten = getattr(rgb_img, "get_flattened_data", None) or rgb_img.getdata
    pixels: list[RGB] = list(flatten())  # type: ignore[arg-type]
    if not pixels:
        raise ColorExtractionError("Image contains no pixels")
    return _filter_pixels(pixels, params)


def _color_from_pixels(pixels: list[RGB], params: ExtractionParams) -> RGB | None:
    """Pick the single representative colour from filtered pixels."""
    if params.method == ExtractionMethod.AVERAGE:
        n = len(pixels)
        color = clamp_rgb(
            (
                sum(p[0] for p in pixels) / n,
                sum(p[1] for p in pixels) / n,
                sum(p[2] for p in pixels) / n,
            )
        )
    else:
        clusters = _clusters(pixels)
        if not clusters:
            raise ColorExtractionError("Quantisation produced no clusters")
        if params.method == ExtractionMethod.DOMINANT:
            color = clusters[0][1]
        else:  # VIBRANT
            total = sum(count for count, _ in clusters)
            color = max(clusters, key=lambda c: _vibrant_score(c[0], c[1], total))[1]

    if params.skip_achromatic and saturation_of(color) < ACHROMATIC_SATURATION:
        return None
    return color


def extract_color(image_bytes: bytes, params: ExtractionParams) -> RGB | None:
    """Return the representative colour of an image, or ``None``.

    ``None`` means the image is effectively grey (and ``skip_achromatic`` is
    set), signalling the caller to fall through to the next colour source.

    Runs synchronously and is CPU bound - call it from an executor.

    :raises ColorExtractionError: if the image cannot be decoded.
    """
    return _color_from_pixels(_load_pixels(image_bytes, params), params)


# --------------------------------------------------------------------------- #
# Palettes
# --------------------------------------------------------------------------- #
def _srgb_to_linear(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb: Iterable[float]) -> tuple[float, float, float]:
    """Convert sRGB (D65) to CIE L*a*b*."""
    r, g, b = (_srgb_to_linear(c) for c in clamp_rgb(rgb))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(a: Iterable[float], b: Iterable[float]) -> float:
    """Perceptual distance (CIE76 delta E) between two RGB colours."""
    la, lb = rgb_to_lab(a), rgb_to_lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb, strict=True)) ** 0.5


def _palette_from_pixels(
    pixels: list[RGB], params: ExtractionParams, size: int
) -> list[RGB]:
    """Rank clusters by vibrancy x coverage and keep distinct ones."""
    clusters = _clusters(
        pixels, colors=PALETTE_QUANTIZE_COLORS, kmeans=PALETTE_KMEANS_ITERATIONS
    )
    if not clusters:
        return []
    total = sum(count for count, _ in clusters)
    # Ignore specks (and blended edge clusters) below the coverage floor.
    big = [c for c in clusters if c[0] / total >= PALETTE_MIN_COVERAGE]
    clusters = big or clusters
    ranked = sorted(
        clusters, key=lambda c: _vibrant_score(c[0], c[1], total), reverse=True
    )
    candidates = [rgb for _, rgb in ranked]
    if params.skip_achromatic:
        chromatic = [c for c in candidates if saturation_of(c) >= ACHROMATIC_SATURATION]
        if not chromatic:
            return []
        candidates = chromatic

    palette: list[RGB] = []
    for color in candidates:
        if all(delta_e(color, kept) >= PALETTE_MIN_DELTA_E for kept in palette):
            palette.append(color)
            if len(palette) >= size:
                break
    return palette


def extract_palette(
    image_bytes: bytes, params: ExtractionParams, size: int = 4
) -> list[RGB]:
    """Return up to ``size`` distinct colours from an image, best first.

    Clusters come from median cut refined by k-means, are ranked by the same
    vibrancy x coverage score as the *vibrant* method, and near-duplicates
    (CIE76 delta E below ``PALETTE_MIN_DELTA_E``) are dropped, so a poster
    that is mostly one hue may yield fewer than ``size`` colours. An empty
    list means the image is grey (with ``skip_achromatic``).

    :raises ColorExtractionError: if the image cannot be decoded.
    """
    return _palette_from_pixels(_load_pixels(image_bytes, params), params, size)


@dataclass(frozen=True, slots=True)
class ImageAnalysis:
    """Single colour and palette of one image (cached together)."""

    color: RGB | None
    palette: tuple[RGB, ...] = ()


def analyze_image(
    image_bytes: bytes, params: ExtractionParams, palette_size: int = 0
) -> ImageAnalysis:
    """Decode once and compute the colour plus (optionally) a palette.

    ``palette_size`` 0 skips the palette. In palette mode the first palette
    entry leads, so the "current colour" matches the first light.

    :raises ColorExtractionError: if the image cannot be decoded.
    """
    pixels = _load_pixels(image_bytes, params)
    color = _color_from_pixels(pixels, params)
    if palette_size <= 0:
        return ImageAnalysis(color)
    palette = tuple(_palette_from_pixels(pixels, params, palette_size))
    return ImageAnalysis(palette[0] if palette else color, palette)


def distribute_palette(
    palette: Iterable[RGB], lights: list[str], strategy: PaletteStrategy
) -> dict[str, RGB]:
    """Assign palette colours to ``lights`` (kept in configured order).

    * ``spread`` - round robin: 1, 2, 3, 1, 2, ...
    * ``dominant_first`` - the first light gets colour 1, the others cycle
      through the remaining colours (all get colour 1 for a 1-colour palette).
    * ``mirrored`` - symmetric from both ends, so left/right strips match:
      4 lights with 3 colours -> 1, 2, 2, 1.
    """
    colors = list(palette)
    if not colors or not lights:
        return {}
    n = len(colors)
    count = len(lights)
    result: dict[str, RGB] = {}
    for index, light in enumerate(lights):
        if strategy is PaletteStrategy.DOMINANT_FIRST:
            slot = 0 if index == 0 or n == 1 else 1 + (index - 1) % (n - 1)
        elif strategy is PaletteStrategy.MIRRORED:
            slot = min(index, count - 1 - index) % n
        else:
            slot = index % n
        result[light] = colors[slot]
    return result


def adjust_color(rgb: Iterable[float], params: AdjustParams) -> RGB:
    """Make a colour suitable for bias lighting.

    * Saturation is multiplied by ``saturation_boost`` and then raised to at
      least ``min_saturation`` (hue is preserved).
    * HSV value is raised to at least ``min_brightness`` so very dark posters
      do not produce an almost-off light. Overall light brightness is still
      controlled separately via ``brightness_pct``.
    """
    r, g, b = clamp_rgb(rgb)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    s = min(1.0, max(params.min_saturation, s * params.saturation_boost))
    v = min(1.0, max(params.min_brightness, v))
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    return clamp_rgb((nr * 255, ng * 255, nb * 255))


# --------------------------------------------------------------------------- #
# Titles & genres
# --------------------------------------------------------------------------- #
def normalize_title(title: str | None) -> str:
    """Return a normalised key for title matching.

    ``"The Dark Knight (2008)"``, ``"dark knight"`` and ``"Dark  Knight!"``
    all normalise to ``"dark knight"``. Accents are stripped, case folded,
    leading English articles and a trailing ``(year)`` removed, and
    punctuation collapsed.
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", str(title))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("&", " and ")
    text = _YEAR_RE.sub(" ", text)
    text = _NON_ALNUM_RE.sub(" ", text).strip()
    text = re.sub(r"\s+", " ", text)
    for article in _LEADING_ARTICLES:
        if text.startswith(article) and len(text) > len(article):
            text = text[len(article) :]
            break
    return text


# --------------------------------------------------------------------------- #
# Title cleanup (box sets, editions, quality tags)
# --------------------------------------------------------------------------- #
_APOS = "['\u2019]?"
_NUMBER_WORD = r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten)"
_COLLECTION_TAG = (
    rf"(?:{_NUMBER_WORD}[\s-]*(?:movie|film|disc|feature)s?[\s-]*"
    r"(?:collection|set|pack|box\s*set|bundle)"
    r"|(?:the\s+)?(?:complete\s+|ultimate\s+|essential\s+)?"
    r"(?:collection|trilogy|quadrilogy|duology|anthology|saga|box\s*set"
    r"|double\s+feature|triple\s+feature|movie\s+collection|film\s+collection))"
)
_EDITION_TAG = (
    r"(?:(?:\d+(?:st|nd|rd|th)\s+)?anniversary\s+edition"
    r"|(?:extended|theatrical|unrated|uncut|special|collector" + _APOS + r"s"
    r"|ultimate|remastered|limited|criterion|imax|definitive)"
    r"\s+(?:edition|cut|version)"
    r"|director" + _APOS + r"s\s+cut|(?:the\s+)?final\s+cut"
    r"|extended|unrated|remastered)"
)
_QUALITY_TAG = (
    r"(?:4k(?:\s+ultra\s+hd)?(?:\s*\+\s*blu-?ray)?|ultra\s+hd|uhd|hdr(?:10\+?)?"
    r"|dolby\s+vision|imax|2160p|1080p|blu-?ray)"
)
_ANY_TAG = rf"(?:{_COLLECTION_TAG}|{_EDITION_TAG}|{_QUALITY_TAG})"
_TAG_LIST = rf"{_ANY_TAG}(?:\s*(?:[/,+&]|and|-)?\s*{_ANY_TAG})*"
_CLEAN_PATTERNS = (
    # "Title (Director's Cut)", "Title [4K Ultra HD]"
    re.compile(rf"\s*[\(\[]\s*(?P<tag>{_TAG_LIST})\s*[\)\]]\s*$", re.IGNORECASE),
    # "Title: 2-Movie Collection", "Title - Extended Edition", "Title | 4K"
    re.compile(
        rf"\s*(?::|\s-\s|\u2013|\u2014|\|)\s*(?P<tag>{_TAG_LIST})\s*$",
        re.IGNORECASE,
    ),
    # "Title Trilogy", "Title Extended Edition 4K"
    re.compile(rf"\s+(?P<tag>{_TAG_LIST})\s*$", re.IGNORECASE),
)
_TRAILING_YEAR_RE = re.compile(r"\s*[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]\s*$")
_COLLECTION_RE = re.compile(_COLLECTION_TAG, re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CleanTitle:
    """A media title stripped of box-set / edition / quality decorations."""

    title: str
    year: int | None = None
    collection: bool = False  # the original was a collection / box set


def clean_title(title: object) -> CleanTitle | None:
    """Strip decorations that break title lookups and mappings.

    ``"High School Musical: 2-Movie Collection"`` -> ``"High School Musical"``
    (``collection=True``), ``"Blade Runner (Director's Cut) (1982)"`` ->
    ``"Blade Runner"`` (year 1982), ``"Aliens - Extended Edition 4K"`` ->
    ``"Aliens"``. Only *trailing* decorations are removed and never the whole
    title, so ``"The Collection"``, ``"The Final Cut"`` or ``"Uncut Gems"``
    are left alone. Returns ``None`` for an empty title.
    """
    if title is None:
        return None
    text = " ".join(str(title).split())
    if not text:
        return None
    year: int | None = None
    collection = False
    changed = True
    while changed:
        changed = False
        if (m := _TRAILING_YEAR_RE.search(text)) and _keeps_title(text[: m.start()]):
            year = year or int(m.group(1))
            text = text[: m.start()].rstrip()
            changed = True
            continue
        for pattern in _CLEAN_PATTERNS:
            m = pattern.search(text)
            if m is None or not _keeps_title(text[: m.start()]):
                continue
            collection = collection or bool(_COLLECTION_RE.search(m.group("tag")))
            text = text[: m.start()].rstrip(" :-\u2013\u2014|,")
            changed = True
            break
    return CleanTitle(text, year, collection)


def _keeps_title(rest: str) -> bool:
    """Whether ``rest`` is still a real title (not empty / a bare article)."""
    key = _NON_ALNUM_RE.sub(" ", rest.casefold()).strip()
    return bool(key) and key not in {"the", "a", "an"}


def _canonical_genre(raw: str) -> str | None:
    """Map a raw genre string onto a GENRE_COLORS key."""
    key = raw.strip().casefold()
    key = GENRE_ALIASES.get(key, key)
    if key in GENRE_COLORS:
        return key
    # "Action Thriller", "Horror/Comedy" … use the first known word.
    for raw_part in re.split(r"[/,|;&]+|\s+", key):
        part = GENRE_ALIASES.get(raw_part.strip(), raw_part.strip())
        if part in GENRE_COLORS:
            return part
    return None


def iter_genres(value: object) -> list[str]:
    """Flatten a genre attribute (str, comma list or list) into strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [v for v in re.split(r"[,;|]", value) if v.strip()]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(iter_genres(item))
        return out
    return [str(value)]


def match_genre_color(
    genres: Iterable[str], titles: Iterable[str | None]
) -> tuple[RGB, str] | None:
    """Return ``(rgb, genre)`` from explicit genres or title keywords."""
    for raw in genres:
        if genre := _canonical_genre(raw):
            return GENRE_COLORS[genre], genre
    for title in titles:
        words = normalize_title(title).split()
        for word in words:
            if genre := TITLE_KEYWORD_GENRES.get(word):
                return GENRE_COLORS[genre], genre
    return None
