# -*- coding: utf-8 -*-
"""make_rail_full.py —— 由线位（经纬度点）生成"铺轨用"数据文件（含过水/高架声明）。

- 读线位 JSON（points=[[lat,lon],…]）→ 换算游戏世界坐标 → 输出 segments（[x0,z0,x1,z1]）
- 用 OSM 水体面（data/water_in_box.geojson）沿线逐 10 m 采样，自动找出**过水区间**，
  在 crossings 里声明（桥面用**负值** = 只要"水面以上净空"，由 mod 结合两端地形自动取值）
- 可按 --stop-lat/--stop-lon 截断（例：只铺到隧道口）

用法：
  python tools/make_rail_full.py --map yilan --align maps/yilan/rail/rail_alignment_v4.json
        --out maps/yilan/rail/rail_full_v4.json [--clearance 8.5] [--ramp 250]
        [--stop-lat 46.3014 --stop-lon 129.6282] [--extra-crossings "seg,from,to,deck;…"]
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="yilan")
    ap.add_argument("--align", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--clearance", type=float, default=8.5, help="桥面高出水面的净空（米）")
    ap.add_argument("--ramp", type=float, default=250.0)
    ap.add_argument("--deck-step", type=float, default=60.0)
    ap.add_argument("--ramp-step", type=float, default=120.0)
    ap.add_argument("--stop-lat", type=float, default=None)
    ap.add_argument("--stop-lon", type=float, default=None)
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    cfg = json.loads((ROOT / "maps" / a.map / f"{a.map}.config.json").read_text(encoding="utf-8"))
    bb = cfg["roads_bounds"]
    L, R, Bo, T = bb["left"], bb["right"], bb["bottom"], bb["top"]
    play = 14336.0
    water_level = float(cfg.get("water_level_m", 92.5)) if "water_level_m" in cfg else 92.5

    def w(lat, lon):
        u = (lon - L) / (R - L)
        v = (lat - Bo) / (T - Bo)
        return ((u - 0.5) * play, (v - 0.5) * play)

    pts = json.loads(Path(a.align).read_text(encoding="utf-8"))["points"]
    if a.stop_lat is not None and a.stop_lon is not None:
        keep = []
        for la, lo in pts:
            if math.hypot((la - a.stop_lat) * 111320, (lo - a.stop_lon) * 111320 * math.cos(math.radians(la))) < 5:
                keep.append([la, lo])
                break
            keep.append([la, lo])
        pts = keep
    world = [w(la, lo) for la, lo in pts]
    segs = [[round(world[i][0], 1), round(world[i][1], 1), round(world[i + 1][0], 1), round(world[i + 1][1], 1)]
            for i in range(len(world) - 1)]

    # 水体面 → 过水区间
    geo = json.loads((ROOT / "maps" / a.map / "data" / "water_in_box.geojson").read_text(encoding="utf-8"))
    rings = []
    for f in geo["features"]:
        g = f["geometry"]
        polys = [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]
        for r in polys:
            rings.append([w(p[1], p[0]) for p in r])

    def inside(x, z, ring):
        c = False
        n = len(ring)
        for i in range(n):
            x0, z0 = ring[i]
            x1, z1 = ring[(i + 1) % n]
            if ((z0 > z) != (z1 > z)) and (x < (x1 - x0) * (z - z0) / (z1 - z0) + x0):
                c = not c
        return c

    def in_water(x, z):
        return any(inside(x, z, r) for r in rings)

    crossings = []
    for i, s in enumerate(segs, 1):
        x0, z0, x1, z1 = s
        length = math.hypot(x1 - x0, z1 - z0)
        n = max(2, int(length / 10))
        flags = [in_water(x0 + (x1 - x0) * k / n, z0 + (z1 - z0) * k / n) for k in range(n + 1)]
        if not any(flags):
            continue
        idx = [k for k, f in enumerate(flags) if f]
        runs, st, prev = [], idx[0], idx[0]
        for k in idx[1:]:
            if k != prev + 1:
                runs.append((st, prev))
                st = k
            prev = k
        runs.append((st, prev))
        for (r0, r1) in runs:
            frm = r0 / n * length
            to = r1 / n * length
            if to - frm < 20:      # 太短（多半是边角噪声）忽略
                continue
            crossings.append([i, round(frm, 1), round(to, 1), -a.clearance])

    out = {
        "prefab_name": "Double Train Track - Twoway",
        "prefab_index": 16121,
        "prefab_version": 1,
        "note": (a.note or f"由 {Path(a.align).name} 生成；过水区间由 OSM 水体面逐 10 m 采样自动声明；"
                 f"桥面用负值 = 只给水面以上 {a.clearance} m 净空，由 mod 结合两端地形自动取值。"),
        "water_level_m": water_level,
        "ramp_m": a.ramp,
        "deck_step_m": a.deck_step,
        "ramp_step_m": a.ramp_step,
        "crossings": crossings,
        "segments": segs,
    }
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tot = sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in segs)
    print(f"[save] {a.out}  {len(pts)} 点 / {len(segs)} 段 / 总长 {tot/1000:.2f} km；"
          f"过水声明 {len(crossings)} 处：{crossings}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
