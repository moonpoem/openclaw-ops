from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
ICONSET_DIR = ASSETS_DIR / "openclaw.iconset"
PNG_PATH = ASSETS_DIR / "openclaw.png"
ICO_PATH = ASSETS_DIR / "openclaw.ico"
ICNS_PATH = ASSETS_DIR / "openclaw.icns"


def draw_icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    teal = (14, 116, 144, 255)

    claws = [
        [(0.24, 0.80), (0.39, 0.28), (0.44, 0.29), (0.32, 0.81)],
        [(0.43, 0.82), (0.56, 0.14), (0.62, 0.15), (0.55, 0.83)],
        [(0.64, 0.78), (0.76, 0.37), (0.81, 0.38), (0.74, 0.79)],
    ]
    for points in claws:
        draw.polygon([(int(size * x), int(size * y)) for x, y in points], fill=teal)

    return image


def write_iconset() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if ICONSET_DIR.exists():
        shutil.rmtree(ICONSET_DIR)
    ICONSET_DIR.mkdir(parents=True, exist_ok=True)

    base = draw_icon(1024)
    base.save(PNG_PATH)
    base.save(ICO_PATH, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    icon_sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, size in icon_sizes.items():
        if size == 1024:
            image = base
        else:
            image = base.resize((size, size), Image.Resampling.LANCZOS)
        image.save(ICONSET_DIR / filename)

    subprocess.run(["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(ICNS_PATH)], check=True)


def main() -> int:
    write_iconset()
    print(f"Generated {PNG_PATH}")
    print(f"Generated {ICO_PATH}")
    print(f"Generated {ICNS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
