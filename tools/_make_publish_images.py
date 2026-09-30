# 一次性脚本（2026-09-30 发布准备）：用我们自己的地形/水系/铁路数据渲"发布用图"。
# 规格（Paradox 官方 "Map Creation: Settings and Polish"）：
#   Preview   16:9，推荐 685×385（L×W）
#   Thumbnail 1:1，推荐 200×200（俯视）
# 素材：products/yilan_playable.png（16-bit 高度图，gray/65535*4096 = 米）+ rail_full_v14 线位（世界坐标）。
# 输出：maps/yilan/products/publish/ 下 preview_685x385.png、thumbnail_200x200.png（另存一张大图预览）
import os, json, math
import numpy as np
from PIL import Image, ImageDraw

ROOT = r"<repo>"
HM = os.path.join(ROOT, "maps", "yilan", "products", "yilan_playable.png")
RAIL = os.path.join(ROOT, "maps", "yilan", "rail", "rail_full_v14_grade.json")
OUTDIR = os.path.join(ROOT, "maps", "yilan", "products", "publish")
SEA = 92.5
PLAY = 14336.0
N = 2048                      # 渲染分辨率（正方形，覆盖可玩区）

os.makedirs(OUTDIR, exist_ok=True)

im = Image.open(HM)
if im.mode != "I;16":
    im = im.convert("I;16")
H = np.array(im, dtype=np.float64) / 65535.0 * 4096.0
h = H.shape[0]

# 统一采样到 N×N（覆盖 ±PLAY/2）
idx = (np.arange(N) + 0.5) / N * h
ii = np.clip(idx.astype(int), 0, h - 1)
Z = H[np.ix_(ii, ii)]

# 水体：低于海平面的河床（社区岸线模型把河床挖到海平面以下）
water = Z < SEA - 0.05

# 色带：陆地 92.5..300 m 绿→黄→棕→白；水：深蓝→浅蓝
def ramp(t):
    t = np.clip(t, 0, 1)
    stops = [(0.00, (58, 92, 50)), (0.25, (104, 138, 66)), (0.5, (168, 168, 96)),
             (0.72, (150, 118, 78)), (0.90, (176, 156, 130)), (1.0, (212, 204, 188))]
    out = np.zeros(t.shape + (3,), np.float64)
    for i in range(len(stops) - 1):
        a, ca = stops[i]
        b, cb = stops[i + 1]
        m = (t >= a) & (t <= b)
        f = np.zeros_like(t)
        f[m] = (t[m] - a) / (b - a)
        for c in range(3):
            out[..., c] = np.where(m, ca[c] + (cb[c] - ca[c]) * f, out[..., c])
    return out

land = ramp((Z - SEA) / (230.0 - SEA))
water_col = np.zeros((N, N, 3))
dep = np.clip((SEA - Z) / 6.0, 0, 1)                     # 越深越暗
water_col[..., 0] = 40 + 30 * (1 - dep)
water_col[..., 1] = 90 + 70 * (1 - dep)
water_col[..., 2] = 150 + 80 * (1 - dep)
rgb = np.where(water[..., None], water_col, land)

# 晕渲
gy, gx = np.gradient(Z, PLAY / N)
slope = np.arctan(np.hypot(gx, gy))
aspect = np.arctan2(-gx, gy)
az, alt = math.radians(315.0), math.radians(45.0)
shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
shade = np.clip(0.65 + 0.55 * shade, 0.35, 1.35)
rgb = np.clip(rgb * shade[..., None], 0, 255).astype(np.uint8)

img = Image.fromarray(rgb, "RGB")

# 铁路线（世界坐标 → 渲染像素）
d = json.load(open(RAIL, encoding="utf-8"))
segs = d["segments"]
pts = [(segs[0][0], segs[0][1])] + [(s[2], s[3]) for s in segs]
def px(x, z):
    return ((x + PLAY / 2) / PLAY * N, (PLAY / 2 - z) / PLAY * N)
dr = ImageDraw.Draw(img)
dr.line([px(x, z) for x, z in pts], fill=(255, 236, 120), width=max(2, N // 700))
# 依兰站
sx, sz = 3339.3, -4240.4
X, Y = px(sx, sz)
r = N // 160
dr.ellipse([X - r, Y - r, X + r, Y + r], outline=(255, 255, 255), width=max(2, N // 900))

img.save(os.path.join(OUTDIR, "render_2048.png"))

# Thumbnail 200×200（整幅俯视）
img.resize((200, 200), Image.LANCZOS).save(os.path.join(OUTDIR, "thumbnail_200x200.png"))

# Preview 685×385（16:9，取中间一条展示江口与城区）
wide = 685
sq = img.resize((wide, wide), Image.LANCZOS)
top = (wide - 385) // 2
sq.crop((0, top, wide, top + 385)).save(os.path.join(OUTDIR, "preview_685x385.png"))
print("saved:", OUTDIR)
for f in ("render_2048.png", "thumbnail_200x200.png", "preview_685x385.png"):
    p = os.path.join(OUTDIR, f)
    print("  ", f, os.path.getsize(p), "B")
