# -*- coding: utf-8 -*-
"""64 取 NASA POWER 逐日气候数据（配置驱动）。

哈尔滨已验证口径（maps/harbin/docs/哈尔滨气候-填值表.md）：
  逐日 2015–2024、点 = 地图正中心（config center）、参数 T2M/T2M_MAX/T2M_MIN/PRECTOTCORR/CLOUD_AMT。
本脚本只取数存档（files.nasa_daily），填值表由 65 从这份算出——取数与算表分离，
换坐标/换年份重跑不动算法。

自检：天数 = 区间历日数；各参数条数与 times 一致；-999（POWER 缺测哨兵）计数报出。
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config

MISSING = -999.0
DAYS_IN_YEAR = lambda y: 366 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 365


def main() -> int:
    mp = config.load()
    cl = mp.raw.get("climate") or {}
    ep = cl.get("endpoint", "https://power.larc.nasa.gov/api/temporal/daily/point")
    y0, y1 = cl.get("years", [2015, 2024])
    params = cl.get("parameters", ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "CLOUD_AMT"])
    out_p = mp.path("nasa_daily")

    lon = float(mp.raw["center"]["lon"])
    lat = float(mp.raw["center"]["lat"])
    url = (f"{ep}?parameters={','.join(params)}&community=RE"
           f"&longitude={lon}&latitude={lat}&start={y0}0101&end={y1}1231&format=JSON")
    print(f"GET {url}")
    with urllib.request.urlopen(url, timeout=300) as r:
        j = json.loads(r.read().decode("utf-8"))

    times = j.get("times") or {}
    # POWER 的顶层 times 是 {start,end} 两键字典，不是日期列表；
    # 真正的日期序列 = 参数表的键（与哈尔滨存档 nasa_daily_harbin.json 同构）。
    props = j["properties"]["parameter"]
    n_days = len(props.get(params[0]) or {})
    want = sum(DAYS_IN_YEAR(y) for y in range(y0, y1 + 1))
    print(f"times 字段 {len(times)} 键（start/end）；参数表 {n_days} 天（历日应为 {want}）")
    if n_days != want:
        print("  !! 天数不符——POWER 返回不完整，不存档")
        return 1

    bad = 0
    for p in params:
        d = props.get(p) or {}
        n_miss = sum(1 for v in d.values() if abs(float(v) - MISSING) < 1e-6)
        bad += n_miss
        print(f"  {p:<12} {len(d)} 条  缺测 {n_miss}")
        if len(d) != n_days:
            print("  !! 参数条数与首参数不一致，不存档")
            return 1
    if bad:
        print(f"  !! 共 {bad} 个缺测哨兵值——如实存档但在 65 里按缺测剔除")

    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(j, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n[save] {out_p}  {out_p.stat().st_size/1024:.0f} KB")

    prec = props.get("PRECTOTCORR") or {}
    tot = sum(float(v) for v in prec.values() if abs(float(v) - MISSING) > 1e-6)
    print(f"年降水均值（逐日合计/年数）：{tot / (y1 - y0 + 1):.0f} mm（NASA 逐日口径，绝对值别当准数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
