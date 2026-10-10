"""Build the short still videos TV Mgmt can AirPlay to the Apple TV.

Run from the repo root: python scripts/make_airplay_videos.py
Needs Pillow, ffmpeg and the Inter font. The videos have no sound track, so
the Apple TV integration plays them over AirPlay as video.
"""

from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

OUT = Path("custom_components/tv_mgmt/frontend/airplay")
FONTS = Path("/usr/share/fonts/opentype/inter")
ICON = Path("custom_components/tv_mgmt/brand/dark_icon@2x.png")
W, H = 1920, 1080
SECONDS = 10

SLIDES = {
    "almost-up": ("TV time is almost up", "Finish what you're watching. The TV turns off soon.", (255, 170, 40)),
    "time-up": ("TV time is up", "The TV is turning off now.", (235, 87, 87)),
    "quiet-time": ("It's quiet time", "The TV is turning off now.", (120, 150, 255)),
}
# "5 minutes of TV time left", for the usual warning times. Others use "almost-up".
WARN_MINUTES = [*range(1, 16), 20, 25, 30, 45, 60]
for _m in WARN_MINUTES:
    SLIDES[f"left-{_m}"] = (
        f"{_m} minute{'s' if _m != 1 else ''} of TV time left",
        "Finish what you're watching. The TV turns off soon.",
        (255, 170, 40),
    )
DOCS = Path("docs/images")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def slide(title: str, sub: str, accent: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (W, H), (14, 17, 22))
    draw = ImageDraw.Draw(img)
    # Soft glow at the top in the accent colour.
    glow = Image.new("RGB", (W, H), accent)
    mask = Image.new("L", (W, H), 0)
    mdraw = ImageDraw.Draw(mask)
    for i in range(60):
        mdraw.ellipse((W / 2 - 1200 + i * 12, -900 + i * 8, W / 2 + 1200 - i * 12, 500 - i * 4), fill=int(i * 0.9))
    img = Image.composite(glow, img, mask)
    draw = ImageDraw.Draw(img)

    if ICON.exists():
        icon = Image.open(ICON).convert("RGBA").resize((140, 140))
        img.paste(icon, (W // 2 - 70, 250), icon)
    title_font = font("InterDisplay-SemiBold.otf", 128)
    sub_font = font("Inter-Regular.otf", 54)
    tw = draw.textlength(title, font=title_font)
    draw.text(((W - tw) / 2, 450), title, font=title_font, fill=(243, 246, 250))
    sw = draw.textlength(sub, font=sub_font)
    draw.text(((W - sw) / 2, 630), sub, font=sub_font, fill=(190, 198, 208))
    # A bar in the accent colour.
    draw.rounded_rectangle((W / 2 - 90, 770, W / 2 + 90, 780), radius=5, fill=accent)
    label_font = font("Inter-Medium.otf", 34)
    label = "TV Mgmt"
    lw = draw.textlength(label, font=label_font)
    draw.text(((W - lw) / 2, H - 110), label, font=label_font, fill=(120, 130, 142))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, (title, sub, accent) in SLIDES.items():
            png = Path(tmp) / f"{name}.png"
            slide(title, sub, accent).save(png)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "1", "-i", str(png),
                    "-t", str(SECONDS), "-r", "10", "-c:v", "libx264", "-tune", "stillimage",
                    "-pix_fmt", "yuv420p", "-profile:v", "high", "-crf", "24", "-an",
                    "-movflags", "+faststart", str(OUT / f"{name}.mp4"),
                ],
                check=True,
            )
            if not name.startswith("left-") or name == "left-5":
                # Previews for the README.
                slide(title, sub, accent).resize((W // 2, H // 2)).save(DOCS / f"airplay-{name}.jpg", quality=85)


if __name__ == "__main__":
    main()
