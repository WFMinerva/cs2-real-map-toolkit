# 一次性放线图（2026-09-30 车站轮）：在真实影像上标出
#   ① 我们铺的线位 v11（已开缺口）+ 缺口两端 ② 车站月台轨跨度 ③ 站内自带"站前道路线"（贴着车站那条）
# 供机主手连轨道 / 手画站前路时对照。参数为新校准的车站位姿（见 rail_station.json）。
import json, math
from PIL import Image, ImageDraw, ImageFont

IMG = r"<repo>\maps\yilan\products\imagery\yilan_imagery_z16_candidate_z18_46.2700_129.5890.png"
OUT = r"<repo>\maps\yilan\products\imagery\_站场放线_缺口与站前路.png"
V11 = r"<repo>\maps\yilan\rail\rail_full_v11_gap.json"
S, W, N, E = 46.27000, 129.58900, 46.28650, 129.61450

def w2ll(x, z):
    lat = (z + 5155877.4) / 111321.9
    return lat, (x + 9964383.9 + 1.9 * lat) / 76911.9

# 车站新位姿：数据点 + yaw 偏移 + 横向平移
YAW, YOFF, LAT = 51.79, -90.0, 8.5
th = math.radians(YAW + YOFF)
c, s = math.cos(th), math.sin(th)
D = (3331.7, -4242.7)
O = (D[0] + s * LAT, D[1] + c * LAT)          # 建筑原点（横移后）

def loc(x, z):                                 # 本地 → 世界
    return (O[0] + x * c + z * s, O[1] - x * s + z * c)

im = Image.open(IMG).convert("RGB"); w, h = im.size
dr = ImageDraw.Draw(im, "RGBA")
def px(lat, lon): return ((lon - W) / (E - W) * w, (N - lat) / (N - S) * h)
def wpx(p): return px(*w2ll(*p))
def font(sz):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try: return ImageFont.truetype(p, sz)
        except Exception: pass
    return ImageFont.load_default()
F, Fs = font(30), font(25)

def label_at(p, text, color, dy=-40):
    X, Y = wpx(p)
    tb = dr.textbbox((0, 0), text, font=F); tw, th = tb[2]-tb[0], tb[3]-tb[1]
    dr.rectangle([X+14, Y+dy-6, X+14+tw+14, Y+dy+th+10], fill=(0,0,0,190))
    dr.text((X+21, Y+dy), text, font=F, fill=color)

# ① 线位 v11（含缺口）
d = json.load(open(V11, encoding="utf-8"))
for seg in d["segments"]:
    dr.line([wpx((seg[0], seg[1])), wpx((seg[2], seg[3]))], fill=(255, 214, 0, 240), width=5)
gapA, gapB = (3222.6, -4315.2), (3425.7, -4152.6)     # SW / NE 缺口端
for g, tag in ((gapB, "缺口 NE 端 (3425.7,-4152.6)"), (gapA, "缺口 SW 端 (3222.6,-4315.2)")):
    X, Y = wpx(g)
    dr.line([(X-18, Y-18), (X+18, Y+18)], fill=(255, 70, 70), width=6)
    dr.line([(X-18, Y+18), (X+18, Y-18)], fill=(255, 70, 70), width=6)
    label_at(g, tag, (255, 120, 120), dy=-46)

# ② 车站月台轨跨度（三条：local z = -8.5 / -24 / -40，各 ±106 m）
for zl, col, nm in ((-8.5, (0, 235, 255), "月台轨①(local z=-8.5，正压线路上)"),
                    (-24.0, (0, 190, 230), "月台轨②(z=-24)"),
                    (-40.0, (0, 150, 210), "月台轨③(z=-40)")):
    dr.line([wpx(loc(-106, zl)), wpx(loc(106, zl))], fill=col, width=4)

# ③ 站内自带"站前道路线"（local z≈12.2–12.6，x 从 -70 到 +88）——贴着车站那条
dr.line([wpx(loc(-70.09, 12.21)), wpx(loc(88.46, 12.61))], fill=(80, 255, 120), width=6)
label_at(loc(30, 12.4), "站内自带站前道路线（贴着车站，可照此画路）", (80, 255, 120), dy=-150)
# 车站建筑原点
X, Y = wpx(O)
dr.ellipse([X-12, Y-12, X+12, Y+12], outline=(255, 255, 255), width=5)
label_at(O, "⑧车站建筑原点(3331.7,-4242.7 横移后)", (255, 255, 255), dy=20)

# ④ 真实站（OSM）
dr.line([(wpx((3312.5, -4213.7))[0]-16, wpx((3312.5, -4213.7))[1]), (wpx((3312.5, -4213.7))[0]+16, wpx((3312.5, -4213.7))[1])], fill=(255,60,60), width=5)
label_at((3312.5, -4213.7), "OSM 真实依兰站节点", (255, 90, 90), dy=20)

L = ["黄 = 我们铺的铁路 v11（★红叉处为缺口两端，缺口内没有轨，交给你手连）",
     "青/蓝 = 车站自带三条月台轨（各 212 m，①正压在我们线路上）",
     "绿 = 车站自带的『站前道路线』(local z≈12.2–12.6 m，长 158 m) —— 站前路应贴着它画",
     "白圈 = 车站建筑原点（数据点横移 8.5 m 后）；红 = OSM 真实依兰站节点"]
bh = 32 * len(L) + 18
dr.rectangle([20, h-bh-20, 1560, h-20], fill=(0, 0, 0, 195))
for i, t in enumerate(L):
    dr.text((34, h-bh-4+i*32), t, font=Fs, fill=(255, 255, 255))
im.save(OUT)
Image.open(OUT).crop((700, 700, 1800, 1550)).resize((1500, 1160), Image.LANCZOS).save(OUT.replace(".png", "_放大.png"))
print("saved", OUT)
