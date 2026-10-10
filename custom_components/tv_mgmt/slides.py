"""Full-screen message slides, AirPlayed to the Apple TV as short still videos.

Pure module (Pillow only, no Home Assistant imports). Used at runtime for
messages parents type on the dashboard, and by scripts/make_airplay_videos.py
for the built-in warnings.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
FONTS = HERE / "fonts"
ICON = HERE / "brand" / "dark_icon@2x.png"
W, H = 1920, 1080
SECONDS = 10

AMBER = (255, 170, 40)
RED = (235, 87, 87)
BLUE = (120, 150, 255)
MAX_WIDTH = W - 240


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if not line or draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit(draw: ImageDraw.ImageDraw, text: str, sizes: tuple[int, ...], max_lines: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """The biggest title size that fits in max_lines."""
    for size in sizes:
        font = _font("InterDisplay-SemiBold.otf", size)
        lines = _wrap(draw, text, font, MAX_WIDTH)
        if len(lines) <= max_lines and all(draw.textlength(l, font=font) <= MAX_WIDTH for l in lines):
            return font, lines
    font = _font("InterDisplay-SemiBold.otf", sizes[-1])
    lines = _wrap(draw, text, font, MAX_WIDTH)[:max_lines]
    return font, lines


def render(title: str, sub: str = "", accent: tuple[int, int, int] = BLUE) -> Image.Image:
    img = Image.new("RGB", (W, H), (14, 17, 22))
    # Soft glow at the top in the accent colour.
    glow = Image.new("RGB", (W, H), accent)
    mask = Image.new("L", (W, H), 0)
    mdraw = ImageDraw.Draw(mask)
    for i in range(60):
        mdraw.ellipse((W / 2 - 1200 + i * 12, -900 + i * 8, W / 2 + 1200 - i * 12, 500 - i * 4), fill=int(i * 0.9))
    img = Image.composite(glow, img, mask)
    draw = ImageDraw.Draw(img)

    title_font, lines = _fit(draw, title, (128, 112, 96, 84, 72), 3)
    line_h = int(title_font.size * 1.18)
    sub_font = _font("Inter-Regular.otf", 54)
    sub_lines = _wrap(draw, sub, sub_font, MAX_WIDTH)[:2] if sub else []
    icon_h = 140
    block = icon_h + 60 + line_h * len(lines) + (40 + 66 * len(sub_lines) if sub_lines else 0) + 60 + 10
    y = max(80, (H - block) // 2 - 20)

    if ICON.exists():
        icon = Image.open(ICON).convert("RGBA").resize((icon_h, icon_h))
        img.paste(icon, (W // 2 - icon_h // 2, y), icon)
    y += icon_h + 60
    for line in lines:
        draw.text(((W - draw.textlength(line, font=title_font)) / 2, y), line, font=title_font, fill=(243, 246, 250))
        y += line_h
    if sub_lines:
        y += 40
        for line in sub_lines:
            draw.text(((W - draw.textlength(line, font=sub_font)) / 2, y), line, font=sub_font, fill=(190, 198, 208))
            y += 66
    y += 60
    draw.rounded_rectangle((W / 2 - 90, y, W / 2 + 90, y + 10), radius=5, fill=accent)
    label_font = _font("Inter-Medium.otf", 34)
    draw.text(((W - draw.textlength("TV Mgmt", font=label_font)) / 2, H - 110), "TV Mgmt", font=label_font, fill=(120, 130, 142))
    return img


def to_video(image: Image.Image, out: Path, ffmpeg: str = "ffmpeg", seconds: int = SECONDS) -> None:
    """Write a still image as a short H.264 video the Apple TV can play over AirPlay."""
    out.parent.mkdir(parents=True, exist_ok=True)
    png = out.with_suffix(".png")
    image.save(png)
    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-framerate", "1", "-i", str(png),
                "-t", str(seconds), "-r", "10", "-c:v", "libx264", "-tune", "stillimage",
                "-pix_fmt", "yuv420p", "-profile:v", "high", "-crf", "24", "-an",
                "-movflags", "+faststart", str(out),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    finally:
        png.unlink(missing_ok=True)
