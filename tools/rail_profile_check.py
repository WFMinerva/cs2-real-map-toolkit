# -*- coding: utf-8 -*-
"""rail_profile_check.py —— 沿线位逐点采 DEM，输出各段的天然坡度画像（只读，不改任何东西）。

用途：判断"贴地铺轨"时哪些段会被地形逼出超限坡度（铁路一般 ≤2%，困难段 ≤3%）。
用法： python tools/rail_profile_check.py --map yilan [--step 20] [--segments maps/yilan/rail/rail_full.json]
输出：每段的里程-高程采样表（每 step 米）、段内最大坡度（含位置）、超 2% 的区间清单。
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="yilan")
    ap.add_argument("--step", type=float, default=20.0)
    ap.add_argument("--segments", default=None)
    ap.add_argument("--threshold", type=float, default=2.0, help="坡度阈值 %%")
    a = ap.parse_args()

    cfg = json.loads((ROOT / "maps" / a.map / f"{a.map}.config.json").read_text(encoding="utf-8"))
    b = cfg["roads_bounds"]
    L, R, Bo, T = b["left"], b["right"], b["bottom"], b["top"]
    play = 14336.0
    segpath = Path(a.segments) if a.segments else (ROOT / "maps" / a.map / "rail" / "rail_full.json")
    segs = json.loads(segpath.read_text(encoding="utf-8"))["segments"]

    dem_dir = ROOT / "maps" / a.map / "data" / "dem"
    tif = next(dem_dir.glob("*DEM.tif"))
    ds = rasterio.open(tif)
    print(f"DEM {tif.name}  size={ds.width}x{ds.height}  crs={ds.crs}")

    def dem_at(x, z):
        u = x / play + 0.5
        v = z / play + 0.5
        lon = L + u * (R - L)
        lat = Bo + v * (T - Bo)
        r, c = ds.index(lon, lat)
        r = min(max(r, 0), ds.height - 1)
        c = min(max(c, 0), ds.width - 1)
        return float(ds.read(1, window=((r, r + 1), (c, c + 1)))[0, 0])

    print(f"\n段路径 {segpath.name}  步长 {a.step:.0f} m  阈值 {a.threshold:.1f}%")
    allmax = []
    for i, s in enumerate(segs, 1):
        x0, z0, x1, z1 = s
        length = math.hypot(x1 - x0, z1 - z0)
        n = max(2, int(length / a.step))
        ds_ = [dem_at(x0 + (x1 - x0) * k / n, z0 + (z1 - z0) * k / n) for k in range(n + 1)]
        dist = [k * length / n for k in range(n + 1)]
        # 逐 step 坡度
        sl = [abs(ds_[k + 1] - ds_[k]) / (length / n) * 100 for k in range(n)]
        mx = max(sl)
        kx = sl.index(mx)
        allmax.append(mx)
        runs = []
        cur = None
        for k, v in enumerate(sl):
            if v > a.threshold:
                if cur is None:
                    cur = [k, k]
                else:
                    cur[1] = k
            else:
                if cur is not None:
                    runs.append(tuple(cur))
                    cur = None
        if cur is not None:
            runs.append(tuple(cur))
        flag = "  ⚠" if mx > a.threshold else ""
        print(f"  段{i:2d} 长 {length:6.0f} m  高程 {ds_[0]:7.2f} → {ds_[-1]:7.2f} （Δ{ds_[-1]-ds_[0]:+6.2f} m）"
              f"  平均坡 {abs(ds_[-1]-ds_[0])/length*100:5.2f}%  最大 {mx:5.2f}% @ {dist[kx]:6.0f} m{flag}")
        for a0, a1 in runs:
            print(f"        > {a.threshold:.1f}% 区间：{dist[a0]:.0f}–{dist[a1+1]:.0f} m（{ds_[a0]:.2f} → {ds_[a1+1]:.2f} m）")
    print(f"\n全线最大天然坡度 {max(allmax):.2f}%；超阈值的段数 {sum(1 for v in allmax if v > a.threshold)}/{len(segs)}")


if __name__ == "__main__":
    main()
