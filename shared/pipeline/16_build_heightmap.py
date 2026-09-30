# -*- coding: utf-8 -*-
"""16 生成 CS2 可玩区高度图（社区标准岸线模型：Water Depth + Riparian sine 断面）。

来源：旧工作区 scripts/18_rebuild_community_water.py（已验证终版；当前游戏里部署的
哈尔滨高度图就是本脚本的产物）。历史替代关系（勿走回头路）：
  06 统一 8 m 下挖  →  17 真实水深 + 700 m 自创渐变  →  18 社区标准（现行）

社区标准口径（一手来源：Terraining Heightmap Generator 源码，原生支持 CS2）
  Water Depth   = config water.shore_model.water_depth_m（哈尔滨 2.5 m = 松花江城区段真实航道水深）
  Riparian Zone = riparian_m（社区默认 10 m，岸→底过渡带宽度）
  断面曲线      = sine（社区默认；岸边≈0 深度，过渡带内侧≈满深度）

产出：files.playable_png（交付物）、files.preview_playable、files.build_meta（真实参数留痕）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image, ImageDraw

from cs2map import config


def dist_to_edge(mask, cap):
    """迭代腐蚀求「到掩膜边缘的像素距离」，上限 cap。"""
    dist = np.zeros(mask.shape, dtype=np.int16)
    cur = mask.copy()
    d = 0
    while d < cap and cur.any():
        dist[cur] = d
        nxt = cur.copy()
        nxt[1:, :] &= cur[:-1, :]
        nxt[:-1, :] &= cur[1:, :]
        nxt[:, 1:] &= cur[:, :-1]
        nxt[:, :-1] &= cur[:, 1:]
        cur = nxt
        d += 1
    dist[cur] = d
    return dist


def main() -> int:
    mp = config.load()
    px, mpp = mp.playable
    play_m = mp.playable_m
    height_scale = mp.height_scale
    sea = mp.sea_level
    if sea is None:
        print("[STOP] config sea_level_m 未定（岸线模型与灰度编码都依赖它）。\n"
              "       正确顺序：11_estimate_sea_level 估水位 → 人工核对 → 填 sea_level_m → 14 → 本脚本。")
        return 1
    sm = mp.water["shore_model"]
    depth_max = float(sm["water_depth_m"])
    riparian_m = float(sm["riparian_m"])
    profile = np.array(sm["profile_sine"], dtype="float64")
    tol = float(sm["surface_tol_m"])

    z = np.load(mp.path("mask_npz"))
    dem = z["dem"].astype("float64")
    mask = z["mask"].astype(bool)
    water = mask & (dem <= sea + tol)
    print(f"水域（OSM 岸线 ∩ DEM 水面高度）: {water.sum()} px = {water.sum()*mpp*mpp/1e6:.2f} km2")

    zone_px = max(1, int(round(riparian_m / mpp)))
    print(f"Riparian Zone = {riparian_m:.0f} m = {zone_px} px；断面曲线 = sine（社区默认）")
    dist = dist_to_edge(water, zone_px)
    frac = np.interp(dist.astype("float64") / zone_px,
                     np.linspace(0, 1, len(profile)), profile)
    depth = depth_max * np.clip(frac, 0, 1)

    bed = dem.copy()
    bed[water] = dem[water] - depth[water]
    print(f"\nWater Depth = {depth_max} m")
    print(f"  河床: {bed[water].min():.2f} .. {bed[water].max():.2f} m"
          f"（主槽 {sea-depth_max:.1f} m，岸边≈江面）")
    print(f"  全图: {bed.min():.1f} .. {bed.max():.1f} m")

    gray = np.clip(np.round(bed / height_scale * 65535.0), 0, 65535).astype("<u2")
    png = mp.path("playable_png")
    png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray).save(png)
    print(f"[save] {png.name}  {png.stat().st_size/1e6:.2f} MB  gray {gray.min()}..{gray.max()}")

    # 不同水位下的水面范围（水面恒在江面高程）
    print("\n注：社区做法是「水域整片统一深度 + 岸边窄过渡」——水面范围恒等于真实江面范围。")
    for lv in [sea, sea - 0.1, sea - 0.2, sea - 0.5, sea - 1.5]:
        w = bed < lv
        print(f"  若水位设 {lv}: 水面 {w.sum()*mpp*mpp/1e6:6.2f} km2")

    # 预览
    side = 760
    rgb = np.zeros((px, px, 3), dtype="uint8")
    rgb[..., 0], rgb[..., 1], rgb[..., 2] = 70, 100, 60
    w = bed < sea
    rgb[w] = (40, 120, 220)
    edge = w & ~(np.roll(w, 1, 0) & np.roll(w, -1, 0) & np.roll(w, 1, 1) & np.roll(w, -1, 1))
    rgb[edge] = (180, 240, 255)
    im = Image.fromarray(rgb, "RGB").resize((side, side), Image.LANCZOS)
    c = Image.new("RGB", (side, side + 64), (18, 18, 18))
    c.paste(im, (0, 64))
    d = ImageDraw.Draw(c)
    d.text((12, 12), f"{mp.display_name} 社区标准: Water Depth {depth_max} m + "
                     f"Riparian {riparian_m:.0f} m (sine)", font=geo_font(), fill=(255, 255, 120))
    pp = mp.path("preview_playable")
    c.save(pp)
    print(f"[save] {pp.name}")

    meta = {
        "grid": {"epsg": int(mp.epsg.split(":")[1]), "px": px, "res_m": play_m / px, "extent_m": play_m},
        "center_wgs84": list(mp.center),
        "height_scale_m": height_scale,
        "water_level_m": sea,
        "shore_model": {"water_depth_m": depth_max, "riparian_m": riparian_m,
                        "profile": "sine", "surface_tol_m": tol},
        "elev_min": float(bed.min()), "elev_max": float(bed.max()),
        "gray_min": int(gray.min()), "gray_max": int(gray.max()),
        "water_px": int(water.sum()),
        "source_dem": mp.raw["dem"]["source"],
        "source_water": mp.raw["dem"]["source_water"],
        "lineage": "18_rebuild_community_water（现行）；06 统一8m 与 17 700m 渐变已废弃",
    }
    mp.path("build_meta").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[save] {mp.path('build_meta')}")
    return 0


def geo_font():
    from cs2map import geo
    return geo.cjk_font(28)


if __name__ == "__main__":
    sys.exit(main())
