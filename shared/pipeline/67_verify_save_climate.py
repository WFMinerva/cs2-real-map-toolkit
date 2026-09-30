# -*- coding: utf-8 -*-
"""67 读存档 MapMetadata 自校验气候填值（配置驱动，只读，不动存档）。

.cok = ZIP；其中 <stem>.MapMetadata 是明文 JSON（utf-8-sig），无需 zstd 解帧。
校验项（哈尔滨已验证口径，见 maps/harbin/docs/哈尔滨气候-填值表.md「校验方法」）：
  longitude / latitude  = config center
  temperatureRange      派生包围 {min(各季夜温), max(各季日温)}（游戏按偏差外扩；
                        依兰 base7 实测锚 {−28.33, 26.76}）
  cloudiness / precipitation 脱离预设默认 0.239953 / 0.0969261
另可用 --diff <stem2> 并排两存档的气候相关键（看这一轮到底改了什么）。

用法：67_verify_save_climate.py --map yilan --save yilan_base7 [--diff yilan_base6]
"""
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib.util

from cs2map import config

_spec = importlib.util.spec_from_file_location(
    "ct65", Path(__file__).resolve().parent / "65_climate_table.py")
ct65 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct65)

DEFAULT_CLOUDINESS = 0.239953
DEFAULT_PRECIP = 0.0969261
CLIMATE_KEY_HINTS = ("climate", "temperature", "cloud", "precip", "longitude", "latitude",
                     "freez", "sun", "aurora", "turbul", "weather", "season")


def find_all(obj, pred, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if pred(k):
                out.append((p, v))
            out += find_all(v, pred, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += find_all(v, pred, f"{path}[{i}]")
    return out


def read_meta(stem):
    g = config.game()
    p = Path(g["maps_dir"]) / g["steamid"] / f"{stem}.cok"
    with zipfile.ZipFile(p) as z:
        names = [n for n in z.namelist() if n.endswith(".MapMetadata")]
        if not names:
            raise SystemExit(f"!! {stem}.cok 里没有 MapMetadata 条目")
        return json.loads(z.read(names[0]).decode("utf-8-sig")), p


def two_floats(v):
    if isinstance(v, dict):
        nums = [x for x in v.values() if isinstance(x, (int, float))]
        return nums[:2] if len(nums) >= 2 else None
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return [float(v[0]), float(v[1])]
    return None


def main() -> int:
    argv = sys.argv[1:]
    mp = config.load()
    stem = argv[argv.index("--save") + 1] if "--save" in argv else None
    diff = argv[argv.index("--diff") + 1] if "--diff" in argv else None
    if not stem:
        raise SystemExit("用法：--map <id> --save <stem> [--diff <stem2>]")

    meta, p = read_meta(stem)
    c = ct65.compute(mp)
    rows = c["rows"]
    exp_lo, exp_hi = min(r["night"] for r in rows), max(r["day"] for r in rows)

    print(f"存档：{p}")
    lon = find_all(meta, lambda k: k.lower() == "longitude")
    lat = find_all(meta, lambda k: k.lower() == "latitude")
    tr = find_all(meta, lambda k: k.lower() == "temperaturerange")
    cl = find_all(meta, lambda k: k.lower() == "cloudiness")
    pr = find_all(meta, lambda k: k.lower() == "precipitation")

    ok = True

    def check(label, cond, detail):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}：{detail}")

    if lon:
        v = float(lon[0][1])
        check("longitude", abs(v - c["lon"]) < 0.001, f"{v} vs 期望 {c['lon']}")
    else:
        check("longitude", False, "MapMetadata 里找不到 longitude 键")
    if lat:
        v = float(lat[0][1])
        check("latitude", abs(v - c["lat"]) < 0.001, f"{v} vs 期望 {c['lat']}")
    else:
        check("latitude", False, "找不到 latitude 键")
    if tr:
        tf = two_floats(tr[0][1])
        if tf:
            # temperatureRange 是游戏由「季均温+偏差」外扩的派生值（依兰 base7 实测
            # {−28.33, 26.76} = 季范围 {−18.4, 23.9} 外扩约一个偏差宽；哈尔滨文档口径亦只写「约」）。
            # 校验 = 派生包围：比季范围宽、不离谱、且脱离预设默认。
            lo_ok = (tf[0] < exp_lo - 0.5) and (tf[0] > exp_lo - 20)
            hi_ok = (tf[1] > exp_hi + 0.5) and (tf[1] < exp_hi + 20)
            check("temperatureRange", lo_ok and hi_ok,
                  f"{{{tf[0]:.2f}, {tf[1]:.2f}}} 派生包围季范围 {{{exp_lo:.1f}, {exp_hi:.1f}}}"
                  f"（外扩 {exp_lo - tf[0]:.1f} / {tf[1] - exp_hi:.1f}，游戏派生口径；预设默认是正窄范围，不可能过此检）")
        else:
            check("temperatureRange", False, f"结构不认识：{tr[0][1]}")
    else:
        check("temperatureRange", False, "找不到 temperatureRange 键")
    if cl:
        v = float(cl[0][1]) if not isinstance(cl[0][1], (list, dict)) else None
        if v is None:
            vals = two_floats(cl[0][1]) or ([float(x) for x in cl[0][1]] if isinstance(cl[0][1], list) else None)
            changed = vals is not None and any(abs(x - DEFAULT_CLOUDINESS) > 1e-3 for x in vals)
            check("cloudiness", changed, f"{vals} 脱离默认 {DEFAULT_CLOUDINESS}")
        else:
            check("cloudiness", abs(v - DEFAULT_CLOUDINESS) > 1e-3,
                  f"{v} vs 预设默认 {DEFAULT_CLOUDINESS}")
    else:
        check("cloudiness", False, "找不到 cloudiness 键")
    if pr:
        v = pr[0][1]
        if isinstance(v, (int, float)):
            check("precipitation", abs(float(v) - DEFAULT_PRECIP) > 1e-3,
                  f"{v} vs 预设默认 {DEFAULT_PRECIP}")
        else:
            vals = two_floats(v) or ([float(x) for x in v] if isinstance(v, list) else None)
            changed = vals is not None and any(abs(x - DEFAULT_PRECIP) > 1e-3 for x in vals)
            check("precipitation", changed, f"{vals} 脱离默认 {DEFAULT_PRECIP}")
    else:
        check("precipitation", False, "找不到 precipitation 键")

    if diff:
        meta2, _ = read_meta(diff)
        a = dict(find_all(meta, lambda k: any(h in k.lower() for h in CLIMATE_KEY_HINTS)))
        b = dict(find_all(meta2, lambda k: any(h in k.lower() for h in CLIMATE_KEY_HINTS)))
        print(f"\n=== 气候相关键并排（{stem} vs {diff}）===")
        keys = sorted(set(a) | set(b))
        n_diff = 0
        for k in keys:
            va, vb = a.get(k), b.get(k)
            if va != vb:
                n_diff += 1
                print(f"  {k}\n    {stem}: {va}\n    {diff}: {vb}")
        print(f"  共 {n_diff} 个气候相关键不同")

    print("\n结论：" + ("气候已写进存档 ✓" if ok else "气候校验未全过 ✗（看上面 FAIL 行）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
