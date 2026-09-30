# 一次性探针（2026-09-30 车站轮）：把站区线位拉直（真实依兰站在直线段上）。
# 为什么：v10 线位在站区有一个 14° 折点、且折点落在站台 212 m 跨度内（进站 56.4°/出站 42.2°），
# 直线站台与两端钢轨必然看着不平行（机主实拍"站台有点歪"）。
# 做法：站中心 ±A 内的点投影到最小二乘主轴（完全取直），±A..±B 用 smoothstep 过渡回原线位；
#       段数与逐段高程保持不变。
# 用法：python tools/_rail_straighten_station.py [--a 200] [--b 450]
import argparse, json, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v10.json")
DST = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v12_straight.json")
OSM_WORLD = (3312.5, -4213.7)      # OSM 依兰站节点 → 世界坐标（线位仿射拟合反解，残差 ≤5 cm）


def build_cum(pts):
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + math.dist(pts[i], pts[i + 1]))
    return cum


def nearest_on_poly(pts, cum, target):
    best = None
    for i in range(len(pts) - 1):
        ax, az = pts[i]
        bx, bz = pts[i + 1]
        abx, abz = bx - ax, bz - az
        l2 = abx * abx + abz * abz
        t = max(0.0, min(1.0, ((target[0] - ax) * abx + (target[1] - az) * abz) / l2)) if l2 else 0.0
        qx, qz = ax + abx * t, az + abz * t
        dd = math.hypot(target[0] - qx, target[1] - qz)
        if best is None or dd < best[0]:
            best = (dd, i, t, qx, qz)
    dd, i, t, qx, qz = best
    s = cum[i] + t * math.dist(pts[i], pts[i + 1])
    return dd, s, (qx, qz), i, t


def point_at(pts, cum, s):
    s = max(0.0, min(cum[-1], s))
    for k in range(len(pts) - 1):
        if s <= cum[k + 1] or k == len(pts) - 2:
            L = cum[k + 1] - cum[k]
            t = (s - cum[k]) / L if L > 0 else 0.0
            return (pts[k][0] + (pts[k + 1][0] - pts[k][0]) * t,
                    pts[k][1] + (pts[k + 1][1] - pts[k][1]) * t)
    return pts[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=float, default=250.0, help="完全取直的半长（米）")
    ap.add_argument("--b", type=float, default=500.0, help="平滑过渡到原线位的半长（米）")
    args = ap.parse_args()
    A, B = args.a, args.b

    d = json.load(open(SRC, encoding="utf-8"))
    segs, elev = d["segments"], d["elevations"]
    pts = [(segs[0][0], segs[0][1])] + [(s[2], s[3]) for s in segs]
    cum = build_cum(pts)

    perp, s0, c0, _, _ = nearest_on_poly(pts, cum, OSM_WORLD)
    print(f"[取直] 站中心 ({c0[0]:.1f},{c0[1]:.1f})  弧长 {s0:.1f}/{cum[-1]:.1f} m  离 OSM 站 {perp:.1f} m")

    # 站中心 ±A 内**每 25 m 采样**（折点很稀，只用折点等于没拟合）→ 最小二乘主轴
    near = []
    s = s0 - A
    while s <= s0 + A + 1e-6:
        near.append(point_at(pts, cum, s))
        s += 25.0
    print(f"[取直] 参与拟合的采样点 {len(near)} 个（±{A:.0f} m，每 25 m）")
    mx = sum(p[0] for p in near) / len(near)
    my = sum(p[1] for p in near) / len(near)
    sxx = syy = sxy = 0.0
    for (x, z) in near:
        dx, dz = x - mx, z - my
        sxx += dx * dx
        syy += dz * dz
        sxy += dx * dz
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ux, uz = math.cos(ang), math.sin(ang)
    if uz < 0:
        ux, uz = -ux, -uz
    brg = math.degrees(math.atan2(ux, uz)) % 360
    print(f"[取直] 站区主轴方向 = {brg:.2f}°（拟合点 {len(near)} 个）")

    def proj(p):
        t = (p[0] - mx) * ux + (p[1] - my) * uz
        return (mx + ux * t, my + uz * t)

    pts2, moved = [], 0
    for k, p in enumerate(pts):
        ds = abs(cum[k] - s0)
        if ds <= A:
            w = 1.0
        elif ds >= B:
            w = 0.0
        else:
            w = 1.0 - (ds - A) / (B - A)
            w = w * w * (3.0 - 2.0 * w)          # smoothstep
        q = proj(p)
        np_ = (p[0] + (q[0] - p[0]) * w, p[1] + (q[1] - p[1]) * w)
        if math.dist(np_, p) > 0.05:
            moved += 1
        pts2.append(np_)

    segs2 = [[round(pts2[i][0], 1), round(pts2[i][1], 1), round(pts2[i + 1][0], 1), round(pts2[i + 1][1], 1)]
             for i in range(len(pts2) - 1)]
    out = dict(d)
    out["segments"] = segs2
    out["elevations"] = elev
    out["note"] = (f"v12：站区线位**拉直**（真实依兰站在直线段上）。站中心 ±{A:.0f} m 内投影到最小二乘主轴、"
                   f"±{A:.0f}..±{B:.0f} m smoothstep 过渡回原线位；段数/逐段高程沿用 v10。"
                   f"原折点（进站 56.4°/出站 42.2°，且折点落在站台跨度内）造成的站台歪斜由此消除。")
    json.dump(out, open(DST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    cum2 = build_cum(pts2)
    perp2, s0n, cN, i2, t2 = nearest_on_poly(pts2, cum2, OSM_WORLD)
    p1 = point_at(pts2, cum2, s0n - 106)
    p2 = point_at(pts2, cum2, s0n + 106)
    yaw = math.degrees(math.atan2(p2[0] - p1[0], p2[1] - p1[1]))
    e = elev[i2]
    y = e[0] + (e[1] - e[0]) * t2
    print(f"[取直] 移动点 {moved} 个")
    print(f"[车站] 新位姿：x={cN[0]:.1f}, z={cN[1]:.1f}, yaw={yaw:.2f}°, y={y:.2f} m（离 OSM 站横向 {perp2:.1f} m）")
    print(f"[写出] {DST}")


if __name__ == "__main__":
    main()
