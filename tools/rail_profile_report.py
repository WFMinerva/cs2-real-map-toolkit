# -*- coding: utf-8 -*-
"""rail_profile_report.py —— 读一份铺轨数据（rail_full_*.json），沿全线逐点给出：
地形 / 轨面 / 埋深(负=隧道) / 坡度，并按"连续埋入段"汇总隧道（起止里程、长度、最深、平均埋深），
再按分区（默认：起点—7800 m 为平原/过江段，7800 m—终点为东北山区）给对比统计。

用途：机主说"山区低一点、隧道更明显但要顺畅"时，量化对比几组剖面参数。
用法：
  python tools/rail_profile_report.py --map yilan --full maps/yilan/rail/rail_full_v7.json
        [--zone 7800] [--step 20]
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="yilan")
    ap.add_argument("--full", required=True)
    ap.add_argument("--zone", type=float, default=7800.0, help="分区里程（米）：之前=平原/过江，之后=山区")
    ap.add_argument("--step", type=float, default=20.0, help="沿线采样步长（米）")
    ap.add_argument("--water", default="", help="可选：顺带打印这些里程点是否在水体面内")
    a = ap.parse_args()

    cfg = json.loads((ROOT / "maps" / a.map / f"{a.map}.config.json").read_text(encoding="utf-8"))
    bb = cfg["roads_bounds"]
    L, R, Bo, T = bb["left"], bb["right"], bb["bottom"], bb["top"]
    play = 14336.0

    def ll(x, z):
        return (Bo + (z / play + 0.5) * (T - Bo), L + (x / play + 0.5) * (R - L))

    def w(lat, lon):
        return (((lon - L) / (R - L) - 0.5) * play, ((lat - Bo) / (T - Bo) - 0.5) * play)

    full = json.loads((ROOT / a.full).read_text(encoding="utf-8"))
    segs = full["segments"]
    elev = full["elevations"]
    water_level = full.get("water_level_m", 0.0)
    ds = rasterio.open(next((ROOT / "maps" / a.map / "data" / "dem").glob("*DEM.tif")))

    def dem(x, z):
        la, lo = ll(x, z)
        r, c = ds.index(lo, la)
        r = min(max(r, 0), ds.height - 1)
        c = min(max(c, 0), ds.width - 1)
        return float(ds.read(1, window=((r, r + 1), (c, c + 1)))[0, 0])

    cum = [0.0]
    for i in range(len(segs)):
        cum.append(cum[-1] + math.hypot(segs[i][2] - segs[i][0], segs[i][3] - segs[i][1]))

    rows = []   # (里程, x, z, 地形, 轨面, 埋深, 坡度)
    for i, s in enumerate(segs):
        ln = cum[i + 1] - cum[i]
        n = max(2, int(round(ln / a.step)))
        for k in range(n):
            t = k / n
            x = s[0] + (s[2] - s[0]) * t
            z = s[1] + (s[3] - s[1]) * t
            y = elev[i][0] + (elev[i][1] - elev[i][0]) * t
            rows.append((cum[i] + ln * t, x, z, dem(x, z), y))
    rows.sort()

    grades = []
    for i in range(1, len(rows)):
        d = rows[i][0] - rows[i - 1][0]
        grades.append((rows[i][4] - rows[i - 1][4]) / d * 100 if d > 0.1 else 0.0)

    print(f"线位 {a.full}：{len(segs)} 段 / 全长 {cum[-1]:.0f} m；采样 {len(rows)} 点（步长 {a.step:.0f} m）")
    print(f"轨面 {min(r[4] for r in rows):.1f}–{max(r[4] for r in rows):.1f} m；"
          f"最大坡 {max(abs(g) for g in grades):.2f}%")
    # 顺畅度：坡度变化率（%/100 m），越小越顺
    curv = []
    for i in range(1, len(grades)):
        ds_ = rows[i][0] - rows[i - 1][0]
        if ds_ > 0.1:
            curv.append(abs(grades[i] - grades[i - 1]) / ds_ * 100)
    if curv:
        print(f"顺畅度：坡变率 均 {sum(curv) / len(curv):.3f} / 最大 {max(curv):.3f} %/100m")

    def zone(lo, hi, name):
        z = [r for r in rows if lo <= r[0] < hi]
        if not z:
            return
        above = [r[4] - r[3] for r in z]
        tun = [r for r in z if r[4] - r[3] < -2]
        g = [abs(x) for x in
             [(z[i][4] - z[i - 1][4]) / max(0.1, z[i][0] - z[i - 1][0]) * 100 for i in range(1, len(z))]]
        print(f"\n[{name}] 里程 {lo:.0f}–{hi:.0f} m：{len(z)} 点")
        print(f"  轨面 {min(r[4] for r in z):.1f}–{max(r[4] for r in z):.1f} m；"
              f"离地 {min(above):+.1f}…{max(above):+.1f} m；最大坡 {max(g) if g else 0:.2f}%")
        print(f"  埋入(<-2 m) 的点 {len(tun)}/{len(z)}（{len(tun) / len(z) * 100:.0f}%），"
              f"最深 {min(above):+.1f} m")
        # 连续隧道段
        runs = []
        cur = []
        for r in z:
            if r[4] - r[3] < -2:
                cur.append(r)
            else:
                if cur:
                    runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for run in runs:
            if len(run) < 2:
                continue
            print(f"    隧道 {run[0][0]:6.0f}–{run[-1][0]:6.0f} m  长 {run[-1][0] - run[0][0]:5.0f} m  "
                  f"最深 {min(r[4] - r[3] for r in run):+6.1f} m")

    zone(0, a.zone, "平原/过江段")
    zone(a.zone, 1e9, "东北山区")

    if a.water:
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

        print(f"\n水体检查（水面 {water_level} m）：")
        for chain in [float(v) for v in a.water.split(",") if v.strip()]:
            best = min(rows, key=lambda r: abs(r[0] - chain))
            wet = any(inside(best[1], best[2], r) for r in rings)
            print(f"  里程 {best[0]:6.0f}：地形 {best[3]:6.1f}  轨面 {best[4]:6.1f}  "
                  f"{'在水体内' if wet else '不在水体内'}  (地形−水面 {best[3] - water_level:+.1f} m)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
