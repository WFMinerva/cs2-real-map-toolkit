# 一次性对照图脚本（2026-09-30 车站轮）：把"我们铺的线位 / 我们放的车站 / OSM 真实依兰站节点"
# 标到刚抓的真实影像上，供机主目视对照。参数全部来自命令行/常量，不写进管线。
import json, math, sys
from PIL import Image, ImageDraw, ImageFont

IMG = r"<repo>\maps\yilan\products\imagery\yilan_imagery_z16_candidate_z18_46.2700_129.5890.png"
OUT = r"<repo>\maps\yilan\products\imagery\_站场对照_我们vs真实.png"
RAIL = r"<repo>\maps\yilan\rail\rail_full_v10.json"
# 出图时脚本打印的 bbox（S,W,N,E）与换算：px_x=(lon-W)/(E-W)*w, px_y=(N-lat)/(N-S)*h
S, W, N, E = 46.27000, 129.58900, 46.28650, 129.61450

# 世界坐标 ↔ 经纬度（由线位 55 点仿射拟合，残差 ≤5 cm）
def w2ll(x, z):
    lat = (z + 5155877.4) / 111321.9
    lon = (x + 9964383.9 + 1.9 * lat) / 76911.9
    return lat, lon

im = Image.open(IMG).convert("RGB")
w, h = im.size
dr = ImageDraw.Draw(im, "RGBA")

def px(lat, lon):
    return ((lon - W) / (E - W) * w, (N - lat) / (N - S) * h)

def font(sz):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            continue
    return ImageFont.load_default()

F = font(30); F2 = font(24)

def marker(lat, lon, color, label, r=14, dy=-42):
    X, Y = px(lat, lon)
    dr.line([(X - r * 1.8, Y), (X + r * 1.8, Y)], fill=color, width=5)
    dr.line([(X, Y - r * 1.8), (X, Y + r * 1.8)], fill=color, width=5)
    dr.ellipse([X - r, Y - r, X + r, Y + r], outline=color, width=5)
    if label:
        tb = dr.textbbox((0, 0), label, font=F)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        ty = Y + dy
        dr.rectangle([X + 12, ty - 4, X + 12 + tw + 12, ty + th + 8], fill=(0, 0, 0, 170))
        dr.text((X + 18, ty), label, font=F, fill=color)
    return X, Y

# ① 我们铺的线位（v10）——黄色
segs = json.load(open(RAIL, encoding="utf-8"))["segments"]
poly = [(segs[0][0], segs[0][1])] + [(s[2], s[3]) for s in segs]
pts = [px(*w2ll(x, z)) for x, z in poly]
dr.line(pts, fill=(255, 214, 0, 235), width=5)

# ② 我们放的车站：数据原点（世界 3566.3,-3997.3；实际落位再横移 8.5 m）
ox, oz = 3566.3, -3997.3
th = math.radians(42.1 - 90.0)
ax_, az_ = ox + math.sin(th) * 8.5, oz + math.cos(th) * 8.5
X1, Y1 = marker(*w2ll(ox, oz), (0, 230, 255), "我们放的车站(数据原点)")
X2, Y2 = marker(*w2ll(ax_, az_), (0, 160, 255), "⑧实际落位(横移8.5m)", dy=6)
# ③ 车站月台轨跨度（±106 m 沿线路走向 42.1°）
u = (math.sin(math.radians(42.1)), math.cos(math.radians(42.1)))
p0 = px(*w2ll(ax_ - u[0] * 106, az_ - u[1] * 106))
p1 = px(*w2ll(ax_ + u[0] * 106, az_ + u[1] * 106))
for i in range(0, 60, 2):
    t0, t1 = i / 60.0, (i + 1) / 60.0
    dr.line([(p0[0] + (p1[0] - p0[0]) * t0, p0[1] + (p1[1] - p0[1]) * t0),
             (p0[0] + (p1[0] - p0[0]) * t1, p0[1] + (p1[1] - p0[1]) * t1)],
            fill=(0, 230, 255, 200), width=3)

# ④ OSM 真实依兰站节点——红色
osm_lat, osm_lon = 46.2771813, 129.6000269
Xo, Yo = marker(osm_lat, osm_lon, (255, 60, 60), "OSM 依兰站节点(真实)")
# ⑤ 两点连线 + 距离/方位
dr.line([(X1, Y1), (Xo, Yo)], fill=(255, 255, 255, 170), width=3)
dxw, dzw = ox - 3312.5, oz - (-4213.7)
dist = math.hypot(dxw, dzw)
along = dxw * u[0] + dzw * u[1]
across = dxw * u[1] - dzw * u[0]
mid = ((X1 + Xo) / 2, (Y1 + Yo) / 2)
txt = f"相距 {dist:.0f} m：沿线路 {along:+.0f} m（东北）/ 横向 {across:+.0f} m"
dr.rectangle([mid[0] + 10, mid[1] - 20, mid[0] + 10 + 620, mid[1] + 22], fill=(0, 0, 0, 180))
dr.text((mid[0] + 18, mid[1] - 14), txt, font=F2, fill=(255, 255, 255))

# ⑥ 图例
L = ["黄线 = 我们铺的铁路线位（v10，已验收）",
     "青/蓝 = 我们放的车站（⑧；蓝=横移后实际落位，双点间虚线=月台轨 212 m 跨度）",
     "红 = OSM 真实『依兰站』节点（46.2771813, 129.6000269）",
     "底图 = Esri World Imagery z18（约 0.82 m/px）"]
bh = 30 * len(L) + 20
dr.rectangle([20, h - bh - 20, 1000, h - 20], fill=(0, 0, 0, 190))
for i, s in enumerate(L):
    dr.text((34, h - bh - 6 + i * 30), s, font=F2, fill=(255, 255, 255))

im.save(OUT)
print("saved:", OUT, im.size)
