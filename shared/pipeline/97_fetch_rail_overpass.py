# -*- coding: utf-8 -*-
"""97 取铁路/车站原始 OSM 数据（铁路轮的输入，配置驱动）。

背景（2026-09-29 机主定「先研究布置火车站的办法」）：Earth2Cities 只导公路，
不导铁路（实测：dll 里没有 railway/Rail/Train 字面量，RoadPrefabResolver 还把
train/track lane 明确排除在候选之外）→ 铁路只能另外造。造之前先把**真实线位**取到手。

口径（沿用 96 路网取数的已验证套路）：
  - bbox = config bounds 外扩 railways.pad_m；
  - way 取 railway 白名单 + node 取 railway=station/halt（车站是 node，不是 way）；
  - way 命中后 (._;>;) 带全节点递归（后续裁剪/建网都要真实端点）；
  - 响应原样落盘（不改写），保留 osm_base 数据时点。
全部参数读 maps/<id>/<id>.config.json 的 railways 块，无地图字面值。
端点全失败或自检不过 → 退出码 1，不擅自改用未验证数据路线。
"""
import math
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config


def bbox_pad(mp, pad_m: float):
    L, R, B, T = mp.bounds
    lat_c = abs(mp.center[1])
    d_lat = pad_m / 110940.0
    d_lon = pad_m / (111320.0 * math.cos(math.radians(lat_c)))
    return (B - d_lat, L - d_lon, T + d_lat, R + d_lon)


def main() -> int:
    mp = config.load()
    rw = mp.raw.get("railways")
    if not rw:
        print("[config] 缺 railways 块，拒绝猜测口径")
        return 1
    classes = [str(c) for c in rw["railway_classes"]]
    stations = [str(c) for c in rw.get("station_classes", ["station", "halt"])]
    endpoints = [str(u) for u in rw["endpoints"]]
    S, W, N, E = bbox_pad(mp, float(rw["pad_m"]))
    regex = "^(" + "|".join(re.escape(c) for c in classes) + ")$"
    sregex = "^(" + "|".join(re.escape(c) for c in stations) + ")$"
    query = (
        "[out:xml][timeout:180];\n"
        f'way["railway"~"{regex}"]({S:.6f},{W:.6f},{N:.6f},{E:.6f});\n'
        f'node["railway"~"{sregex}"]({S:.6f},{W:.6f},{N:.6f},{E:.6f});\n'
        "(._;>;);\n"
        "out body;"
    )
    dst = mp.path("railways_osm")
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"{mp.display_name}: bbox S={S:.6f} W={W:.6f} N={N:.6f} E={E:.6f}"
          f"（外扩 {float(rw['pad_m']):.0f} m）")
    print(f"railway 白名单 {classes}；车站类别 {stations}\n")

    data, last_err = None, None
    for ep in endpoints:
        print(f"[post] {ep}")
        try:
            req = urllib.request.Request(
                ep, data=query.encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "User-Agent": "cs2-map-workshop/1 (personal map making)"})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            print(f"[resp ] {len(data)/1e3:.1f} KB")
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[fail] {type(exc).__name__}: {exc}")
            last_err = exc
    if data is None:
        print(f"\n[缺口] 所有 Overpass 端点均失败（{last_err}）。按规程停下来报告机主。")
        return 1

    tmp = dst.with_suffix(".part")
    tmp.write_bytes(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        print(f"[FAIL] 响应不是合法 XML：{exc}")
        return 1
    if root.tag != "osm":
        print(f"[FAIL] 根元素 <{root.tag}> != <osm>")
        return 1

    nodes = root.findall("node")
    ways = root.findall("way")
    nlat = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in nodes}
    L, R, B, T = mp.bounds
    print(f"node {len(nodes):,}  way {len(ways):,}")
    in_frame = 0
    for w in ways:
        t = {x.get("k"): x.get("v") for x in w.findall("tag")}
        pts = [nlat[nd.get("ref")] for nd in w.findall("nd") if nd.get("ref") in nlat]
        if not pts:
            continue
        inside = sum(1 for la, lo in pts if B <= la <= T and L <= lo <= R)
        if inside:
            in_frame += 1
        span_lat = f"{min(p[0] for p in pts):.4f}..{max(p[0] for p in pts):.4f}"
        span_lon = f"{min(p[1] for p in pts):.4f}..{max(p[1] for p in pts):.4f}"
        print(f"  way {w.get('id'):<11} {t.get('railway',''):<11} {t.get('name','(无名)'):<12}"
              f" 点数{len(pts):>4} 框内{inside:>4} bridge={t.get('bridge','-'):<4} tunnel={t.get('tunnel','-'):<3}"
              f" lat {span_lat} lon {span_lon}")
    print(f"→ 至少一端/一点在框内的铁路 way {in_frame} 条")
    for n in nodes:
        t = {x.get("k"): x.get("v") for x in n.findall("tag")}
        if t.get("railway") in ("station", "halt"):
            print(f"  车站 {n.get('id')} {t.get('name','(无名)')} {t.get('railway')}"
                  f"  lat {n.get('lat')} lon {n.get('lon')}"
                  f"  框内={'是' if B <= float(n.get('lat')) <= T and L <= float(n.get('lon')) <= R else '否'}")
    meta = root.find("meta")
    if meta is not None:
        print(f"osm_base {meta.get('osm_base')}")
    if not ways:
        print("[FAIL] 0 条铁路 way（bbox/口径可疑）")
        return 1
    tmp.replace(dst)
    print(f"[save] {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
