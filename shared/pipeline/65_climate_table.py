# -*- coding: utf-8 -*-
"""65 由 NASA POWER 逐日数据算 CS2 气候填值表（配置驱动，哈尔滨已验证口径）。

口径（maps/harbin/docs/哈尔滨气候-填值表.md；官方 Map Creation: Climate，Paradox 认证 1.6.0f1）：
  四季 = 日历年四季（Start Time 0.000/0.250/0.500/0.750，游戏自己的划分）
  平均温度 夜间/日间 = 该季日最低(T2M_MIN)/日最高(T2M_MAX)的均值
  温度偏差 夜间/日间 = 该季日值的 10–90 百分位半宽（游戏提示口径）
  云朵概率 = CLOUD_AMT ≥ climate.cloud_day_pct(默认50)% 的天数占比
  云朵数量 = 该季 CLOUD_AMT 均值(%)；云朵数量偏差 = 其 10–90 半宽
  降水概率 = PRECTOTCORR ≥ climate.prec_day_mm(默认1 mm) 的天数占比
  降水量   = 多年平均的季降水 mm ÷ 6（锚定刻度：比值准确、绝对值别当准数；
             逐日档跨 10 年，季合计必须先除年数——哈尔滨回归锚即此口径）
  降水量偏差 = climate.prec_deviation(默认30)；天气多样性 = climate.turbulence(默认0.2)；极光概率 0
  面板顶部：纬度/经度 = config center；最高太阳抬升角度 = 90 − lat + 23.44；
            太阳抬升初始夹角 45.000 不动；结冰温度 0.000（预设的 0.023 要改）

回归锚：--map harbin 时与填值表公布值比对（均值/概率/降水量容差 0.05，偏差类 0.15），
不过 = 口径漂移，禁止出表。锚值抄自 maps/harbin/docs/哈尔滨气候-填值表.md（仅自检用）。
输出：config files.climate_table 存在才写 md；否则只打印（供回归比对）。
66_make_climate_doc.py 复用本模块 compute() 出打印对照表（同口径单源）。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config

SEASON_MONTHS = [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]
START_TIMES = [0.000, 0.250, 0.500, 0.750]
MONTH_LABEL = ["1–3月", "4–6月", "7–9月", "10–12月"]

# 哈尔滨回归锚（抄自 maps/harbin/docs/哈尔滨气候-填值表.md 照抄表；仅 --map harbin 自检用）
HARBIN_EXPECT = {
    "night":   [-17.2, 7.3, 15.3, -10.8],
    "day":     [-5.7, 20.0, 25.4, -0.7],
    "dnight":  [12.2, 8.9, 6.7, 13.0],
    "dday":    [11.9, 8.9, 5.5, 14.7],
    "cchance": [37.2, 67.5, 66.4, 42.7],
    "camount": [42.9, 62.9, 62.0, 46.4],
    "cdev":    [34.2, 37.5, 40.3, 38.8],
    "pchance": [7.5, 37.9, 48.0, 14.3],
    "pamount": [4.1, 38.6, 64.2, 9.8],
}
TOL_TIGHT, TOL_DEV = 0.05, 0.15


def half_width(x):
    p10, p90 = np.percentile(x, [10, 90])
    return (p90 - p10) / 2.0


def compute(mp):
    """从 files.nasa_daily 算全表。返回 dict（rows/tot/lat/lon/max_sun/各默认参数/天数）。"""
    cl = mp.raw.get("climate") or {}
    prec_day = float(cl.get("prec_day_mm", 1.0))
    cloud_day = float(cl.get("cloud_day_pct", 50.0))
    prec_dev = float(cl.get("prec_deviation", 30.0))
    turb = float(cl.get("turbulence", 0.2))

    j = json.loads(mp.path("nasa_daily").read_text(encoding="utf-8"))
    props = j["properties"]["parameter"]
    dates = sorted(props["T2M"].keys())
    mon = np.array([int(d[4:6]) for d in dates])
    yr = np.array([int(d[:4]) for d in dates])
    tmin = np.array([float(props["T2M_MIN"][d]) for d in dates])
    tmax = np.array([float(props["T2M_MAX"][d]) for d in dates])
    prec = np.array([float(props["PRECTOTCORR"][d]) for d in dates])
    cld = np.array([float(props["CLOUD_AMT"][d]) for d in dates])
    miss = (np.abs(tmin + 999) < 1e-6) | (np.abs(tmax + 999) < 1e-6) \
        | (np.abs(prec + 999) < 1e-6) | (np.abs(cld + 999) < 1e-6)
    n_miss = int(miss.sum())
    if miss.any():
        keep = ~miss
        mon, yr, tmin, tmax, prec, cld = (mon[keep], yr[keep], tmin[keep], tmax[keep],
                                          prec[keep], cld[keep])
    n_years = float(len(np.unique(yr)))

    rows = []
    for months, st, lab in zip(SEASON_MONTHS, START_TIMES, MONTH_LABEL):
        m = np.isin(mon, months)
        p_mm = float(prec[m].sum()) / n_years
        rows.append(dict(
            label=lab, start=st, ndays=int(m.sum()),
            night=float(tmin[m].mean()), day=float(tmax[m].mean()),
            dnight=float(half_width(tmin[m])), dday=float(half_width(tmax[m])),
            cchance=float((cld[m] >= cloud_day).mean() * 100),
            camount=float(cld[m].mean()), cdev=float(half_width(cld[m])),
            pchance=float((prec[m] >= prec_day).mean() * 100),
            pmm=p_mm, pamount=p_mm / 6.0,
        ))
    lat = float(mp.raw["center"]["lat"])
    lon = float(mp.raw["center"]["lon"])
    return dict(rows=rows, tot=sum(r["pmm"] for r in rows), lat=lat, lon=lon,
                max_sun=90.0 - lat + 23.44, prec_day=prec_day, cloud_day=cloud_day,
                prec_dev=prec_dev, turb=turb, n_days=len(mon), n_miss=n_miss,
                n_years=n_years, years=cl.get("years", [2015, 2024]))


def main() -> int:
    mp = config.load()
    c = compute(mp)
    rows = c["rows"]
    print(f"逐日用 {c['n_days']} 天（缺测剔除 {c['n_miss']}），跨 {c['n_years']:.0f} 年")

    # ---- 回归锚（仅 harbin）
    if mp.raw.get("map_id") == "harbin":
        fails = []
        for key, tol in (("night", TOL_TIGHT), ("day", TOL_TIGHT), ("dnight", TOL_DEV),
                         ("dday", TOL_DEV), ("cchance", TOL_TIGHT), ("camount", TOL_TIGHT),
                         ("cdev", TOL_DEV), ("pchance", TOL_TIGHT), ("pamount", TOL_TIGHT)):
            for i, r in enumerate(rows):
                d = abs(r[key] - HARBIN_EXPECT[key][i])
                if d > tol:
                    fails.append(f"{key}[{i}] 算 {r[key]:.2f} vs 锚 {HARBIN_EXPECT[key][i]} (差 {d:.2f})")
        if fails:
            print("回归失败：")
            print("\n".join("  " + f for f in fails))
            return 1
        print("回归通过：9 项 × 4 季全在容差内（均值/概率/降水量 ±0.05，偏差类 ±0.15）")

    print(f"\n=== 照抄表（{mp.raw.get('map_id')}）===")
    print(f"{'字段':<12}" + "".join(f"{r['label']:>10}" for r in rows))
    for key, lab, fmt in (("start", "Start Time", "{:.3f}"), ("night", "平均温度 夜间", "{:.1f}"),
                          ("day", "平均温度 日间", "{:.1f}"), ("dnight", "温度偏差 夜间", "{:.1f}"),
                          ("dday", "温度偏差 日间", "{:.1f}"), ("cchance", "云朵概率", "{:.1f}"),
                          ("camount", "云朵数量", "{:.1f}"), ("cdev", "云朵数量偏差", "{:.1f}"),
                          ("pchance", "降水概率", "{:.1f}"), ("pamount", "降水量", "{:.1f}")):
        print(f"{lab:<12}" + "".join(f"{fmt.format(r[key]):>10}" for r in rows))
    print(f"{'降水量偏差':<11}" + "".join(f"{c['prec_dev']:>10.0f}" for _ in rows))
    print(f"{'天气多样性':<11}" + "".join(f"{c['turb']:>10.3f}" for _ in rows))
    print(f"{'极光概率':<12}" + "".join(f"{0:>10}" for _ in rows))
    print(f"\n顶部：纬度 {c['lat']}  经度 {c['lon']}  最高太阳抬升角度 {c['max_sun']:.1f}  "
          f"太阳抬升初始夹角 45.000  结冰温度 0.000")
    print(f"年降水 {c['tot']:.0f} mm；各季占比 "
          + " / ".join(f"{r['pmm']/c['tot']*100:.1f}%" for r in rows))

    out_key = (mp.raw.get("files") or {}).get("climate_table")
    if not out_key:
        print("\n（config 无 files.climate_table，只打印不写盘）")
        return 0
    out_p = mp.path("climate_table")
    name = mp.raw.get("display_name") or mp.raw.get("map_id")
    L = [f"# {name}气候 —— 四季填值表", "",
         f"来源：**NASA POWER 逐日数据 {c['years'][0]}–{c['years'][1]}**，"
         f"坐标 ({c['lat']}N, {c['lon']}E) = 地图正中心。",
         "季节边界按**游戏自己的划分**（`Start Time` 是 0 到 1 的年内比例，四个季节 = 日历年四季）。", "",
         "---", "", "## 照抄表", "",
         "| 字段 | 部分0 | 部分1 | 部分2 | 部分3 |", "|---|---|---|---|---|",
         "| **Start Time** | **0.000** | **0.250** | **0.500** | **0.750** |",
         "| 对应月份 | " + " | ".join(r["label"] for r in rows) + " |"]
    for key, lab, fmt in (("night", "**平均温度 夜间**", "{:.1f}"), ("day", "**平均温度 日间**", "{:.1f}"),
                          ("dnight", "温度偏差 夜间", "{:.1f}"), ("dday", "温度偏差 日间", "{:.1f}"),
                          ("cchance", "云朵概率", "{:.1f}"), ("camount", "云朵数量", "{:.1f}"),
                          ("cdev", "云朵数量偏差", "{:.1f}"), ("pchance", "降水概率", "{:.1f}"),
                          ("pamount", "降水量", "{:.1f}")):
        L.append(f"| {lab} | " + " | ".join(f"**{fmt.format(r[key])}**" if key in
                   ("night", "day", "pamount") else fmt.format(r[key]) for r in rows) + " |")
    L += [f"| 降水量偏差 | {c['prec_dev']:.0f} | {c['prec_dev']:.0f} | {c['prec_dev']:.0f} | {c['prec_dev']:.0f} |",
          "| `Prefab` | 无 | 无 | 无 | 无 |",
          "| **极光概率** | **0** | **0** | **0** | **0** |",
          f"| **天气多样性** | {c['turb']:.3f} | {c['turb']:.3f} | {c['turb']:.3f} | {c['turb']:.3f} |", "",
          "**气候面板顶部（不是季节里）还有：**", "",
          "| 字段 | 值 |", "|---|---|",
          f"| 纬度 | **{c['lat']}** |", f"| 经度 | **{c['lon']:.3f}** |",
          f"| 最高太阳抬升角度 | **{c['max_sun']:.1f}**（= 90 − {c['lat']} + 23.44，夏至正午太阳高度） |",
          "| 太阳抬升初始夹角 | 45.000 |",
          "| **结冰温度** | **0.000**（预设带来的 0.023 要改掉） |",
          "| 默认天气 | `DefaultContinental` 的 6 项，保持不动 |", "",
          "---", "", "## 口径说明", "",
          "| 字段 | 算法 |", "|---|---|",
          "| 平均温度 夜间/日间 | 该季**日最低/日最高气温的均值** |",
          "| 温度偏差 / 云朵数量偏差 | 日值的 **10–90 百分位半宽**，与游戏文档口径一致 |",
          "| 云朵数量 | 平均云量 **%**，直接对应 |",
          f"| 云朵概率 | 云量 ≥{c['cloud_day']:.0f}% 的天数占比 |",
          f"| 降水概率 | **有降水的天数占比**（≥{c['prec_day']:.0f} mm/日） |",
          "| 降水量 | 多年平均季降水 mm ÷ 6 |", "",
          "## 原始统计（未换算）", "",
          "| 季节 | 夜温 | 日温 | 季降水 mm | 占全年 |", "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['label']} | {r['night']:.1f} | {r['day']:.1f} | {r['pmm']:.0f} | "
                 f"{r['pmm']/c['tot']*100:.1f}% |")
    L += ["", f"全年降水 **{c['tot']:.0f} mm**（NASA 逐日口径的多年平均）。", "",
          "## 不确定处（如实记录，沿用哈尔滨口径）", "",
          "1. **`降水量` 的绝对刻度是锚定的**（季 mm ÷ 6）。真实**比值**准确；若游戏里雨显得过猛，"
          "四个数同比例除以一个系数即可。",
          "2. NASA 逐日年降水通常比站点实测偏高（哈尔滨逐日 700 mm vs 站点 530–570 mm）。"
          "**比值可用，绝对值别当准数。**",
          "3. **`云朵数量偏差` 按 10–90 百分位算出通常 30–40，预设默认只有 20。** 若天气显得太跳，这一行砍一半。",
          "", "## 校验方法（我自己读文件，不用机主判断）", "",
          "机主填完存盘后读存档 `MapMetadata`：", "",
          f"- `longitude` 应变成 **{c['lon']:.3f}**",
          f"- `temperatureRange` 应约 **{{{min(r['night'] for r in rows):.1f}, "
          f"{max(r['day'] for r in rows):.1f}}}**",
          "- `cloudiness` / `precipitation` 应脱离预设默认值", ""]
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("\n".join(L), encoding="utf-8")
    print(f"\n[save] {out_p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
