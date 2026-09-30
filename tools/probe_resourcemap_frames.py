# -*- coding: utf-8 -*-
"""probe_resourcemap_frames.py —— 帧级定位资源图在存档里的存储格式（只读）。

思路：
  1) 解压两个 .cok 的 MapData 帧（帧结构同 probe_mapdata_names.split_mapdata）；
  2) 找出解压后内容发生变化的帧（资源图导入应新增若干帧或改写某帧）；
  3) 在这些帧里找我们那张 256×256 图的像素，按**多种可能格式**试：
     uint8 / float32(0..1) / float32(0..255) / uint16，× 8 种旋转镜像。
用法：python tools/probe_resourcemap_frames.py <旧.cok> <新.cok> <图.png> [...]
"""
import sys
import zipfile
from pathlib import Path

import numpy as np
import zstandard as zstd
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_mapdata_names import split_mapdata  # noqa: E402


def frames_of(cok):
    with zipfile.ZipFile(cok) as z:
        key = [n for n in z.namelist() if n.endswith(".MapData")][0]
        md = z.read(key)
    out = []
    for b in split_mapdata(md):
        try:
            out.append(zstd.ZstdDecompressor().decompressobj().decompress(b))
        except Exception:
            out.append(b)
    return out


def symmetries(a):
    v = {}
    for k in range(4):
        r = np.rot90(a, k)
        v[f"rot{k*90}"] = r
        v[f"rot{k*90}+镜像"] = np.fliplr(r)
    return v


def encodings(a):
    f = a.astype(np.float32)
    return {
        "uint8": a.tobytes(),
        "f32_0to1": (f / 255.0).tobytes(),
        "f32_0to255": f.tobytes(),
        "u16": (a.astype(np.uint16)).tobytes(),
    }


def main():
    old, new = Path(sys.argv[1]), Path(sys.argv[2])
    imgs = [Path(p) for p in sys.argv[3:]]
    fo, fn = frames_of(old), frames_of(new)
    print(f"{old.name}: {len(fo)} 帧 / {sum(len(x) for x in fo):,} B")
    print(f"{new.name}: {len(fn)} 帧 / {sum(len(x) for x in fn):,} B")

    changed = []
    for i in range(max(len(fo), len(fn))):
        a = fo[i] if i < len(fo) else b""
        b = fn[i] if i < len(fn) else b""
        if a != b:
            changed.append(i)
            print(f"  帧{i} 变化：{len(a):,} → {len(b):,} B（Δ {len(b)-len(a):+,}）")
    print(f"→ 变化帧 {len(changed)} 个：{changed}")

    for p in imgs:
        a = np.array(Image.open(p).convert("L"))
        if a.shape != (256, 256):
            print(f"{p.name}: 尺寸 {a.shape}，跳过")
            continue
        hits = []
        for sym, arr in symmetries(a).items():
            for label, blob in encodings(np.ascontiguousarray(arr)).items():
                for i in changed:
                    if blob in fn[i]:
                        hits.append(f"{label}/{sym}@帧{i}")
        print(f"  {p.name:<28}: " + ("命中 " + "；".join(hits[:8]) if hits else "未命中（可能被归一化/存成纹理资源）"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
