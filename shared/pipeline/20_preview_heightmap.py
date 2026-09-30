# -*- coding: utf-8 -*-
"""20 生成验图：对比度拉伸灰度 + 山体阴影（纯 numpy，无额外依赖）。

来源：旧工作区 scripts/07_preview.py（已验证），配置驱动改造。
用途：人眼验证地形走向、江道位置、覆盖范围。不参与游戏导入。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image, ImageDraw

from cs2map import config
from cs2map import geo


def main() -> int:
    mp = config.load()
    meta = mp.path("build_meta")
    import json
    meta_d = json.loads(meta.read_text(encoding="utf-8"))
    with Image.open(mp.path("playable_png")) as im:
        a = np.array(im, dtype=np.uint16)
    z = a.astype("float64") / 65535 * meta_d["height_scale_m"]
    print(f"读回高度图: {a.shape} {a.dtype}  高程 {z.min():.1f}..{z.max():.1f} m")

    lo, hi = np.percentile(z, [0.5, 99.5])
    g = np.clip((z - lo) / (hi - lo), 0, 1)
    px, mpp = mp.playable
    hs = geo.hillshade(z, dx=mpp)
    rgb = np.dstack([g * hs, g * hs, g * hs])
    rgb = (np.clip(rgb, 0, 1) * 255).astype("uint8")

    # 河床（低于 判定水位 = 海平面 − riverbed_depth_below_sea_m，即挖出的河道）高亮
    depth_b = float(mp.water.get("riverbed_depth_below_sea_m", 2.0))
    below = mp.sea_level - depth_b  # 修复：原稿引用了未定义的 sea（2026-09-30 依兰首跑发现）
    river = z < below
    rgb[river] = (rgb[river] * 0.3 + np.array([30, 110, 220]) * 0.7).astype("uint8")
    print(f"河床像素 {river.sum()} ({river.mean()*100:.2f}%)  拉伸区间 {lo:.1f}..{hi:.1f} m")

    side = 1400
    img = Image.fromarray(rgb, "RGB").resize((side, side), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    d.text((10, 8), f"{mp.display_name} playable {mp.playable_m/1000:.3f}km | {mp.epsg} | "
                    f"hillshade 3x | blue = dug riverbed (<{below:.0f}m)", fill=(255, 230, 80))
    # 中心十字
    c = side // 2
    d.line([c - 22, c, c + 22, c], fill=(255, 0, 0), width=2)
    d.line([c, c - 22, c, c + 22], fill=(255, 0, 0), width=2)
    lo_c, la_c = mp.center
    d.text((c + 26, c - 8), f"center {lo_c:.3f}E {la_c:.3f}N", fill=(255, 80, 80))
    p = mp.path("verify_hillshade")
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    print(f"[save] {p} {p.stat().st_size/1e6:.2f} MB")

    for q in (0, 1, 5, 25, 50, 75, 95, 99, 100):
        print(f"  p{q:<3} = {np.percentile(z, q):7.2f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
