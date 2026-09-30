# -*- coding: utf-8 -*-
"""probe_mapdata_names.py —— 诊断：把 .cok 地图文件的 MapData 逐 zstd 帧解压，
搜索给定字符串（UTF-8）出现在哪些帧。只读，不改任何文件。

用法： python tools/probe_mapdata_names.py <x.cok> [名字1 名字2 ...]
不带名字时用默认对照集（依兰/哈尔滨街名 + 系统类型名 + 花名对照）。

帧结构依据旧工作区已验证的 97_refscan.py：
  MapData = [u32 table_len][table][u32 raw][u32 comp][帧0]...
  之后每帧在 64 字节窗口内找 zstd 魔数，[raw,comp] 存于魔数前 8 字节。
"""
import struct
import sys
import zipfile

import zstandard as zstd

MAGIC = b"\x28\xb5\x2f\xfd"

DEFAULT_NAMES = [
    "五国城路", "中央大街", "通江路", "京抚线", "哈同高速", "依丹旅游公路",
    "上海街", "松北大道", "绥满高速", "松花江公路大桥",
    "Game.UI.NameSystem", "WaterSource", "风信子街", "白菊路",
]


def split_mapdata(md):
    table_len = struct.unpack_from("<I", md, 0)[0]
    off = 4 + table_len
    f0raw, f0comp = struct.unpack_from("<II", md, off)
    off += 8
    frames = [md[off:off + f0comp]]
    pos = off + f0comp
    while pos < len(md):
        m = md.find(MAGIC, pos, pos + 64)
        if m < 0:
            frames.append(md[pos:])
            break
        _raw, comp = struct.unpack_from("<II", md, m - 8)
        frames.append(md[m:m + comp])
        pos = m + comp
    return frames


def main():
    path = sys.argv[1]
    names = sys.argv[2:] or DEFAULT_NAMES
    with zipfile.ZipFile(path) as z:
        key = [n for n in z.namelist() if n.endswith(".MapData")][0]
        md = z.read(key)
    frames = split_mapdata(md)
    dec = []
    for i, b in enumerate(frames):
        try:
            dec.append(zstd.ZstdDecompressor().decompressobj().decompress(b))
        except Exception as ex:
            print(f"帧{i}: 解压失败 {ex}（按原文处理）")
            dec.append(b)
    print(f"{path}\n  MapData {len(md)} B → {len(dec)} 帧，解压后共 {sum(len(d) for d in dec)} B")
    pats = [(n, n.encode("utf-8")) for n in names]
    for n, p in pats:
        hits = []
        for i, d in enumerate(dec):
            c = d.count(p)
            if c:
                hits.append((i, c))
        print(f"  {n:<24} : " + (", ".join(f"帧{i}x{c}" for i, c in hits) if hits else "无"))
    # 顺带打印含 WaterSource 的具名帧原文（若存在）
    for i, d in enumerate(dec):
        if b"WaterSource" in d and len(d) < 4096:
            print(f"  帧{i} 原文(hex): {d.hex()}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
