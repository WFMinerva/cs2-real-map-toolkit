# -*- coding: utf-8 -*-
"""92 从 Geofabrik 分省提取包（.zip）里抽出需要的 Shapefile 图层到 config.region_shp 目录。

新脚本（迁移时新增）：管线各脚本直接读解压后的图层文件（pyshp 不能直接读 zip）。
图层名不带 _free_1 后缀写，如 water_a / waterways / roads。

源包选择（2026-09-30 修正：不再默认黑龙江包、不再"随便取第一个"，防换图用错省的数据）：
  1) config `region_zip`（工作区相对路径）—— 多包共存时的唯一正确来源；
  2) 未设 region_zip 且 data_common 恰好只有一个 *.shp.zip → 用它并打印提醒；
  3) 未设且有多于一个包 → **停止**，列出候选要求显式配置。
输出目录 = config `region_shp`（读取方 12/18/54/90 都认这个目录）。
"""
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config

SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def pick_zip(mp) -> Path | None:
    """按上述规则选源包；不满足条件时打印停止原因并返回 None。"""
    cfg_zip = mp.raw.get("region_zip")
    if cfg_zip:
        p = config.ROOT / cfg_zip
        if not p.exists():
            print(f"[STOP] config region_zip 指向的包不存在: {p}")
            return None
        print(f"[zip ] config region_zip -> {p}")
        return p
    cands = sorted((config.ROOT / "data_common").glob("*.shp.zip"))
    if not cands:
        print("[STOP] data_common 下没有 *.shp.zip。请下载目标省的 Geofabrik 提取包放入 data_common，"
              "并在 config 设 region_zip（多包共存时必须显式指定）。")
        return None
    if len(cands) == 1:
        print(f"[zip ] 未设 region_zip，data_common 唯一候选 {cands[0].name}（建议在 config 显式设 region_zip）")
        return cands[0]
    names = ", ".join(c.name for c in cands)
    print(f"[STOP] data_common 有多个包且 config 未设 region_zip，为避免用错省的数据停止：{names}")
    return None


def main() -> int:
    mp = config.load()
    zip_p = pick_zip(mp)
    if zip_p is None:
        return 1
    argv = sys.argv[1:]
    layers = []
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--map":
            skip = True
            continue
        if a.startswith("--map="):
            continue
        layers.append(a)
    layers = layers or ["water_a", "waterways", "roads"]
    out_dir = mp.region_shp  # config region_shp：读取方（12/18/54/90）都用这个目录
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[out ] 图层目录 = config region_shp -> {out_dir}")

    with zipfile.ZipFile(zip_p) as z:
        names = z.namelist()
        for layer in layers:
            got = 0
            for ext in SIDECARS:
                target = f"gis_osm_{layer}_free_1{ext}"
                if target in names:
                    (out_dir / target).write_bytes(z.read(target))
                    got += 1
            print(f"[layer] {layer}: {got} 个 sidecar 文件 -> {out_dir}")
            if got == 0:
                avail = sorted({n.split("gis_osm_")[1].split("_free_1")[0]
                                for n in names if n.startswith("gis_osm_") and n.endswith(".shp")})
                print(f"        包内可用图层: {avail}")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
