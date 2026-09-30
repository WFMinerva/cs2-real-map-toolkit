# -*- coding: utf-8 -*-
"""28 导出「抬桥」高度场数据给 HarbinBridgeLift mod 用。

来源：旧工作区 scripts/105_export_bridge_data.py（已验证），配置驱动改造。

原理
----
Earth2Cities 把每条路都压在地形高度上（见 桥隧问题-诊断与修复.md）。
要修，就得知道哪些路网节点应该抬多高。这份数据就是这个高度场。

1. OSM 里带 bridge 标签的 way 的全部节点 = 桥面，给满净空 clearance_m（桥面是平的）。
2. 从这些节点出发，沿路网图（所有 way 的节点连接）向外做多源 Dijkstra，
   每走 1 m 高度衰减 clearance_m/ramp_length_m —— 自动生成引道坡。
3. 节点最终净空 = max over 所有源 (C - 图上距离 × C/L_ramp)，下限 min_c_m。

输入：Earth2Cities 缓存里的 overpass_roads.osm（= 26 裁剪产物 + 30 部署的那份）
输出：files.bridges_lift（mod 只按 XZ 配对，不做任何几何推理）
"""
import collections
import heapq
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

from cs2map import config

Image.MAX_IMAGE_PIXELS = None


def main() -> int:
    mp = config.load()
    osm_p = Path(config.game()["e2c"]["cache_osm"])
    out_p = mp.path("bridges_lift")
    # 抬桥数据必须用「E2C 导入实际生效的四至」（roads_bounds；哈尔滨与圆整 bounds 在
    # 第 5-6 位小数不同，直接导致 z 向 ~4 m 级偏差——这是迁移复现时实测发现的坑）。
    rb = mp.raw.get("roads_bounds") or mp.raw["bounds"]
    L, R, B, T = rb["left"], rb["right"], rb["bottom"], rb["top"]
    play = mp.playable_m
    sea = mp.sea_level
    clearance = float(mp.raw["bridge_lift"]["clearance_m"])
    ramp = float(mp.raw["bridge_lift"]["ramp_length_m"])
    min_c = float(mp.raw["bridge_lift"]["min_c_m"])
    match_radius = float(mp.raw["bridge_lift"]["match_radius_m"])

    with Image.open(mp.path("playable_png")) as im:
        if im.mode != "I;16":
            im = im.convert("I;16")
        H = np.array(im, dtype=np.float64) / 65535.0 * mp.height_scale
    hn = H.shape[0]

    def terrain(x, z):
        c = min(max(int((x + play / 2) / play * hn), 0), hn - 1)
        r = min(max(int((play / 2 - z) / play * hn), 0), hn - 1)
        return float(H[r, c])

    raw = osm_p.read_text(encoding="utf-8")
    ll = {}
    for m in re.finditer(r'<node id="(\d+)" lat="([-\d.]+)" lon="([-\d.]+)"', raw):
        ll[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
    ways = {}
    for m in re.finditer(r"<way id=\"(\d+)\"[^>]*>(.*?)</way>", raw, re.S):
        ways[int(m.group(1))] = {
            "refs": [int(x) for x in re.findall(r'<nd ref="(\d+)"', m.group(2))],
            "tags": dict(re.findall(r'<tag k="([^"]+)" v="([^"]*)"', m.group(2))),
        }

    def to_world(lat, lon):
        u = min(max((lon - L) / (R - L), 0.0), 1.0)
        v = min(max((lat - B) / (T - B), 0.0), 1.0)
        return ((u - 0.5) * play, (v - 0.5) * play)

    W = {n: to_world(*p) for n, p in ll.items()}
    print(f"OSM: {len(ll)} 节点 / {len(ways)} way  ({osm_p})")

    isb = lambda t: t.get("bridge") not in (None, "no", "false")

    # restrict_near（可选，config bridge_lift.restrict_near）：只抬位置圈内的桥。
    # 依兰 2026-09-30 增：其余桥 CS2 自动立柱表现良好，只修北桥。无此键 = 抬全部（哈尔滨口径）。
    rn = (mp.raw.get("bridge_lift") or {}).get("restrict_near")
    sel = None
    if rn:
        clon, clat, rad = float(rn["lon"]), float(rn["lat"]), float(rn["radius_m"])
        kx = 111320.0 * math.cos(math.radians(clat))

        def _near(n):
            la, lo = ll[n]
            return math.hypot((lo - clon) * kx, (la - clat) * 110940.0) <= rad

        sel = set()
        for wid, w in ways.items():
            if isb(w["tags"]) and any(n in ll and _near(n) for n in w["refs"]):
                sel.add(wid)
        _lab = sorted((ways[wid]["tags"].get("name") or ways[wid]["tags"].get("ref")
                       or f"way{wid}") for wid in sel)
        print(f"restrict_near=({clat},{clon}) r={rad:.0f}m：命中桥 way {len(sel)} 个：{_lab}")

    def wanted(wid, w):
        return isb(w["tags"]) and (sel is None or wid in sel)

    adj = collections.defaultdict(list)
    for w in ways.values():
        r = w["refs"]
        for a, b in zip(r, r[1:]):
            if a == b:
                continue
            d = math.hypot(W[b][0] - W[a][0], W[b][1] - W[a][1])
            adj[a].append((b, d))
            adj[b].append((a, d))

    seeds, bridge_nodes = set(), 0
    for wid, w in ways.items():
        if wanted(wid, w):
            for n in w["refs"]:
                seeds.add(n)
                bridge_nodes += 1
    print(f"带 bridge 的 way: {sum(1 for w in ways.values() if isb(w['tags']))}"
          f"（选中 {len(sel) if sel is not None else '全部'}），"
          f"桥节点引用 {bridge_nodes} 次，去重 {len(seeds)} 个")

    # densify_step_m（可选）：桥面折线等距加布伪节点点。
    # 依兰 2026-09-29 实测：CS2/E2C 把过长边细分出插节点并按地形压低（图内 2686 节点 vs
    # OSM 2019），插节点落在 OSM 节点点位之间，只按 OSM 节点布点永远配不上 → 淹水段挂在
    # 插节点上，抬 OSM 节点视觉「没变化」。步长 < 2×match_radius 即兜住任意位置插节点。
    # 无此键 = 只按 OSM 节点布点（哈尔滨口径不变）。
    dens = float((mp.raw["bridge_lift"].get("densify_step_m") or 0))
    dens_added = 0
    if dens > 0:
        for wid, w in ways.items():
            if not wanted(wid, w):
                continue
            refs = [n for n in w["refs"] if n in W]
            for a, b in zip(refs, refs[1:]):
                (x0, z0), (x1, z1) = W[a], W[b]
                seg = math.hypot(x1 - x0, z1 - z0)
                for j in range(1, int(seg // dens)):
                    t = j * dens / seg
                    key = ("dens", wid, a, b, j)
                    W[key] = (x0 + (x1 - x0) * t, z0 + (z1 - z0) * t)
                    seeds.add(key)
                    dens_added += 1
        print(f"densify_step={dens:.0f} m：桥面折线加布 {dens_added} 个伪节点，种子共 {len(seeds)} 个")

    slope = clearance / ramp
    # 堆元素带自增序号：种子键可能是 int（OSM 节点）或 tuple（densify 伪节点），
    # 同值时 heapq 会比较键本身 → TypeError，序号隔开即免疫。
    best, pq, seq = {}, [], 0
    for s in seeds:
        if clearance > best.get(s, -1e9):
            best[s] = clearance
            heapq.heappush(pq, (-clearance, seq, s))
            seq += 1
    visited, pushes = set(), 0
    while pq:
        negv, _seq, n = heapq.heappop(pq)
        v = -negv
        if n in visited:
            continue
        visited.add(n)
        for m, d in adj[n]:
            nv = v - d * slope
            if nv <= min_c:
                continue
            if nv > best.get(m, -1e9) + 1e-9:
                best[m] = nv
                heapq.heappush(pq, (-nv, seq, m))
                seq += 1
                pushes += 1
    print(f"Dijkstra 完成：访问 {len(visited)} 节点，{pushes} 次入堆，净空>0 的节点 {len(best)} 个")

    raised_bridge = [n for n in best if n in seeds]
    raised_ramp = [n for n in best if n not in seeds]
    over_water = sum(1 for n in best if terrain(*W[n]) < sea)
    print(f"  其中桥面节点 {len(raised_bridge)}，引道节点 {len(raised_ramp)}，地形低于海平面的 {over_water}")

    parent = {n: n for n in seeds}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for wid, w in ways.items():
        if not wanted(wid, w):
            continue
        r = w["refs"]
        for a, b in zip(r, r[1:]):
            union(a, b)
    names = collections.defaultdict(collections.Counter)
    for wid, w in ways.items():
        if not wanted(wid, w):
            continue
        for n in w["refs"]:
            names[find(n)][w["tags"].get("name") or "(无名)"] += 1
    top_names = [c.most_common(1)[0][0] for c in names.values()]

    points = []
    for n, c in best.items():
        x, z = W[n]
        points.append([round(x, 2), round(z, 2), round(c, 3)])
    points.sort(key=lambda p: (-p[2], p[0]))
    print(f"发出 {len(points)} 个点；净空分布：")
    for lo, hi in ((0.15, 1), (1, 3), (3, 6), (6, 9), (9, clearance + 0.01)):
        k = sum(1 for p in points if lo <= p[2] < hi)
        print(f"   {lo:>4}–{hi:<5} m : {k:>5}")

    res = {
        "version": 1,
        "generated_by": "shared/pipeline/28_export_bridge_lift.py",
        "source_osm": osm_p.name,
        "sea_level": sea,
        "default_clearance": clearance,
        "ramp_length": ramp,
        "match_radius": match_radius,
        "counts": {
            "bridge_nodes": len(raised_bridge),
            "ramp_nodes": len(raised_ramp),
            "points": len(points),
            "bridge_structures": len(names),
        },
        "structure_count": len(names),
        # 去重后的名字表；同一名字可能对应多处结构，结构真实数量看 structure_count。
        "structure_names": sorted(set(top_names)),
        "points": points,
    }
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n输出 {out_p}  {out_p.stat().st_size/1024:.0f} KB")
    print(f"涉及结构 {len(names)} 处，举几个名字：{sorted(set(top_names))[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
