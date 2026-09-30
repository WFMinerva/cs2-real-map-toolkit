# -*- coding: utf-8 -*-
"""verify_harbin_reproduction.py —— 迁移验证：从新工作区复跑 harbin 全链，与旧产物逐一比对。

原则：
  - 逐字节一致 = PASS（最强的迁移验证）
  - 已知会差异的产物（预览图标注文字、build_meta 的陈旧 world 块、JSON 的 generated_by）
    单独判定并列出差异原因
  - 旧工作区全程只读
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

NEW = Path(r"<repo>")
OLD = Path(r"<old-workspace>")
PY = NEW / ".venv/Scripts/python.exe"
PIPE = NEW / "shared/pipeline"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


results = []


def run(script):
    print(f"\n===== 运行 {script.name} =====")
    r = subprocess.run([str(PY), str(script)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = "\n".join((r.stdout or "").splitlines()[-6:])
    print(tail)
    if r.returncode != 0:
        print(r.stderr[-1500:] if r.stderr else "(无 stderr)")
    return r.returncode


def cmp_bytes(label, new_p, old_p, expect="same", note=""):
    if not new_p.exists():
        results.append((label, "MISSING", str(new_p)))
        return
    if not old_p.exists():
        results.append((label, "NO-REFERENCE", f"旧参照不存在: {old_p}"))
        return
    same = sha(new_p) == sha(old_p)
    if same:
        results.append((label, "PASS- identical", note))
    elif expect == "same":
        results.append((label, "DIFF", note or "字节不一致（未预期，需要查明）"))
    else:
        results.append((label, "DIFF-预期内", note))


def cmp_json_subset(label, new_p, old_p, exclude_keys=(), note=""):
    a = json.loads(new_p.read_text(encoding="utf-8"))
    b = json.loads(old_p.read_text(encoding="utf-8"))
    for k in exclude_keys:
        a.pop(k, None)
        b.pop(k, None)
    same = a == b
    results.append((label, "PASS-除排除键外一致" if same else "DIFF",
                    note + ("" if same else f"（排除键: {','.join(exclude_keys)}）")))


def cmp_npz(label, new_p, old_p):
    import numpy as np
    a = np.load(new_p)
    b = np.load(old_p)
    dem_same = (a["dem"] == b["dem"]).all()
    mask_same = (a["mask"] == b["mask"]).all()
    tr_same = (a["transform"] == b["transform"]).all()
    ok = dem_same and mask_same and tr_same
    results.append((label, "PASS-数组逐位一致" if ok else "DIFF",
                    f"dem={dem_same} mask={mask_same} transform={tr_same}"))


def main() -> int:
    print("迁移验证：新工作区复跑 harbin 管线 vs 旧工作区已验证产物")

    steps = [
        (PIPE / "12_extract_water.py", True),
        (PIPE / "14_build_mask.py", True),
        (PIPE / "16_build_heightmap.py", True),
        (PIPE / "20_preview_heightmap.py", True),
        (PIPE / "24_river_edges.py", True),
        (PIPE / "26_trim_roads.py", True),
        (PIPE / "28_export_bridge_lift.py", True),
        (PIPE / "18_build_world.py", False),  # 用 --no-deploy 需要参数，单独处理
    ]
    for script, _ in steps[:-1]:
        if run(script) != 0:
            results.append((script.name, "RUN-FAIL", "返回码非 0"))

    # 世界图（带 --no-deploy，本轮不写游戏目录）
    print("\n===== 运行 18_build_world.py --no-deploy =====")
    r = subprocess.run([str(PY), str(PIPE / "18_build_world.py"), "--no-deploy"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("\n".join((r.stdout or "").splitlines()[-6:]))
    if r.returncode != 0:
        results.append(("18_build_world", "RUN-FAIL", (r.stderr or "")[-800:]))

    # ---- 比对 ----
    cmp_bytes("water_in_box.geojson", NEW / "maps/harbin/data/water_in_box.geojson",
              OLD / "data/water_in_box.geojson")
    cmp_npz("mask_playable.npz", NEW / "maps/harbin/data/mask_playable.npz",
            OLD / "data/mask_playable.npz")
    cmp_bytes("harbin_playable.png", NEW / "maps/harbin/products/harbin_playable.png",
              OLD / "out/harbin_playable.png",
              note="核心交付物（18 社区岸线模型），必须逐字节一致")
    cmp_bytes("preview_playable.png", NEW / "maps/harbin/products/preview_playable.png",
              OLD / "out/preview_playable.png", expect="diff",
              note="预览为 18 岸线风格（旧文件是已废弃的 06 风格），预期不同")
    cmp_bytes("build_meta.json", NEW / "maps/harbin/products/build_meta.json",
              OLD / "out/build_meta.json", expect="diff",
              note="新 meta 记录 18 岸线模型真实参数；旧 meta 是 06/09 时代陈旧产物，不可比")
    cmp_bytes("verify_hillshade.png", NEW / "maps/harbin/products/verify_hillshade.png",
              OLD / "out/verify_hillshade.png", expect="diff", note="标注文字变化，预期不同")
    cmp_bytes("river_edges.json", NEW / "maps/harbin/products/river_edges.json",
              OLD / "out/river_edges.json", expect="diff",
              note="旧值是 06 统一8m时代口径(bed<107)；现行 18 模型用 海平面−2.0。"
                   "过口经纬度应一致，宽度不同。旧原件留档 products/river_edges_8m历史参照.json")
    cmp_bytes("overpass_roads_trimmed.osm", NEW / "maps/harbin/data/roads/overpass_roads_trimmed.osm",
              OLD / "out/调研-Earth2Cities/overpass_roads_trimmed.osm",
              note="给 Earth2Cities 的路网；generator 串对 harbin 同名")
    cmp_json_subset("bridges_lift(regen)", NEW / "maps/harbin/bridge/bridges_lift.regen_候选.json",
                    OLD / "out/mod_input/bridges_lift.json", exclude_keys=("generated_by",),
                    note="28 重新生成版 vs 游戏验证过的旧版；须除 generated_by 外全等。"
                         "bridge/bridges_lift.json 保持旧版不动（游戏验证锚点）")
    cmp_bytes("harbin_world.png", NEW / "maps/harbin/products/harbin_world.png",
              OLD / "out/harbin_world.png", note="57 版世界图，必须逐字节一致")

    print("\n================ 迁移验证结果 ================")
    bad = 0
    for label, verdict, note in results:
        flag = "!!" if verdict in ("DIFF", "RUN-FAIL", "MISSING") else "  "
        if flag == "!!":
            bad += 1
        print(f"{flag} {label:<28} {verdict:<24} {note}")
    print(f"共 {len(results)} 项，{bad} 项需关注")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
