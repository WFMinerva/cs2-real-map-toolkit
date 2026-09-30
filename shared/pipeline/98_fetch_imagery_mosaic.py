# -*- coding: utf-8 -*-
"""98 取影像镶嵌图（reference overlay，配置驱动）——用于"OSM 缺数据"时判读与描摹。

为什么需要：铁路轮实测 OSM 在依兰附近**没有铁路线**（只有依兰站节点），
线位只能从影像判读；另外松花江铁路桥是否存在也要靠影像确认（不许凭空放置）。

口径：
  - 源 = config imagery.source（默认 Esri World Imagery XYZ 瓦片）；
  - 范围 = config imagery.bbox_margin_m 外扩后的 bounds（或 imagery.bbox_override 直接给经纬度）；
  - 输出 = 长边 imagery.out_px 的 PNG（等比，覆盖范围按瓦片拼好后整幅缩放）；
  - 只读网络、只写 products 下的候选图；瓦片间限速，失败重试，缺瓦片以灰底填充并报告。
用法：python shared/pipeline/98_fetch_imagery_mosaic.py --map yilan [--zoom 15] [--out-px 4096]
"""
import argparse
import io
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

# 我们主动拼大画布（z18 局部图可达 2.5 亿像素），关掉 PIL 的"解压炸弹"上限——
# 2026-09-30 实测：z18 6.5×7.2 km 拼完 3864 张瓦片后，在最后裁切处被
# DecompressionBombError（>1.79 亿像素）打断，白下 30 分钟。这里显式放开。
Image.MAX_IMAGE_PIXELS = None

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config

TILE = 256
DEFAULT_SOURCE = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
                  "World_Imagery/MapServer/tile/{z}/{y}/{x}")


def deg2num(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cs2-map-workshop/1 (map authoring)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if i == tries - 1:
                print(f"    [warn] {url} 失败: {type(exc).__name__}")
                return None
            time.sleep(1.0 + i)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="harbin")
    ap.add_argument("--zoom", type=int, default=None)
    ap.add_argument("--out-px", type=int, default=None)
    ap.add_argument("--margin-m", type=float, default=None)
    ap.add_argument("--bbox", default=None, help="直接给 S,W,N,E（经纬度，逗号分隔），覆盖 config 的范围")
    args = ap.parse_args()

    mp = config.load(args.map)
    im = mp.raw.get("imagery") or {}
    src = im.get("source") or DEFAULT_SOURCE
    z = args.zoom if args.zoom is not None else int(im.get("zoom", 15))
    out_px = args.out_px if args.out_px is not None else int(im.get("out_px", 4096))
    margin = args.margin_m if args.margin_m is not None else float(im.get("bbox_margin_m", 500.0))
    delay = float(im.get("tile_delay_s", 0.05))

    ov = im.get("bbox_override")
    if args.bbox:
        ov = [float(x) for x in args.bbox.split(",")]
    if ov:
        S, W, N, E = float(ov[0]), float(ov[1]), float(ov[2]), float(ov[3])
    else:
        L, R, B, T = mp.bounds
        lat_c = abs(mp.center[1])
        d_lat = margin / 110940.0
        d_lon = margin / (111320.0 * math.cos(math.radians(lat_c)))
        S, W, N, E = B - d_lat, L - d_lon, T + d_lat, R + d_lon

    x0f, y0f = deg2num(N, W, z)          # 左上
    x1f, y1f = deg2num(S, E, z)          # 右下
    x0, y0 = int(math.floor(x0f)), int(math.floor(y0f))
    x1, y1 = int(math.floor(x1f)), int(math.floor(y1f))
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    print(f"{mp.display_name or args.map}: bbox S{S:.5f} W{W:.5f} N{N:.5f} E{E:.5f}  z{z}  "
          f"瓦片 {nx}×{ny} = {nx*ny} 张 → 源 {src}")
    if nx * ny > int(im.get("max_tiles", 4000)):
        print(f"[FAIL] 需要 {nx*ny} 张瓦片，超过 max_tiles；降 zoom 或缩范围")
        return 1

    canvas = Image.new("RGB", (nx * TILE, ny * TILE), (32, 32, 32))
    miss = 0
    t0 = time.time()
    for iy in range(ny):
        for ix in range(nx):
            b = fetch(src.format(z=z, x=x0 + ix, y=y0 + iy))
            if b is None:
                miss += 1
                continue
            try:
                canvas.paste(Image.open(io.BytesIO(b)).convert("RGB"), (ix * TILE, iy * TILE))
            except Exception:
                miss += 1
            if delay:
                time.sleep(delay)
        if iy % 4 == 0:
            print(f"    行 {iy+1}/{ny}  用时 {time.time()-t0:.0f}s  缺 {miss}")
    print(f"拼接完成：{canvas.size[0]}×{canvas.size[1]}，缺瓦片 {miss}，用时 {time.time()-t0:.0f}s")

    # 按经纬度把 bbox 精确裁出来，再缩到 out_px
    px_x0 = (deg2num(N, W, z)[0] - x0) * TILE
    px_y0 = (deg2num(N, W, z)[1] - y0) * TILE
    px_x1 = (deg2num(S, E, z)[0] - x0) * TILE
    px_y1 = (deg2num(S, E, z)[1] - y0) * TILE
    crop = canvas.crop((int(round(px_x0)), int(round(px_y0)), int(round(px_x1)), int(round(px_y1))))
    scale = out_px / max(crop.size)
    out = crop.resize((max(1, int(round(crop.size[0] * scale))), max(1, int(round(crop.size[1] * scale)))),
                      Image.LANCZOS)
    out_p = mp.path("imagery_mosaic") if im.get("out") is None else Path(im["out"])
    if args.bbox or args.zoom is not None or args.out_px is not None:
        # 细图/局部图不要覆盖整幅镶嵌图：文件名带上 zoom 与 bbox 特征
        tag = f"z{z}" + (f"_{S:.4f}_{W:.4f}" if args.bbox else "")
        out_p = out_p.with_name(f"{out_p.stem}_{tag}{out_p.suffix}")
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_p)
    m_per_px = (E - W) * 111320 * math.cos(math.radians((S + N) / 2)) / out.size[0]
    print(f"[save] {out_p}  {out.size[0]}×{out.size[1]}  约 {m_per_px:.2f} m/px"
          f"（bbox W{W:.5f}..{E:.5f} N{N:.5f}..{S:.5f} 覆盖 {out.size[1]} px 高）")
    print(f"       经纬度↔像素：px_x = (lon - W)/(E-W)*{out.size[0]}，px_y = (N - lat)/(N-S)*{out.size[1]}")
    return 0 if miss == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
