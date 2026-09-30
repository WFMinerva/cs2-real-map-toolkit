# -*- coding: utf-8 -*-
"""68 导出「批量街名」数据给 mod 用（配置驱动，只读 E2C 缓存）。

根因（2026-09-29 实测）：Earth2Cities 的 dll 字符串表只有 highway/ref 两个 OSM 标签，
**不读 name** → 导入的路全拿 CS2 主题默认花名（中文环境风信子街/白菊路之流）。
游戏侧官方批量入口 = Game.UI.NameSystem.SetCustomName(Entity, string)（Game.dll 公开方法，
NameSystem 自带 Serialize/Deserialize → 自定义名进存档）。

数据口径：
  - 源 = E2C 缓存 overpass_roads.osm（= 导入实际生效的那份）
  - 每条带 name（无 name 用 ref，config street_names.name_fallback_ref）的 way，
    逐节点对取**段中点**世界坐标（roads_bounds 线性映射，与 28 同口径）
  - mod 按边 Bezier 中点配对（半径 = 本文件 radius），SetCustomName
  - OSM 本就无名的 way 不发射 → 保留游戏花名（现实也无名，正常）
坐标口径必须用 roads_bounds（E2C 实测生效四至），理由同 28 头注。
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config

DEFAULT_CLASSES = ["service", "primary", "trunk", "tertiary", "secondary",
                   "residential", "unclassified", "motorway", "motorway_link"]


def main() -> int:
    mp = config.load()
    sn = mp.raw.get("street_names") or {}
    classes = set(sn.get("highway_classes", DEFAULT_CLASSES))
    fallback_ref = bool(sn.get("name_fallback_ref", True))
    radius = float(sn.get("match_radius_m", 6.0))
    min_component_edges = int(sn.get("min_component_edges", 2))
    unnamed_label = str(sn.get("unnamed_label", "无名路"))
    split_multi_name = bool(sn.get("split_multi_name", True))
    osm_p = Path(config.game()["e2c"]["cache_osm"])
    out_p = mp.path("street_names")

    rb = mp.raw.get("roads_bounds") or mp.raw["bounds"]
    L, R, B, T = rb["left"], rb["right"], rb["bottom"], rb["top"]
    play = mp.playable_m

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

    points = []
    named_ways = 0
    for wid, w in ways.items():
        t = w["tags"]
        if t.get("highway") not in classes:
            continue
        nm = t.get("name") or (t.get("ref") if fallback_ref else None)
        if not nm:
            continue
        named_ways += 1
        refs = [n for n in w["refs"] if n in ll]
        for a, b in zip(refs, refs[1:]):
            (x0, z0), (x1, z1) = to_world(*ll[a]), to_world(*ll[b])
            points.append([round((x0 + x1) / 2, 2), round((z0 + z1) / 2, 2), nm])

    names = sorted(set(p[2] for p in points))
    print(f"带名 way {named_ways} 个 → 段中点 {len(points)} 个，去重街名 {len(names)} 个")
    print("  街名样例：" + "、".join(names[:14]))
    print(f"配对半径 {radius} m；分组最小边数 {min_component_edges}；无名字段「{unnamed_label}」；多街名拆分={split_multi_name}")

    res = {"version": 1, "generated_by": "shared/pipeline/68_export_street_names.py",
           "source_osm": osm_p.name, "radius": radius,
           "min_component_edges": min_component_edges, "unnamed_label": unnamed_label,
           "split_multi_name": split_multi_name,
           "counts": {"ways": named_ways, "points": len(points), "names": len(names)},
           "points": points}
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[save] {out_p}  {out_p.stat().st_size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
