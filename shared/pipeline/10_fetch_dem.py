# -*- coding: utf-8 -*-
"""10 下载 Copernicus DEM GLO-30（AWS 开放数据，匿名）+ 框内高程统计。

来源：旧工作区 scripts/01_fetch_dem.py（已验证），配置驱动改造。
GLO-30 是 DSM（含建筑/植被，水体已被压平），30 m 网格，EPSG:4326，垂直基准 EGM2008。
tile 列表与目标框都来自 <map>.config.json。
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import rasterio
from rasterio.windows import from_bounds

from cs2map import config

BASE = "https://copernicus-dem-30m.s3.amazonaws.com"


def fetch(tile: str, dst_dir: Path) -> Path:
    name = f"Copernicus_DSM_COG_10_{tile}_DEM.tif"
    dst = dst_dir / name
    if dst.exists() and dst.stat().st_size > 1_000_000:
        print(f"[skip] {name} 已存在 {dst.stat().st_size/1e6:.1f} MB")
        return dst
    url = f"{BASE}/Copernicus_DSM_COG_10_{tile}_DEM/{name}"
    print(f"[get ] {url}")
    tmp = dst.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=180) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dst)
    print(f"[ok  ] {name} {dst.stat().st_size/1e6:.1f} MB (expect {total/1e6:.1f})")
    return dst


def main() -> int:
    mp = config.load()
    tiles = mp.raw["dem"]["tiles"]
    dem_dir = config.ROOT / "maps" / mp.map_id / "data" / "dem"
    dem_dir.mkdir(parents=True, exist_ok=True)
    paths = [fetch(t, dem_dir) for t in tiles]

    L, R, B, T = mp.bounds
    print(f"\n=== DEM 元数据与 {mp.display_name} 框内高程统计 ===")
    for p in paths:
        with rasterio.open(p) as ds:
            print(f"\n{p.name}")
            print(f"  crs={ds.crs}  res={ds.res}  shape={ds.shape}  dtype={ds.dtypes[0]}")
            print(f"  bounds={tuple(round(v, 4) for v in ds.bounds)}")
            win = from_bounds(L, B, R, T, ds.transform)
            arr = ds.read(1, window=win, boundless=True, fill_value=np.nan).astype("float64")
            arr[arr <= -1000] = np.nan
            if np.isfinite(arr).any():
                print(f"  window shape={arr.shape}")
                print(f"  elev min={np.nanmin(arr):.1f}  max={np.nanmax(arr):.1f}  "
                      f"mean={np.nanmean(arr):.1f}  p1={np.nanpercentile(arr, 1):.1f}  "
                      f"p99={np.nanpercentile(arr, 99):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
