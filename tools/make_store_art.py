"""ストア用の画像を、木のプレビュー(ui/preview)のスクリーンショットから作る。

    cd ui && npx vite build -c vite.preview.config.ts && node preview/shot.mjs   # → PNG のパスが出る
    uv run --with pillow tools/make_store_art.py <その PNG>

出力: assets/icon.png(512x512 不透明)、assets/hero.png(1200x675)、assets/hero-detail.png(1200x288)、
      assets/screenshots/forest.png(1280x720)
プレビューは 2300x1100 で撮られ、Crowded / Sparse / Lifecycle の3枚の木が並ぶ前提で切り出す。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
LIFECYCLE = (1537, 202, 2271, 726)  # 3枚目の木(枝が層ごとに揃っている)
HERO_TREE = (1590, 250, 2250, 700)  # 3枚目の木(層の名前は main で消す)
LAYER_LABELS = [(1552, 322, 1660, 344), (1552, 427, 1660, 449), (1552, 532, 1660, 554)]  # CORE / KNOWLEDGE / LOG
SPARSE_TREE = (982, 262, 1334, 666)  # 2枚目の木の幹と葉(左右の枝の名前は入れない)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["seguisb.ttf", "segoeui.ttf"] if bold else ["segoeui.ttf"]
    for n in names + ["meiryo.ttc", "arial.ttf", "DejaVuSans.ttf"]:
        for d in (Path("C:/Windows/Fonts"), Path("/System/Library/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (d / n).exists():
                return ImageFont.truetype(str(d / n), size)
    return ImageFont.load_default()


def fit(img: Image.Image, w: int, h: int, bg: tuple[int, int, int]) -> Image.Image:
    out = Image.new("RGB", (w, h), bg)
    s = min(w / img.width, h / img.height)
    im = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    out.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return out


def fade_left(img: Image.Image, width: int) -> Image.Image:
    """左端 width px を透明から不透明へ。木の切り口を地の色になじませる。"""
    m = Image.new("L", img.size, 255)
    for x in range(width):
        Image.Image.paste(m, round(255 * x / width), (x, 0, x + 1, img.height))
    return m


def main(src: Path) -> None:
    shot = Image.open(src).convert("RGB")
    d0 = ImageDraw.Draw(shot)
    for x0, y0, x1, y1 in LAYER_LABELS:  # 層の名前は切り出すと半端に切れるので消す
        d0.rectangle((x0, y0, x1, y1), fill=shot.getpixel((x0 - 4, (y0 + y1) // 2)))
    assets = REPO / "assets"
    (assets / "screenshots").mkdir(parents=True, exist_ok=True)
    card = shot.getpixel((LIFECYCLE[0] + 10, LIFECYCLE[1] + 10))
    ht = shot.crop(HERO_TREE)

    # hero 1200x675: 左に名前、右に木
    hero = Image.new("RGB", (1200, 675), card)
    t = ht.resize((round(ht.width * 520 / ht.height), 520), Image.LANCZOS)
    hero.paste(t, (1200 - t.width - 20, 80), fade_left(t, 90))
    d = ImageDraw.Draw(hero)
    d.text((64, 200), "Stira Forest", font=font(76, True), fill=(236, 240, 232))
    d.text((68, 300), "for Kiro Crew", font=font(36), fill=(134, 200, 120))
    for k, line in enumerate(["An immutable core.", "Knowledge that grows", "from your crew's work."]):
        d.text((68, 380 + k * 42), line, font=font(30), fill=(200, 205, 198))
    hero.save(assets / "hero.png")

    # hero-detail 1200x288: 左に名前、右に木を丸ごと縮めて
    band = Image.new("RGB", (1200, 288), card)
    bt = ht.resize((round(ht.width * 268 / ht.height), 268), Image.LANCZOS)
    band.paste(bt, (1200 - bt.width - 40, 10), fade_left(bt, 50))
    b = ImageDraw.Draw(band)
    b.text((56, 88), "Stira Forest", font=font(64, True), fill=(236, 240, 232))
    b.text((60, 176), "for Kiro Crew", font=font(30), fill=(134, 200, 120))
    band.save(assets / "hero-detail.png")

    # icon 512x512: 木1本(左右の枝の名前は入れない)
    leaf = shot.crop(SPARSE_TREE)
    side = max(leaf.size)
    sq = Image.new("RGB", (side, side), shot.getpixel((SPARSE_TREE[0] + 4, SPARSE_TREE[1] + 4)))
    sq.paste(leaf, ((side - leaf.width) // 2, 0))
    sq.resize((512, 512), Image.LANCZOS).save(assets / "icon.png")

    # screenshot 1280x720: 3枚目の木(層の名前つきの元画像で)
    orig = Image.open(src).convert("RGB")
    fit(orig.crop(LIFECYCLE), 1280, 720, card).save(assets / "screenshots" / "forest.png")
    for p in ["icon.png", "hero.png", "hero-detail.png", "screenshots/forest.png"]:
        im = Image.open(assets / p)
        print(p, im.size, im.mode)

if __name__ == "__main__":
    main(Path(sys.argv[1]))
