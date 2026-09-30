# -*- coding: utf-8 -*-
"""rail_draw_base.py —— 生成"给机主画线用"的底图：经纬网格 + 公路(绿) + 现有线位(橙) + 锚点(青)，
**整张图不含红色**，方便机主用红色画笔描出真实铁路走廊。

用法：
  python tools/rail_draw_base.py <镶嵌图.png> <线位.json|-> <输出.jpg> --bbox 'S,W,N,E' [--px 2600]
        [--grid 0.005] [--roads <osm>] [--anchors "lat,lon;lat,lon"] [--title "..."]
线位 json 传 "-" 表示不画现有线位。
"""
import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

GREEN = (0, 220, 90)
ORANGE = (255, 150, 0)
CYAN = (0, 230, 255)
BLACK = (0, 0, 0)
YELLOW = (255, 240, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mosaic")
    ap.add_argument("align")
    ap.add_argument("out")
    ap.add_argument("--bbox", required=True, help="镶嵌图范围 S,W,N,E")
    ap.add_argument("--px", type=int, default=2600)
    ap.add_argument("--grid", type=float, default=0.005, help="经纬网格间隔（度）")
    ap.add_argument("--roads", default=None)
    ap.add_argument("--anchors", default=None, help="已知锚点 lat,lon;lat,lon")
    ap.add_argument("--align2", default=None, help="第二条线位 json（画成蓝色，例如 DEM 外推候选）")
    ap.add_argument("--title", default="")
    a = ap.parse_args()

    s, w, n, e = (float(x) for x in a.bbox.split(","))
    im = Image.open(a.mosaic).convert("RGB")
    W_, H_ = im.size

    def to_px(lon, lat):
        return ((lon - w) / (e - w) * W_, (n - lat) / (n - s) * H_)

    x0, y0 = to_px(w, n)
    x1, y1 = to_px(e, s)
    box = (int(max(0, x0)), int(max(0, y0)), int(min(W_, x1)), int(min(H_, y1)))
    crop = im.crop(box)
    scale = a.px / max(crop.size)
    crop = crop.resize((round(crop.size[0] * scale), round(crop.size[1] * scale)), Image.LANCZOS)
    d = ImageDraw.Draw(crop, "RGBA")
    wpx, hpx = crop.size

    def m2px(lon, lat):
        gx, gy = to_px(lon, lat)
        return ((gx - box[0]) * scale, (gy - box[1]) * scale)

    # 1) 经纬网格（半透明黑线 + 标签）
    import numpy as np  # noqa: F401  (只为确认依赖存在)
    lat = math.ceil(s / a.grid) * a.grid
    while lat <= n + 1e-9:
        _, gy = m2px(w, lat)
        d.line([(0, gy), (wpx, gy)], fill=(0, 0, 0, 90), width=1)
        d.text((4, gy + 2), f"{lat:.4f}", fill=YELLOW)
        d.text((wpx - 64, gy + 2), f"{lat:.4f}", fill=YELLOW)
        lat = round(lat + a.grid, 10)
    lon = math.ceil(w / a.grid) * a.grid
    while lon <= e + 1e-9:
        gx, _ = m2px(lon, n)
        d.line([(gx, 0), (gx, hpx)], fill=(0, 0, 0, 90), width=1)
        d.text((gx + 2, 4), f"{lon:.4f}", fill=YELLOW)
        d.text((gx + 2, hpx - 16), f"{lon:.4f}", fill=YELLOW)
        lon = round(lon + a.grid, 10)

    # 2) OSM 公路：绿色
    if a.roads:
        root = ET.parse(a.roads).getroot()
        nll = {nd.get("id"): (float(nd.get("lat")), float(nd.get("lon"))) for nd in root.findall("node")}
        drawn = 0
        for way in root.findall("way"):
            refs = [nll[nd.get("ref")] for nd in way.findall("nd") if nd.get("ref") in nll]
            if len(refs) < 2:
                continue
            lpx = []
            for (la, lo) in refs:
                if not (s - 0.01 <= la <= n + 0.01 and w - 0.01 <= lo <= e + 0.01):
                    continue
                lpx.append(m2px(lo, la))
            if len(lpx) >= 2:
                d.line(lpx, fill=GREEN, width=2)
                drawn += 1
        print(f"  公路 way 画了 {drawn} 条（绿色）")

    # 3) 现有线位：橙色虚线 + 点号
    if a.align != "-":
        pts = json.loads(Path(a.align).read_text(encoding="utf-8"))["points"]
        px = [m2px(lo, la) for la, lo in pts]
        for i in range(len(px) - 1):
            x0_, y0_ = px[i]
            x1_, y1_ = px[i + 1]
            L = math.hypot(x1_ - x0_, y1_ - y0_)
            seg = max(1, int(L / 24))
            for k in range(seg):
                t0, t1 = k / seg, (k + 0.55) / seg
                d.line([(x0_ + (x1_ - x0_) * t0, y0_ + (y1_ - y0_) * t0),
                        (x0_ + (x1_ - x0_) * t1, y0_ + (y1_ - y0_) * t1)], fill=ORANGE, width=3)
        for i, (x, y) in enumerate(px):
            d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=ORANGE, outline=BLACK)
            d.text((x + 7, y + 3), f"{i}", fill=ORANGE)

    # 4) 已知锚点：青色十字
    if a.anchors:
        for pair in a.anchors.split(";"):
            la, lo = (float(v) for v in pair.split(","))
            x, y = m2px(lo, la)
            d.line([(x - 12, y), (x + 12, y)], fill=CYAN, width=3)
            d.line([(x, y - 12), (x, y + 12)], fill=CYAN, width=3)

    # 4b) 第二条线位（蓝色实线，如 DEM 外推候选）
    if a.align2 and a.align2 != "-":
        pts2 = json.loads(Path(a.align2).read_text(encoding="utf-8"))["points"]
        px2 = [m2px(lo, la) for la, lo in pts2]
        d.line(px2, fill=(30, 120, 255), width=3, joint="curve")

    # 5) 图例与映射说明
    info = (f"{a.title}  范围 S{s:.4f} W{w:.4f} N{n:.4f} E{e:.4f}  像素 {wpx}x{hpx}\n"
            f"绿=OSM公路  橙=现有(错误)线位  青=已知锚点  网格 {a.grid}°\n"
            f"请在图上用【红色】沿真实铁路走廊画一条线，不要裁剪/缩放，存回 PNG 或高质量 JPG")
    d.rectangle([0, hpx - 54, wpx, hpx], fill=(0, 0, 0, 170))
    d.multiline_text((6, hpx - 50), info, fill=(255, 255, 255))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    crop.save(a.out, quality=92)
    print(f"[save] {a.out}  {wpx}x{hpx}  {Path(a.out).stat().st_size/1e6:.2f} MB\n"
          f"       经纬度↔像素：px_x=(lon-{w})/({e}-{w})*{wpx}  px_y=({n}-lat)/({n}-{s})*{hpx}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
