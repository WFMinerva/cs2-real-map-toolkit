# -*- coding: utf-8 -*-
"""rail_align_to_bounds.py —— 把线位两端按「跨出图边 margin 米」收窄或延伸。

为什么需要：原版**只有铺到可玩区边缘才会生成对外连接**（官方 Map Creation: Outside Connections
"Rail outside connections are created by drawing a railway network to the edge of the playable area"）。
本图公路侧的既定口径是 `roads_connector.boundary_margin_m = 100`（E2C 把干线裁到 半宽+100 m 越边，
原版随后在图边生成对外连接）——铁路沿用同一口径，两端各跨出 100 m。

规则（唯一一条，不写死地图参数）：
  - 图边 = 可玩区边长的一半（`playable.px × playable.m_per_px / 2`，本图 = 7168 m，即 ±7168 方框）；
  - 线位端点若**没跨出**（在图内或超出不足 margin）→ 沿该端切线**向外直线延伸**到「超出 margin」处；
  - 线位端点若**超太多**（> margin）→ 沿折线**向回收**到「超出 margin」的交叉点（多余点整段删掉，不留折角）。

用法：
  python tools/rail_align_to_bounds.py --map yilan --align <in.json> --out <out.json> [--margin 100]
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
    ap.add_argument("--margin", type=float, default=100.0,
                    help="两端各跨出图边多少米（公路侧同口径 = roads_connector.boundary_margin_m）")
    ap.add_argument("--min-seg", type=float, default=100.0,
                    help="端点那一小段至少多长（米）：收/放之后若紧邻的折点离端点太近，"
                         "且它与前后两点共线（夹角 < 1°），就把它删掉——几何不变，但避免给游戏一段 16 m 的碎边")
    a = ap.parse_args()

    cfg = json.loads((ROOT / "maps" / a.map / f"{a.map}.config.json").read_text(encoding="utf-8"))
    bb = cfg["roads_bounds"]
    L, R, Bo, T = bb["left"], bb["right"], bb["bottom"], bb["top"]
    play = float(cfg["playable"]["px"]) * float(cfg["playable"]["m_per_px"])
    half = play / 2.0

    def w(lat, lon):
        return ((lon - L) / (R - L) - 0.5) * play, ((lat - Bo) / (T - Bo) - 0.5) * play

    def ll(x, z):
        return Bo + (z / play + 0.5) * (T - Bo), L + (x / play + 0.5) * (R - L)

    doc = json.loads(Path(a.align).read_text(encoding="utf-8"))
    pts = doc["points"]
    world = [list(w(la, lo)) for la, lo in pts]

    def over(p):
        """正 = 出图边多少米；负 = 还在图内多少米。"""
        return max(abs(p[0]) - half, abs(p[1]) - half)

    def unit(p, q):
        dx, dz = p[0] - q[0], p[1] - q[1]
        n = math.hypot(dx, dz)
        return (dx / n, dz / n) if n > 1e-9 else (0.0, 0.0)

    def at(p, d, s):
        return [p[0] + d[0] * s, p[1] + d[1] * s]

    def solve_between(p_in, p_out):
        """在「over(p_in) ≤ margin < over(p_out)」的两点之间二分，返回 over == margin 的点。"""
        for _ in range(80):
            t = 0.5
            mid = [0.5 * (p_in[0] + p_out[0]), 0.5 * (p_in[1] + p_out[1])]
            if over(mid) < a.margin:
                p_in = mid
            else:
                p_out = mid
        return [0.5 * (p_in[0] + p_out[0]), 0.5 * (p_in[1] + p_out[1])]

    def solve_ray(p, d):
        """从 p 沿 d 走到 over == margin（over 沿 d 单调增）。"""
        hi = 1.0
        while over(at(p, d, hi)) < a.margin and hi < 1e6:
            hi *= 2.0
        return solve_between(p, at(p, d, hi))

    def report(tag, before, after, extra):
        print(f"  {tag}：出界 {before:+8.1f} m → {after:+8.1f} m{extra}")

    # ---- 尾端（末点）----
    o_before = over(world[-1])
    if o_before <= a.margin:                      # 延伸
        d = unit(world[-1], world[-2])
        newp = solve_ray(world[-1], d)
        s = math.hypot(newp[0] - world[-1][0], newp[1] - world[-1][1])
        world.append(newp)
        report("尾端", o_before, over(newp), f"，沿线位切向外延 {s:.1f} m → ({newp[0]:.1f},{newp[1]:.1f})")
        tail_action = f"extend {s:.1f} m"
    else:                                          # 回收
        i = len(world) - 1
        while i > 0 and over(world[i]) > a.margin:
            i -= 1
        newp = solve_between(world[i], world[i + 1])
        dropped = len(world) - (i + 1)
        world = world[:i + 1]
        world[-1] = newp
        report("尾端", o_before, over(newp), f"，回收 {dropped} 个点 → ({newp[0]:.1f},{newp[1]:.1f})")
        tail_action = f"trim, drop {dropped} points"

    # ---- 首端（首点）----
    o_before = over(world[0])
    if o_before <= a.margin:                      # 延伸
        d = unit(world[0], world[1])
        newp = solve_ray(world[0], d)
        s = math.hypot(newp[0] - world[0][0], newp[1] - world[0][1])
        world.insert(0, newp)
        report("首端", o_before, over(newp), f"，沿线位切向外延 {s:.1f} m → ({newp[0]:.1f},{newp[1]:.1f})")
        head_action = f"extend {s:.1f} m"
    else:                                          # 回收
        i = 0
        while i < len(world) - 1 and over(world[i]) > a.margin:
            i += 1
        newp = solve_between(world[i], world[i - 1])
        dropped = i
        world = world[i:]
        world[0] = newp
        report("首端", o_before, over(newp), f"，回收 {dropped} 个点 → ({newp[0]:.1f},{newp[1]:.1f})")
        head_action = f"trim, drop {dropped} points"

    def total(ws):
        return sum(math.hypot(ws[i + 1][0] - ws[i][0], ws[i + 1][1] - ws[i][1]) for i in range(len(ws) - 1))

    # ---- 端点紧邻的碎段清理：只删「共线」的折点，几何不变 ----
    def ang_deg(p, q, r):
        ax, az = q[0] - p[0], q[1] - p[1]
        bx, bz = r[0] - q[0], r[1] - q[1]
        na, nb = math.hypot(ax, az), math.hypot(bx, bz)
        if na < 1e-9 or nb < 1e-9:
            return 180.0
        c = max(-1.0, min(1.0, (ax * bx + az * bz) / (na * nb)))
        return math.degrees(math.acos(c))

    dropped_pts = []
    while len(world) >= 3 and math.hypot(world[1][0] - world[0][0], world[1][1] - world[0][1]) < a.min_seg \
            and ang_deg(world[0], world[1], world[2]) < 1.0:
        dropped_pts.append(f"首端 ({world[1][0]:.1f},{world[1][1]:.1f})")
        del world[1]
    while len(world) >= 3 and math.hypot(world[-1][0] - world[-2][0], world[-1][1] - world[-2][1]) < a.min_seg \
            and ang_deg(world[-3], world[-2], world[-1]) < 1.0:
        dropped_pts.append(f"尾端 ({world[-2][0]:.1f},{world[-2][1]:.1f})")
        del world[-2]
    if dropped_pts:
        print(f"  共线碎点清理：删掉 {len(dropped_pts)} 个（{'、'.join(dropped_pts)}）→ 两端首末段 ≥ {a.min_seg:.0f} m")
    else:
        print(f"  共线碎点清理：无需删点（两端首末段已 ≥ {a.min_seg:.0f} m）")

    print(f"  总长：{total([list(w(la, lo)) for la, lo in pts]):.0f} m → {total(world):.0f} m；"
          f"点数 {len(pts)} → {len(world)}；图边 ±{half:.0f} m，跨出 {a.margin:.0f} m")

    out = {
        "source": f"{Path(a.align).name} + rail_align_to_bounds.py（两端跨出图边 {a.margin:.0f} m）",
        "note": (f"两端按「铺到可玩区边缘才生成对外连接」的官方口径处理：首端 {head_action}，尾端 {tail_action}；"
                 f"图边 = playable.px×m_per_px/2 = ±{half:.0f} m，边缘跨出量与公路侧 boundary_margin_m 同口径。"
                 f"中间折点未动" + (f"；两端各删 1 个共线碎点以保首末段 ≥ {a.min_seg:.0f} m" if dropped_pts else "") + "。"),
        "status": "候选",
        "points": [[round(la, 6), round(lo, 6)] for la, lo in (ll(x, z) for x, z in world)],
    }
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[save] {a.out}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
