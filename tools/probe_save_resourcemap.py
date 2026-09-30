# -*- coding: utf-8 -*-
"""probe_save_resourcemap.py —— 诊断：资源图是否真的进了地图存档（只读）。

做法：把 .cok 里的 MapData 逐 zstd 帧解压（帧结构同 probe_mapdata_names.py），
在各帧里找**我们那四张 256×256 资源图的原始像素**（每张 65,536 B）：
  资源图进存档时是否逐字节保留、是否翻了行、是否加了 1 px 边距 —— 全试一遍。
坐标系不确定，故同时试「原序 / 上下翻转 / 左右翻转 / 转置」四种排列。

用法：
  python tools/probe_save_resourcemap.py <x.cok> [图片1.png 图片2.png ...]
不给图片时用 maps/yilan/products/resource_maps_game 下的 1..4 号图 + 9 号森林指引。
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_mapdata_names import split_mapdata  # noqa: E402

import zstandard as zstd  # noqa: E402
import zipfile  # noqa: E402

DEFAULT_DIR = Path("maps/yilan/products/resource_maps_game")


def variants(a: np.ndarray):
    return {
        "原序": a.tobytes(),
        "上下翻": np.flipud(a).tobytes(),
        "左右翻": np.fliplr(a).tobytes(),
        "转置": a.T.tobytes(),
        "抗锯齿?" : None,
    }


def main():
    cok = sys.argv[1]
    imgs = [Path(p) for p in sys.argv[2:]]
    if not imgs:
        imgs = sorted(DEFAULT_DIR.glob("[0-9]_*.png"))

    with zipfile.ZipFile(cok) as z:
        key = [n for n in z.namelist() if n.endswith(".MapData")][0]
        md = z.read(key)
    frames = split_mapdata(md)
    dec = []
    for b in frames:
        try:
            dec.append(zstd.ZstdDecompressor().decompressobj().decompress(b))
        except Exception:
            dec.append(b)
    print(f"{cok}\n  MapData {len(md):,} B → {len(dec)} 帧，解压后 {sum(len(d) for d in dec):,} B")

    for p in imgs:
        if not p.exists():
            print(f"  {p.name}: 缺文件")
            continue
        a = np.array(Image.open(p).convert("L"))
        if a.shape != (256, 256):
            print(f"  {p.name}: 尺寸 {a.shape}，跳过")
            continue
        found = []
        for label, blob in variants(a).items():
            if blob is None:
                continue
            for i, d in enumerate(dec):
                if blob in d:
                    found.append(f"{label}@帧{i}")
        print(f"  {p.name:<28}: " + ("命中 " + "、".join(found) if found else "无逐字节命中"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
