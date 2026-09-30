# -*- coding: utf-8 -*-
"""90 范围预览：把「拟定的 14.336 km 见方地图框」画在真实 OSM 底图上，供机主确认边界与比例。

新脚本（迁移时新增，依兰提案首次使用）。
输入：地图 config（center/bounds/playable + 可选 preview_annotations）+ region_shp 图层
输出：maps/<map_id>/range_preview.png + 关键位置到框边距离表（stdout）

渲染口径：局部等距投影（x = Δlon·cos(中心纬度)·111320，y = Δlat·110940 m），
只用于范围预览，不代表制作管线里的 UTM 网格。
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import shapefile
from PIL import Image, ImageDraw

from cs2map import config
from cs2map import geo

SIDE = 1600
MARGIN = 60

ROAD_STYLE = [  # (fclass, 线宽px, 颜色)
    ("motorway", 5, (60, 60, 60)), ("trunk", 4, (80, 80, 80)),
    ("primary", 3, (110, 110, 110)), ("secondary", 2, (140, 140, 140)),
    ("tertiary", 1.5, (165, 165, 165)),
]
WWAY_W = {"river": 4.0, "canal": 2.5, "stream": 1.2, "drain": 1.0}

KIND_STYLE = {  # kind -> (marker 颜色, 文字颜色)
    "bridge": ((178, 34, 34), (140, 20, 20)),
    "station": ((20, 20, 20), (20, 20, 20)),
    "mouth": ((30, 100, 200), (20, 80, 170)),
    "toll": ((120, 120, 120), (100, 100, 100)),
    "river": (None, (20, 80, 170)),
}


class Frame:
    """经纬度 <-> 像素（局部等距）。bbox = (W, S, E, N)。"""

    def __init__(self, bbox, center_lat, proposal=None):
        self.w, self.s, self.e, self.n = bbox
        self.pw, self.ps, self.pe, self.pn = proposal or (self.w, self.s, self.e, self.n)
        self.kx = math.cos(math.radians(center_lat)) * 111_320.0
        self.ky = 110_940.0
        self.width_m = (self.e - self.w) * self.kx
        self.height_m = (self.n - self.s) * self.ky

    def px(self, lon, lat, side, margin):
        x = (lon - self.w) * self.kx / self.width_m
        y = (self.n - lat) * self.ky / self.height_m
        return (margin + x * (side - 2 * margin), margin + y * (side - 2 * margin))

    def dist_to_frame_m(self, lon, lat):
        """点到【提案框】四边的最近距离（米）与方位（不是外扩后的预览图边）。"""
        cands = {
            "西": (lon - self.pw) * self.kx,
            "东": (self.pe - lon) * self.kx,
            "南": (lat - self.ps) * self.ky,
            "北": (self.pn - lat) * self.ky,
        }
        k = min(cands, key=cands.get)
        return k, cands[k]


def main() -> int:
    mp = config.load()
    b = mp.raw["bounds"]
    W, E, S, N = b["left"], b["right"], b["bottom"], b["top"]
    center_lon, center_lat = mp.center
    pad_lon = (E - W) * 0.18
    pad_lat = (N - S) * 0.18
    fr = Frame((W - pad_lon, S - pad_lat, E + pad_lon, N + pad_lat), center_lat,
               proposal=(W, S, E, N))

    img = Image.new("RGB", (SIDE, SIDE), (246, 243, 235))
    d = ImageDraw.Draw(img)

    shp = mp.region_shp
    base_layers = ["water_a", "waterways", "roads"]
    have = [l for l in base_layers if (shp / f"gis_osm_{l}_free_1.shp").exists()]
    if not have:
        print(f"[STOP] 预览底图图层一个都没有（目录 {shp} 下无 gis_osm_{{water_a,waterways,roads}}_free_1.shp）。\n"
              f"       准备步骤：下载目标省 Geofabrik 提取包（*.shp.zip）到 data_common\\，"
              f"config 设 region_zip，跑 92_extract_layers 抽图层到 region_shp，再跑本脚本。")
        return 1
    missing = [l for l in base_layers if l not in have]
    if missing:
        print(f"[warn] 底图缺图层 {missing}（可选层，跳过绘制；全缺才停止）")

    def draw_polys(layer, classes, fill, outline=None):
        p = shp / f"gis_osm_{layer}_free_1.shp"
        if not p.exists():
            print(f"[skip] 无图层 {layer}")
            return 0
        r = shapefile.Reader(str(p))
        fields = [f[0] for f in r.fields[1:]]
        n = 0
        for sr in r.iterShapeRecords():
            rec = dict(zip(fields, sr.record))
            if str(rec.get("fclass", "")) not in classes:
                continue
            pts = sr.shape.points
            if len(pts) < 3:
                continue
            parts = list(sr.shape.parts) + [len(pts)]
            for i in range(len(parts) - 1):
                ring = pts[parts[i]:parts[i + 1]]
                if len(ring) < 3:
                    continue
                poly = [fr.px(lo, la, SIDE, MARGIN) for lo, la in ring if W - pad_lon <= lo <= E + pad_lon
                        and S - pad_lat <= la <= N + pad_lat]
                if len(poly) >= 3:
                    d.polygon(poly, fill=fill, outline=outline or fill)
                    n += 1
        print(f"[{layer}] 画出 {n} 个面")
        return n

    def draw_lines(layer, style_map, color):
        p = shp / f"gis_osm_{layer}_free_1.shp"
        if not p.exists():
            print(f"[skip] 无图层 {layer}")
            return 0
        r = shapefile.Reader(str(p))
        fields = [f[0] for f in r.fields[1:]]
        n = 0
        for sr in r.iterShapeRecords():
            rec = dict(zip(fields, sr.record))
            fc = str(rec.get("fclass", ""))
            pts = sr.shape.points
            if len(pts) < 2 or fc not in style_map:
                continue
            w = style_map[fc]
            line = [fr.px(lo, la, SIDE, MARGIN) for lo, la in pts]
            d.line(line, fill=color, width=max(1, int(round(w))), joint="curve")
            n += 1
        print(f"[{layer}] 画出 {n} 条线")
        return n

    draw_polys("water_a", {"riverbank", "water", "reservoir", "wetland", "wetland_marsh",
                           "wetland_swamp", "basin"}, (150, 190, 230), (110, 160, 215))
    draw_lines("waterways", WWAY_W, (110, 160, 215))
    for fclass, w, col in ROAD_STYLE:
        draw_lines("roads", {fclass: w, fclass + "_link": w * 0.7}, col)

    # ---- 提案框（红）与 2 km 网格 ----
    def m(lon, lat):
        return fr.px(lon, lat, SIDE, MARGIN)

    sq = [m(W, S), m(E, S), m(E, N), m(W, N)]
    d.polygon(sq, outline=(220, 30, 30), width=4)
    px_per_m = (SIDE - 2 * MARGIN) / fr.width_m
    km = 2000.0
    for gx in np.arange(math.ceil((W - pad_lon) * 1e5) / 1e5, E + pad_lon, km / fr.kx):
        d.line([m(gx, S), m(gx, N)], fill=(210, 205, 195), width=1)
    for gy in np.arange(math.ceil((S - pad_lat) * 1e5) / 1e5, N + pad_lat, km / fr.ky):
        d.line([m(W, gy), m(E, gy)], fill=(210, 205, 195), width=1)

    c = m(center_lon, center_lat)
    d.line([c[0] - 20, c[1], c[0] + 20, c[1]], fill=(220, 30, 30), width=3)
    d.line([c[0], c[1] - 20, c[0], c[1] + 20], fill=(220, 30, 30), width=3)

    # ---- 标注层（config preview_annotations）----
    ann = mp.raw.get("preview_annotations") or {}
    f_main = geo.cjk_font(22)
    f_small = geo.cjk_font(17)

    def text_bg(xy, txt, font, fill):
        l, t, r_, b_ = d.textbbox(xy, txt, font=font)
        d.rectangle([l - 3, t - 2, r_ + 3, b_ + 2], fill=(255, 253, 248))
        d.text(xy, txt, font=font, fill=fill)
        return (r_, b_)

    def dashed_rect(p1, p2, color, dash=14, gap=9, width=3):
        x1, y1 = p1
        x2, y2 = p2
        for (a, b_) in (((x1, y1), (x2, y1)), ((x1, y2), (x2, y2)),
                        ((x1, y1), (x1, y2)), ((x2, y1), (x2, y2))):
            length = math.hypot(b_[0] - a[0], b_[1] - a[1])
            nseg = int(length // (dash + gap)) + 1
            for i in range(nseg):
                t0 = i * (dash + gap) / length
                t1 = min((i * (dash + gap) + dash) / length, 1.0)
                pa = (a[0] + (b_[0] - a[0]) * t0, a[1] + (b_[1] - a[1]) * t0)
                pb = (a[0] + (b_[0] - a[0]) * t1, a[1] + (b_[1] - a[1]) * t1)
                d.line([pa, pb], fill=color, width=width)

    bu = ann.get("builtup")
    if bu:
        p1 = m(bu["west"], bu["north"])
        p2 = m(bu["east"], bu["south"])
        dashed_rect(p1, p2, (224, 128, 0))
        text_bg((p1[0] + 6, p1[1] + 4), bu["name"], f_small, (180, 100, 0))
        print(f"[builtup] 虚线框 {(bu['west'], bu['south'])}..{(bu['east'], bu['north'])}")

    print("\n=== 关键位置到提案框边的距离 ===")
    for p in ann.get("points", []):
        kind = p.get("kind", "poi")
        x, y = m(p["lon"], p["lat"])
        mk, tc = KIND_STYLE.get(kind, ((90, 90, 90), (60, 60, 60)))
        if mk:  # 画标记
            if kind == "bridge":
                d.polygon([(x, y - 8), (x - 8, y + 6), (x + 8, y + 6)], fill=mk)
            elif kind == "station":
                d.rectangle([x - 7, y - 7, x + 7, y + 7], fill=mk, outline=(255, 255, 255))
            elif kind == "mouth":
                d.ellipse([x - 7, y - 7, x + 7, y + 7], outline=mk, width=3)
            else:
                d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=mk)
        edge, dist_m = fr.dist_to_frame_m(p["lon"], p["lat"])
        if kind != "river":
            print(f"  {p['name']:<22} {p['lat']:.5f},{p['lon']:.5f}  距{edge}框 {dist_m/1000:.1f} km")
        if kind == "river":
            label = p["name"]
        else:
            label = f"{p['name']}  距{edge}框{dist_m/1000:.1f}km"
        la = p.get("label_at", "e")
        tw = d.textlength(label, font=f_small)
        th = 20
        offs = {"w": (-tw - 12, -th // 2), "e": (12, -th // 2), "n": (-tw // 2, -th - 12),
                "s": (-tw // 2, 12), "ne": (10, -th - 8), "nw": (-tw - 10, -th - 8),
                "se": (10, 8), "sw": (-tw - 10, 8)}
        dx, dy = offs.get(la, (12, -th // 2))
        text_bg((x + dx, y + dy), label, f_small, tc)

    # ---- 角点坐标、标题、图例 ----
    font = geo.cjk_font(24)
    small = geo.cjk_font(19)
    for name, lo, la, align, xx in [("NW", W, N, "left", MARGIN + 8), ("NE", E, N, "right", SIDE - MARGIN - 8),
                                    ("SW", W, S, "left", MARGIN + 8), ("SE", E, S, "right", SIDE - MARGIN - 8)]:
        p = m(lo, la)
        txt = f"{name} {la:.4f}N {lo:.4f}E"
        tw = d.textlength(txt, font=small)
        xx2 = xx if align == "left" else xx - tw
        yy = p[1] - 30 if name in ("NW", "NE") else p[1] + 8
        text_bg((xx2, yy), txt, small, (160, 20, 20))
    d.text((MARGIN, 12),
           f"{mp.display_name} 范围预览（提案）  地图框 {mp.playable_m/1000:.3f} km 见方 ｜ "
           f"比例 1:1（{mp.playable[1]} m/px）",
           font=font, fill=(30, 30, 30))
    d.text((MARGIN, SIDE - MARGIN + 14),
           "灰=OSM道路 蓝=OSM水系 红框=地图框 橙虚线=建成区(推算) ▲桥 ■火车站 ○河口 "
           "｜ 底图 Geofabrik 分省提取（ODbL）",
           font=small, fill=(90, 90, 90))

    out = config.ROOT / "maps" / mp.map_id / "range_preview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"[save] {out}  {out.stat().st_size/1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
