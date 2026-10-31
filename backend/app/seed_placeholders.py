"""Placeholder image generator for AI Animal Lab.

Draws cute GEOMETRIC animal faces (cat / dog / rabbit / bird / fish) with
Pillow — ellipses, polygons and lines on a soft pastel gradient background.
10 variants per category, each differing in background hue and face
proportions so every image is visually distinguishable. 640x640 JPEG q88 with
a small semi-transparent "placeholder" tag in a corner.

`seed_if_empty(data_dir)` fills the images table ONLY when it is empty.
Run directly with:  python -m app.seed_placeholders
"""
from __future__ import annotations

import colorsys
import json
import random
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import db
from .config import settings

SIZE = 640
JPEG_QUALITY = 88
VARIANTS_PER_CATEGORY = 10
FONT_DIR = Path("/usr/share/fonts/noto")
FONT_CANDIDATES = (
    "NotoSans-Regular.ttf",
    "NotoSansDisplay-Regular.ttf",
    "NotoSans-Medium.ttf",
    "DejaVuSans.ttf",
)

# base background hue per category (variants rotate around these)
BASE_HUE = {"cat": 30, "dog": 205, "rabbit": 320, "bird": 55, "fish": 170}

# base fur / body colour per category
BASE_FUR = {
    "cat": (235, 173, 115),
    "dog": (184, 141, 94),
    "rabbit": (214, 206, 224),
    "bird": (247, 208, 100),
    "fish": (110, 192, 198),
}


# --------------------------------------------------------------------------
# drawing utilities
# --------------------------------------------------------------------------

def _pastel(hue: float, sat: float = 0.45, light: float = 0.88) -> tuple:
    r, g, b = colorsys.hls_to_rgb((hue % 360) / 360.0, light, sat)
    return (round(r * 255), round(g * 255), round(b * 255))


def _shade(color: tuple, factor: float) -> tuple:
    return tuple(max(0, min(255, round(c * factor))) for c in color)


def _jitter_color(rng: random.Random, color: tuple, amount: float = 0.08) -> tuple:
    return _shade(color, 1.0 + rng.uniform(-amount, amount))


def _gradient_image(size: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (size, size), top)
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / (size - 1)
        row = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=row)
    return img


def _load_font(size: int):
    if FONT_DIR.is_dir():
        for name in FONT_CANDIDATES:
            p = FONT_DIR / name
            if p.is_file():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    continue
        for p in sorted(FONT_DIR.glob("NotoSans*.ttf")):
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _add_placeholder_tag(img: Image.Image) -> Image.Image:
    """Tiny semi-transparent 'placeholder' text in the bottom-right corner."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    font = _load_font(22)
    text = "placeholder"
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    x = img.size[0] - (right - left) - 18
    y = img.size[1] - (bottom - top) - 14
    d.text((x, y), text, font=font, fill=(50, 45, 45, 110))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _eye(d: ImageDraw.ImageDraw, x: float, y: float, r: float) -> None:
    d.ellipse([x - r, y - r, x + r, y + r], fill=(62, 52, 52))
    d.ellipse([x - r * 0.55, y - r * 0.6, x - r * 0.1, y - r * 0.15],
              fill=(255, 255, 255))


# --------------------------------------------------------------------------
# the five animal faces (cx, cy = face centre; r = head radius)
# --------------------------------------------------------------------------

def draw_cat(d, cx, cy, r, k):
    ear = k["ear"]
    for sx in (-1, 1):
        d.polygon([
            (cx + sx * r * 0.85, cy - r * 0.5),
            (cx + sx * (0.5 + ear * 0.25) * r, cy - r * (1.0 + ear * 0.25)),
            (cx + sx * r * 0.2, cy - r * 0.92),
        ], fill=k["fur"])
        d.polygon([
            (cx + sx * r * 0.72, cy - r * 0.58),
            (cx + sx * (0.52 + ear * 0.2) * r, cy - r * (0.86 + ear * 0.2)),
            (cx + sx * r * 0.34, cy - r * 0.82),
        ], fill=k["inner"])
    d.ellipse([cx - r, cy - r * 0.92, cx + r, cy + r * 0.92], fill=k["fur"])
    for i in (-1, 0, 1):  # forehead stripes
        x = cx + i * r * 0.24
        d.line([(x, cy - r * 0.88), (x, cy - r * 0.52)],
               fill=k["shade"], width=max(3, int(r * 0.07)))
    er = r * k["eye"]
    _eye(d, cx - r * 0.4, cy - r * 0.1, er)
    _eye(d, cx + r * 0.4, cy - r * 0.1, er)
    d.polygon([(cx - r * 0.13, cy + r * 0.22), (cx + r * 0.13, cy + r * 0.22),
               (cx, cy + r * 0.4)], fill=(233, 140, 152))
    w = max(2, int(r * 0.04))
    d.arc([cx - r * 0.3, cy + r * 0.18, cx, cy + r * 0.58], 20, 90,
          fill=k["shade"], width=w)
    d.arc([cx, cy + r * 0.18, cx + r * 0.3, cy + r * 0.58], 90, 160,
          fill=k["shade"], width=w)
    for sy in (-1, 1):  # whiskers
        for vy in (0.16, 0.3, 0.44):
            d.line([(cx + sy * r * 0.3, cy + r * vy),
                    (cx + sy * r * 0.98, cy + r * (vy - 0.18))],
                   fill=k["shade"], width=max(2, int(r * 0.03)))


def draw_dog(d, cx, cy, r, k):
    for sx in (-1, 1):  # floppy ears
        ex = cx + sx * r * 0.95
        d.ellipse([ex - r * 0.3, cy - r * (0.7 + k["ear"] * 0.2),
                   ex + r * 0.3, cy + r * (0.4 + k["ear"] * 0.4)],
                  fill=k["shade"])
    d.ellipse([cx - r, cy - r * 0.95, cx + r, cy + r * 0.95], fill=k["fur"])
    sn = r * k["snout"]
    d.ellipse([cx - r * 0.55, cy + r * 0.12 - sn, cx + r * 0.55,
               cy + r * 0.12 + sn], fill=k["light"])
    d.ellipse([cx - r * 0.17, cy - r * 0.08, cx + r * 0.17, cy + r * 0.2],
              fill=(60, 50, 50))
    w = max(2, int(r * 0.045))
    d.line([(cx, cy + r * 0.2), (cx, cy + r * 0.38)], fill=(60, 50, 50), width=w)
    d.arc([cx - r * 0.22, cy + r * 0.16, cx, cy + r * 0.44], 0, 90,
          fill=(60, 50, 50), width=w)
    d.arc([cx, cy + r * 0.16, cx + r * 0.22, cy + r * 0.44], 90, 180,
          fill=(60, 50, 50), width=w)
    d.ellipse([cx - r * 0.14, cy + r * 0.36, cx + r * 0.14, cy + r * 0.72],
              fill=(240, 130, 140))  # tongue
    er = r * k["eye"]
    _eye(d, cx - r * 0.42, cy - r * 0.25, er)
    _eye(d, cx + r * 0.42, cy - r * 0.25, er)
    for sx in (-1, 1):  # brow dots
        bx = cx + sx * r * 0.42
        d.ellipse([bx - r * 0.07, cy - r * 0.55, bx + r * 0.07, cy - r * 0.44],
                  fill=k["shade"])


def draw_rabbit(d, cx, cy, r, k):
    ear_len = r * (1.15 + k["ear"] * 0.5)
    ear_w = r * 0.32
    for sx in (-1, 1):
        ex = cx + sx * r * 0.45 + sx * r * 0.1
        d.ellipse([ex - ear_w, cy - r * 0.65 - ear_len, ex + ear_w, cy - r * 0.55],
                  fill=k["fur"])
        d.ellipse([ex - ear_w * 0.45, cy - r * 0.72 - ear_len * 0.9,
                   ex + ear_w * 0.45, cy - r * 0.68], fill=k["inner"])
    d.ellipse([cx - r * 0.95, cy - r * 0.8, cx + r * 0.95, cy + r * 0.85],
              fill=k["fur"])
    er = r * k["eye"]
    _eye(d, cx - r * 0.38, cy - r * 0.05, er)
    _eye(d, cx + r * 0.38, cy - r * 0.05, er)
    for sx in (-1, 1):  # cheeks
        chx = cx + sx * r * 0.55
        d.ellipse([chx - r * 0.22, cy + r * 0.22, chx + r * 0.22, cy + r * 0.52],
                  fill=k["inner"])
    d.polygon([(cx - r * 0.1, cy + r * 0.14), (cx + r * 0.1, cy + r * 0.14),
               (cx, cy + r * 0.28)], fill=(233, 140, 152))
    w = max(2, int(r * 0.04))
    d.line([(cx, cy + r * 0.28), (cx, cy + r * 0.42)], fill=k["shade"], width=w)
    d.arc([cx - r * 0.18, cy + r * 0.24, cx, cy + r * 0.46], 0, 90,
          fill=k["shade"], width=w)
    d.arc([cx, cy + r * 0.24, cx + r * 0.18, cy + r * 0.46], 90, 180,
          fill=k["shade"], width=w)
    tw = r * 0.16  # front teeth
    d.rectangle([cx - tw, cy + r * 0.4, cx - 1, cy + r * 0.62],
                fill=(255, 255, 255), outline=k["shade"], width=1)
    d.rectangle([cx + 1, cy + r * 0.4, cx + tw, cy + r * 0.62],
                fill=(255, 255, 255), outline=k["shade"], width=1)


def draw_bird(d, cx, cy, r, k):
    head_r = r * (0.72 + k["head"] * 0.2)
    hx, hy = cx, cy - r * 0.35
    d.ellipse([cx - r * 0.85, cy - r * 0.05, cx + r * 0.85, cy + r * 1.0],
              fill=k["fur"])  # body
    wing = k["wing"]
    d.ellipse([cx - r * 0.55, cy + r * (0.15 + wing * 0.1),
               cx + r * 0.25, cy + r * (0.6 + wing * 0.2)], fill=k["shade"])
    d.ellipse([hx - head_r, hy - head_r, hx + head_r, hy + head_r], fill=k["fur"])
    for sx in (-1, 1):  # head tuft
        d.line([(hx + sx * r * 0.1, hy - head_r * 0.95),
                (hx + sx * r * 0.28, hy - head_r * (1.25 + k["ear"] * 0.2))],
               fill=k["shade"], width=max(3, int(r * 0.05)))
    er = r * k["eye"] * 1.1
    _eye(d, hx - r * 0.26, hy - r * 0.05, er)
    _eye(d, hx + r * 0.26, hy - r * 0.05, er)
    by = hy + r * 0.28  # diamond beak
    bw = r * (0.16 + k["snout"] * 0.08)
    d.polygon([(hx - bw, by - bw * 0.7), (hx + bw, by - bw * 0.7),
               (hx, by + bw * 1.1)], fill=(240, 150, 70))
    for sx in (-1, 1):  # feet
        fx = cx + sx * r * 0.3
        d.line([(fx, cy + r * 0.95), (fx, cy + r * 1.12)],
               fill=(240, 150, 70), width=max(3, int(r * 0.05)))


def draw_fish(d, cx, cy, r, k):
    d.polygon([  # tail
        (cx - r * 0.85, cy),
        (cx - r * (1.35 + k["ear"] * 0.2), cy - r * 0.55),
        (cx - r * (1.35 + k["ear"] * 0.2), cy + r * 0.55),
    ], fill=k["shade"])
    d.polygon([  # top fin
        (cx - r * 0.25, cy - r * 0.48),
        (cx + r * 0.3, cy - r * 0.48),
        (cx + r * 0.02, cy - r * (0.85 + k["fin"] * 0.3)),
    ], fill=k["shade"])
    d.ellipse([cx - r * 1.1, cy - r * 0.55, cx + r * 1.05, cy + r * 0.55],
              fill=k["fur"])
    d.arc([cx + r * 0.12, cy - r * 0.4, cx + r * 0.6, cy + r * 0.4],
          60, 300, fill=k["shade"], width=max(3, int(r * 0.05)))  # gill
    er = r * k["eye"] * 1.15
    _eye(d, cx + r * 0.55, cy - r * 0.12, er)
    d.arc([cx + r * 0.72, cy + r * 0.02, cx + r * 0.98, cy + r * 0.22],
          0, 120, fill=k["shade"], width=max(2, int(r * 0.04)))  # mouth
    for i in range(3):  # scales (two staggered rows of small arcs)
        sx = cx - r * (0.55 + i * 0.28)
        for sy_ in (0.0, 0.26):
            y = cy - r * 0.12 + sy_ * r
            d.arc([sx - r * 0.14, y - r * 0.12, sx + r * 0.14, y + r * 0.12],
                  0, 180, fill=k["shade"], width=2)
    for i, (bx, by, br) in enumerate(  # bubbles
        [(cx + r * 0.9, cy - r * 0.75, 0.07),
         (cx + r * 1.15, cy - r * 1.0, 0.05),
         (cx + r * 0.75, cy - r * 1.05, 0.04)]
    ):
        b = br * r * (1 + k["wing"] * 0.3)
        d.ellipse([bx - b, by - b, bx + b, by + b],
                  outline=(255, 255, 255), width=max(2, int(r * 0.03)))


_DRAWERS = {"cat": draw_cat, "dog": draw_dog, "rabbit": draw_rabbit,
            "bird": draw_bird, "fish": draw_fish}

# proportion params every drawer reads from the variant dict `k`
_PARAM_KEYS = ("ear", "eye", "snout", "head", "wing", "fin")


# --------------------------------------------------------------------------
# variant + seeding
# --------------------------------------------------------------------------

def generate_variant(category: str, variant: int) -> Image.Image:
    """One 640x640 placeholder; variant index changes hue + proportions."""
    rng = random.Random(f"{category}:{variant}")
    hue = BASE_HUE.get(category, variant * 36) + variant * (360 / VARIANTS_PER_CATEGORY)
    top = _pastel(hue, sat=0.5, light=0.9)
    bottom = _pastel(hue + 20, sat=0.42, light=0.78)
    img = _gradient_image(SIZE, top, bottom)
    d = ImageDraw.Draw(img)

    k = {key: round(rng.uniform(0.0, 1.0), 3) for key in _PARAM_KEYS}
    k["eye"] = 0.09 + k["eye"] * 0.05   # eye radius 0.09..0.14 of head radius
    fur = _jitter_color(rng, BASE_FUR.get(category, (180, 180, 180)))
    pal = {
        "fur": fur,
        "shade": _shade(fur, 0.72),
        "light": _shade(fur, 1.25),
        "inner": _jitter_color(rng, (244, 187, 196), 0.05),
    }
    drawer = _DRAWERS.get(category, draw_cat)
    drawer(d, SIZE // 2, SIZE // 2 + 12, 170, k | pal)
    return _add_placeholder_tag(img)


def seed_if_empty(data_dir: str | Path | None = None) -> int:
    """Seed placeholder images + rows ONLY when the images table is empty.
    Returns the number of images created (0 when already seeded)."""
    if data_dir is not None:
        db.set_data_dir(data_dir)
    db.init_db()
    if db.count_images() != 0:
        return 0
    categories = list(settings.categories)
    created = 0
    for category in categories:
        for variant in range(VARIANTS_PER_CATEGORY):
            img = generate_variant(category, variant)
            filename = uuid.uuid4().hex + ".jpg"
            img.save(db.images_dir() / filename, "JPEG", quality=JPEG_QUALITY)
            choices = db.compute_choices(category, categories)
            db.insert_image(
                filename,
                f"{category}_placeholder_{variant + 1:02d}.jpg",
                category,
                json.dumps(choices),
            )
            created += 1
    return created


if __name__ == "__main__":
    db.init_db()
    n = seed_if_empty()
    if n:
        print(f"seeded {n} placeholder images into {db.images_dir()}")
    else:
        print(f"images table not empty — nothing seeded ({db.images_dir()})")
