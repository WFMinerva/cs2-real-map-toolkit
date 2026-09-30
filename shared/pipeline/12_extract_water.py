# -*- coding: utf-8 -*-
"""12 从 Geofabrik 分省 OSM 提取中筛出地图框内的水体多边形（区分外环/内环）。

来源：旧工作区 scripts/04_extract_water.py（已验证），配置驱动改造。
复用来源：Geofabrik 官方免费 OSM 分省提取（如 heilongjiang-latest-free.shp.zip）
Shapefile 多边形环方向约定：外环与外环同向，洞反向。这里用"最大环的符号"判定外环，
再把洞按点包含关系挂到对应外环上，输出规范 GeoJSON（Polygon / MultiPolygon）。

框：config water.extract_bbox_swnw = [S, W, N, E]（通常比可玩区大一圈）
输出：files.water_geojson
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shapefile

from cs2map import config


def signed_area(ring):
    a = 0.0
    for i in range(len(ring) - 1):
        a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return a / 2


def point_in_ring(pt, ring):
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def build_geometry(rings):
    """按符号分组外环/内环，洞挂到包含它的最小外环上。"""
    if not rings:
        return None
    areas = [signed_area(r) for r in rings]
    outer_sign = areas[max(range(len(areas)), key=lambda i: abs(areas[i]))] > 0
    outers = [(r, abs(areas[i])) for i, r in enumerate(rings) if (areas[i] > 0) == outer_sign]
    holes = [r for i, r in enumerate(rings) if (areas[i] > 0) != outer_sign]
    if not outers:
        return None
    polys = [[o[0], []] for o in outers]
    order = sorted(range(len(polys)), key=lambda i: abs(signed_area(polys[i][0])))
    for h in holes:
        probe = h[0]
        for i in order:  # 最小外环先试
            if point_in_ring(probe, polys[i][0]):
                polys[i][1].append(h)
                break
    coords = [[o] + hs for o, hs in polys]
    if len(coords) == 1:
        return {"type": "Polygon", "coordinates": coords[0]}
    return {"type": "MultiPolygon", "coordinates": coords}


def main() -> int:
    mp = config.load()
    shp_dir = mp.region_shp
    s, w, n, e = mp.water["extract_bbox_swnw"]

    r = shapefile.Reader(str(shp_dir / "gis_osm_water_a_free_1.shp"))
    fields = [f[0] for f in r.fields[1:]]
    print("字段:", fields, "要素总数:", len(r))

    feats, by_class = [], {}
    for sr in r.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        pts = list(sr.shape.points)
        if not pts:
            continue
        if not any(w <= x <= e and s <= y <= n for x, y in pts):
            continue
        parts = list(sr.shape.parts) + [len(pts)]
        rings = [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
        rings = [r_ for r_ in rings if len(r_) >= 4]
        geom = build_geometry(rings)
        if geom is None:
            continue
        fclass = rec.get("fclass", "")
        by_class[fclass] = by_class.get(fclass, 0) + 1
        feats.append({
            "type": "Feature",
            "properties": {"osm_id": rec.get("osm_id"), "fclass": fclass,
                           "name": (rec.get("name") or "").strip(), "code": rec.get("code")},
            "geometry": geom,
        })

    print(f"\n框内水体面 {len(feats)} 个，按 fclass: {by_class}")

    def geom_area(g):
        rings = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        tot = 0.0
        for poly in rings:
            for i, ring in enumerate(poly):
                a = abs(signed_area(ring))
                tot += a if i == 0 else -a
        return tot

    feats.sort(key=lambda f: -geom_area(f["geometry"]))
    print("\n面积前 12（含洞扣除）:")
    for f in feats[:12]:
        p = f["properties"]
        nring = 1 if f["geometry"]["type"] == "Polygon" else len(f["geometry"]["coordinates"])
        print(f"  osm_id={p['osm_id']:<12} {p['fclass']:<12} area={geom_area(f['geometry']):.6f}deg2 "
              f"外环数={nring} name={p['name'] or '-'}")

    out = mp.path("water_geojson")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\n[save] {out} {out.stat().st_size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
