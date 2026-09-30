# -*- coding: utf-8 -*-
"""rail_bounds_check.py —— 线位与**图边**的关系体检（出图/进图点、图外里程、两端跨出量）。

为什么有这一步：原版只有**网络铺到可玩区边缘**才生成对外连接；线位端点若停在图内
（哪怕只差 3 m）就是死头，若冲出图外几百米就是悬在图外的废轨。
本工具把这两个数直接打出来，配 `tools/rail_align_to_bounds.py` 用（那个负责收/放，这个负责核对）。

输入可以是**铺轨数据**（rail_full_*.json，按 segments + elevations）或**线位**（rail_alignment_*.json，按 points 经纬度）；
图边一律取 config：`playable.px × playable.m_per_px ÷ 2`（本图 = 7168 m，即 ±7168 方框），不写死。

用法：
  python tools/rail_bounds_check.py --map yilan --in maps/yilan/rail/rail_full_v9.json
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
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--step", type=float, default=10.0, help="沿线采样步长（米），用于统计图外里程")
    a = ap.parse_args()

    cfg = json.loads((ROOT / "maps" / a.map / f"{a.map}.config.json").read_text(encoding="utf-8"))
    bb = cfg["roads_bounds"]
    L, R, Bo, T = bb["left"], bb["right"], bb["bottom"], bb["top"]
    play = float(cfg["playable"]["px"]) * float(cfg["playable"]["m_per_px"])
    half = play / 2.0

    doc = json.loads(Path(a.src).read_text(encoding="utf-8"))
    if "segments" in doc:
        segs = doc["segments"]
    else:
        def w(lat, lon):
            return ((lon - L) / (R - L) - 0.5) * play, ((lat - Bo) / (T - Bo) - 0.5) * play
        pts = [w(la, lo) for la, lo in doc["points"]]
        segs = [[pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]] for i in range(len(pts) - 1)]

    cum = [0.0]
    for s in segs:
        cum.append(cum[-1] + math.hypot(s[2] - s[0], s[3] - s[1]))
    total = cum[-1]

    def over(p):
        return max(abs(p[0]) - half, abs(p[1]) - half)

    print(f"线位 {a.src}：{len(segs)} 段 / 全长 {total:.0f} m；图边 = ±{half:.0f} m（可玩区 {play:.0f} m 见方）")
    print(f"  首端 ({segs[0][0]:.1f},{segs[0][1]:.1f}) 出界 {over((segs[0][0], segs[0][1])):+8.1f} m；"
          f"尾端 ({segs[-1][2]:.1f},{segs[-1][3]:.1f}) 出界 {over((segs[-1][2], segs[-1][3])):+8.1f} m")

    names = [("东 x=+", 0, +half), ("西 x=-", 0, -half), ("北 z=+", 1, +half), ("南 z=-", 1, -half)]
    for name, idx, target in names:
        for i, s in enumerate(segs):
            p0, p1 = s[idx], s[idx + 2]
            if p0 != p1 and (p0 - target) * (p1 - target) <= 0:
                t = (target - p0) / (p1 - p0)
                x = s[0] + t * (s[2] - s[0]); z = s[1] + t * (s[3] - s[1])
                print(f"  边界交叉 {name}{half:.0f}：段{i+1} 里程 {cum[i] + t * (cum[i+1] - cum[i]):.0f} m"
                      f"  world=({x:.1f},{z:.1f})")

    # 图外里程（逐 step 采样）
    inside = outside = 0.0
    for i, s in enumerate(segs):
        ln = cum[i + 1] - cum[i]
        n = max(2, int(round(ln / a.step)))
        for k in range(n):
            t = (k + 0.5) / n
            x = s[0] + (s[2] - s[0]) * t; z = s[1] + (s[3] - s[1]) * t
            if over((x, z)) > 0:
                outside += ln / n
            else:
                inside += ln / n
    print(f"  图内 {inside:.0f} m（{inside/total*100:.1f}%）/ 图外 {outside:.0f} m（{outside/total*100:.1f}%）")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
