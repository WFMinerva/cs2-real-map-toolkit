# -*- coding: utf-8 -*-
"""18 生成 CS2 世界高度图：让河流在 57 km 画外也真实存在。

来源：旧工作区 scripts/57_rebuild_world.py（已验证终版；09 是其被替代的旧版），配置驱动改造。
要点：
  - 世界图 4096×4096 @ world.m_per_px（哈尔滨 14 m/px = 57.344 km），中心 1024² = 可玩区
  - 水系直接从 Geofabrik 图层按整个世界框提取（water_a 面 + waterways 线按宽度加粗）
  - 画外水域按 world_depth_m 下挖（哈尔滨 2.5 m）；中心 1024² 用可玩高度图 4×4 块平均覆盖
  - 算河道穿越世界图边界的位置（放画外水源用）→ files.world_river_edges

产出：files.world_png、files.preview_world、files.world_river_edges
注意：本脚本不写 build_meta（与 57 行为一致；旧 out/build_meta.json 里的 world 块
是已被替代的 09 写的陈旧值，不代表当前世界图）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import rasterio
import shapefile
from PIL import Image, ImageDraw
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform as warp_transform

from cs2map import config
from cs2map import geo


def main() -> int:
    mp = config.load()
    center_lon, center_lat = mp.center
    px, res = mp.world
    world_m = mp.world_m
    epsg = mp.epsg
    height_scale = mp.height_scale
    sea = mp.sea_level
    depth = float(mp.water["world_depth_m"])
    tol = float(mp.water["world_tol_m"])
    water_classes = set(mp.water["world_classes"])
    wway_w = {k: float(v) for k, v in mp.water["world_way_width_m"].items()}
    shp_dir = mp.region_shp

    cx, cy = warp_transform("EPSG:4326", epsg, [center_lon], [center_lat])
    cx, cy = float(cx[0]), float(cy[0])
    x0, y1 = cx - world_m / 2, cy + world_m / 2
    tr = from_origin(x0, y1, res, res)
    print(f"世界框: {world_m/1000:.3f} km, {px}px @ {res} m/px")

    # ---- 1. DEM ----
    dem = np.full((px, px), np.nan)
    tiles = mp.raw["dem"]["tiles"]
    dem_dir = config.ROOT / "maps" / mp.map_id / "data" / "dem"
    for tile in tiles:
        p = dem_dir / f"Copernicus_DSM_COG_10_{tile}_DEM.tif"
        part = np.full((px, px), np.nan)
        with rasterio.open(p) as src:
            reproject(source=rasterio.band(src, 1), destination=part,
                      src_transform=src.transform, src_crs=src.crs,
                      dst_transform=tr, dst_crs=epsg,
                      resampling=Resampling.cubic, dst_nodata=np.nan)
        part = np.where(part < -100, np.nan, part)
        fill = np.isfinite(part) & ~np.isfinite(dem)
        dem[fill] = part[fill]
    bad = ~np.isfinite(dem)
    print(f"DEM: {np.nanmin(dem):.1f}..{np.nanmax(dem):.1f} m；无效 {bad.sum()} px")
    if bad.any():
        dem[bad] = np.nanmedian(dem)

    # ---- 2. 从 shp 按世界框提取水系 ----
    lon0, lat0 = warp_transform(epsg, "EPSG:4326", [x0], [y1 - world_m])
    lon1, lat1 = warp_transform(epsg, "EPSG:4326", [x0 + world_m], [y1])
    box = (float(lat0[0]), float(lon0[0]), float(lat1[0]), float(lon1[0]))  # S,W,N,E
    print(f"世界框经纬度: lat {box[0]:.4f}..{box[2]:.4f}, lon {box[1]:.4f}..{box[3]:.4f}")

    def in_box(bb):
        xmin, ymin, xmax, ymax = bb[0], bb[1], bb[2], bb[3]
        return not (ymax < box[0] or ymin > box[2] or xmax < box[1] or xmin > box[3])

    geoms = []
    r = shapefile.Reader(str(shp_dir / "gis_osm_water_a_free_1.shp"))
    fields = [f[0] for f in r.fields[1:]]
    for i in range(len(r)):
        try:
            sh, rec = r.shape(i), dict(zip(fields, r.record(i)))
        except Exception:  # noqa: BLE001
            continue
        if not sh or not getattr(sh, "bbox", None) or len(sh.bbox) < 4 or not in_box(sh.bbox):
            continue
        if str(rec.get("fclass", "")) not in water_classes:
            continue
        pts = list(sh.points or [])
        if len(pts) < 4:
            continue
        geoms.append({"type": "Polygon", "coordinates": [pts]})
    print(f"  水面多边形 {len(geoms)} 个（世界框内）")

    mask = rasterize([(geo.reproject_geom(g, epsg, step_m=70.0), 1) for g in geoms],
                     out_shape=(px, px), transform=tr, fill=0, all_touched=True,
                     dtype="uint8").astype(bool)
    print(f"  水面像素 {mask.sum()} = {mask.sum()*res*res/1e6:.2f} km2（画外）")

    # 线状水系：按宽度画粗线
    lines = []
    r2 = shapefile.Reader(str(shp_dir / "gis_osm_waterways_free_1.shp"))
    f2 = [f[0] for f in r2.fields[1:]]
    for i in range(len(r2)):
        try:
            sh, rec = r2.shape(i), dict(zip(f2, r2.record(i)))
        except Exception:  # noqa: BLE001
            continue
        if not sh or not getattr(sh, "bbox", None) or len(sh.bbox) < 4 or not in_box(sh.bbox):
            continue
        fc = str(rec.get("fclass", ""))
        if fc not in wway_w:
            continue
        pts = list(sh.points or [])
        if len(pts) < 2:
            continue
        lines.append((fc, pts))
    print(f"  线状水系 {len(lines)} 条")

    wmask = Image.fromarray((mask * 255).astype("uint8"), "L")
    wd = ImageDraw.Draw(wmask)
    for fc, pts in lines:
        nx, ny = warp_transform("EPSG:4326", epsg, [p[0] for p in pts], [p[1] for p in pts])
        cols = [(float(a) - x0) / res for a in nx]
        rows = [(y1 - float(b)) / res for b in ny]
        wpx = max(2, int(round(wway_w[fc] / res)))
        wd.line([(cols[i], rows[i]) for i in range(len(cols))], fill=255, width=wpx, joint="curve")
    water = np.array(wmask) > 128
    print(f"  合并线状后水域 {water.sum()} px = {water.sum()*res*res/1e6:.2f} km2")

    # ---- 3. 下挖 ----
    bed = dem.copy()
    dep = (dem <= sea + tol)
    carve = water & dep
    bed[carve] = dem[carve] - depth
    print(f"下挖 {carve.sum()} px（水深 {depth} m）；被高度过滤排除 {int((water & ~dep).sum())} px")

    # ---- 4. 中心用可玩高度图块平均覆盖（块数 = 可玩px / 世界中心px）----
    play_px = int(round(mp.playable_m / res))          # 哈尔滨 = 14336/14 = 1024
    ratio = mp.playable[0] // play_px                  # 哈尔滨 = 4（4×4 块平均）
    with Image.open(mp.path("playable_png")) as im:
        play = np.array(im, dtype=np.uint16).astype("float64") / 65535 * height_scale
    centre = play.reshape(play_px, ratio, play_px, ratio).mean(axis=(1, 3))
    off = (px - play_px) // 2
    before = bed[off:off + play_px, off:off + play_px].copy()
    bed[off:off + play_px, off:off + play_px] = centre
    print(f"中心对齐: {before.min():.1f}..{before.max():.1f} -> "
          f"{centre.min():.1f}..{centre.max():.1f} m，平均差 {np.abs(before - centre).mean():.2f} m")

    gray = np.clip(np.round(bed / height_scale * 65535.0), 0, 65535).astype("<u2")
    png = mp.path("world_png")
    png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray).save(png)
    print(f"[save] {png.name}  {png.stat().st_size/1e6:.2f} MB  gray {gray.min()}..{gray.max()}")

    # ---- 5. 算河道穿越世界图边界的位置（供放画外水源）----
    wl = bed < sea
    edges = {}
    for side, sl in (("西", wl[:, 0]), ("东", wl[:, -1]), ("北", wl[0, :]), ("南", wl[-1, :])):
        edges[side] = geo.runs(sl)
        print(f"  {side}边 河道段: {edges[side]}")

    def to_world(col, row):
        return (round((col / px - 0.5) * world_m, 2), round((0.5 - row / px) * world_m, 2))

    out_edges = {}
    for side, segs in edges.items():
        out_edges[side] = []
        for a, b in segs:
            mid = (a + b) // 2
            if side in ("西", "东"):
                c = 0 if side == "西" else px - 1
                x, z = to_world(c, mid)
            else:
                rr = 0 if side == "北" else px - 1
                x, z = to_world(mid, rr)
            out_edges[side].append({"idx_from": a, "idx_to": b,
                                    "width_m": round((b - a + 1) * res, 1),
                                    "world_x": x, "world_z": z})
    wep = mp.path("world_river_edges")
    wep.parent.mkdir(parents=True, exist_ok=True)
    wep.write_text(json.dumps(out_edges, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[save] {wep}")

    # ---- 6. 预览 ----
    lo, hi = np.percentile(bed, [0.5, 99.5])
    g = np.clip((bed - lo) / (hi - lo), 0, 1)
    gy, gx = np.gradient(bed, res)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy) * 3.0)
    aspect = np.arctan2(-gx, gy)
    az, alt = np.deg2rad(315.0), np.deg2rad(45.0)
    hs = np.clip(np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect), 0, 1)
    rgb = np.dstack([g * hs] * 3)
    rgb[wl] = (rgb[wl] * 0.25 + np.array([30, 110, 220]) / 255 * 0.75)
    side_out = 1300
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype("uint8"), "RGB").resize(
        (side_out, side_out), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    s = side_out * play_px / px
    o = (side_out - s) / 2
    d.rectangle([o, o, o + s, o + s], outline=(255, 0, 0), width=3)
    d.text((12, 10),
           f"{mp.display_name} 世界地形 {world_m/1000:.3f} km ｜ 红框 = 可玩区 {mp.playable_m/1000:.3f} km "
           f"｜ 蓝 = 按 OSM 下挖的河道",
           font=geo.cjk_font(26), fill=(255, 230, 80))
    pp = mp.path("preview_world")
    img.save(pp)
    print(f"[save] {pp.name}")

    # ---- 7. 部署到游戏 Heightmaps（config 可关；--no-deploy 可临时关）----
    if "--no-deploy" in sys.argv:
        print("[skip] --no-deploy：不部署到游戏目录")
    elif mp.raw.get("deploy", {}).get("auto_deploy_heightmaps", False):
        import shutil
        hd = Path(config.game()["heightmaps_dir"])
        if hd.exists():
            shutil.copy2(png, hd / png.name)
            print(f"已部署 {hd / png.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
