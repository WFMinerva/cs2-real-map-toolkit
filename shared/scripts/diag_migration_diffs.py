# -*- coding: utf-8 -*-
"""diag_migration_diffs.py —— 解释迁移复跑中「字节不一致」项的真实差异。

1. playable/world 高度图：像素数组是否一致（字节不一致只可能是 PNG 编码器出处不同）
2. bridges_lift.json：除 generated_by 外还有哪里不同
3. build_meta.json：除 world 块外逐键对比
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
NEW = Path(r"<repo>\maps\harbin")
OLD = Path(r"<old-workspace>\out")


def pixel_cmp(label, new_p, old_p):
    a = np.array(Image.open(new_p), dtype=np.uint16)
    b = np.array(Image.open(old_p), dtype=np.uint16)
    same_shape = a.shape == b.shape
    diff = same_shape and (a != b).any()
    n = int((a != b).sum()) if same_shape else -1
    print(f"[{label}] shape {a.shape} vs {b.shape}  shape_same={same_shape}  "
          f"像素不同个数={n}")
    if same_shape and n:
        idx = np.argwhere(a != b)[:5]
        for r, c in idx:
            print(f"   ({r},{c}) new={a[r,c]} old={b[r,c]} "
                  f"(m new={a[r,c]/65535*4096:.3f} old={b[r,c]/65535*4096:.3f})")
    if not diff:
        print(f"[{label}] 像素完全一致 —— 字节差异只来自 PNG 编码，数据等价 ✓")


def bridges_cmp(new_p=None):
    import sys as _s
    new_p = Path(new_p) if new_p else NEW / "bridge/bridges_lift.regen_候选.json"
    a = json.loads(new_p.read_text(encoding="utf-8"))
    b = json.loads((OLD / "mod_input/bridges_lift.json").read_text(encoding="utf-8"))
    for k in sorted(set(a) | set(b)):
        if k == "points":
            pa, pb = a["points"], b["points"]
            same = pa == pb
            if not same:
                print(f"[bridges] points 不同！ new={len(pa)} old={len(pb)}")
                for i, (x, y) in enumerate(zip(pa, pb)):
                    if x != y:
                        print(f"   首个不同 idx={i}: new={x} old={y}")
                        break
            else:
                print(f"[bridges] points 一致 ({len(pa)} 个) ✓")
            continue
        if a.get(k) != b.get(k):
            va, vb = a.get(k), b.get(k)
            if isinstance(va, list) and isinstance(vb, list):
                sa, sb = set(va), set(vb)
                print(f"[bridges] {k}: new {len(va)} vs old {len(vb)}  "
                      f"仅new有={sorted(sa-sb)[:5]} 仅old有={sorted(sb-sa)[:5]}")
            else:
                print(f"[bridges] {k}: new={va!r}  old={vb!r}")
        else:
            print(f"[bridges] {k}: 一致 ✓")


def meta_cmp():
    a = json.loads((NEW / "products/build_meta.json").read_text(encoding="utf-8"))
    b = json.loads((OLD / "build_meta.json").read_text(encoding="utf-8"))
    for k in sorted(set(a) | set(b)):
        same = a.get(k) == b.get(k)
        print(f"[meta] {k}: {'一致 ✓' if same else f'不同 new={a.get(k)!r} old={b.get(k)!r}'}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "bridges":
        bridges_cmp(sys.argv[2] if len(sys.argv) > 2 else None)
        return 0
    pixel_cmp("playable", NEW / "products/harbin_playable.png", OLD / "harbin_playable.png")
    pixel_cmp("world", NEW / "products/harbin_world.png", OLD / "harbin_world.png")
    print()
    bridges_cmp()
    print()
    meta_cmp()
    return 0


if __name__ == "__main__":
    sys.exit(main())
