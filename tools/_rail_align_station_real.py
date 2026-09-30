# 一次性探针（2026-09-30 车站轮）：把站区线位对齐到**真实哈佳线**（OSM 缺口两侧的真实几何）。
# 依据：OSM 哈佳线在依兰缺口两侧有真实点 A(2925.5,-4655.7, 离站584m) / B(4252.3,-3324.2, 离站1296m)，
#       两端局部切向 45.1°/44.9° ⇒ 站区真实铁路 ≈ **45.0° 直线**（我们原线位在站区是 56.4°/42.2° 带 14° 折点，
#       折点还落在站台 212 m 跨度内 ⇒ 站台看着歪）。门前村道 44.5° 其实与真实铁路平行。
# 做法：以"OSM 依兰站节点在真实铁路上的投影点"为锚，站中心 ±A 内投影到 45.0° 直线，±A..±B smoothstep 过渡回原线位；
#       段数与逐段高程保持不变（只动平面）。同时输出车站新位姿。
# 用法：python tools/_rail_align_station_real.py [--a 400] [--b 800]
import argparse, json, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v10.json")
DST = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v13_real45.json")
A_REAL = (2925.5, -4655.7)      # 哈佳线 西南侧真实点
B_REAL = (4252.3, -3324.2)      # 哈佳线 东北侧真实点
OSM_WORLD = (3312.5, -4213.7)   # OSM 依兰站节点 → 世界坐标


def cum_of(pts):
    c = [0.0]
    for i in range(len(pts) - 1):
        c.append(c[-1] + math.dist(pts[i], pts[i + 1]))
    return c


def nearest(pts, cum, target):
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
            best = (dd, i, t, (qx, qz))
    dd, i, t, q = best
    return dd, cum[i] + t * math.dist(pts[i], pts[i + 1]), q, i, t


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
    ap.add_argument("--a", type=float, default=400.0)
    ap.add_argument("--b", type=float, default=800.0)
    args = ap.parse_args()
    A, B = args.a, args.b

    # 真实铁路方向（A→B 弦）与"站台锚点"（OSM 站在该弦上的投影）
    ab = (B_REAL[0] - A_REAL[0], B_REAL[1] - A_REAL[1])
    L = math.hypot(*ab)
    ux, uz = ab[0] / L, ab[1] / L
    brg = math.degrees(math.atan2(ux, uz)) % 360
    t = (OSM_WORLD[0] - A_REAL[0]) * ux + (OSM_WORLD[1] - A_REAL[1]) * uz
    pivot = (A_REAL[0] + ux * t, A_REAL[1] + uz * t)
    print(f"[真实线] 弦长 {L:.0f} m 走向 {brg:.2f}°；站台锚点（OSM 站在真实线上的投影）= ({pivot[0]:.1f},{pivot[1]:.1f})")

    d = json.load(open(SRC, encoding="utf-8"))
    segs, elev = d["segments"], d["elevations"]
    pts = [(segs[0][0], segs[0][1])] + [(s[2], s[3]) for s in segs]
    cum = cum_of(pts)
    dd, s0, q, i0, t0 = nearest(pts, cum, pivot)
    print(f"[对齐] 我们线位离锚点最近处 ({q[0]:.1f},{q[1]:.1f}) 弧长 {s0:.1f}，横向差 {dd:.1f} m → 该段将被拉到真实线上")

    def proj(p):
        tt = (p[0] - pivot[0]) * ux + (p[1] - pivot[1]) * uz
        return (pivot[0] + ux * tt, pivot[1] + uz * tt)

    pts2, moved = [], 0
    for k, p in enumerate(pts):
        ds = abs(cum[k] - s0)
        if ds <= A:
            w = 1.0
        elif ds >= B:
            w = 0.0
        else:
            w = 1.0 - (ds - A) / (B - A)
            w = w * w * (3.0 - 2.0 * w)
        qq = proj(p)
        np_ = (p[0] + (qq[0] - p[0]) * w, p[1] + (qq[1] - p[1]) * w)
        if math.dist(np_, p) > 0.05:
            moved += 1
        pts2.append(np_)

    segs2 = [[round(pts2[i][0], 1), round(pts2[i][1], 1), round(pts2[i + 1][0], 1), round(pts2[i + 1][1], 1)]
             for i in range(len(pts2) - 1)]
    out = dict(d)
    out["segments"] = segs2
    out["elevations"] = elev
    out["note"] = (f"v13：站区线位**对齐真实哈佳线**（OSM 缺口两侧真实点：A(2925.5,-4655.7)/B(4252.3,-3324.2)，"
                   f"两端切向 45.1°/44.9°，弦走向 {brg:.2f}°）。以 OSM 依兰站在真实线上的投影为锚，"
                   f"站中心 ±{A:.0f} m 拉到该直线、±{A:.0f}..±{B:.0f} m smoothstep 过渡回原线位；段数/高程沿用 v10。")
    json.dump(out, open(DST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    cum2 = cum_of(pts2)
    dd2, s0n, cN, i2, t2 = nearest(pts2, cum2, OSM_WORLD)
    p1 = point_at(pts2, cum2, s0n - 106)
    p2 = point_at(pts2, cum2, s0n + 106)
    yaw = math.degrees(math.atan2(p2[0] - p1[0], p2[1] - p1[1]))
    e = elev[i2]
    y = e[0] + (e[1] - e[0]) * t2
    print(f"[对齐] 移动点 {moved} 个")
    print(f"[车站] 新位姿：x={cN[0]:.1f}, z={cN[1]:.1f}, yaw={yaw:.2f}°, y={y:.2f} m（离 OSM 站横向 {dd2:.1f} m）")
    print(f"[写出] {DST}")


if __name__ == "__main__":
    main()
