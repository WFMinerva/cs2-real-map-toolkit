# 一次性探针（2026-09-30 车站轮）：给"依兰站"生成带缺口的铺轨数据，并把车站位姿校准到线路局部真值。
# 为什么：① 站点处线路是弯的，全线平均走向(42.1°)与局部走向差 14°，站台会歪 → 用站内跨距(±106 m)的弦向；
#          ② 机主要"把车站两侧和下面的铁轨截掉、自己手连" → 在折线上按弧长开一个 [中心−GAP, 中心+GAP] 的缺口。
# 用法：python tools/_rail_gap_yilan_station.py [--gap 131] [--span 106]
# 产物：maps/yilan/rail/rail_full_v11_gap.json + 打印车站校准后的 x/z/yaw/y
import argparse, json, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v10.json")
DST = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v11_gap.json")
# OSM 依兰站节点 → 世界坐标（由线位 55 点仿射拟合反解，残差 ≤5 cm）
OSM_WORLD = (3312.5, -4213.7)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=131.0, help="缺口半长（米，沿线路从车站中心算）")
    ap.add_argument("--span", type=float, default=106.0, help="车站站内轨道半跨（米，用于算局部弦向）")
    a = ap.parse_args()

    d = json.load(open(SRC, encoding="utf-8"))
    segs, elev = d["segments"], d["elevations"]
    pts = [(segs[0][0], segs[0][1])] + [(s[2], s[3]) for s in segs]
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + math.dist(pts[i], pts[i + 1]))
    total = cum[-1]

    def point_at(s):
        s = max(0.0, min(total, s))
        for k in range(len(pts) - 1):
            if s <= cum[k + 1] or k == len(pts) - 2:
                L = cum[k + 1] - cum[k]
                t = (s - cum[k]) / L if L > 0 else 0.0
                return (pts[k][0] + (pts[k + 1][0] - pts[k][0]) * t,
                        pts[k][1] + (pts[k + 1][1] - pts[k][1]) * t), k, t
        return pts[-1], len(pts) - 2, 1.0

    def y_at(s):
        _, k, t = point_at(s)
        e = elev[k]
        return e[0] + (e[1] - e[0]) * t

    # 1) OSM 站投影到折线 → 车站中心（严格在线）
    best = None
    for i in range(len(pts) - 1):
        (ax, az), (bx, bz) = pts[i], pts[i + 1]
        abx, abz = bx - ax, bz - az
        l2 = abx * abx + abz * abz
        t = max(0.0, min(1.0, ((OSM_WORLD[0] - ax) * abx + (OSM_WORLD[1] - az) * abz) / l2)) if l2 else 0.0
        qx, qz = ax + abx * t, az + abz * t
        dd = math.hypot(OSM_WORLD[0] - qx, OSM_WORLD[1] - qz)
        if best is None or dd < best[0]:
            best = (dd, i, t, qx, qz)
    perp, si, st, cx, cz = best
    s0 = cum[si] + st * math.dist(pts[si], pts[si + 1])
    print(f"[车站] 线上中心 = ({cx:.1f},{cz:.1f})  弧长 {s0:.1f}/{total:.1f} m  离 OSM 节点 {perp:.1f} m（横向）")

    # 2) 局部弦向（站内跨距） → 校准 yaw
    pa, _, _ = point_at(s0 - a.span)
    pb, _, _ = point_at(s0 + a.span)
    yaw = math.degrees(math.atan2(pb[0] - pa[0], pb[1] - pa[1]))
    # 弦向拟合残差（看这段到底多弯）
    maxoff = 0.0
    for k in range(0, 101):
        s = s0 - a.span + 2 * a.span * k / 100.0
        p, _, _ = point_at(s)
        ux, uz = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        off = abs((p[0] - pa[0]) * uz - (p[1] - pa[1]) * ux)
        maxoff = max(maxoff, off)
    yc = y_at(s0)
    print(f"[车站] 站内跨距弦向 yaw = {yaw:.3f}°（原用 42.1°，差 {yaw-42.1:+.2f}°）；该弦最大偏差 {maxoff:.2f} m")
    print(f"[车站] 该处轨面绝对高程 = {yc:.2f} m")
    print(f"[车站] → 数据应为 x={cx:.1f}, z={cz:.1f}, yaw={yaw:.2f}, y={yc:.2f}")

    # 3) 按弧长开缺口，重建段 + 高程
    sA, sB = s0 - a.gap, s0 + a.gap
    new_segs, new_elev = [], []
    for i in range(len(segs)):
        s1, s2 = cum[i], cum[i + 1]
        for lo, hi in ((s1, min(s2, sA)), (max(s1, sB), s2)):
            if hi - lo < 0.5:
                continue
            p1, _, _ = point_at(lo)
            p2, _, _ = point_at(hi)
            new_segs.append([round(p1[0], 1), round(p1[1], 1), round(p2[0], 1), round(p2[1], 1)])
            new_elev.append([round(y_at(lo), 2), round(y_at(hi), 2)])
    out = dict(d)
    out["segments"] = new_segs
    out["elevations"] = new_elev
    out["note"] = (f"v11：依兰站处**留缺口**给机主手连（沿线路弧长 {s0:.0f} ± {a.gap:.0f} m，"
                   f"即世界 ({cx:.1f},{cz:.1f}) 沿走向 ±{a.gap:.0f} m；站内轨道自身跨度 ±{a.span:.0f} m）。"
                   f"原 v10 段 {len(segs)} → v11 段 {len(new_segs)}；其余字段沿用 v10（{d.get('note','')[:60]}…）")
    json.dump(out, open(DST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[缺口] 段 {len(segs)} → {len(new_segs)}；缺口两端世界坐标：")
    q1, _, _ = point_at(sA)
    q2, _, _ = point_at(sB)
    print(f"        NE 端 (s={sB:.0f}) = ({q2[0]:.1f},{q2[1]:.1f})  y={y_at(sB):.2f}")
    print(f"        SW 端 (s={sA:.0f}) = ({q1[0]:.1f},{q1[1]:.1f})  y={y_at(sA):.2f}")
    print(f"[写出] {DST}")


if __name__ == "__main__":
    main()
