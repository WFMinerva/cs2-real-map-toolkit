# -*- coding: utf-8 -*-
"""rail_road_crossings.py —— 找出线位与**公路（OSM，= 图内 E2C 导入的那批）**的交叉点，
并逐点给出：线路里程 / 所在段号 / 轨面绝对高程 / DEM 地形 / 轨面相对地形 / 公路名与等级。

用途：机主反馈"跨公路还是从下面过"时，先离线判定是**剖面问题**（交叉点轨面在地形之下
= 隧道，公路自然在上面）还是**游戏行为问题**（轨面明明在地形之上却仍从下面过）。

数据源一律走 config / 命令行，不写死。
用法：
  python tools/rail_road_crossings.py --map yilan --align maps/yilan/rail/rail_alignment_v6.json
        --full maps/yilan/rail/rail_full_v7.json --osm maps/yilan/data/roads/overpass_roads_FULL.osm
        [--window 60] [--csv out.csv]
"""
import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]

# 只关心"公路"：排除人行/自行车/步道/在建
SKIP_HIGHWAY = {
    "footway", "path", "steps", "cycleway", "bridleway", "pedestrian",
    "construction", "proposed", "platform", "corridor", "raceway",
}


def parse_osm(path):
    tree = ET.parse(path)
    root = tree.getroot()
    nodes = {}
    for n in root.findall("node"):
        nodes[n.get("id")] = (float(n.get("lat")), float(n.get("lon")))
    ways = []
    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        hw = tags.get("highway")
        if hw is None or hw in SKIP_HIGHWAY:
            continue
        refs = [nd.get("ref") for nd in w.findall("nd")]
        pts = [nodes[r] for r in refs if r in nodes]
        if len(pts) < 2:
            continue
        ways.append({
            "id": w.get("id"),
            "highway": hw,
            "name": tags.get("name", ""),
            "ref": tags.get("ref", ""),
            "bridge": tags.get("bridge", ""),
            "tunnel": tags.get("tunnel", ""),
            "pts": pts,
        })
    return ways


def seg_intersect(p1, p2, p3, p4):
    """2D 线段求交，返回 (t, u) 或 None。"""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return t, u
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="yilan")
    ap.add_argument("--align", required=True)
    ap.add_argument("--full", required=True, help="铺轨数据 json（含 segments/elevations）")
    ap.add_argument("--osm", required=True)
    ap.add_argument("--window", type=float, default=60.0,
                    help="交叉点左右各取多长里程算局部地形（米）")
    a = ap.parse_args()

    cfg = json.loads((ROOT / "maps" / a.map / f"{a.map}.config.json").read_text(encoding="utf-8"))
    bb = cfg["roads_bounds"]
    L, R, Bo, T = bb["left"], bb["right"], bb["bottom"], bb["top"]
    play = 14336.0
    half = play / 2.0

    def w(lat, lon):
        u = (lon - L) / (R - L)
        v = (lat - Bo) / (T - Bo)
        return ((u - 0.5) * play, (v - 0.5) * play)

    def ll(x, z):
        return (Bo + (z / play + 0.5) * (T - Bo), L + (x / play + 0.5) * (R - L))

    full = json.loads((ROOT / a.full).read_text(encoding="utf-8") if not Path(a.full).is_absolute()
                      else Path(a.full).read_text(encoding="utf-8"))
    segs = full["segments"]
    elev = full.get("elevations") or []
    if len(elev) != len(segs):
        print(f"[!] elevations 段数 {len(elev)} != segments {len(segs)}，退化为按段端点直接取 segments 无高程")
    pts = [np.array([s[0], s[1]]) for s in segs] + [np.array([segs[-1][2], segs[-1][3]])]
    cum = [0.0]
    for i in range(len(segs)):
        cum.append(cum[-1] + math.hypot(segs[i][2] - segs[i][0], segs[i][3] - segs[i][1]))

    def rail_y(chain):
        """按里程取轨面绝对高程（段内线性）。"""
        for i in range(len(segs)):
            if chain <= cum[i + 1] + 1e-6:
                t = 0.0 if cum[i + 1] == cum[i] else (chain - cum[i]) / (cum[i + 1] - cum[i])
                t = min(max(t, 0.0), 1.0)
                if len(elev) == len(segs):
                    return elev[i][0] + (elev[i][1] - elev[i][0]) * t
                return None
        return None

    ways = parse_osm(ROOT / a.osm if not Path(a.osm).is_absolute() else Path(a.osm))
    road_ll = []
    for way in ways:
        xy = [w(la, lo) for la, lo in way["pts"]]
        # 只保留与可玩区有交集的
        if max(p[0] for p in xy) < -half or min(p[0] for p in xy) > half:
            continue
        if max(p[1] for p in xy) < -half or min(p[1] for p in xy) > half:
            continue
        road_ll.append((way, xy))

    hits = []
    for way, xy in road_ll:
        for j in range(len(xy) - 1):
            p3 = xy[j]
            p4 = xy[j + 1]
            for i in range(len(segs)):
                p1 = (segs[i][0], segs[i][1])
                p2 = (segs[i][2], segs[i][3])
                r = seg_intersect(p1, p2, p3, p4)
                if r is None:
                    continue
                t, u = r
                chain = cum[i] + t * (cum[i + 1] - cum[i])
                x = p1[0] + (p2[0] - p1[0]) * t
                z = p1[1] + (p2[1] - p1[1]) * t
                # 夹角（度）：越接近 0 越平行
                a1 = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
                a2 = math.atan2(p4[1] - p3[1], p4[0] - p3[0])
                ang = math.degrees(abs(((a1 - a2) + math.pi / 2) % math.pi - math.pi / 2))
                hits.append({
                    "chain": chain, "seg": i + 1, "x": x, "z": z,
                    "lat": ll(x, z)[0], "lon": ll(x, z)[1],
                    "road": way, "angle": ang, "y_rail": rail_y(chain),
                })
    hits.sort(key=lambda h: h["chain"])

    ds = rasterio.open(next((ROOT / "maps" / a.map / "data" / "dem").glob("*DEM.tif")))

    def dem_at(x, z):
        lat, lon = ll(x, z)
        r, c = ds.index(lon, lat)
        r = min(max(r, 0), ds.height - 1)
        c = min(max(c, 0), ds.width - 1)
        return float(ds.read(1, window=((r, r + 1), (c, c + 1)))[0, 0])

    print(f"线位：{a.full}")
    print(f"公路图层：{a.osm}（{len(road_ll)} 条落在可玩区）")
    print(f"交叉点共 {len(hits)} 处\n")
    print(f"{'里程m':>7} {'段':>3} {'纬/经':>17} {'公路':<26} {'等级':<12} {'夹角':>5} "
          f"{'轨面':>7} {'地形':>7} {'轨-地':>7} {'判定'}")
    rows = []
    for h in hits:
        terr = dem_at(h["x"], h["z"])
        y = h["y_rail"]
        d = None if y is None else y - terr
        road = h["road"]
        nm = road["name"] or road["ref"] or "(无名)"
        tag = ""
        if road["bridge"]:
            tag += "[桥]"
        if road["tunnel"]:
            tag += "[隧]"
        if d is None:
            verdict = "?"
        elif d < -1.0:
            verdict = "轨在地形下→公路在上（下穿/隧道）"
        elif d < 4.0:
            verdict = "贴地/净空不足"
        else:
            verdict = f"轨在上（净空 {d:.1f} m）"
        print(f"{h['chain']:7.0f} {h['seg']:3d} {h['lat']:8.5f},{h['lon']:8.5f} "
              f"{nm[:24]:<26} {road['highway']+tag:<12} {h['angle']:5.1f} "
              f"{'--' if y is None else f'{y:7.1f}'} {terr:7.1f} "
              f"{'--' if d is None else f'{d:+7.1f}'} {verdict}")
        rows.append({**{k: h[k] for k in ("chain", "seg", "lat", "lon", "x", "z", "angle", "y_rail")},
                     "road_name": nm, "highway": road["highway"], "bridge": road["bridge"],
                     "tunnel": road["tunnel"], "dem": terr, "delta": d})

    # 局部剖面：每个交叉点附近 ±window 内轨面与地形的高差范围
    print(f"\n-- 交叉点 ±{a.window:.0f} m 局部剖面（正=轨在上，负=轨在地形下）--")
    for r in rows:
        c0, c1 = r["chain"] - a.window, r["chain"] + a.window
        ds_ = []
        cc = c0
        while cc <= c1:
            y = rail_y(max(cc, 0.0))
            if y is not None:
                # 该里程处的坐标
                for i in range(len(segs)):
                    if cc <= cum[i + 1] or i == len(segs) - 1:
                        t = 0.0 if cum[i + 1] == cum[i] else (cc - cum[i]) / (cum[i + 1] - cum[i])
                        t = min(max(t, 0.0), 1.0)
                        x = segs[i][0] + (segs[i][2] - segs[i][0]) * t
                        z = segs[i][1] + (segs[i][3] - segs[i][1]) * t
                        ds_.append(y - dem_at(x, z))
                        break
            cc += 10.0
        if ds_:
            print(f"  {r['road_name'][:22]:<24} 里程 {r['chain']:6.0f}  "
                  f"局部高差 {min(ds_):+6.1f} … {max(ds_):+6.1f} m  "
                  f"（交叉点 {r['delta']:+.1f}）")

    if a.csv:
        import csv
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            wr.writerows(rows)
        print(f"\n[save] {a.csv}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
