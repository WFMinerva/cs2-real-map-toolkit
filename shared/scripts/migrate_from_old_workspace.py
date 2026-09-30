# -*- coding: utf-8 -*-
"""migrate_from_old_workspace.py —— 从旧工作区复制到 cs2-map-workshop（只复制，不删旧）。

本脚本是迁移动作本身的存档：谁被复制到了哪里，都在下面两张表里。
旧工作区 <old-workspace> 保持只读、原样保留。
可重复执行（幂等）：目录 copytree(dirs_exist_ok=True)，文件 copy2 覆盖。
"""
import shutil
import sys
from pathlib import Path

OLD = Path(r"<old-workspace>")
NEW = Path(r"<repo>")

EXCLUDE_ALL = shutil.ignore_patterns("obj", "bin", "Library", "__pycache__", ".venv")

# (源目录, 目标目录, 排除)
COPY_TREE = [
    (OLD / "mod/HarbinBridgeLift",          NEW / "shared/mod/HarbinBridgeLift",       EXCLUDE_ALL),
    (OLD / "mod/HarbinRoadImport",          NEW / "archive/withdrawn/HarbinRoadImport_mod", EXCLUDE_ALL),
    (OLD / "tools/ParseTest",               NEW / "shared/tools/ParseTest",            EXCLUDE_ALL),
    (OLD / "tools/TypeDump",                NEW / "shared/tools/TypeDump",             EXCLUDE_ALL),
    (OLD / "decompiled",                    NEW / "archive/decompiled",                None),
    (OLD / "shots",                         NEW / "archive/reference_shots",           None),
    (OLD / "scripts",                       NEW / "archive/legacy_scripts",            None),
    (OLD / "out/调研-Earth2Cities",          NEW / "maps/harbin/data/roads",            None),
]

# (源文件, 目标文件)
COPY_FILES = [
    # 旧根目录文档分流
    (OLD / "cok格式-实测结论.md",               NEW / "archive/cok-逆向/docs/cok格式-实测结论.md"),
    (OLD / "帧级逆向-进展与结论.md",            NEW / "archive/cok-逆向/docs/帧级逆向-进展与结论.md"),
    (OLD / "CS1vsCS2-引擎差异与导路可行性.md",  NEW / "archive/research/CS1vsCS2-引擎差异与导路可行性.md"),
    (OLD / "真实路网-调研与资产包.md",          NEW / "archive/withdrawn/真实路网-调研与资产包.md"),
    (OLD / "下一步操作卡.md",                   NEW / "archive/withdrawn/下一步操作卡_已过时.md"),
    (OLD / "测试卡-接管验证.md",                NEW / "archive/withdrawn/测试卡-接管验证_已过时.md"),
    (OLD / "README.md",                         NEW / "archive/原工作区README快照.md"),
    # 工具链级文档 -> shared/docs
    (OLD / "Earth2Cities-操作卡.md",            NEW / "shared/docs/Earth2Cities-操作卡.md"),
    (OLD / "Earth2Cities-操作卡.pdf",           NEW / "shared/docs/Earth2Cities-操作卡.pdf"),
    (OLD / "道路导入-官方配方.md",              NEW / "shared/docs/道路导入-官方配方.md"),
    (OLD / "游戏内操作卡.md",                   NEW / "shared/docs/游戏内操作卡.md"),
    (OLD / "底板重建-操作卡.md",                NEW / "shared/docs/底板重建-操作卡.md"),
    # 哈尔滨地图专属文档
    (OLD / "桥隧问题-诊断与修复.md",            NEW / "maps/harbin/docs/桥隧问题-诊断与修复.md"),
    (OLD / "抬桥-操作卡.md",                    NEW / "maps/harbin/docs/抬桥-操作卡.md"),
    (OLD / "验证记录.md",                       NEW / "maps/harbin/docs/验证记录.md"),
    (OLD / "哈尔滨气候-填值表.md",              NEW / "maps/harbin/docs/哈尔滨气候-填值表.md"),
    (OLD / "哈尔滨气候-填值对照表.docx",        NEW / "maps/harbin/docs/哈尔滨气候-填值对照表.docx"),
    (OLD / "哈尔滨气候-填值对照表.pdf",         NEW / "maps/harbin/docs/哈尔滨气候-填值对照表.pdf"),
    # 哈尔滨管线输入数据
    (OLD / "data/Copernicus_DSM_COG_10_N45_00_E126_00_DEM.tif", NEW / "maps/harbin/data/dem/Copernicus_DSM_COG_10_N45_00_E126_00_DEM.tif"),
    (OLD / "data/Copernicus_DSM_COG_10_N46_00_E126_00_DEM.tif", NEW / "maps/harbin/data/dem/Copernicus_DSM_COG_10_N46_00_E126_00_DEM.tif"),
    (OLD / "data/heilongjiang-latest-free.shp.zip",             NEW / "data_common/heilongjiang-latest-free.shp.zip"),
    (OLD / "data/osm_meta.json",                                NEW / "data_common/osm_meta.json"),
]

# (源目录内 glob, 目标目录)  —— 水系两层 shapefile 全套 sidecar
GLOB_COPY = [
    (OLD / "data/osm_hlj", "gis_osm_water_a_free_1.*",    NEW / "maps/harbin/data/osm_hlj"),
    (OLD / "data/osm_hlj", "gis_osm_waterways_free_1.*",  NEW / "maps/harbin/data/osm_hlj"),
    # 哈尔滨当前有效产物
    (OLD / "out", "harbin_playable.png",        NEW / "maps/harbin/products"),
    (OLD / "out", "harbin_world.png",           NEW / "maps/harbin/products"),
    (OLD / "out", "build_meta.json",            NEW / "maps/harbin/products"),
    (OLD / "out", "preview_playable.png",       NEW / "maps/harbin/products"),
    (OLD / "out", "preview_world.png",          NEW / "maps/harbin/products"),
    (OLD / "out", "preview_sealevel_1135.png",  NEW / "maps/harbin/products"),
    (OLD / "out", "verify_hillshade.png",       NEW / "maps/harbin/products"),
    (OLD / "out", "verify_water_mask.png",      NEW / "maps/harbin/products"),
    (OLD / "out", "river_edges.json",           NEW / "maps/harbin/products"),
    (OLD / "out", "world_river_edges.json",     NEW / "maps/harbin/products"),
    (OLD / "out", "画外水源放置表.md",           NEW / "maps/harbin/products"),
    (OLD / "out", "res_*_256.png",              NEW / "maps/harbin/products"),
    (OLD / "out", "guide_forest_256.png",       NEW / "maps/harbin/products"),
    (OLD / "out", "resource_maps_preview.png",  NEW / "maps/harbin/products"),
    (OLD / "out", "nasa_daily_harbin.json",     NEW / "maps/harbin/products"),
    (OLD / "out", "哈尔滨气候参数.md",           NEW / "maps/harbin/products"),
    # 桥隧审计产物 + 抬桥 mod 输入
    (OLD / "out", "bridge_audit.json",          NEW / "maps/harbin/bridge"),
    (OLD / "out", "bridge_audit_preview.png",   NEW / "maps/harbin/bridge"),
    (OLD / "out", "bridge_issue_map.png",       NEW / "maps/harbin/bridge"),
    (OLD / "out", "underwater_audit.json",      NEW / "maps/harbin/bridge"),
    (OLD / "out", "underwater_spots_table.md",  NEW / "maps/harbin/bridge"),
    (OLD / "out/mod_input", "bridges_lift.json", NEW / "maps/harbin/bridge"),
]

# 游戏侧 ResourceMaps（只读复制一份入档）
GAME_RMAPS = Path.home() / "AppData/LocalLow/Colossal Order/Cities Skylines II/ResourceMaps"


def main() -> int:
    missing = []
    for src, dst, _ig in COPY_TREE:
        if not src.exists():
            missing.append(str(src))
    for src, dst in COPY_FILES:
        if not src.exists():
            missing.append(str(src))
    if missing:
        print("[MISS]")
        for m in missing:
            print("  " + m)
        return 1

    n_tree = n_file = 0
    for src, dst, ig in COPY_TREE:
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=ig if ig else None)
        n_tree += 1
        print(f"[tree] {src.name} -> {dst.relative_to(NEW)}")

    for src, dst in COPY_FILES:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n_file += 1
    print(f"[files] {n_file} 个已复制")

    for d, pat, dst in GLOB_COPY:
        hits = sorted(d.glob(pat))
        if not hits:
            print(f"[MISS-GLOB] {d}\\{pat}")
            return 1
        dst.mkdir(parents=True, exist_ok=True)
        for h in hits:
            shutil.copy2(h, dst / h.name)
        print(f"[glob] {pat} x{len(hits)} -> {dst.relative_to(NEW)}")

    if GAME_RMAPS.exists():
        dst = NEW / "maps/harbin/products/resource_maps_game"
        dst.mkdir(parents=True, exist_ok=True)
        k = 0
        for f in GAME_RMAPS.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
                k += 1
        print(f"[game] ResourceMaps x{k}")

    files = [p for p in NEW.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"TOTAL files={len(files)}  {total/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
