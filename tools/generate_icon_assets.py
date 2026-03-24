from __future__ import annotations

from pathlib import Path
import math
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
ICONSET_DIR = ASSETS_DIR / "openclaw.iconset"
PNG_PATH = ASSETS_DIR / "openclaw.png"
ICO_PATH = ASSETS_DIR / "openclaw.ico"
ICNS_PATH = ASSETS_DIR / "openclaw.icns"


def lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def draw_gradient(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    pixels = image.load()
    top = (6, 24, 38)
    bottom = (13, 72, 88)
    for y in range(size):
        t = y / max(size - 1, 1)
        row = (
            lerp(top[0], bottom[0], t),
            lerp(top[1], bottom[1], t),
            lerp(top[2], bottom[2], t),
            255,
        )
        for x in range(size):
            pixels[x, y] = row
    return image


def draw_icon(size: int) -> Image.Image:
    image = draw_gradient(size)
    draw = ImageDraw.Draw(image)
    margin = int(size * 0.08)
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=int(size * 0.22),
        outline=(255, 255, 255, 36),
        width=max(2, size // 64),
    )

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (
            int(size * 0.18),
            int(size * 0.10),
            int(size * 0.82),
            int(size * 0.74),
        ),
        fill=(255, 177, 64, 48),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(4, size // 20)))
    image.alpha_composite(glow)

    shell_box = (
        int(size * 0.22),
        int(size * 0.54),
        int(size * 0.78),
        int(size * 0.75),
    )
    draw.rounded_rectangle(
        shell_box,
        radius=int(size * 0.05),
        fill=(15, 33, 45, 255),
        outline=(142, 207, 217, 120),
        width=max(2, size // 96),
    )
    draw.rectangle(
        (
            int(size * 0.22),
            int(size * 0.54),
            int(size * 0.78),
            int(size * 0.59),
        ),
        fill=(22, 58, 72, 255),
    )

    accent = (255, 184, 77, 255)
    accent_soft = (255, 209, 120, 255)
    claw_points = [
        [
            (0.34, 0.23),
            (0.45, 0.10),
            (0.49, 0.28),
            (0.43, 0.46),
            (0.33, 0.39),
        ],
        [
            (0.50, 0.16),
            (0.62, 0.08),
            (0.63, 0.30),
            (0.57, 0.47),
            (0.48, 0.39),
        ],
        [
            (0.63, 0.22),
            (0.74, 0.17),
            (0.70, 0.36),
            (0.62, 0.50),
            (0.56, 0.38),
        ],
    ]
    for index, points in enumerate(claw_points):
        fill = accent if index < 2 else accent_soft
        draw.polygon([(int(size * x), int(size * y)) for x, y in points], fill=fill)

    draw.arc(
        (
            int(size * 0.26),
            int(size * 0.26),
            int(size * 0.72),
            int(size * 0.60),
        ),
        start=10,
        end=165,
        fill=(255, 224, 170, 220),
        width=max(3, size // 64),
    )

    term = (214, 244, 248, 255)
    width = max(3, size // 64)
    draw.line(
        (
            int(size * 0.34),
            int(size * 0.64),
            int(size * 0.42),
            int(size * 0.68),
            int(size * 0.34),
            int(size * 0.72),
        ),
        fill=term,
        width=width,
        joint="curve",
    )
    draw.line(
        (
            int(size * 0.48),
            int(size * 0.72),
            int(size * 0.63),
            int(size * 0.72),
        ),
        fill=term,
        width=width,
    )

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
