# -*- coding: utf-8 -*-
"""14 在 CS2 可玩区网格上建水体掩膜，并与 DEM 的"水面压平"尖峰交叉验证。

来源：旧工作区 scripts/05_build_mask.py（已验证），配置驱动改造。
网格口径（官方 Wiki + 社区一致）：
  可玩区边长 = playable.px × playable.m_per_px（哈尔滨 = 4096 × 3.5 = 14336 m）
  投影 EPSG 取自 config（哈尔滨 = UTM 52N）

产出：files.mask_npz（水体掩膜 + 重投影后的 DEM 高程），供 16 出图用。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform as warp_transform
from rasterio.features import rasterize

from cs2map import config
from cs2map import geo


def main() -> int:
    mp = config.load()
    sea = mp.sea_level
    if sea is None:
        print("[STOP] config sea_level_m 未定。14 的掩膜交叉验证与后续 16/18/24 都依赖它。\n"
              "       正确顺序：先跑 11_estimate_sea_level（多瓦片会自动合并）估水位，"
              "人工核对 products/sea_level_estimate.json 后把值填进 config sea_level_m，再跑本脚本。")
        return 1
    center_lon, center_lat = mp.center
    px, mpp = mp.playable
    play_m = mp.playable_m
    epsg = mp.epsg
    water_classes = set(mp.water["classes"])

    # ---- 目标网格 ----
    cx, cy = warp_transform("EPSG:4326", epsg, [center_lon], [center_lat])
    cx, cy = float(cx[0]), float(cy[0])
    transform = from_origin(cx - play_m / 2, cy + play_m / 2, mpp, mpp)
    print(f"目标网格: {px}x{px} @ {mpp} m/px = {play_m:.0f} m")
    print(f"  中心 WGS84 ({center_lon}, {center_lat}) -> {epsg} ({cx:.1f}, {cy:.1f})")
    print(f"  范围 UTM: x {cx-play_m/2:.0f}..{cx+play_m/2:.0f}  y {cy-play_m/2:.0f}..{cy+play_m/2:.0f}")

    # ---- DEM 重采样到目标网格（现状限制：只读第一块 tile）----
    tiles = mp.raw["dem"]["tiles"]
    if len(tiles) > 1:
        print(f"[note] config dem.tiles 有 {len(tiles)} 块，14 只读第一块（{tiles[0]}）。"
              f"哈尔滨属 playable 恰好整块覆盖的合法情形；可玩区本身跨瓦片的图见下方覆盖度检查。")
    dem_p = config.ROOT / "maps" / mp.map_id / "data" / "dem" / f"Copernicus_DSM_COG_10_{tiles[0]}_DEM.tif"
    if not dem_p.exists():
        print(f"[STOP] DEM 不存在: {dem_p}（先跑 10_fetch_dem）")
        return 1
    with rasterio.open(dem_p) as src:
        dem = np.full((px, px), np.nan, dtype="float64")
        reproject(
            source=rasterio.band(src, 1), destination=dem,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=transform, dst_crs=epsg,
            resampling=Resampling.cubic, dst_nodata=np.nan,
        )
    dem = np.where(dem < -100, np.nan, dem)
    valid = np.isfinite(dem)
    coverage = valid.sum() / (px * px)
    print(f"\nDEM 重采样后: 有效 {valid.sum()} / {px*px} px（覆盖 {coverage*100:.2f}%）, "
          f"elev {np.nanmin(dem):.1f}..{np.nanmax(dem):.1f} m, mean {np.nanmean(dem):.1f}")
    if coverage < 0.995:
        print(f"[STOP] DEM 对可玩区网格的覆盖只有 {coverage*100:.2f}%（<99.5%）。"
              f"14 现状只读 dem.tiles[0] = {tiles[0]}；可玩区跨 DEM 瓦片的图会被静默截半，故停止。\n"
              f"       处置：把多块 Copernicus COG 合并成一景 GeoTIFF（命名 "
              f"Copernicus_DSM_COG_10_<新tile名>_DEM.tif），config dem.tiles 改为单元素指向合并文件；"
              f"或等 14 扩展为多瓦片合并（11/18 已是多瓦片合并，可对照）。")
        return 1
    if coverage < 0.9999:
        print(f"[warn] DEM 覆盖 {coverage*100:.3f}% < 100%，确认边缘缺数据是否可接受。")

    # ---- 水体掩膜 ----
    import json
    gj = json.loads(mp.path("water_geojson").read_text(encoding="utf-8"))
    geoms, used = [], {}
    for f in gj["features"]:
        fc = f["properties"]["fclass"]
        if fc not in water_classes:
            continue
        used[fc] = used.get(fc, 0) + 1
        geoms.append(geo.reproject_geom(f["geometry"], epsg, step_m=40.0))
    print(f"\n参与栅格化的水体面 {len(geoms)} 个，按 fclass: {used}")
    mask = rasterize(
        [(g, 1) for g in geoms], out_shape=(px, px), transform=transform,
        fill=0, all_touched=True, dtype="uint8",
    ).astype(bool)
    mask &= valid
    print(f"水体掩膜: {mask.sum()} px = {mask.sum()*mpp*mpp/1e6:.2f} km2 "
          f"（占可玩区 {mask.mean()*100:.2f}%）")

    # ---- 与 DEM 水面压平尖峰交叉验证 ----
    vals = dem[mask]
    print(f"\n=== 掩膜内高程 ===\n  min={vals.min():.2f} max={vals.max():.2f} "
          f"mean={vals.mean():.2f} median={np.median(vals):.2f} std={vals.std():.2f}")
    flat = np.abs(dem - sea) <= 0.3
    print(f"\n=== 与 DEM {sea}m 尖峰的一致性 ===")
    print(f"  DEM 尖峰像素 {flat.sum()} px；OSM 掩膜像素 {mask.sum()} px")
    inter = (flat & mask).sum()
    print(f"  交集 {inter} px")
    print(f"  尖峰中被 OSM 覆盖: {inter/max(flat.sum(),1)*100:.1f}%")
    print(f"  掩膜中属于尖峰:   {inter/max(mask.sum(),1)*100:.1f}%")
    print(f"  掩膜内高程中位数与 {sea}m 之差: {np.median(vals)-sea:+.2f} m")

    out = mp.path("mask_npz")
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, dem=dem.astype("float32"), mask=mask,
                        transform=np.array(transform).reshape(3, 3))
    print(f"\n[save] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
