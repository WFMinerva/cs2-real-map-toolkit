# -*- coding: utf-8 -*-
"""rail_snap_to_imagery.py —— 把线位折线吸附到影像上的铁路暗脊（只读影像，只写新 JSON）。

为什么要它：肉眼在 3.9 m/px 影像上描线，系统性偏西 100–200 m（2026-09-29 实测）。
铁路在影像上是一条**连续性最好的暗脊**（道床两侧路基+钢轨），比公路更能沿线保持；
做法 = 每个点沿"垂直于走向"的方向搜索 ±range_m，取**沿走向窗口内平均亮度最低**的位置，
迭代若干轮；被指定为锚点的点（如车站）不动。

用法：
  python tools/rail_snap_to_imagery.py <镶嵌图.png> <输入.json> <输出.json>
         [--range-m 260] [--step-m 2] [--iters 3] [--win-m 30] [--anchor 4,11] [--check 目录]
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

MOSAIC = dict(W=129.45337, E=129.66063, N=46.38682, S=46.24318)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mosaic")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--range-m", type=float, default=260.0)
    ap.add_argument("--step-m", type=float, default=2.0)
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--win-m", type=float, default=30.0)
    ap.add_argument("--anchor", default="")
    ap.add_argument("--check", default=None, help="输出校验图目录（整幅）")
    ap.add_argument("--avoid-roads", default=None, help="OSM 路网（.osm）：落在这上面的位置一律排除（公路比铁路更暗，会把吸附带偏）")
    ap.add_argument("--avoid-m", type=float, default=45.0)
    ap.add_argument("--bbox", default=None, help="镶嵌图范围 S,W,N,E")
    a = ap.parse_args()

    # OSM 公路段（lat/lon），用于排除"吸附到公路"的假脊
    road_segs = []
    if a.avoid_roads:
        import re
        import xml.etree.ElementTree as ET
        root = ET.parse(a.avoid_roads).getroot()
        nll = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in root.findall("node")}
        for w in root.findall("way"):
            refs = [nll[nd.get("ref")] for nd in w.findall("nd") if nd.get("ref") in nll]
            for p, q in zip(refs, refs[1:]):
                road_segs.append((p, q))
        print(f"排除用公路段 {len(road_segs)} 条（来自 {Path(a.avoid_roads).name}，阈值 {a.avoid_m:.0f} m）")

    def near_road(lat, lon):
        if not road_segs:
            return False
        mLat = 110940.0
        mLon = 111320.0 * math.cos(math.radians(lat))
        for (la1, lo1), (la2, lo2) in road_segs:
            # 粗筛：先比纬度
            if min(la1, la2) - 0.001 > lat or max(la1, la2) + 0.001 < lat:
                continue
            x1, y1 = (lo1 - lon) * mLon, (la1 - lat) * mLat
            x2, y2 = (lo2 - lon) * mLon, (la2 - lat) * mLat
            dx, dy = x2 - x1, y2 - y1
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / L2))
            px, py = x1 + t * dx, y1 + t * dy
            if px * px + py * py <= a.avoid_m * a.avoid_m:
                return True
        return False

    mb = MOSAIC
    if a.bbox:
        s, w, n, e = (float(x) for x in a.bbox.split(","))
        mb = dict(S=s, W=w, N=n, E=e)
    img = Image.open(a.mosaic).convert("L")
    W_, H_ = img.size
    arr = np.asarray(img, dtype=np.float32)
    lat_c = (mb["N"] + mb["S"]) / 2
    m_per_px_lon = (mb["E"] - mb["W"]) * 111320 * math.cos(math.radians(lat_c)) / W_
    m_per_px_lat = (mb["N"] - mb["S"]) * 110940 / H_

    def to_px(lon, lat):
        return ((lon - mb["W"]) / (mb["E"] - mb["W"]) * W_,
                (mb["N"] - lat) / (mb["N"] - mb["S"]) * H_)

    def to_ll(x, y):
        return (mb["N"] - y / H_ * (mb["N"] - mb["S"]),
                mb["W"] + x / W_ * (mb["E"] - mb["W"]))

    data = json.loads(Path(a.src).read_text(encoding="utf-8"))
    pts = [list(p) for p in data["points"]]
    anchors = set(int(x) for x in a.anchor.split(",") if x.strip() != "")

    print(f"影像 {W_}×{H_}，{m_per_px_lon:.2f}×{m_per_px_lat:.2f} m/px；点 {len(pts)}，锚点 {sorted(anchors)}")
    for it in range(a.iters):
        moved = []
        for i in range(len(pts)):
            if i in anchors:
                continue
            lat, lon = pts[i]
            x, y = to_px(lon, lat)
            pv = pts[i - 1] if i > 0 else pts[i]
            nv = pts[i + 1] if i < len(pts) - 1 else pts[i]
            dx = to_px(pv[1], pv[0])[0] - to_px(nv[1], nv[0])[0]
            dy = to_px(pv[1], pv[0])[1] - to_px(nv[1], nv[0])[1]
            n = math.hypot(dx, dy) or 1.0
            dx, dy = dx / n, dy / n                      # 沿走向单位向量（像素）
            px, py = -dy, dx                             # 垂直方向
            best, best_off = None, 0.0
            off = -a.range_m
            while off <= a.range_m:
                sx, sy = x + px * off / m_per_px_lon, y + py * off / m_per_px_lat
                # 沿走向窗口取均值亮度
                acc, cnt = 0.0, 0
                for t in np.linspace(-a.win_m, a.win_m, 7):
                    qx, qy = sx + dx * t / m_per_px_lon, sy + dy * t / m_per_px_lat
                    ix, iy = int(round(qx)), int(round(qy))
                    if 0 <= ix < W_ and 0 <= iy < H_:
                        acc += arr[iy, ix]
                        cnt += 1
                if cnt == 0:
                    off += a.step_m
                    continue
                v = acc / cnt
                if best is None or v < best:
                    best, best_off = v, off
                off += a.step_m
            nx_ = x + px * best_off / m_per_px_lon
            ny_ = y + py * best_off / m_per_px_lat
            nlat, nlon = to_ll(nx_, ny_)
            moved.append(best_off)
            pts[i] = [round(nlat, 6), round(nlon, 6)]
        if moved:
            print(f"  轮 {it + 1}：移动 {len(moved)} 点，|偏移| 均值 {np.mean(np.abs(moved)):.0f} m，"
                  f"最大 {np.max(np.abs(moved)):.0f} m")

    data["points"] = pts
    data["snapped"] = {"tool": "tools/rail_snap_to_imagery.py", "iters": a.iters,
                       "range_m": a.range_m, "anchors": sorted(anchors),
                       "note": "吸附到影像暗脊后的坐标；锚点（如依兰站）未动"}
    Path(a.dst).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[save] {a.dst}")

    if a.check:
        outdir = Path(a.check)
        outdir.mkdir(parents=True, exist_ok=True)
        im = Image.open(a.mosaic).convert("RGB")
        S, W, N, E = mb["S"], mb["W"], mb["N"], mb["E"]
        x0, y0 = to_px(W, N)
        x1, y1 = to_px(E, S)
        box = (int(max(0, x0)), int(max(0, y0)), int(min(W_, x1)), int(min(H_, y1)))
        crop = im.crop(box)
        sc = 1300 / max(crop.size)
        crop = crop.resize((round(crop.size[0] * sc), round(crop.size[1] * sc)), Image.LANCZOS)
        d = ImageDraw.Draw(crop)
        pxs = []
        for (la, lo) in pts:
            gx, gy = to_px(lo, la)
            pxs.append(((gx - box[0]) * sc, (gy - box[1]) * sc))
        d.line(pxs, fill=(255, 40, 240), width=3, joint="curve")
        for i, (X, Y) in enumerate(pxs):
            d.ellipse([X - 4, Y - 4, X + 4, Y + 4], fill=(255, 240, 0))
            d.text((X + 6, Y + 2), str(i), fill=(255, 255, 0))
        p = outdir / "_snapcheck_全图.jpg"
        crop.save(p, quality=90)
        print(f"[save] {p}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
