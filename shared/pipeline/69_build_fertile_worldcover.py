# -*- coding: utf-8 -*-
"""69 肥沃图新源轮：ESA WorldCover 10 m → 官方 256²「肥沃」资源图（配置驱动）。

为什么换源（2026-09-29 实测）：54 的肥沃图取 OSM landuse，
**依兰框内 farmland = 0 面、landuse 共 31 面**（Geofabrik 全省 landuse 仅 5.6 万面，
覆盖缺口），近空白 → 与实际农业县不符。机主 2026-09-29 选「肥沃押后、另立新源轮」。

新源：ESA WorldCover 10 m 2021 v200（AWS open data，COG 直读，无需整幅下载）。
  唯一依据 = 像素级土地覆盖分类（CC BY 4.0）：
    10 树 / 20 灌木 / 30 草地 / 40 耕地 / 50 建成 / 60 裸地 / 70 冰雪 /
    80 水体 / 90 草本湿地 / 95 红树林 / 100 苔藓
  口径（权重与阈值全部来自 config，脚本内不写死）：
    肥沃值 = 每 256 格的**像素加权均值**（weight[class] 来自 config），
    建成区占比 > build_threshold 的格子 × build_damp（城区耕地不该按满值算），
    水体/裸地权重 0。产出灰度 PNG 与 54 同规格（L 模式、只用红通道、黑=无 白=最大）。

对照与审批：本脚本**只出候选图 + 对照图 + 说明**，不覆盖已部署的 54 版肥沃图；
机主批准口径后再由 69 的 --deploy 开关写入 resource_maps_game 并同步游戏 Overlays。

用法：
  python shared/pipeline/69_build_fertile_worldcover.py --map yilan            # 候选+对照
  python shared/pipeline/69_build_fertile_worldcover.py --map yilan --deploy   # 批准后落盘
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config

RES = 256  # 游戏内部资源图网格

# WorldCover 类别名（仅用于报告可读性）
CLASS_NAMES = {
    10: "树 cover", 20: "灌木", 30: "草地", 40: "耕地", 50: "建成",
    60: "裸地/稀疏", 70: "冰雪", 80: "水体", 90: "草本湿地", 95: "红树林", 100: "苔藓",
}


def tile_name(lon: float, lat: float) -> str:
    """WorldCover 瓦片 = 3°×3°，命名取 SW 角。"""
    lon0 = int(np.floor(lon / 3.0) * 3)
    lat0 = int(np.floor(lat / 3.0) * 3)
    return f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="harbin")
    ap.add_argument("--deploy", action="store_true", help="批准后：写入 resource_maps_game 并同步游戏 Overlays")
    args = ap.parse_args()

    mp = config.load(args.map)
    wc = mp.raw.get("fertile_worldcover") or {}
    weights = {int(k): float(v) for k, v in (wc.get("class_weights") or {}).items()}
    build_thr = float(wc.get("builtup_frac_threshold", 0.24))
    build_damp = float(wc.get("builtup_damp", 0.25))
    if not weights:
        print("!! config 缺 fertile_worldcover.class_weights")
        return 1

    b = mp.raw["bounds"]
    L, R, B, T = b["left"], b["right"], b["bottom"], b["top"]
    tile = wc.get("tile") or tile_name(L, B)
    url = (wc.get("url_template") or
           "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
           ).format(tile=tile)
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

    print(f"源：{url}")
    with rasterio.open("/vsicurl/" + url) as ds:
        win = from_bounds(L, B, R, T, ds.transform)
        arr = ds.read(1, window=win)
        tr = ds.window_transform(win)
    h, w = arr.shape
    mpp_x = abs(tr.a) * 111320 * np.cos(np.radians((B + T) / 2))
    mpp_y = abs(tr.e) * 110940
    print(f"窗口 {w}×{h} px（约 {mpp_x:.1f}×{mpp_y:.1f} m/px），类别 "
          f"{sorted(int(c) for c in np.unique(arr))}")

    # 像素 → 256 格索引（行 0 = 北）；仿射：x = c + a*col + b*row，y = f + d*col + e*row
    rows, cols = np.indices((h, w))
    xs = tr.c + tr.a * (cols + 0.5) + tr.b * (rows + 0.5)
    ys = tr.f + tr.d * (cols + 0.5) + tr.e * (rows + 0.5)
    ci = np.clip(((xs - L) / (R - L) * RES).astype(int), 0, RES - 1)
    ri = np.clip(((T - ys) / (T - B) * RES).astype(int), 0, RES - 1)
    flat = (ri * RES + ci).ravel()
    vals = arr.ravel()
    valid = vals > 0                      # 0 = nodata，且非任何类别

    total = np.bincount(flat[valid], minlength=RES * RES).astype(np.float64)
    acc = np.zeros(RES * RES, np.float64)
    for cls, wt in weights.items():
        if wt <= 0:
            continue
        m = valid & (vals == cls)
        if m.any():
            acc += np.bincount(flat[m], minlength=RES * RES) * wt

    with np.errstate(invalid="ignore", divide="ignore"):
        val = np.where(total > 0, acc / np.maximum(total, 1), 0.0)

    # 建成区压制
    bm = valid & (vals == 50)
    bfrac = np.where(total > 0, np.bincount(flat[bm], minlength=RES * RES) / np.maximum(total, 1), 0.0)
    damped = int(((bfrac > build_thr) & (val > 0)).sum())
    val = np.where(bfrac > build_thr, val * build_damp, val)
    a = np.clip(np.rint(val), 0, 255).astype(np.uint8).reshape(RES, RES)

    # 统计
    cls_frac = {}
    for cls in sorted(set(int(c) for c in np.unique(vals[valid]))):
        cls_frac[cls] = float((vals[valid] == cls).mean())
    print("框内土地覆盖占比：" + "  ".join(
        f"{CLASS_NAMES.get(c, c)}{cls_frac[c] * 100:.1f}%" for c in sorted(cls_frac, key=lambda k: -cls_frac[k])))
    print(f"肥沃图：非零 {np.count_nonzero(a) / a.size:.1%}  均值 {a.mean():.0f}  最大 {a.max()}  "
          f"建成压制 {damped} 格")

    out_dir = Path(wc.get("out_dir") or (mp.path("resource_maps_game").parent / "fertile_worldcover"))
    out_dir.mkdir(parents=True, exist_ok=True)
    cand = out_dir / "2_fertile_肥沃_worldcover候选.png"
    Image.fromarray(a, "L").save(cand)
    print(f"  [save] {cand}")

    # 与 54 的 OSM 版并排对照
    old_p = mp.path("resource_maps_game") / "2_fertile_肥沃.png"
    panels = []
    if old_p.exists():
        panels.append(("OSM landuse（54 现版）", np.array(Image.open(old_p).convert("L"))))
    panels.append(("WorldCover 2021（69 候选）", a))
    gap = 10
    pv = Image.new("L", (RES * len(panels) + gap * (len(panels) - 1), RES), 30)
    for i, (_, p) in enumerate(panels):
        pv.paste(Image.fromarray(p), (i * (RES + gap), 0))
    cmp_p = out_dir / "肥沃_对照_OSM现版_vs_WorldCover候选.png"
    pv.save(cmp_p)
    print(f"  [save] {cmp_p}（左=现版，右=候选）")

    # 说明 + 口径
    name = mp.raw.get("display_name") or mp.raw.get("map_id")
    lines = [f"{name} 肥沃图新源候选 · ESA WorldCover 10 m 2021 v200", "=" * 40, "",
             f"源文件：{url}", f"瓦片 {tile}；框内窗口 {w}×{h} px（约 {mpp_x:.1f} m/px）",
             f"网格：{RES}×{RES}，灰度 L（只用红通道，黑=无 白=最大），范围 = maps/{args.map} 的 bounds",
             "", "口径（全部来自 config.fertile_worldcover，未写死在脚本里）", "-" * 30]
    for cls in sorted(weights):
        lines.append(f"  类别 {cls:3d} {CLASS_NAMES.get(cls, '?'):8s} 权重 {weights[cls]:.0f}")
    lines += [f"  建成区占比 > {build_thr:.2f} 的格子 × {build_damp:.2f}", "",
              "框内土地覆盖占比", "-" * 16]
    for c in sorted(cls_frac, key=lambda k: -cls_frac[k]):
        lines.append(f"  {CLASS_NAMES.get(c, c):8s} {cls_frac[c] * 100:5.1f}%")
    lines += ["", f"非零格 {np.count_nonzero(a) / a.size:.1%}  均值 {a.mean():.0f}  最大 {a.max()}",
              "", "状态", "-" * 4,
              "**候选**：尚未覆盖已部署的 54 版肥沃图。机主批准口径后运行",
              f"  python shared/pipeline/69_build_fertile_worldcover.py --map {args.map} --deploy",
              "才会写入 resource_maps_game 并同步游戏 Overlays。" if not args.deploy else "（本次已 --deploy：已落盘）",
              "", "数据来源", "-" * 8,
              "ESA WorldCover 10 m 2021 v200（© ESA WorldCover project 2021, CC BY 4.0），AWS open data COG 直读。"]
    (out_dir / "肥沃_README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  [save] {out_dir / '肥沃_README.md'}")

    if args.deploy:
        tgt = mp.path("resource_maps_game") / "2_fertile_肥沃.png"
        Image.fromarray(a, "L").save(tgt)
        print(f"  [deploy] {tgt}")

        low = (Path(os.environ.get("LOCALAPPDATA", "")) / ".." / "LocalLow" / "Colossal Order" /
               "Cities Skylines II").resolve()
        ov = Path(mp.raw.get("overlays_dir")).expanduser() if mp.raw.get("overlays_dir") else low / "Overlays"
        ov.mkdir(parents=True, exist_ok=True)
        Image.fromarray(a, "L").save(ov / "2_fertile_肥沃.png")
        print(f"  [deploy] {ov / '2_fertile_肥沃.png'}")

        # Resource Inspector 用的目录：**加图名前缀**，不覆盖里面别的图的同名文件
        pref = wc.get("deploy_prefix") or f"{mp.raw.get('map_id', 'map')}_"
        rm = low / "ResourceMaps"
        rm.mkdir(parents=True, exist_ok=True)
        Image.fromarray(a, "L").save(rm / f"{pref}2_fertile_肥沃.png")
        print(f"  [deploy] {rm / (pref + '2_fertile_肥沃.png')}（供 Resource Inspector 导入）")

        # 更新 54 写的 说明.txt：追加换源说明（不重写旧内容）
        doc = mp.path("resource_maps_game") / "说明.txt"
        if doc.exists():
            txt = doc.read_text(encoding="utf-8")
            if "WorldCover" not in txt:
                txt += ("\n\n【4】肥沃图已换源（2026-09-29）：ESA WorldCover 10m 2021 v200，"
                        "口径见 maps/yilan/yilan.config.json 的 fertile_worldcover。\n"
                        "     原因：OSM landuse 在依兰框内 farmland=0（Geofabrik 覆盖缺口），54 版近空白。\n"
                        "     生成：shared/pipeline/69_build_fertile_worldcover.py；对照图见 products\\fertile_worldcover\\。\n")
                doc.write_text(txt, encoding="utf-8")
                print(f"  [deploy] 更新 {doc}（追加换源说明）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
