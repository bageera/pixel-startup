#!/usr/bin/env python3
"""G30-C: original 16x16 health-state sprites, drawn deterministically.

Eight states, each recognizable by SHAPE as well as color (G30-D: never rely
on color alone): seed (dot+shoot), growing (two-leaf sprout), thriving (bush
with berry), needs-attention (!-triangle), withering (drooped stem), dormant
(zzz dot), completed (checkered flag), archived (lidded box).

Deterministic: same code -> same bytes; `--check` verifies a re-render.
"""

import sys
from PIL import Image

OUT = "assets/health"

GREEN = (56, 142, 60)
LEAF = (102, 187, 106)
TRUNK = (121, 85, 72)
GOLD = (255, 179, 0)
SKY = (100, 181, 246)
RED = (229, 115, 115)
GRAY = (158, 158, 158)
INK = (33, 33, 33)


def px(im, x, y, c):
    im.putpixel((x, y), c + (255,))


def hline(im, x0, x1, y, c):
    for x in range(x0, x1 + 1):
        px(im, x, y, c)


def vline(im, x, y0, y1, c):
    for y in range(y0, y1 + 1):
        px(im, x, y, c)


def seed(im):
    px(im, 7, 11, TRUNK); px(im, 8, 11, TRUNK)
    px(im, 6, 12, TRUNK); px(im, 7, 12, TRUNK)
    px(im, 9, 3, GREEN); px(im, 8, 4, GREEN)  # shoot


def growing(im):
    vline(im, 8, 9, 13, TRUNK)
    hline(im, 4, 7, 8, LEAF)
    hline(im, 9, 12, 7, LEAF)


def thriving(im):
    for dx, dy in [(0, 0), (1, 0), (-1, 0), (0, -1), (0, 1), (1, -1), (-1, -1), (1, 1), (-1, 1), (2, -1)]:
        px(im, 6 + dx, 5 + dy, GREEN)
    px(im, 7, 4, LEAF)
    vline(im, 8, 10, 13, TRUNK)
    px(im, 9, 3, RED)  # berry


def attention(im):
    for i in range(5):
        hline(im, 8 - i, 8 + i, 12 - i, GOLD)
    px(im, 8, 7, INK); px(im, 8, 8, INK); px(im, 8, 10, INK)


def withering(im):
    px(im, 8, 5, GRAY); px(im, 8, 6, TRUNK)
    px(im, 7, 7, TRUNK); px(im, 8, 7, TRUNK); px(im, 9, 8, TRUNK)
    px(im, 6, 9, GRAY); px(im, 7, 9, TRUNK); px(im, 8, 9, TRUNK)
    px(im, 8, 10, TRUNK); px(im, 10, 10, GRAY)


def dormant(im):
    px(im, 7, 11, GRAY); px(im, 8, 11, GRAY); px(im, 7, 12, GRAY)
    px(im, 4, 4, SKY); px(im, 5, 5, SKY); px(im, 4, 6, SKY)  # z
    px(im, 9, 3, SKY); px(im, 10, 4, SKY); px(im, 9, 5, SKY)  # z


def completed(im):
    vline(im, 5, 4, 13, TRUNK)
    hline(im, 6, 13, 5, GREEN)
    hline(im, 6, 13, 8, GREEN)
    for x, y in [(7, 6), (8, 7), (9, 7), (10, 6)]:
        px(im, x, y, (255, 255, 255))


def archived(im):
    hline(im, 3, 12, 5, TRUNK)
    hline(im, 3, 12, 7, TRUNK)
    vline(im, 4, 8, 12, TRUNK); vline(im, 11, 8, 12, TRUNK)
    hline(im, 4, 11, 13, TRUNK)
    px(im, 7, 6, GOLD); px(im, 8, 6, GOLD)  # latch


SPRITES = {
    "seed": seed,
    "growing": growing,
    "thriving": thriving,
    "needs-attention": attention,
    "withering": withering,
    "dormant": dormant,
    "completed": completed,
    "archived": archived,
}


def render_all():
    import os

    os.makedirs(OUT, exist_ok=True)
    out = {}
    for name, fn in SPRITES.items():
        im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        fn(im)
        path = f"{OUT}/{name}.png"
        im.save(path)
        out[path] = list(im.getdata())
    return out


if __name__ == "__main__":
    data = render_all()
    if "--check" in sys.argv:
        import hashlib
        import json
        existing = {p: hashlib.md5(bytes(b for t in list(Image.open(p).getdata()) for b in t)).hexdigest()[:8] for p in data}
        fresh = {p: hashlib.md5(bytes(b for t in d for b in t)).hexdigest()[:8] for p, d in data.items()}
        sys.exit(0 if existing == fresh else 1)
    for p in sorted(data):
        print(p)
