# -*- coding: utf-8 -*-
"""24 算出河流在可玩区四条边上的进出口位置（供游戏内放 Border River 水源）。

来源：旧工作区 scripts/12_river_edges.py（已验证），配置驱动改造。
Border River 水源官方要求放在可玩区边缘；横穿整图的河流需要在上游入口与下游出口各放一个。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_transform

from cs2map import config
from cs2map import geo


def main() -> int:
    mp = config.load()
    px, mpp = mp.playable
    play_m = mp.playable_m
    epsg = mp.epsg
    center_lon, center_lat = mp.center
    # 判定水位 = 海平面 − riverbed_depth_below_sea_m（建模参数，无占位绝对值）
    sea = mp.sea_level
    depth_b = float(mp.water.get("riverbed_depth_below_sea_m", 2.0))
    below = sea - depth_b

    z = geo.load_height_m(mp.path("playable_png"), mp.height_scale)
    water = z < below  # 挖过的河床

    cx, cy = warp_transform("EPSG:4326", epsg, [center_lon], [center_lat])
    tr = from_origin(float(cx[0]) - play_m / 2, float(cy[0]) + play_m / 2, mpp, mpp)

    edges = {
        "西边 W (上游入口)": ("col", 0),
        "东边 E (下游出口)": ("col", px - 1),
        "北边 N": ("row", 0),
        "南边 S": ("row", px - 1),
    }
    result = {}
    for name, (kind, fixed) in edges.items():
        line = water[:, fixed] if kind == "col" else water[fixed, :]
        segs = geo.runs(line)
        print(f"\n=== {name} ===  水面段数 {len(segs)}")
        entries = []
        for a, b in segs:
            mid = (a + b) // 2
            if kind == "col":
                x, y = fixed + 0.5, mid + 0.5
            else:
                x, y = mid + 0.5, fixed + 0.5
            ux, uy = tr * (x, y)
            lon, lat = warp_transform(epsg, "EPSG:4326", [ux], [uy])
            width_m = (b - a + 1) * mpp
            entries.append({"idx_from": int(a), "idx_to": int(b), "width_m": round(width_m, 1),
                            "lon": round(float(lon[0]), 6), "lat": round(float(lat[0]), 6),
                            "elev_at_mid_m": round(float(z[mid if kind == 'col' else fixed,
                                                          fixed if kind == 'col' else mid]), 2)})
            print(f"  像素 {a}..{b}  宽 {width_m:8.1f} m  经度 {lon[0]:.6f} 纬度 {lat[0]:.6f}  "
                  f"高程 {entries[-1]['elev_at_mid_m']:.2f} m")
        result[name] = entries

    print("\n=== 可玩区四角与中心（WGS84） ===")
    for label, (x, y) in {
        "左上 NW": (0.5, 0.5), "右上 NE": (px - 0.5, 0.5),
        "左下 SW": (0.5, px - 0.5), "右下 SE": (px - 0.5, px - 0.5),
        "中心": (px / 2, px / 2),
    }.items():
        ux, uy = tr * (x, y)
        lon, lat = warp_transform(epsg, "EPSG:4326", [ux], [uy])
        print(f"  {label:8s} 经度 {lon[0]:.6f}  纬度 {lat[0]:.6f}")

    out = mp.path("river_edges")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[save] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
