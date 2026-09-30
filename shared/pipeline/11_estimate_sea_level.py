# -*- coding: utf-8 -*-
"""11 水面高程估计（DEM 尖峰法）—— 得到【DEM 数据内】的水面高程估计，不是当地水位实测。

新脚本（依兰首次使用；哈尔滨当时是临时分析，口径一致）。
原理：Copernicus GLO-30 是 DSM，水面在数据制作时被压平成同一高程；
把 DEM 重采样到可玩区网格后做直方图，最高的"平值尖峰"即水面候选。

输出：候选值 + ±0.3 m 占比 + 前 5 直方峰（stdout），并存 products/sea_level_estimate.json。
不自动写 config——人工核对后填 sea_level_m，再跑 14 做掩膜交叉验证。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform as warp_transform

from cs2map import config


def main() -> int:
    mp = config.load()
    if mp.sea_level is not None:
        print(f"[note] config 已有 sea_level_m={mp.sea_level}；本脚本只做估计，不覆盖。")

    center_lon, center_lat = mp.center
    px, mpp = mp.playable
    play_m = mp.playable_m
    epsg = mp.epsg

    cx, cy = warp_transform("EPSG:4326", epsg, [center_lon], [center_lat])
    cx, cy = float(cx[0]), float(cy[0])
    transform = from_origin(cx - play_m / 2, cy + play_m / 2, mpp, mpp)

    dem = np.full((px, px), np.nan)
    dem_dir = config.ROOT / "maps" / mp.map_id / "data" / "dem"
    for tile in mp.raw["dem"]["tiles"]:
        p = dem_dir / f"Copernicus_DSM_COG_10_{tile}_DEM.tif"
        part = np.full((px, px), np.nan)
        with rasterio.open(p) as src:
            reproject(source=rasterio.band(src, 1), destination=part,
                      src_transform=src.transform, src_crs=src.crs,
                      dst_transform=transform, dst_crs=epsg,
                      resampling=Resampling.cubic, dst_nodata=np.nan)
        part = np.where(part < -100, np.nan, part)
        fill = np.isfinite(part) & ~np.isfinite(dem)
        dem[fill] = part[fill]
        print(f"  {tile}: 贡献 {int(fill.sum())} px")
    vals = dem[np.isfinite(dem)]
    print(f"可玩区 DEM: {vals.min():.1f}..{vals.max():.1f} m, mean {vals.mean():.1f}, "
          f"p1 {np.percentile(vals,1):.1f}, p99 {np.percentile(vals,99):.1f}")

    lo = np.percentile(vals, 1)
    hi = np.percentile(vals, 99)
    bins = np.arange(math_floor(lo), math_floor(hi) + 1, 0.1)
    hist, edges = np.histogram(vals, bins=bins)
    order = np.argsort(hist)[::-1][:5]
    print("\n直方前 5 峰（0.1 m bin）:")
    for i in order:
        print(f"  {edges[i]:7.1f}..{edges[i+1]:7.1f} m : {hist[i]:>8} px "
              f"({hist[i]/vals.size*100:.2f}%)")

    i0 = int(order[0])
    peak_lo, peak_hi = edges[i0], edges[i0 + 1]
    cand = round(float((peak_lo + peak_hi) / 2), 2)
    near03 = int((np.abs(vals - cand) <= 0.3).sum())
    near01 = int((np.abs(vals - cand) <= 0.1).sum())
    print(f"\n候选水面高程 ≈ {cand} m")
    print(f"  ±0.1 m 占比 {near01/vals.size*100:.2f}%   ±0.3 m 占比 {near03/vals.size*100:.2f}%")

    # ---- 与 OSM 水面多边形交叉验证：真水面必然压在 OSM 水面上 ----
    wj = mp.path("water_geojson")
    check = None
    if wj.exists():
        import json as _json
        from rasterio.features import rasterize
        from cs2map import geo as _geo
        gj = _json.loads(wj.read_text(encoding="utf-8"))
        classes = set(mp.water["classes"])
        geoms = [_geo.reproject_geom(f["geometry"], epsg, step_m=40.0)
                 for f in gj["features"] if f["properties"]["fclass"] in classes]
        wmask = rasterize([(g, 1) for g in geoms], out_shape=(px, px), transform=transform,
                          fill=0, all_touched=True, dtype="uint8").astype(bool)
        print(f"\n与 OSM 水面交叉验证（OSM 水面 {wmask.sum()} px）：")
        cands = sorted({round(float(edges[int(i)] + 0.05), 2) for i in order})
        check = []
        for c in cands:
            on_flat = wmask & (np.abs(dem - c) <= 0.3)
            cover = on_flat.sum() / max(wmask.sum(), 1)
            check.append({"cand_m": c, "water_px_on_flat": int(on_flat.sum()),
                          "water_cover_frac": round(float(cover), 4)})
            print(f"  候选 {c:6.2f} m : OSM 水面落在其 ±0.3 m 内的占比 {cover*100:5.1f}%"
                  f"  ({int(on_flat.sum())} px)")
        best = max(check, key=lambda x: x["water_cover_frac"])
        print(f"  → 与 OSM 水面最吻合的候选：{best['cand_m']} m")
    else:
        print("\n[skip] 无 water_in_box.geojson（先跑 12），跳过 OSM 交叉验证")

    print("（参照：哈尔滨 113.5 m 时 ±0.3 m 占 8.4%；确认后人工填 config sea_level_m。）")

    out = mp.path("build_meta").parent / "sea_level_estimate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "candidate_m": cand,
        "frac_within_01": near01 / vals.size,
        "frac_within_03": near03 / vals.size,
        "osm_check": check,
        "dem_min": float(vals.min()), "dem_max": float(vals.max()),
        "top_bins": [[round(float(edges[int(i)]), 1), int(hist[int(i)])] for i in order],
        "note": "DEM 数据内水面高程估计（尖峰法）+ OSM 水面交叉验证，非当地水位实测；确认后人工填 config sea_level_m",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[save] {out}")
    return 0


def math_floor(v: float) -> float:
    import math
    return math.floor(v)


if __name__ == "__main__":
    sys.exit(main())
