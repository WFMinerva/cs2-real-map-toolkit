# -*- coding: utf-8 -*-
"""96 从 Overpass API 取可玩区周边路网原始响应，存为 26 的输入 overpass_roads_FULL.osm。

背景（管线缺口补全，2026-09-30）：哈尔滨链的 FULL 文件是 Earth2Cities 浏览器工具
"Download roads from Overpass" 的原样保存（Overpass API 0.7.62 响应，见 MANIFEST）。
本图同口径改由脚本直取，避免把数据下载默认分派给机主。与已验证链的关系：
数据源相同（官方 Overpass API）、文件格式相同（26 按原始响应设计，30 部署后进游戏导入）；
差异仅在"谁按的下载按钮"。产物在游戏验收前一律是**候选版本**。

口径（自哈尔滨 FULL 实测样本反推）：
  - way 只取 E2C 关注的 highway 白名单（哈尔滨 FULL 实测仅 7 类，无 _link/footway 等）；
  - way(bbox) 命中后 (._;>;) 带全节点递归——26 的 Liang-Barsky 裁剪需要真实端点插值；
  - bbox = config bounds 外扩 roads_overpass.pad_m；
  - 响应原样落盘（不改写），meta.osm_base 保留数据时点溯源。
全部参数读 maps/<id>/<id>.config.json 的 roads_overpass 块，无地图字面值。
端点全部失败或自检不过 → 退出码 1 并写明缺口，不擅自改用未验证数据路线。
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


def bbox_pured_deg(mp) -> tuple[float, float, float, float]:
    """返回 Overpass 顺序 (S, W, N, E)；外扩按 Δlat=m/110940、Δlon=m/(111320·cos|lat_c|)）。"""
    L, R, B, T = mp.bounds
    pad = float(mp.raw["roads_overpass"]["pad_m"])
    lat_c = abs(mp.center[1])
    d_lat = pad / 110940.0
    d_lon = pad / (111320.0 * math.cos(math.radians(lat_c)))
    return (B - d_lat, L - d_lon, T + d_lat, R + d_lon)


def main() -> int:
    mp = config.load()
    ro = mp.raw.get("roads_overpass")
    if not ro:
        print("[config] 缺 roads_overpass 块，拒绝猜测口径")
        return 1
    classes = [str(c) for c in ro["highway_classes"]]
    endpoints = [str(u) for u in ro["endpoints"]]
    S, W, N, E = bbox_pured_deg(mp)
    L, R, B, T = mp.bounds
    regex = "^(" + "|".join(re.escape(c) for c in classes) + ")$"
    query = (
        "[out:xml][timeout:180];\n"
        f'way["highway"~"{regex}"]({S:.6f},{W:.6f},{N:.6f},{E:.6f});\n'
        "(._;>;);\n"
        "out body;"
    )
    dst = mp.path("roads_osm_full")
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"{mp.display_name}: 框 lat {B:.6f}..{T:.6f} lon {L:.6f}..{R:.6f}，"
          f"外扩 {float(ro['pad_m']):.0f} m 后 S={S:.6f} W={W:.6f} N={N:.6f} E={E:.6f}")
    print(f"highway 白名单({len(classes)}): {classes}\n")

    data = None
    last_err = None
    for ep in endpoints:
        print(f"[post] {ep}")
        try:
            req = urllib.request.Request(
                ep, data=query.encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "User-Agent": "cs2-map-workshop/1 (personal map making)"})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            print(f"[resp ] {len(data)/1e6:.2f} MB")
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[fail] {type(exc).__name__}: {exc}")
            last_err = exc
    if data is None:
        print(f"\n[缺口] 所有 Overpass 端点均失败（{last_err}）。"
              "按规程不擅自改用未验证数据路线，停下来报告机主。")
        return 1

    tmp = dst.with_suffix(".part")
    tmp.write_bytes(data)

    # ---- 结构自检（不过就不转正）----
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        print(f"[FAIL] 响应不是合法 XML：{exc}（可能被错误页替换）")
        return 1
    if root.tag != "osm":
        print(f"[FAIL] 根元素 <{root.tag}> != <osm>")
        return 1
    nodes = root.findall("node")
    ways = root.findall("way")
    nids = {n.get("id") for n in nodes}
    dangling = 0
    cls: dict[str, int] = {}
    named = 0
    for w in ways:
        has_name = False
        for t in w.findall("tag"):
            k, v = t.get("k"), t.get("v")
            if k == "highway":
                cls[v] = cls.get(v, 0) + 1
            elif k == "name":
                has_name = True
        if has_name:
            named += 1
        for nd in w.findall("nd"):
            if nd.get("ref") not in nids:
                dangling += 1
    lat = [float(n.get("lat")) for n in nodes]
    lon = [float(n.get("lon")) for n in nodes]
    top = dict(sorted(cls.items(), key=lambda kv: -kv[1]))
    meta = root.find("meta")
    print(f"node {len(nodes):,}  way {len(ways):,}  悬空节点引用 {dangling}")
    print(f"节点范围 lat {min(lat):.5f}..{max(lat):.5f}  lon {min(lon):.5f}..{max(lon):.5f}")
    print(f"highway 分布 {top}")
    print(f"带 name 的 way {named:,}（28 抬桥按名称报告结构，依赖 name 标签在裁剪时保留）")
    if meta is not None:
        print(f"osm_base 数据时点 {meta.get('osm_base')}")

    ok = True
    if dangling:
        print("[FAIL] 递归不完整（有 way 引用了不在响应里的节点）")
        ok = False
    if not ({"trunk", "primary", "secondary"} & set(cls)):
        print("[FAIL] 干线类缺失，取数范围/口径可疑")
        ok = False
    if not ways:
        print("[FAIL] 0 条 way")
        ok = False
    if not ok:
        return 1
    tmp.replace(dst)
    print(f"[save] {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
