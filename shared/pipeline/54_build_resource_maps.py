# -*- coding: utf-8 -*-
"""54 生成官方「资源分布图」256²（配置驱动，移植自旧区 54_build_asset_pack.py）。

官方口径（Paradox 认证 v1.6.0f1，Map Creation: Resources）：
  - 每种资源**单独导入**，用 Resource Inspector 里对应的 Import 按钮，一次一种
  - 灰度图，**只用红通道**：黑=无、白=最大；游戏内部就是 256×256 网格
  - 能导入的只有四种：肥沃 / 矿石 / 石油 / 地下水
  - **森林不能用图导入**（由图上种的树生成）；鱼由水体自动生成

数据来源：Geofabrik 黑龙江 OSM（ODbL）。
  肥沃 ← landuse：farmland/farm/farmyard/orchard/vineyard/allotments/meadow/grassland/…
         （水体处清零；建成区按建筑密度降到 25%）
  地下水 ← 水体 + 1.5 km 高斯衰减（**代理估算，非水文实测**）
  矿石 ← landuse=quarry 计数：框内 0 处 = 全黑（哈尔滨口径：留空即正确）；有则 255 代理（候选）
  石油 ← 全黑（OSM 土地层无油田语义；黑龙江油田在大庆，两图框外）
图层来源：water/waterways 用 config region_shp；landuse/buildings 从 config ref_layers_zip
（旧工作区只读档案缓存的全省 zip）按框提取，不改旧区。
"""
import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import shapefile
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config

RES = 256
MID = 2048          # 先画大图再降采样抗锯齿
BLUR_PX = 214       # 1.5 km 在 2048 网格上 = 2048/14336*1500

FERTILE = {
    "farmland": 255, "farm": 255, "farmyard": 200, "orchard": 220, "vineyard": 210,
    "plant_nursery": 200, "allotments": 150, "meadow": 170, "grassland": 130,
    "grass": 130, "village_green": 90, "recreation_ground": 70, "park": 60,
    "scrub": 80, "forest": 40, "wood": 40, "forestry": 40, "wetland": 30, "heath": 60,
}
FOREST_GUIDE = {"forest": 255, "wood": 255, "forestry": 230, "scrub": 120, "meadow": 60,
                "grassland": 50, "park": 70, "nature_reserve": 150, "heath": 80}
WATER_F = {"water", "reservoir", "riverbank", "wetland", "wetland_marsh",
           "wetland_swamp", "basin"}
WWAY_W = {"river": 255, "canal": 200, "stream": 140, "drain": 120}
ORE_F = {"quarry"}


class Layer:
    """一层矢量：[(fclass, [parts of (col,row) on MID grid]) ]"""

    def __init__(self, name):
        self.name = name
        self.items = []


def read_reader(shp_dir, zip_path, base):
    if shp_dir and (shp_dir / f"{base}.shp").exists():
        return shapefile.Reader(str(shp_dir / f"{base}.shp"))
    if zip_path:
        z = zipfile.ZipFile(zip_path)
        return shapefile.Reader(
            shp=io.BytesIO(z.read(f"{base}.shp")),
            shx=io.BytesIO(z.read(f"{base}.shx")),
            dbf=io.BytesIO(z.read(f"{base}.dbf")))
    return None


def extract(rd, lonlat_to_px, bbox_pad, want_f=None):
    """bbox 预筛 + 像素化。返回 [(f, parts)]；want_f 非空时只留这些 fclass。"""
    out = []
    if rd is None:
        return out
    fi = [f[0].lower() for f in rd.fields[1:]]
    ic = fi.index("fclass") if "fclass" in fi else 0
    n_bad = 0
    for i in range(len(rd)):
        try:
            sh = rd.shape(i)
            rec = rd.record(i)
        except shapefile.ShapefileException:
            n_bad += 1          # Geofabrik 层里的空几何记录，跳过
            continue
        bb = sh.bbox            # minx,miny,maxx,maxy (lon,lat)
        if not bbox_pad(bb):
            continue
        f = str(rec[ic]).lower()
        if want_f is not None and f not in want_f:
            continue
        pts = [lonlat_to_px(x, y) for x, y in sh.points]
        parts = []
        cuts = list(sh.parts) + [len(pts)]
        for a, b in zip(cuts, cuts[1:]):
            parts.append(pts[a:b])
        out.append((f, parts))
    if n_bad:
        print(f"    {rd.basePath if hasattr(rd, 'basePath') else ''} 跳过空几何 {n_bad} 条")
    return out


def draw_polys(items, keep):
    im = Image.new("L", (MID, MID), 0)
    d = ImageDraw.Draw(im)
    n = 0
    for f, parts in items:
        v = keep.get(f)
        if v is None:
            continue
        for pts in parts:
            if len(pts) < 3:
                continue
            d.polygon(pts, fill=int(v))
            n += 1
    return im, n


def draw_lines(items, keep, width_mul=1.0):
    im = Image.new("L", (MID, MID), 0)
    d = ImageDraw.Draw(im)
    n = 0
    for f, parts in items:
        v = keep.get(f)
        if v is None:
            continue
        for pts in parts:
            if len(pts) < 2:
                continue
            d.line(pts, fill=int(v), width=max(2, int(v / 255 * 14 * (MID / 4096) * width_mul)),
                   joint="curve")
            n += 1
    return im, n


def down(im):
    return im.resize((RES, RES), Image.LANCZOS)


def main() -> int:
    mp = config.load()
    b = mp.raw["bounds"]
    L, R, B, T = b["left"], b["right"], b["bottom"], b["top"]
    pad = 0.02

    def in_box(bb):
        return not (bb[2] < L - pad or bb[0] > R + pad or bb[3] < B - pad or bb[1] > T + pad)

    def to_px(x, y):
        return ((x - L) / (R - L) * MID, (1 - (y - B) / (T - B)) * MID)

    shp_dir = Path(mp.raw.get("region_shp") or "")
    zip_path = mp.raw.get("ref_layers_zip")
    if zip_path and not Path(zip_path).exists():
        print(f"!! ref_layers_zip 不存在：{zip_path}")
        return 1

    water = extract(read_reader(shp_dir, zip_path, "gis_osm_water_a_free_1"), to_px, in_box)
    wways = extract(read_reader(shp_dir, zip_path, "gis_osm_waterways_free_1"), to_px, in_box)
    land = extract(read_reader(shp_dir, zip_path, "gis_osm_landuse_a_free_1"), to_px, in_box)
    blds = extract(read_reader(shp_dir, zip_path, "gis_osm_buildings_a_free_1"), to_px, in_box)
    print(f"框内图层条数：water={len(water)} waterways={len(wways)} "
          f"landuse={len(land)} buildings={len(blds)}")

    out_dir = mp.path("resource_maps_game")
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []

    # ---------- 1 肥沃 ----------
    im, n = draw_polys(land, FERTILE)
    f = down(im)
    wm, _ = draw_polys(water, {k: 255 for k in WATER_F})
    wmw = down(wm).point(lambda v: 255 if v > 128 else 0)
    a = np.array(f).astype(np.int16)
    a[np.array(wmw) > 0] = 0
    bl, nb = draw_polys(blds, {"building": 255})
    bls = np.array(bl.resize((RES, RES), Image.BOX))
    a[bls > 60] = (a[bls > 60] * 0.25).astype(np.int16)
    a = np.clip(a, 0, 255).astype(np.uint8)
    p = out_dir / "2_fertile_肥沃.png"
    Image.fromarray(a, "L").save(p)
    made.append(("肥沃 Fertile", p, f"画入 {n} 多边形；非零 {np.count_nonzero(a)/a.size:.1%} 均值 {a.mean():.0f}"))
    print(f"  [save] {p.name}  画入 {n}  非零 {np.count_nonzero(a)/a.size:.1%}  均值 {a.mean():.0f}  最大 {a.max()}")
    fert_im = a

    # ---------- 2 地下水（代理估算） ----------
    wmask, _ = draw_polys(water, {k: 255 for k in WATER_F})
    wmask2, nw = draw_lines(wways, WWAY_W, 0.6)
    m = Image.fromarray(np.maximum(np.array(wmask), np.array(wmask2)), "L")
    g = np.array(m.filter(ImageFilter.GaussianBlur(BLUR_PX)).resize((RES, RES), Image.BOX)).astype("float64")
    if g.max() > 0:
        g = g / g.max()
    gw = np.clip(g * 235 + 20, 0, 255).astype(np.uint8)
    p = out_dir / "1_groundwater_地下水.png"
    Image.fromarray(gw, "L").save(p)
    made.append(("地下水 GroundWater（代理）", p, f"水线 {nw} 条；均值 {gw.mean():.0f} 最大 {gw.max()}"))
    print(f"  [save] {p.name}  水线 {nw}  均值 {gw.mean():.0f}  最大 {gw.max()}")
    gw_im = gw

    # ---------- 3/4 矿石 / 石油 ----------
    quarry = [(f, parts) for f, parts in land if f in ORE_F]
    if quarry:
        oim, no = draw_polys(quarry, {k: 255 for k in ORE_F})
        ore = np.array(down(oim))
        ore_note = f"框内 quarry {no} 处 → 255 代理（**候选口径，非矿产实测**）"
    else:
        ore = np.zeros((RES, RES), np.uint8)
        ore_note = "框内 0 处 quarry → 全黑（哈尔滨口径：留空即正确）"
    p = out_dir / "3_ore_矿石.png"
    Image.fromarray(ore, "L").save(p)
    made.append(("矿石 Ore", p, ore_note))
    print(f"  [save] {p.name}  {ore_note}  非零 {np.count_nonzero(ore)/ore.size:.2%}")
    oil = np.zeros((RES, RES), np.uint8)
    p = out_dir / "4_oil_石油.png"
    Image.fromarray(oil, "L").save(p)
    made.append(("石油 Oil", p, "全黑（OSM 土地层无油田语义；大庆油田在两图框外）"))
    print(f"  [save] {p.name}  全黑")

    # ---------- 5 森林指引（不能导入） ----------
    fim, nf = draw_polys(land, FOREST_GUIDE)
    forest = np.array(down(fim))
    p = out_dir / "9_forest_指引_不能导入.png"
    Image.fromarray(forest, "L").save(p)
    made.append(("森林指引（种树用）", p, f"画入 {nf} 多边形；森林由树生成，**不能导入**"))
    print(f"  [save] {p.name}  画入 {nf}  非零 {np.count_nonzero(forest)/forest.size:.1%}")

    # ---------- 预览 2×2 ----------
    pv = Image.new("L", (RES * 2 + 12, RES * 2 + 12), 40)
    for i, arr in enumerate((gw_im, fert_im, ore, oil)):
        pv.paste(Image.fromarray(arr), ((i % 2) * (RES + 12), (i // 2) * (RES + 12)))
    pvp = mp.path("resource_maps_game").parent / "resource_maps_preview.png"
    pv.save(pvp)
    print(f"  [save] {pvp.name}（左上地下水/右上肥沃/左下矿石/右下石油）")

    # ---------- 说明 ----------
    name = mp.raw.get("display_name") or mp.raw.get("map_id")
    lines = [f"{name}地图 · 资源分布图", "=" * 24, "",
             "官方口径（Paradox 认证 v1.6.0f1，Map Creation: Resources）", "-" * 40,
             "· 每种资源**单独导入**：Resource Inspector 里对应的 Import 按钮，一次一种",
             "· 灰度图只用红通道：黑=无、白=最大；游戏内部 256×256 网格",
             "· 能导入的只有四种：肥沃/矿石/石油/地下水",
             "· **森林不能用图导入**（由图上种的树生成）；鱼由水体自动生成", "",
             "本目录的文件", "-" * 12]
    for label, p, note in made:
        lines.append(f"{p.name}    {label}：{note}")
    lines += ["", "三件必须知道的事", "-" * 12,
              "【1】矿石/石油全黑是**正确的**（框内无矿场油田语义），不是漏了东西。",
              "【2】地下水是**代理估算**（水体+1.5 km 衰减），不是水文实测含水层。",
              "【3】森林要手动种树才会有，照 9_forest 那张指引种。", "",
              "数据来源", "-" * 8,
              "Geofabrik 黑龙江 OSM（ODbL）：landuse/buildings/water/waterways。",
              "生成脚本：shared/pipeline/54_build_resource_maps.py（配置驱动）"]
    (out_dir / "说明.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"  [save] 说明.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
