# -*- coding: utf-8 -*-
"""make_rail_elev_profile.py —— 给线位算一条"高架剖面"（地面以上固定抬高 + 限坡 + 过水净空）。

输出直接写进 rail_test.json 的 `elevations`（逐段 [y0,y1]，**绝对高程**；mod 侧带 FreeHeight 时会换算成相对地面）。
数据驱动的所有参数都在命令行上，不写死在地图配置里。

用法：
  python tools/make_rail_elev_profile.py --map yilan --align <线位.json> --out <铺轨数据.json>
        [--height 14] [--min-above 8] [--max-grade 2.0] [--clearance 8.5] [--iters 300]
说明：
  - 每点初值 = 地形 + height；
  - 约束：y ≥ 地形 + min_above；过水点 y ≥ 水面 + clearance；
  - 反复松弛把相邻坡压到 max-grade 以内（等价于"坡度限幅"），再投影回约束；
  - 结果写入 elevations（全部 16 段都给显式值）→ 全线走高架；同时清掉 crossings（净空已由剖面保证）。
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
    ap.add_argument("--align", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--height", type=float, default=14.0, help="目标抬高（地面以上，米）")
    ap.add_argument("--min-above", type=float, default=8.0, help="任何点至少高出地面多少米")
    ap.add_argument("--max-grade", type=float, default=2.0, help="允许的最大坡度（百分数，2 = 2%%）")
    ap.add_argument("--clearance", type=float, default=8.5, help="过水处至少高出水面多少米")
    ap.add_argument("--smooth-m", type=float, default=1500.0,
                    help="先对地形做多大尺度的平滑（米）：山谷自然成高架、山脊落到洞顶以下→游戏自动成隧道")
    ap.add_argument("--deepen-m", type=float, default=0.0,
                    help="山区额外下压多少米（0=不压）：越高压得越多 → 隧道更长更明显")
    ap.add_argument("--deepen-above-m", type=float, default=120.0,
                    help="平滑地形高于该高程才开始下压（米）")
    ap.add_argument("--deepen-full-m", type=float, default=150.0,
                    help="平滑地形达到该高程时下压到满值（米）")
    ap.add_argument("--water-comp", action="store_true",
                    help="过水点预补偿：原版对过水位置用 ground=max(地形, 水面+2·m_ElevationLimit)，"
                         "我们写的是 目标−地形，会被多加一份 → 这里把目标按同一口径扣回去，消除水面处的鼓包")
    ap.add_argument("--iters", type=int, default=400)
    a = ap.parse_args()

    cfg = json.loads((ROOT / "maps" / a.map / f"{a.map}.config.json").read_text(encoding="utf-8"))
    bb = cfg["roads_bounds"]
    L, R, Bo, T = bb["left"], bb["right"], bb["bottom"], bb["top"]
    play = 14336.0
    water_level = 92.5

    def w(lat, lon):
        u = (lon - L) / (R - L)
        v = (lat - Bo) / (T - Bo)
        return ((u - 0.5) * play, (v - 0.5) * play)

    data = json.loads(Path(a.out).read_text(encoding="utf-8"))
    segs = data["segments"]
    pts_ll = json.loads(Path(a.align).read_text(encoding="utf-8"))["points"]
    P = np.array([w(la, lo) for la, lo in pts_ll])

    ds = rasterio.open(next((ROOT / "maps" / a.map / "data" / "dem").glob("*DEM.tif")))

    def dem(x, z):
        lon = L + (x / play + 0.5) * (R - L)
        lat = Bo + (z / play + 0.5) * (T - Bo)
        r, c = ds.index(lon, lat)
        r = min(max(r, 0), ds.height - 1)
        c = min(max(c, 0), ds.width - 1)
        return float(ds.read(1, window=((r, r + 1), (c, c + 1)))[0, 0])

    # 水体面 → 每个点是否在水上
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

    n = len(P)
    terr_raw = np.array([dem(x, z) for x, z in P])
    # 挖填结合的关键：先按 --smooth-m 平滑地形，再抬高。
    # 平滑后山谷变化不大（→ 高架 14 m，跨路走上面），山脊被抹平（→ 轨面落到洞顶以下 = 隧道）。
    half = a.smooth_m / 2.0
    cum = np.concatenate([[0.0], np.cumsum(terr_raw)])
    dist = np.concatenate([[0.0], np.cumsum([math.hypot(P[i + 1][0] - P[i][0], P[i + 1][1] - P[i][1])
                                             for i in range(n - 1)])])
    terr = np.zeros(n)
    for i in range(n):
        lo = np.searchsorted(dist, dist[i] - half)
        hi = np.searchsorted(dist, dist[i] + half, side="right")
        terr[i] = (cum[min(hi, n)] - cum[lo]) / max(1, min(hi, n) - lo)
    wet = np.array([in_water(x, z) for x, z in P])
    # v8：山区按"平滑地形高程"平滑地下压（越高压得越多）→ 隧道更长更明显，且不碰平原/过江段。
    deep = np.zeros(n)
    if a.deepen_m > 0:
        span = max(1.0, a.deepen_full_m - a.deepen_above_m)
        t = np.clip((terr - a.deepen_above_m) / span, 0.0, 1.0)
        deep = a.deepen_m * (0.5 - 0.5 * np.cos(np.pi * t))       # 余弦缓入，避免折角
    floor_dry = terr + a.min_above - deep
    floor = np.where(wet, np.maximum(floor_dry, water_level + a.clearance), floor_dry)
    y = np.maximum(terr + a.height - deep, floor)
    d = np.array([math.hypot(P[i + 1][0] - P[i][0], P[i + 1][1] - P[i][1]) for i in range(n - 1)])
    lim = a.max_grade / 100.0 * d

    for it in range(a.iters):
        moved = 0.0
        for i in range(n - 1):
            diff = y[i + 1] - y[i]
            if abs(diff) > lim[i]:
                ex = (abs(diff) - lim[i]) / 2.0
                s = 1.0 if diff > 0 else -1.0
                y[i] += s * ex
                y[i + 1] -= s * ex
                moved += ex
        y = np.maximum(y, floor)
        if moved < 0.01:
            break

    # 过水点预补偿：原版 `InitializeCoursePos` 用 ground = max(地形, 水面+2·m_ElevationLimit)=max(地形, 100.5)，
    # 而我们写进去的是 目标−地形 → 实际节点会被多抬 (100.5−地形)。这里把目标按同口径扣回，水面处不再鼓包。
    y_write = y.copy()
    if a.water_comp:
        g_game = np.where(wet, np.maximum(terr_raw, water_level + 8.0), terr_raw)
        y_write = y - np.maximum(0.0, g_game - terr_raw)

    data["elevations"] = [[round(float(y_write[i]), 2), round(float(y_write[i + 1]), 2)] for i in range(n - 1)]
    data["crossings"] = []          # 净空已由剖面保证，不再需要桥面声明
    data["note"] = (f"高架剖面（tools/make_rail_elev_profile.py）：目标抬高 {a.height} m、"
                    f"最小离地 {a.min_above} m、限坡 {a.max_grade}%、过水净空 {a.clearance} m、"
                    f"地形平滑 {a.smooth_m:.0f} m、山区下压 {a.deepen_m:.0f} m"
                    f"（>{a.deepen_above_m:.0f} m 起压，{a.deepen_full_m:.0f} m 压满）"
                    + ("、过水点已按原版 ground 口径预补偿" if a.water_comp else "")
                    + "；elevations 为逐段绝对高程。")
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    grades = [abs(data["elevations"][i][1] - data["elevations"][i][0]) / d[i] * 100 for i in range(n - 1)]
    above = y - terr_raw
    tun = [i for i in range(n) if above[i] < 0]
    print(f"[save] {a.out}：{n} 点 / {n-1} 段（平滑尺度 {a.smooth_m:.0f} m）")
    print(f"  轨面 {y.min():.1f}–{y.max():.1f} m；相对**真实地形** 高出 {above.max():.1f} m / 埋入 {above.min():.1f} m；"
          f"最大坡 {max(grades):.2f}%；可能成隧道的点 {len(tun)}/{n}"
          + (f"（下压 {deep.max():.0f} m" + ("，已过水补偿" if a.water_comp else "") + "）" if a.deepen_m > 0 or a.water_comp else ""))
    for i in range(n - 1):
        tag = "隧道" if (above[i] < 0 or above[i + 1] < 0) else ("高架" if min(above[i], above[i + 1]) > 2 else "贴地")
        print(f"   段{i+1:2d} 长 {d[i]:6.0f} m  地形 {terr_raw[i]:6.1f}→{terr_raw[i+1]:6.1f}  "
              f"轨面 {y[i]:6.1f}→{y[i+1]:6.1f}  高差 {above[i]:+6.1f}/{above[i+1]:+6.1f} m  坡 {grades[i]:5.2f}%  {tag}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
