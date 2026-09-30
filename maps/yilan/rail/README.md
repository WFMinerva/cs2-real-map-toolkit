# yilan 铁路轮 · 文件索引与验收参数

> 状态（2026-09-30）：**铁路轮与车站轮均已完成，并已随依兰图 v1 发布**
> （PDX Mods id **161349**，发布版 `yilan.cok`）。要点：
> - 线位 v8 + 剖面：两端对外连接 `OutsideConnection=True`，对账 240/242 节点 |Δ|≤1 m
>   （唯一偏差 = 牡丹江桥 +9.93 m 鼓包，**未修，随 v1 发布**）；
> - **车站轮定案（v32）**：站内子网定义 `m_SubPrefab` 必须为 **`Entity.Null`** ⇒ 站内 `SubNet`
>   由 0 边变 **Edge 19 / Node 33**；⑦ 用"站内跨距裁剪 + `CoursePos.m_Entity` 挂节点"接线，
>   实测裁 3 条课程 / 挂 4 个端、横距 0.03/0.02 m、跨距 212 m ⇒ **接线成立**（顺序**先⑧后⑦**）；
> - **站区：线位对齐真实哈佳线 44.90°、轨面由 14 m 路基降到地面线 100.69 m**（车站落平地）；
> - ⑨ 站前路**未采用**（车站落地后站前与村道自然同高，⑧→⑦ 即够）。
> 已知偏差与待办见根 `当前状态.md` 第三/四节；方法与坑（通用）见 `shared\docs\排障经验.md` §三、
> **§八**；建网/建站配方 IL 实证见 `docs\铁路进图-调研.md` §6/§10/**§11.4**；发布相关见
> `docs\发布检查清单.md`、`docs\发布文案.md`。

## 一、现行文件（新一轮从这里接手）

| 文件 | 角色 |
|---|---|
| `rail_alignment_v8.json` | **现行线位**（55 点；两端各跨出图边 10 m，首端共线碎点已清） |
| `rail_full_v14_grade.json` | **现行铺轨数据**（54 段；v13 平面 + **站区地面线剖面**）＝游戏 `ModsData\HarbinBridgeLift\rail_test.json` |
| `rail_full_v13_real45.json` | v13：**站区线位对齐真实哈佳线 44.90°**（±400 m 取直、±400..800 m 过渡；段数/高程沿用 v10） |
| `rail_full_v10.json` | v10：14 m 路基剖面版（已随 rail3/rail4 进档的历史版，留档） |
| `rail_station.json` | 依兰站放置数据（**已部署** ModsData，⑧ 消费）：`TrainStation01`；位姿 **(3339.3, −4240.4) yaw 44.90° y 100.69 m**；`yaw_offset_deg=-90`、`lateral_m=24`（正线接**中间**那条月台轨 ⇒ 一边一个月台）、`trim_span=true` |
| `rail_road.json` | ⑨ 站前路数据（**未采用**，留档）：门前环形路 接入点(3425.0,−4003.8) → 站前 NE 端 → 站前段 → SW 端 → 接入点(3178.7,−4146.4)；高程跟地面线（99.81→100.69 平→100.50） |
| `手绘_东北.webp` / `手绘_全线.webp` | 机主手绘红线（东北段线位的权威来源） |
| `il\` | IL 证据库（高程规则/过路贴地/对外连接/车站子网；索引见排障经验文末） |

## 二、验收时的参数（本图选择，换图重定）

- 线位端点：`tools/rail_align_to_bounds.py --margin 10 --min-seg 100`（对外连接判定 = 节点出
  `playable/2`=±7168 m 图边；10 m 足够，图内 2.8 m 就是死头——排障 §11）。
- 剖面：`tools/make_rail_elev_profile.py --height 14 --min-above 10 --max-grade 2.5 --clearance 8.5
  --smooth-m 1500 --deepen-m 20 --deepen-above-m 105 --deepen-full-m 140`。
- 铺轨数据字段口径：`segments` + 逐段绝对高程 `elevations` + `merge_m`（缺省 20，过路段自动打
  `DisableMerge`）+ `deck_step_m`=60（短课程逐节点控高，排障 §9）。
- 验收读数：v24 实测报告 = 对账 240/242 节点 |Δ|≤1 m（唯二偏差即牡丹江桥 +9.93 m）、2 终端节点
  `OutsideConnection=True`；v10（margin 10）复测两端仍 `OutsideConnection=True` 后机主验收。
  离线核对：5 处跨公路均轨在上（+10.8…+14.0 m）；隧道 2 处 1039+898 m（最深 −48.7 m）；
  坡上限 2.50%；顺畅度 0.217。

## 三、工具链（`tools\`，通用、配置/命令行驱动）

| 工具 | 作用 |
|---|---|
| `rail_draw_base.py` | 出"给机主画线"底图（绿=OSM 公路、橙=现有线位、**整图无红**） |
| `rail_redline_to_json.py` | 机主红线 → 经纬度折线（断口记 gaps = 隧道候选） |
| `rail_snap_to_imagery.py` | 线位自动吸附影像暗脊（**必配 `--avoid-roads`**，排障 §21） |
| `rail_alignment_check.py` | 线位校验图（`--roads` 出红线对照，判"吸的是不是铁路"） |
| `rail_profile_check.py` | 沿线 DEM 逐段剖面（防"连直线翻山"，排障 §22） |
| `make_rail_elev_profile.py` | 高架剖面计算（参数全在命令行） |
| `rail_profile_report.py` | 剖面对比报告（分区/埋入段量化，调 deepen 参数用） |
| `rail_road_crossings.py` | 线位×公路交叉点离线判定（轨在上/下穿） |
| `rail_align_to_bounds.py` / `rail_bounds_check.py` | 端点对齐图边 / 出界体检 |
| `make_rail_full.py` | 线位→铺轨数据（老路径，过水自动声明桥面；v5 之前用） |

## 四、历史版本（留档备查，不要当现行）

- 线位：`rail_alignment.json`(v2 吸附被公路带走，偏西 700 m) → `_v3seed/v4`(影像订正) →
  `v5`(机主手绘东北段) → `v6`(62 点加密) → `v7`(margin 100) → **`v8`(margin 10，现行)**；
  `rail_alignment_snap.json`/`_rail_alignment_backup_15pt.json` 为过程件。
- 铺轨数据：`rail_full.json`(v3 直连) → `v4`…`v9` → `v10`(14 m 路基) → `v11_gap`(试过站区留口，已弃) →
  `v12_straight`(站区取直 50.80°，**仍未对齐真实线**，已弃) → **`v13_real45`(对齐真实哈佳线 44.90°，现行平面)** →
  **`v14_grade`(站区地面线，现行)**；`rail_test_*.json`（single/pair/bridge/full）是 ⑥⑦ 按钮调试期的一次性试件。
- 车站数据：`rail_station.json` 位姿随线位演进而变（114.52 m 高架 → **100.69 m 地面线**），当前字段含义见 §一。
- 画线底图 `_画线底图_*.png` 与 `draw_base_*.png` 同物两代；`_预览_*.jpg` 为当轮快照。
- `_` 前缀脚本（`_dem_ascii/_dem_overlay/_trace_*/_rail_path/_grade` 等）= 当轮一次性探针，勿复用；
  **本轮新增**：`_rail_align_station_real.py`（站区对齐真实线位）、`_rail_grade_station.py`（站区降地面线）、
  `_make_publish_images.py`（发布用 Preview/Thumbnail 渲染）。
- 参数扫描残留（tmp_*）已归档 `archive\tmp_rail_profile_experiments\`。
