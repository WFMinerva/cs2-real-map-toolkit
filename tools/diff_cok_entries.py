# -*- coding: utf-8 -*-
"""diff_cok_entries.py —— 对比两个 .cok 地图存档的内部 zip 条目（只读）。

用途：验证某轮改动到底写进了存档的哪一块（例：资源图导入 → 哪个条目涨了多少字节）。
用法：python tools/diff_cok_entries.py <旧.cok> <新.cok>
"""
import sys
import zipfile
from pathlib import Path


def entries(p):
    with zipfile.ZipFile(p) as z:
        return {i.filename: (i.file_size, i.compress_size) for i in z.infolist()}


def main():
    old, new = Path(sys.argv[1]), Path(sys.argv[2])
    a, b = entries(old), entries(new)
    print(f"{old.name} 条目 {len(a)} ／ {new.name} 条目 {len(b)}")
    print(f"{'条目':<42}{'旧 raw':>12}{'新 raw':>12}{'Δ raw':>12}{'Δ zip':>10}")
    changed = 0
    for k in sorted(set(a) | set(b)):
        x = a.get(k, (0, 0))
        y = b.get(k, (0, 0))
        if x != y:
            changed += 1
            print(f"{k:<42}{x[0]:>12,}{y[0]:>12,}{y[0]-x[0]:>+12,}{y[1]-x[1]:>+10,}")
    if not changed:
        print("（所有条目逐字节大小相同）")
    print(f"→ 变化条目 {changed} 个；总 raw Δ {sum(v[0] for v in b.values()) - sum(v[0] for v in a.values()):+,} B")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
