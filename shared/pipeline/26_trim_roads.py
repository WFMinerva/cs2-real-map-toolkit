# -*- coding: utf-8 -*-
"""26 把 OSM 路网裁剪到可玩区内部，并做坐标预补偿，产出给 Earth2Cities 用的 .osm。

来源：旧工作区 scripts/100_trim_roads.py（已验证），配置驱动改造。

为什么要裁：
  OSM 抽取是矩形，会把城市道路拦腰切断；CS2 会把「碰到地图边界的路」认成对外连接，
  边缘出现大量断头。Earth2Cities 的 IsNearBoundary() 判定是
  |坐标| > playableWidth/2 - boundary_guard（哈尔滨 = 7168-600 = 6568），
  命中就把该路段换成高速预制件。裁剪框取 roads_trim_m（哈尔滨 6500，离边界 668 m），两个问题一起解决。

坐标补偿（沿用已验证的做法）：
  可玩区是 UTM 网格，Earth2Cities 只按经纬度线性铺路，两者差一个小的等效旋转。
  这里把每个节点的经纬度反算成「让它的线性投影正好落在 UTM 网格上」的伪经纬度。
  哈尔滨端到端复核误差 < 1 cm。

节点共享必须保留：两条 way 在 OSM 里共用节点 = 路口连通。重建时按原 id 1:1 映射。

对外连接干线（config `roads_connector`，可选；依兰 2026-09-30 增）：
  命中 match_highway 类的 way（依兰=motorway，即哈同高速 G1011）不按 roads_trim_m 收边，
  而是裁到 可玩区半宽+boundary_margin_m。目的：故意让路段越过 E2C 的 IsNearBoundary 阈值
  （半宽-600），由 E2C 把越界路段换成高速预制件并连到可玩区边——过境主干道在图边形成
  真实对外连接（地图清单 Required 项）。普通道路维持收边防断头的原逻辑。
  未配置该块的地图（哈尔滨）行为与旧版完全一致。
"""
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from rasterio.warp import transform as warp_transform

from cs2map import config


def main() -> int:
    mp = config.load()
    src = mp.path("roads_osm_full")
    dst = mp.path("roads_trimmed")
    L, R, B, T = mp.bounds
    world = mp.playable_m
    half = world / 2.0
    trim = float(mp.raw["roads_trim_m"])
    center_lon, center_lat = mp.center
    rc = mp.raw.get("roads_connector") or {}
    conn_classes = set(rc.get("match_highway", []))
    conn_margin = float(rc.get("boundary_margin_m", 100.0))
    conn_limit = world / 2.0 + conn_margin
    if conn_classes:
        print(f"对外连接干线: highway∈{sorted(conn_classes)} 不按 {trim:.0f} m 收边，"
              f"裁到 {conn_limit:.1f} m（越 IsNearBoundary 阈值 {world/2.0-600:.0f} m，"
              f"由 E2C 换高速预制件连到图边）")

    if not src.exists():
        print(f"[MISS] {src}")
        return 1

    root = ET.parse(src).getroot()
    ways = root.findall("way")
    node_el = {n.get("id"): n for n in root.findall("node")}
    print(f"源: {len(ways):,} 条 way, {len(node_el):,} 个 node")

    # 只处理被 way 引用到的节点
    used, seen = [], set()
    for w in ways:
        for nd in w.findall("nd"):
            r = nd.get("ref")
            if r in node_el and r not in seen:
                seen.add(r)
                used.append(r)
    lat = np.array([float(node_el[r].get("lat")) for r in used])
    lon = np.array([float(node_el[r].get("lon")) for r in used])
    ex, ny = warp_transform("EPSG:4326", mp.epsg, lon.tolist(), lat.tolist())
    e0, n0 = warp_transform("EPSG:4326", mp.epsg, [center_lon], [center_lat])
    ex = np.asarray(ex) - e0[0]
    ny = np.asarray(ny) - n0[0]
    xy = {used[i]: (float(ex[i]), float(ny[i])) for i in range(len(used))}
    print(f"被引用的节点: {len(xy):,}")

    idmap: dict[str, int] = {}
    out_nodes: list[tuple[int, float, float]] = []   # (新id, x, z) 世界坐标
    next_id = 1

    def orig_id(key: str) -> int:
        nonlocal next_id
        v = idmap.get(key)
        if v is None:
            v = next_id
            next_id += 1
            idmap[key] = v
            out_nodes.append((v, xy[key][0], xy[key][1]))
        return v

    def synth_id(x: float, z: float) -> int:
        nonlocal next_id
        v = next_id
        next_id += 1
        out_nodes.append((v, x, z))
        return v

    def clip_seg(p, q, lim):
        """Liang-Barsky：返回线段在 |x|,|z| <= lim 内的参数区间 (t0,t1)，无交集则 None。"""
        x0, z0 = p
        dx, dz = q[0] - x0, q[1] - z0
        t0, t1 = 0.0, 1.0
        for num, den in ((x0 + lim, -dx), (lim - x0, dx), (z0 + lim, -dz), (lim - z0, dz)):
            if abs(den) < 1e-12:
                if num < 0:
                    return None
            else:
                r = num / den
                if den > 0:
                    if r < t0:
                        return None
                    if r < t1:
                        t1 = r
                else:
                    if r > t1:
                        return None
                    if r > t0:
                        t0 = r
        return (t0, t1)

    kept_ways = []
    n_dropped = n_trimmed = n_intact = 0
    conn_names = []
    for w in ways:
        refs = [nd.get("ref") for nd in w.findall("nd")]
        refs = [r for r in refs if r in xy]
        if len(refs) < 2:
            n_dropped += 1
            continue
        hw_tag = next((t.get("v") for t in w.findall("tag") if t.get("k") == "highway"), None)
        is_conn = bool(conn_classes) and hw_tag in conn_classes
        lim = conn_limit if is_conn else trim
        pts = [xy[r] for r in refs]
        had_outside = any(abs(x) > lim or abs(z) > lim for x, z in pts)
        runs: list[list] = []
        cursor = 0
        cur = None
        for i in range(len(pts) - 1):
            iv = clip_seg(pts[i], pts[i + 1], lim)
            if iv is None:
                if cur is not None:
                    runs.append(cur)
                    cur = None
            else:
                t0, t1 = iv
                if cur is None:
                    cur = []
                a_id = refs[i] if t0 == 0.0 else None
                b_id = refs[i + 1] if t1 == 1.0 else None
                pa = pts[i] if t0 == 0.0 else (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t0,
                                               pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t0)
                pb = pts[i + 1] if t1 == 1.0 else (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t1,
                                                   pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t1)
                if cur and cur[-1][0] is not None and a_id is not None and cur[-1][0] == a_id:
                    pass
                else:
                    cur.append((a_id, pa))
                cur.append((b_id, pb))
                if t1 < 1.0:
                    runs.append(cur)
                    cur = None
        if cur is not None:
            runs.append(cur)
        for run in runs:
            ids = []
            prev = None
            for nid, p in run:
                v = orig_id(nid) if nid is not None else synth_id(p[0], p[1])
                if v != prev:
                    ids.append(v)
                    prev = v
            if len(ids) >= 2:
                kept_ways.append((ids, w, is_conn))
                if is_conn:
                    tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
                    conn_names.append(f"{tags.get('ref') or tags.get('name') or '(无名)'}")
        if not runs:
            n_dropped += 1
        elif had_outside:
            n_trimmed += 1
        else:
            n_intact += 1

    print(f"\n源 {len(ways):,} 条 way  ->  输出 {len(kept_ways):,} 条")
    print(f"   原样保留（整条都在裁剪框内） {n_intact:,}")
    print(f"   裁剪后保留                   {n_trimmed:,}")
    print(f"   整条丢弃（全部在框外）       {n_dropped:,}")
    print(f"   核对: {n_intact}+{n_trimmed}+{n_dropped} = {n_intact+n_trimmed+n_dropped:,} (源 {len(ways):,})")
    if conn_classes:
        from collections import Counter
        cn_summary = dict(Counter(conn_names))
        print(f"   对外连接干线保留 {len(conn_names)} 段（按名称合并 {cn_summary}）")

    # 写 .osm（经纬度 = 补偿后的伪值，使线性投影正好落到世界坐标）
    def to_geo(x, z):
        lon2 = L + (x / world + 0.5) * (R - L)
        lat2 = B + (z / world + 0.5) * (T - B)
        return lat2, lon2

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<osm version="0.6" generator="{mp.map_id}-trim-100">']
    for nid, x, z in out_nodes:
        la, lo = to_geo(x, z)
        out.append(f'  <node id="{nid}" lat="{la:.7f}" lon="{lo:.7f}"/>')
    wi = 0
    for ids, w, _is_conn in kept_ways:
        wi += 1
        out.append(f'  <way id="{900000000 + wi}">')
        for v in ids:
            out.append(f'    <nd ref="{v}"/>')
        for t in w.findall("tag"):
            k, v = t.get("k"), t.get("v")
            k = (k.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                  .replace('"', "&quot;"))
            v = (v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                  .replace('"', "&quot;"))
            out.append(f'    <tag k="{k}" v="{v}"/>')
        out.append("  </way>")
    out.append("</osm>")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[save] {dst}  {dst.stat().st_size/1e6:.2f} MB   节点 {len(out_nodes):,}  way {wi:,}")

    # ---- 自检 ----
    chk = ET.parse(dst).getroot()
    cn = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in chk.findall("node")}
    cw = chk.findall("way")
    mx_nc = mz_nc = mx_c = 0.0
    conn_runs_out = 0
    refs_count: dict[str, int] = {}
    for w in cw:
        hw_tag = next((t.get("v") for t in w.findall("tag") if t.get("k") == "highway"), None)
        is_conn = bool(conn_classes) and hw_tag in conn_classes
        if is_conn:
            conn_runs_out += 1
        for nd in w.findall("nd"):
            r = nd.get("ref")
            refs_count[r] = refs_count.get(r, 0) + 1
            la, lo = cn[r]
            x = (lo - L) / (R - L) * world - half
            z = (la - B) / (T - B) * world - half
            if is_conn:
                mx_c = max(mx_c, abs(x), abs(z))
            else:
                mx_nc = max(mx_nc, abs(x))
                mz_nc = max(mz_nc, abs(z))
    shared_out = sum(1 for v in refs_count.values() if v >= 2)

    src_refs: dict[str, int] = {}
    for w in ways:
        for nd in w.findall("nd"):
            r = nd.get("ref")
            if r in xy:
                src_refs[r] = src_refs.get(r, 0) + 1
    shared_src_all = sum(1 for v in src_refs.values() if v >= 2)

    guard = float(mp.raw["roads_boundary_guard_m"])
    worst = 0.0
    n_chk = 0
    sample = [r for r in idmap if r in xy][:4000]
    if sample:
        la_s = [float(node_el[r].get("lat")) for r in sample]
        lo_s = [float(node_el[r].get("lon")) for r in sample]
        exx, nyy = warp_transform("EPSG:4326", mp.epsg, lo_s, la_s)
        for i, r in enumerate(sample):
            la2, lo2 = cn[str(idmap[r])]
            px = (lo2 - L) / (R - L) * world - half
            pz = (la2 - B) / (T - B) * world - half
            worst = max(worst, math.hypot(px - (exx[i] - e0[0]), pz - (nyy[i] - n0[0])))
            n_chk += 1

    ok1 = max(mx_nc, mz_nc) <= trim + 0.5 and (not conn_classes or mx_c <= conn_limit + 0.5)
    print(f"\n自检 1  普通路最大 |x|={mx_nc:.1f} |z|={mz_nc:.1f}（裁剪框 {trim:.0f}）")
    if conn_classes:
        print(f"        连接干线最大距中心 {mx_c:.1f} m（上限 {conn_limit:.1f}，越 {world/2.0-600:.0f} m 阈值 "
              f"{'是' if mx_c > world/2.0 - 600 else '否'}）")
    print(f"        -> {'通过' if ok1 else '失败'}")
    ok2 = half - max(mx_nc, mz_nc) > guard
    print(f"自检 2  普通路单点最近边界距离 >= {half - max(mx_nc, mz_nc):.0f} m（IsNearBoundary 阈值是距边界 {guard:.0f} m）"
          f" -> {'通过，不会被换成高速预制件' if ok2 else '失败'}")
    ok2b = True
    if conn_classes:
        ok2b = conn_runs_out > 0 and mx_c > half - guard
        print(f"自检 2b 连接干线 {conn_runs_out} 段、最远 {mx_c:.1f} m（需 > 阈值 {half - guard:.0f} m 成对外连接）"
              f" -> {'通过' if ok2b else '失败'}")
    print(f"自检 3  共享节点（>=2 条 way 引用，即路口）源 {shared_src_all:,} -> 输出 {shared_out:,}")
    print(f"自检 4  端到端 {n_chk:,} 个节点，mod 线性投影 vs 真实 UTM 世界坐标 最大误差 {worst:.4f} m")
    print(f"自检 5  根=<{chk.tag}>  node={len(cn):,}  way={len(cw):,}")
    return 0 if (ok1 and ok2 and ok2b and worst < 0.05 and chk.tag == "osm") else 1


if __name__ == "__main__":
    sys.exit(main())
