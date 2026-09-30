# -*- coding: utf-8 -*-
"""cs2map.geo —— 共享几何/栅格工具（行为与旧工作区已验证脚本逐位一致）。"""
from __future__ import annotations

import numpy as np
from PIL import Image
from rasterio.warp import transform as warp_transform


def densify(ring, step_m: float = 40.0):
    """按约 step_m 加密环上的点，避免长直段在重投影时失真。

    注意：不同脚本的历史默认值不同（可玩区掩膜 40、世界图 70），
    调用方必须显式传 step_m 以保持与已验证产物一致。
    """
    out = []
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        d = ((x2 - x1) * 77_650) ** 2 + ((y2 - y1) * 111_130) ** 2
        d = d ** 0.5
        n = max(1, int(d / step_m))
        for k in range(n):
            t = k / n
            out.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    out.append(ring[-1])
    return out


def reproject_geom(geom, epsg: str, step_m: float = 40.0):
    """WGS84 GeoJSON 几何 -> 目标 UTM。Polygon/MultiPolygon，洞挂到包含它的最小外环。"""

    def conv_ring(ring):
        ring = densify(ring, step_m)
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        nx, ny = warp_transform("EPSG:4326", epsg, xs, ys)
        return [[float(a), float(b)] for a, b in zip(nx, ny)]

    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [conv_ring(r) for r in geom["coordinates"]]}
    return {"type": "MultiPolygon",
            "coordinates": [[conv_ring(r) for r in poly] for poly in geom["coordinates"]]}


def runs(mask_1d):
    """一维布尔掩膜的连续段 [(起,止), ...]，闭区间。"""
    idx = np.flatnonzero(mask_1d)
    if idx.size == 0:
        return []
    out, start = [], idx[0]
    for a, b in zip(idx, idx[1:]):
        if b != a + 1:
            out.append((int(start), int(a)))
            start = b
    out.append((int(start), int(idx[-1])))
    return out


def load_height_m(png_path, height_scale: float) -> np.ndarray:
    """16-bit 灰度高度图 -> 米。gray = 高程/HeightScale*65535。"""
    with Image.open(png_path) as im:
        a = np.array(im, dtype=np.uint16)
    return a.astype("float64") / 65535.0 * height_scale


def hillshade(z, az: float = 315.0, alt: float = 45.0, dx: float = 3.5):
    """山体阴影（3x 垂直夸张），纯 numpy。"""
    az_r, alt_r = np.deg2rad(az), np.deg2rad(alt)
    gy, gx = np.gradient(z, dx)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy) * 3.0)
    aspect = np.arctan2(-gx, gy)
    shaded = np.sin(alt_r) * np.sin(slope) + np.cos(alt_r) * np.cos(slope) * np.cos(az_r - aspect)
    return np.clip(shaded, 0, 1)


def cjk_font(size: int, candidates=None):
    """中文标注字体（Windows 自带），失败回退默认。"""
    from pathlib import Path as _P
    from PIL import ImageFont
    if candidates is None:
        candidates = [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"]
    for f in candidates:
        if _P(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()
