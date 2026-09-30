# -*- coding: utf-8 -*-
"""rail_alignment_check.py —— 铁路线位（JSON 折线）绘制到影像上做覆盖校验（只读、只出校验图）。

线位 JSON 格式（maps/<id>/rail/rail_alignment.json）：
  { "source": "...", "note": "...", "points": [[lat, lon], ...] }   从西南端到东北端

用法：
  python tools/rail_alignment_check.py <镶嵌图.png> <线位.json> <输出.jpg> [--crop S,W,N,E] [--px 1600]
不传 --crop 就画整幅；传了就只画该范围（便于逐段核对）。
"""
import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

MOSAIC = dict(W=129.45337, E=129.66063, N=46.38682, S=46.24318)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mosaic")
    ap.add_argument("align")
    ap.add_argument("out")
    ap.add_argument("--crop", default=None, help="S,W,N,E")
    ap.add_argument("--px", type=int, default=1600)
    ap.add_argument("--bbox", default=None, help="镶嵌图范围 S,W,N,E")
    ap.add_argument("--roads", default=None, help="OSM 路网 .osm：把公路画成红线（红线之外那条暗线才是铁路）")
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

    if a.crop:
        S, W, N, E = (float(x) for x in a.crop.split(","))
    else:
        S, W, N, E = mb["S"], mb["W"], mb["N"], mb["E"]
    x0, y0 = to_px(W, N)
    x1, y1 = to_px(E, S)
    box = (int(max(0, x0)), int(max(0, y0)), int(min(W_, x1)), int(min(H_, y1)))
    crop = im.crop(box)
    scale = a.px / max(crop.size)
    crop = crop.resize((round(crop.size[0] * scale), round(crop.size[1] * scale)), Image.LANCZOS)
    d = ImageDraw.Draw(crop)
    w, h = crop.size

    pts = json.loads(Path(a.align).read_text(encoding="utf-8"))["points"] if a.align != "-" else []
    px = []
    for lat, lon in pts:
        gx, gy = to_px(lon, lat)
        px.append(((gx - box[0]) * scale, (gy - box[1]) * scale))
    d.line(px, fill=(255, 40, 240), width=3, joint="curve")
    if a.roads:
        import xml.etree.ElementTree as ET
        root = ET.parse(a.roads).getroot()
        nll = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in root.findall("node")}
        for w in root.findall("way"):
            refs = [nll[nd.get("ref")] for nd in w.findall("nd") if nd.get("ref") in nll]
            if len(refs) < 2:
                continue
            lpx = []
            for (la, lo) in refs:
                if not (S - 0.01 <= la <= N + 0.01 and W - 0.01 <= lo <= E + 0.01):
                    continue
                gx, gy = to_px(lo, la)
                lpx.append(((gx - box[0]) * scale, (gy - box[1]) * scale))
            if len(lpx) >= 2:
                d.line(lpx, fill=(255, 60, 60), width=2)
    for i, (x, y) in enumerate(px):
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 240, 0), outline=(0, 0, 0))
        d.text((x + 6, y + 2), f"{i}", fill=(255, 255, 0))
    # 等距标尺：每 1 km 画一条短横（用首点纬度估算）
    lat_c = (S + N) / 2
    d.text((6, h - 18), f"范围 S{S:.4f} W{W:.4f} N{N:.4f} E{E:.4f}  像素 {w}×{h}", fill=(255, 255, 0))

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    crop.save(a.out, quality=90)
    tot = sum(math.hypot((pts[i + 1][0] - pts[i][0]) * 110940,
                         (pts[i + 1][1] - pts[i][1]) * 111320 * math.cos(math.radians(lat_c)))
              for i in range(len(pts) - 1))
    print(f"[save] {a.out}  {w}×{h}  折线 {len(pts)} 点，总长 {tot/1000:.2f} km")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
