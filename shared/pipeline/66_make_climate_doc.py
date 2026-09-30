# -*- coding: utf-8 -*-
"""66 生成「气候填值对照表」DOCX（供打印后对着游戏面板逐格填写，配置驱动）。

移植自旧区 101_make_climate_doc.py（哈尔滨已验证版式），数值单源 = 65.compute()
（NASA POWER 逐日口径；65 --map harbin 回归锚锁定）。
版式：A4 四页 = ①面板顶部+四季总览 ②③逐季填写卡（含「已填」勾列） ④已做/还差/口径/不确定处。
运行环境：需 python-docx（用运行时 bundled Python 跑本脚本，.venv 无此依赖）。
输出：config files.climate_doc（docx）；PDF 由 bundled LibreOffice Kit 转换（交付件）。
"""
import importlib.util
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2map import config

_spec = importlib.util.spec_from_file_location(
    "ct65", Path(__file__).resolve().parent / "65_climate_table.py")
ct65 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct65)

CN = "Microsoft YaHei"


def set_font(style, name=CN, size=None, bold=None):
    style.font.name = name
    if size is not None:
        style.font.size = Pt(size)
    if bold is not None:
        style.font.bold = bold
    rpr = style.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rf)
    rf.set(qn("w:ascii"), name)
    rf.set(qn("w:hAnsi"), name)
    rf.set(qn("w:eastAsia"), name)


def run_font(run, name=CN, size=None, bold=None):
    run.font.name = name
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def para(doc, text, size=9.5, bold=False, space_after=2, color=None, align=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    if align:
        p.alignment = align
    r = p.add_run(text)
    run_font(r, size=size, bold=bold)
    if color:
        r.font.color.rgb = color
    return p


def table(doc, rows, widths=None, header=True, size=9.5, center_cols=None):
    t = doc.add_table(rows=0, cols=len(rows[0]))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    if center_cols is None:
        center_cols = set(range(1, len(rows[0])))
    for i, row in enumerate(rows):
        cells = t.add_row().cells
        for j, v in enumerate(row):
            cells[j].text = ""
            p = cells[j].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.space_before = Pt(0)
            r = p.add_run(str(v))
            run_font(r, size=size, bold=(header and i == 0))
            if j in center_cols:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if widths:
        for w, col in zip(widths, t.columns):
            for cell in col.cells:
                cell.width = Cm(w)
    return t


def season_table(doc, s, c, idx):
    para(doc, f"部分 {idx}　　Start Time = {s['start']:.3f}　　{s['label']}",
         size=11, bold=True, space_after=3)
    rows = [["字段", "填入", "已填"]]
    for label, val, keep in [
        ("Prefab", "无（不动）", True),
        ("Start Time", f"{s['start']:.3f}", False),
        ("平均温度　夜间", f"{s['night']:.1f}", False),
        ("平均温度　日间", f"{s['day']:.1f}", False),
        ("温度偏差　夜间", f"{s['dnight']:.1f}", False),
        ("温度偏差　日间", f"{s['dday']:.1f}", False),
        ("云朵概率", f"{s['cchance']:.1f}", False),
        ("云朵数量", f"{s['camount']:.1f}", False),
        ("云朵数量偏差", f"{s['cdev']:.1f}", False),
        ("降水概率", f"{s['pchance']:.1f}", False),
        ("降水量", f"{s['pamount']:.1f}", False),
        ("降水量偏差", f"{c['prec_dev']:.0f}", True),
        ("天气多样性", f"{c['turb']:.3f}", True),
        ("极光强度", "1.000", True),
        ("极光概率", "0", False),
    ]:
        rows.append([label + ("（不动）" if keep else ""), val, "☐"])
    table(doc, rows, widths=[6.2, 4.4, 2.0], center_cols={1, 2})


def main() -> int:
    mp = config.load()
    out_key = (mp.raw.get("files") or {}).get("climate_doc")
    if not out_key:
        print("config 无 files.climate_doc，不生成")
        return 1
    out_p = mp.path("climate_doc")
    c = ct65.compute(mp)
    rows = c["rows"]
    name = mp.raw.get("display_name") or mp.raw.get("map_id")

    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(1.4)
    sec.bottom_margin = Cm(1.2)
    sec.left_margin = Cm(1.5)
    sec.right_margin = Cm(1.5)
    set_font(doc.styles["Normal"], size=9.5)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(f"{name}地图 · 气候填值对照表")
    run_font(r, size=17, bold=True)
    para(doc, "对着游戏里「气候检视器」逐格填。打印后可直接在「已填」列打勾。",
         size=9, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=8)

    para(doc, "① 面板顶部（不在「季节」里面）", size=12, bold=True, space_after=4)
    table(doc, [
        ["字段", "填入", "说明"],
        ["气候预设", "任选一个（如 Continental 系）", "只是起点，填完季节后它就代表依兰"],
        ["名称", "yilan", "可自定义"],
        ["纬度", f"{c['lat']}", "地图正中心"],
        ["经度", f"{c['lon']:.3f}", "★ 预设值不对，要改"],
        ["最高太阳抬升角度", f"{c['max_sun']:.1f}", "= 90 − 纬度 + 23.44（夏至正午太阳高度）"],
        ["太阳抬升初始夹角", "45.000", "保持不动"],
        ["结冰温度", "0.000", "★ 预设带来的 0.023 要改"],
        ["默认天气", "DefaultContinental 的 6 项", "不动"],
    ], widths=[4.0, 6.4, 6.2], center_cols={1})

    doc.add_paragraph()
    para(doc, "② 四季总览（明细见后两页）", size=12, bold=True, space_after=4)
    table(doc, [
        ["部分", "Start Time", "月份", "夜温 °C", "日温 °C", "云朵数量", "降水概率", "降水量"],
        *[["部分 %d" % i, f"{r['start']:.3f}", r["label"], f"{r['night']:.1f}", f"{r['day']:.1f}",
           f"{r['camount']:.1f}", f"{r['pchance']:.1f}", f"{r['pamount']:.1f}"]
          for i, r in enumerate(rows)],
    ], widths=[2.0, 2.3, 2.2, 2.2, 2.2, 2.2, 2.2, 2.2])
    para(doc, "提示：每个「部分」右上角有复制按钮 → 先填完部分 0，再复制三次改数，能省一大半功夫。",
         size=8.5, space_after=0, color=RGBColor(0x44, 0x44, 0x44))
    para(doc, f"数据源：NASA POWER 逐日 {c['years'][0]}–{c['years'][1]}，"
              f"坐标 ({c['lat']}N, {c['lon']}E)；年降水均值 {c['tot']:.0f} mm，"
              + "夏季占 %.1f%%。" % (rows[2]["pmm"] / c["tot"] * 100),
         size=8.5, space_after=0, color=RGBColor(0x44, 0x44, 0x44))

    doc.add_page_break()
    para(doc, "③ 逐季填写卡", size=13, bold=True, space_after=6)
    season_table(doc, rows[0], c, 0)
    doc.add_paragraph()
    season_table(doc, rows[1], c, 1)

    doc.add_page_break()
    season_table(doc, rows[2], c, 2)
    doc.add_paragraph()
    season_table(doc, rows[3], c, 3)

    doc.add_page_break()
    para(doc, "④ 已经做好的（不用管）", size=12, bold=True, space_after=4)
    table(doc, [
        ["项", "状态"],
        ["海平面高度", "92.5 m（DEM 内估计值，候选参数）"],
        ["可玩区/世界区高度图", "yilan_playable.png / yilan_world.png 已导入"],
        ["道路", "路网 v3（309 way，干线连边成对外连接）"],
        ["北桥抬升", "yilan_base6 全桥出水（机主目检满意）"],
        ["资源图", "地下水/矿石/石油 已备好待导；肥沃押后（OSM 无 farmland 面）"],
    ], widths=[4.5, 12.1], center_cols={1})

    doc.add_paragraph()
    para(doc, "⑤ 还差的", size=12, bold=True, space_after=4)
    table(doc, [
        ["项", "现状", "怎么办"],
        ["气候", "预设值", "本表填完存盘即完成"],
        ["流动性水源", "0 处", "押后（机主指示）；届时按 river_edges.json 两主段加"],
        ["铁路", "未铺", "机主编辑器手铺 A 方案（依兰站 46.27681,129.60036）"],
    ], widths=[3.0, 2.6, 11.0], center_cols={1})

    doc.add_paragraph()
    para(doc, "⑥ 口径与不确定处", size=12, bold=True, space_after=4)
    table(doc, [
        ["字段", "怎么算的"],
        ["平均温度 夜间 / 日间", "该季「日最低气温 / 日最高气温」的均值"],
        ["温度偏差、云朵数量偏差", "日值的 10–90 百分位半宽（游戏提示口径）"],
        ["云朵数量", "平均云量 %，直接对应"],
        ["云朵概率", f"云量 ≥{c['cloud_day']:.0f}% 的天数占比"],
        ["降水概率", f"有降水的天数占比（≥{c['prec_day']:.0f} mm/日）"],
        ["降水量", "多年平均季降水 mm ÷ 6（锚定刻度：比值准、绝对值别当准数）"],
        ["云朵数量偏差偏大", "算出 30–40 而预设默认 20；天气太跳就把这行砍一半"],
    ], widths=[5.0, 11.6], center_cols=set())
    para(doc, "校验：填完存盘后由 agent 读存档 MapMetadata（经度/温度范围/云/降水），不用机主判断。",
         size=8.5, space_after=0, color=RGBColor(0x44, 0x44, 0x44))

    out_p.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_p)
    print(f"[save] {out_p}  {out_p.stat().st_size/1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
