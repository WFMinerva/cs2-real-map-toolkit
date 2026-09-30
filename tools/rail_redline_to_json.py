# -*- coding: utf-8 -*-
"""rail_redline_to_json.py —— 把机主在底图上手绘的**红线**转成经纬度折线 JSON。

v2（2026-09-30）：支持**断线**（机主会因隧道/桥梁断开不画）。
- 阈值找红色像素 → 聚成网格 → 连通分量（BFS）→ 每个分量内贪心串链 →
  分量之间按端点最近邻串起来，并把 **> gap_min_m 的跨接记成 gaps（隧道/断口候选）**。
- 平滑（滑动平均）→ RDP 简化 → 按底图 bbox 线性换算经纬度。
- 同时输出 `segments`（游戏世界坐标 [x0,z0,x1,z1]），供 tools/rail_profile_check.py 直接读。

用法：
  python tools/rail_redline_to_json.py <红线图.png|webp> <输出.json> --bbox 'S,W,N,E'
        [--map yilan] [--tol-m 60] [--cell 6] [--gap-min-m 120]
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]


def rdp(points, eps):
    if len(points) < 3:
        return points
    (x0, y0), (x1, y1) = points[0], points[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy) or 1.0
    idx, dmax = 0, -1.0
    for i in range(1, len(points) - 1):
        x, y = points[i]
        dist = abs(dy * (x - x0) - dx * (y - y0)) / norm
        if dist > dmax:
            idx, dmax = i, dist
    if dmax > eps:
        return rdp(points[:idx + 1], eps)[:-1] + rdp(points[idx:], eps)
    return [points[0], points[-1]]


def order_component(cells):
    """分量内贪心串链：从投影最小端出发，逐步取最近的未用格。"""
    pts = np.asarray(cells, dtype=np.float64)
    c = pts.mean(axis=0)
    d = pts - c
    # 主轴（2x2 协方差的最大特征向量）
    cov = d.T @ d
    w, v = np.linalg.eigh(cov)
    axis = v[:, int(np.argmax(w))]
    proj = d @ axis
    start = int(np.argmin(proj))
    used = np.zeros(len(pts), dtype=bool)
    order = [start]
    used[start] = True
    cur = pts[start]
    for _ in range(len(pts) - 1):
        dd = np.hypot(pts[:, 0] - cur[0], pts[:, 1] - cur[1])
        dd[used] = 1e18
        nxt = int(np.argmin(dd))
        used[nxt] = True
        order.append(nxt)
        cur = pts[nxt]
    return pts[order]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("img")
    ap.add_argument("out")
    ap.add_argument("--bbox", required=True, help="S,W,N,E（必须与底图一致）")
    ap.add_argument("--map", default="yilan")
    ap.add_argument("--tol-m", type=float, default=60.0)
    ap.add_argument("--cell", type=int, default=6, help="聚格边长（像素）")
    ap.add_argument("--gap-min-m", type=float, default=120.0, help="跨接超过此长度记为 gaps（隧道/断口）")
    ap.add_argument("--base", default=None,
                    help="对应的底图（未画线版）：给了就做「与底图差分」提取，能彻底排除影像里的棕红农田噪声")
    ap.add_argument("--red-delta", type=float, default=60.0, help="差分模式下，红度增量阈值")
    ap.add_argument("--rmin", type=float, default=180.0, help="阈值模式：R 下限")
    ap.add_argument("--gbmax", type=float, default=90.0, help="阈值模式：G/B 上限")
    ap.add_argument("--red-min", type=float, default=100.0, help="阈值模式：R-max(G,B) 下限")
    ap.add_argument("--outlier-m", type=float, default=400.0, help="离主走廊超过此距离的红格当噪声剔除")
    a = ap.parse_args()

    s, w, n, e = (float(x) for x in a.bbox.split(","))
    im = Image.open(a.img).convert("RGB")
    W_, H_ = im.size
    arr = np.asarray(im.filter(ImageFilter.GaussianBlur(0.5))).astype(np.int16)
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    redness = R - np.maximum(G, B)
    if a.base:
        bim = Image.open(a.base).convert("RGB")
        if bim.size != (W_, H_):
            bim = bim.resize((W_, H_), Image.LANCZOS)
            print(f"  底图已缩放到 {bim.size} 以对齐手绘图")
        barr = np.asarray(bim.filter(ImageFilter.GaussianBlur(0.5))).astype(np.int16)
        b_red = barr[:, :, 0] - np.maximum(barr[:, :, 1], barr[:, :, 2])
        mask = (redness - b_red) >= a.red_delta
        print(f"  差分模式：红度增量 ≥{a.red_delta:.0f} → {int(mask.sum())} 像素")
    else:
        # 严格红：机主用纯红画笔，严格阈值能排掉影像里的棕红农田（实测 R≥180/G,B≤90/红度≥100 最干净）
        mask = (redness >= a.red_min) & (R >= a.rmin) & (G <= a.gbmax) & (B <= a.gbmax)
        print(f"  阈值模式：R≥{a.rmin:.0f} G,B≤{a.gbmax:.0f} 红度≥{a.red_min:.0f} → {int(mask.sum())} 像素")
    ys, xs = np.nonzero(mask)
    if len(xs) < 50:
        print(f"[FAIL] 只找到 {len(xs)} 个红色像素；请用纯红#FF0000、粗一点的笔")
        return 1
    lat_m = 111320.0
    lon_m = 111320.0 * math.cos(math.radians((s + n) / 2))
    mpp_x = (e - w) * lon_m / W_          # 每像素米（x）
    mpp_y = (n - s) * lat_m / H_          # 每像素米（y）
    print(f"图 {W_}x{H_}  红像素 {len(xs)}  分辨率 {mpp_x:.2f} m/px(x) {mpp_y:.2f} m/px(y)")

    # 聚格
    cell = a.cell
    keys = np.stack([xs // cell, ys // cell], axis=1)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    acc = np.zeros((len(uniq), 3))
    np.add.at(acc, inv, np.concatenate([np.stack([xs, ys], axis=1).astype(float),
                                        np.ones((len(xs), 1))], axis=1))
    centers = acc[:, :2] / acc[:, 2:3]
    idx_of = {tuple(k): i for i, k in enumerate(uniq)}
    # 连通分量（8 邻域）
    comp = -np.ones(len(uniq), dtype=int)
    ncomp = 0
    for i in range(len(uniq)):
        if comp[i] >= 0:
            continue
        stack = [i]
        comp[i] = ncomp
        while stack:
            j = stack.pop()
            kx, ky = uniq[j]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = idx_of.get((kx + dx, ky + dy))
                    if nb is not None and comp[nb] < 0:
                        comp[nb] = ncomp
                        stack.append(nb)
        ncomp += 1
    print(f"聚成 {len(uniq)} 格 → {ncomp} 个连通分量")

    # v3 排序法：先剔除离主走廊过远的红块（影像噪声/误画），再沿"南→北"主轴投影排序。
    # 手绘线是单调东北向时，这个排法比"分量最近邻串联"稳得多。
    allc = centers
    sw = allc[np.argmax(allc[:, 1])]        # y 最大 = 最南
    ne = allc[np.argmin(allc[:, 1])]
    axis = ne - sw
    axis = axis / (np.linalg.norm(axis) or 1.0)
    perp = np.array([-axis[1], axis[0]])
    rel = allc - sw
    off = rel @ perp
    sel = np.abs(off - np.median(off)) <= (a.outlier_m / mpp_x)
    dropped = int((~sel).sum())
    cells = allc[sel]
    proj = (cells - sw) @ axis
    cells = cells[np.argsort(proj)]
    print(f"剔除离群 {dropped} 格（离主走廊 >{a.outlier_m:.0f} m）；参与排序 {len(cells)} 格")

    path = [cells[0]]
    gaps = []
    for p in cells[1:]:
        d_m = math.hypot((p[0] - path[-1][0]) * mpp_x, (p[1] - path[-1][1]) * mpp_y)
        if d_m >= a.gap_min_m:
            gaps.append({"after_index": len(path) - 1, "length_m": round(d_m, 1),
                         "from": [round(float(path[-1][0]), 1), round(float(path[-1][1]), 1)],
                         "to": [round(float(p[0]), 1), round(float(p[1]), 1)]})
        path.append(p)
    path = np.asarray(path)
    print(f"走出 {len(path)} 节点；断口 {len(gaps)} 处：" +
          (", ".join(f"{g['length_m']:.0f} m" for g in gaps) if gaps else "无"))

    # 平滑
    k = 5
    if len(path) > k:
        pad = np.vstack([np.repeat(path[:1], k // 2, axis=0), path, np.repeat(path[-1:], k // 2, axis=0)])
        sm = np.stack([np.convolve(pad[:, i], np.ones(k) / k, mode="valid") for i in range(2)], axis=1)
    else:
        sm = path
    simp = rdp([tuple(p) for p in sm], a.tol_m / mpp_x)
    print(f"RDP（容差 {a.tol_m:.0f} m）→ {len(simp)} 点")

    # 读地图 bounds → 顺便产出游戏坐标 segments
    cfg = json.loads((ROOT / "maps" / a.map / f"{a.map}.config.json").read_text(encoding="utf-8"))
    bb = cfg["roads_bounds"]
    L, Rr, Bo, T = bb["left"], bb["right"], bb["bottom"], bb["top"]
    play = 14336.0

    def to_world(lat, lon):
        u = (lon - L) / (Rr - L)
        v = (lat - Bo) / (T - Bo)
        return (u - 0.5) * play, (v - 0.5) * play

    pts_ll = []
    for (x, y) in simp:
        lon = w + x / W_ * (e - w)
        lat = n - y / H_ * (n - s)
        pts_ll.append([round(float(lat), 6), round(float(lon), 6)])
    if pts_ll[0][0] > pts_ll[-1][0]:
        pts_ll.reverse()
    segs = []
    for i in range(len(pts_ll) - 1):
        (la0, lo0), (la1, lo1) = pts_ll[i], pts_ll[i + 1]
        x0, z0 = to_world(la0, lo0)
        x1, z1 = to_world(la1, lo1)
        segs.append([round(x0, 1), round(z0, 1), round(x1, 1), round(z1, 1)])
    L_km = sum(math.hypot((pts_ll[i + 1][0] - pts_ll[i][0]) * lat_m,
                          (pts_ll[i + 1][1] - pts_ll[i][1]) * lon_m) for i in range(len(pts_ll) - 1)) / 1000
    out = {
        "source": f"机主手绘红线（{Path(a.img).name}）→ 像素提取/串链/RDP（tools/rail_redline_to_json.py v2）",
        "note": f"bbox S{s},W{w},N{n},E{e}；图 {W_}x{H_}；容差 {a.tol_m:.0f} m；断口 {len(gaps)} 处",
        "status": "候选（待 DEM 坡度复核；断口=隧道/桥，需单独处理）",
        "gaps": gaps,
        "points": pts_ll,
        "segments": segs,
    }
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[save] {a.out}  {len(pts_ll)} 点 / {len(segs)} 段 / 总长 {L_km:.2f} km")
    for i, (la, lo) in enumerate(pts_ll):
        print(f"   {i:2d}  {la:.6f}, {lo:.6f}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
