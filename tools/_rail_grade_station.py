# 一次性探针（2026-09-30 车站轮）：把站区轨面从 14 m 路基降到地面线（机主要求：车站放平地上）。
# 依据：站区地形实测是平地 99–101 m（不是天然高坡），真实依兰站是地面站；原先 114.5 m 来自
#       make_rail_elev_profile 的全局 --height 14（全线 14 m 路基）。
# 做法：站中心 ±A 内取地面线高度（现地 +1.0 m 低路堤），±A..±B 线性过渡回原 v13 高程（坡度受控 ≤ --max-grade）。
#       只动 elevations（逐段绝对高程），平面几何与段数不变。
# 用法：python tools/_rail_grade_station.py [--a 150] [--max-grade 2.2] [--lift 1.0]
import argparse, json, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v13_real45.json")
DST = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v14_grade.json")
OSM_WORLD = (3312.5, -4213.7)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=float, default=150.0, help="站中心取地面线的半长（米）")
    ap.add_argument("--max-grade", type=float, default=2.2, help="过渡段最大坡度（百分数）")
    ap.add_argument("--lift", type=float, default=1.0, help="地面线相对现地的高度（米，低路堤）")
    args = ap.parse_args()
    A, GMAX, LIFT = args.a, args.max_grade, args.lift

    # 地形采样（游戏用高度图：gray/65535*4096）
    import numpy as np
    from PIL import Image
    im = Image.open(os.path.join(ROOT, "maps", "yilan", "products", "yilan_playable.png"))
    if im.mode != "I;16":
        im = im.convert("I;16")
    H = np.array(im, dtype=np.float64) / 65535.0 * 4096.0
    hn = H.shape[0]
    play = 14336.0

    def terr(x, z):
        c = min(max(int((x + play / 2) / play * hn), 0), hn - 1)
        r = min(max(int((play / 2 - z) / play * hn), 0), hn - 1)
        return float(H[r, c])

    d = json.load(open(SRC, encoding="utf-8"))
    segs, elev = d["segments"], d["elevations"]
    pts = [(segs[0][0], segs[0][1])] + [(s[2], s[3]) for s in segs]
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + math.dist(pts[i], pts[i + 1]))

    # 站中心弧长（OSM 站投影到折线）
    best = None
    for i in range(len(pts) - 1):
        ax, az = pts[i]
        bx, bz = pts[i + 1]
        abx, abz = bx - ax, bz - az
        l2 = abx * abx + abz * abz
        t = max(0.0, min(1.0, ((OSM_WORLD[0] - ax) * abx + (OSM_WORLD[1] - az) * abz) / l2)) if l2 else 0.0
        qx, qz = ax + abx * t, az + abz * t
        dd = math.hypot(OSM_WORLD[0] - qx, OSM_WORLD[1] - qz)
        if best is None or dd < best[0]:
            best = (dd, i, t, (qx, qz))
    dd, i0, t0, q = best
    s0 = cum[i0] + t0 * math.dist(pts[i0], pts[i0 + 1])
    ground = terr(q[0], q[1]) + LIFT
    print(f"[降坡] 站中心 ({q[0]:.1f},{q[1]:.1f}) 弧长 {s0:.1f}；现地地形 {terr(q[0], q[1]):.2f} → 目标轨面 {ground:.2f} m")

    # 原高程函数（线性插值）
    def orig(s):
        if s <= 0:
            return elev[0][0]
        for i in range(len(elev)):
            if s <= cum[i + 1] or i == len(elev) - 1:
                L = cum[i + 1] - cum[i]
                t = (s - cum[i]) / L if L > 0 else 0.0
                return elev[i][0] + (elev[i][1] - elev[i][0]) * t
        return elev[-1][1]

    # 过渡半长：由最大坡度决定（取两侧最大落差）
    drop = max(orig(s0) - ground, 0.0)
    B = A + drop / (GMAX / 100.0)
    print(f"[降坡] 原轨面 {orig(s0):.2f} m → 落差 {drop:.2f} m；取平半长 {A:.0f} m，过渡到 ±{B:.0f} m（≤{GMAX}%）")

    def newy(s):
        ds = abs(s - s0)
        if ds <= A:
            return ground
        if ds >= B:
            return orig(s)
        # 线性过渡：从 ground 到 orig(sign*B)
        sb = s0 + (B if s > s0 else -B)
        yEnd = orig(sb)
        w = (ds - A) / (B - A)
        return ground + (yEnd - ground) * w

    new_elev = [[round(newy(cum[i]), 2), round(newy(cum[i + 1]), 2)] for i in range(len(elev))]
    maxg = 0.0
    for i in range(len(new_elev)):
        L = cum[i + 1] - cum[i]
        if L > 0.1:
            maxg = max(maxg, abs(new_elev[i][1] - new_elev[i][0]) / L * 100.0)
    out = dict(d)
    out["elevations"] = new_elev
    out["note"] = (f"v14：**站区降到地面线**（机主 2026-09-30：车站放平地）。站中心 ±{A:.0f} m 取现地+{LIFT:.1f} m 低路堤"
                   f"（目标 {ground:.2f} m），±{A:.0f}..±{B:.0f} m 线性过渡回原 v13 高程（≤{GMAX}% 坡）。"
                   f"平面几何/段数同 v13；仅改 elevations。")
    json.dump(out, open(DST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[降坡] 新剖面最大坡 {maxg:.2f}%；车站轨面 = {ground:.2f} m")
    print(f"[写出] {DST}")


if __name__ == "__main__":
    main()
