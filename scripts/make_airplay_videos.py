"""Build the short still videos TV Mgmt can AirPlay to the Apple TV.

Run from the repo root: python scripts/make_airplay_videos.py
Needs Pillow and ffmpeg. Fonts come with the integration. The videos have no sound track, so
the Apple TV integration plays them over AirPlay as video.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "tv_mgmt"))
from slides import AMBER, BLUE, RED, render, to_video  # noqa: E402

OUT = Path("custom_components/tv_mgmt/frontend/airplay")

SLIDES = {
    "almost-up": ("TV time is almost up", "Finish what you're watching. The TV turns off soon.", AMBER),
    "time-up": ("TV time is up", "The TV is turning off now.", RED),
    "quiet-time": ("It's quiet time", "The TV is turning off now.", BLUE),
}
# "5 minutes of TV time left", for the usual warning times. Others use "almost-up".
WARN_MINUTES = [*range(1, 16), 20, 25, 30, 45, 60]
for _m in WARN_MINUTES:
    SLIDES[f"left-{_m}"] = (
        f"{_m} minute{'s' if _m != 1 else ''} of TV time left",
        "Finish what you're watching. The TV turns off soon.",
        AMBER,
    )
DOCS = Path("docs/images")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (title, sub, accent) in SLIDES.items():
        image = render(title, sub, accent)
        to_video(image, OUT / f"{name}.mp4")
        if not name.startswith("left-") or name == "left-5":
            # Previews for the README.
            image.resize((image.width // 2, image.height // 2)).save(DOCS / f"airplay-{name}.jpg", quality=85)


if __name__ == "__main__":
    main()
