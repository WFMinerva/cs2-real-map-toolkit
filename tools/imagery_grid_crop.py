# -*- coding: utf-8 -*-
"""imagery_grid_crop.py —— 从影像镶嵌图裁一块并叠经纬网格（判读/描摹用，只读图）。

用法：
  python tools/imagery_grid_crop.py <镶嵌图.png> <输出.jpg> <S> <W> <N> <E> [--px 1400] [--step-m 500]
参数 S/W/N/E = 要裁的经纬度范围（镶嵌图覆盖 bounds 外扩），网格间隔按米给。
输出图四边标注经纬度，便于把图上看到的地物换算回坐标。
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# 镶嵌图的范围（由 98 的输出行给出；这里按实际产物写死为参数默认，可用 --bbox 覆盖）
MOSAIC = dict(W=129.45337, E=129.66063, N=46.38682, S=46.24318)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mosaic")
    ap.add_argument("out")
    ap.add_argument("S", type=float)
    ap.add_argument("W", type=float)
    ap.add_argument("N", type=float)
    ap.add_argument("E", type=float)
    ap.add_argument("--px", type=int, default=1400)
    ap.add_argument("--step-m", type=float, default=500.0)
    ap.add_argument("--bbox", default=None, help="镶嵌图范围 S,W,N,E（默认按 98 产物）")
    a = ap.parse_args()

    mb = MOSAIC
    if a.bbox:
        s, w, n, e = (float(x) for x in a.bbox.split(","))
        mb = dict(S=s, W=w, N=n, E=e)

    im = Image.open(a.mosaic).convert("RGB")
    W_, H_ = im.size

    def to_px(lon, lat):
        return ((lon - mb["W"]) / (mb["E"] - mb["W"]) * W_,
                (mb["N"] - lat) / (mb["N"] - mb["S"]) * H_)

    x0, y0 = to_px(a.W, a.N)      # 左上
    x1, y1 = to_px(a.E, a.S)      # 右下
    box = (int(max(0, x0)), int(max(0, y0)), int(min(W_, x1)), int(min(H_, y1)))
    crop = im.crop(box)
    scale = a.px / max(crop.size)
    crop = crop.resize((round(crop.size[0] * scale), round(crop.size[1] * scale)), Image.LANCZOS)
    d = ImageDraw.Draw(crop)

    lat_c = (a.S + a.N) / 2
    step_lat = a.step_m / 110940.0
    step_lon = a.step_m / (111320.0 * abs(__import__("math").cos(__import__("math").radians(lat_c))))

    w, h = crop.size
    lat = a.S
    while lat <= a.N + 1e-9:
        py = (a.N - lat) / (a.N - a.S) * h
        d.line([(0, py), (w, py)], fill=(255, 80, 80), width=1)
        d.text((3, max(0, py - 12)), f"{lat:.4f}", fill=(255, 220, 80))
        lat += step_lat
    lon = a.W
    while lon <= a.E + 1e-9:
        px = (lon - a.W) / (a.E - a.W) * w
        d.line([(px, 0), (px, h)], fill=(255, 80, 80), width=1)
        d.text((min(w - 60, px + 2), 3), f"{lon:.4f}", fill=(255, 220, 80))
        lon += step_lon

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    crop.save(a.out, quality=88)
    print(f"[save] {a.out}  {crop.size[0]}×{crop.size[1]}  范围 S{a.S} W{a.W} N{a.N} E{a.E}"
          f"  网格 {a.step_m:.0f} m")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
