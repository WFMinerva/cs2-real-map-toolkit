# HarbinBridgeLift · mod 索引（按钮 / 数据文件 / 机制）

> **定位**：本项目自研的一次性施工工具（把离线算好的线位、抬升、车站、街名等灌进游戏）。
> 地图成品**不依赖它**——发布出去的地图不需要玩家装这个 mod。
>
> **构建**：`pwsh -File shared\scripts\build_mod.ps1`。
> ⚠️ 游戏运行中构建会在复制步报 `MSB3231`（产物 `bin\Release` 已生成），且构建过程会**删掉部署目录里
> 6 个文件中的 5 个** ⇒ 之后必须跑 `pwsh -File shared\scripts\deploy_mod_when_closed.ps1 -Tag "…"`
> 等游戏退出后复制，**并核对 6/6 哈希**（坑见 `shared\docs\排障经验.md` §1）。
> **数据文件**：`ModsData\HarbinBridgeLift\*.json`，改动**无需重启**，重载地图即生效。
> **报告**：`ModsData\HarbinBridgeLift\last_*.txt`（每次点按钮后覆盖写）。

## 一、按钮表（设置面板顺序）

| 序 | 按钮 | 请求 | 数据文件 | 做什么 | 报告 |
|---|---|---|---|---|---|
| ① | 扫描桥 | `Scan` | `bridges_lift.json` | 只读枚举候选桥、对账 | `last_report.txt` |
| ② | 抬升桥 | `Lift` | `bridges_lift.json` | 按数据抬桥面（净空检查） | 同上 |
| ③ | 还原桥 | `Restore` | — | 撤销本工具抬升 | 同上 |
| ④ | 批量街名 | `Names` | `street_names.json` | 按 OSM 名字点给路网写名（含「无名路」） | 同上 |
| ④b | 单路改名 | `NameSingle` | `street_name_single.json` | 单条路改名（调试用） | 同上 |
| ⑤ | 枚举铁路/车站预制件 | `RailPrefabs` | — | 只读 dump：名字/索引/组件（找 `TrainStation01` 这类） | `last_rail_prefabs.txt` |
| ⑥ | 对照组（单段定义实体） | `RailTest` | `rail_test.json` | 不激活工具、只把定义实体排进通用 ECB（与⑦对比） | `last_rail_test.txt` |
| ⑦ | 铺轨（E2C 同构：`ToolOutputBarrier` + Clear/None/Apply） | `RailTestTool` | `rail_test.json` | 按线位铺永久铁路；**v27 起**会按站内跨距裁剪并把端点挂到站内轨道节点（先⑧后⑦） | `last_rail_tool_test.txt` |
| ⑧ | 车站试放（对象分支） | `Station` | `rail_station.json` | 放 `TrainStation01` + **按预制件 `Game.Prefabs.SubNet` 生成站内轨道**（含 `OwnerDefinition`）；幂等重放 | `last_station_test.txt` |
| ⑨ | 站前路（**未采用**，留档） | `StationRoad` | `rail_road.json` | 铺一条公路（默认 `Small Road`）并把**首/末端点挂到最近的既有公路节点** | `last_road_test.txt` |

**顺序纪律**：车站轮是 **先⑧后⑦**（⑧ 先造出站内轨道节点，⑦ 才有对象可挂接）；⑥⑦ 一轮只点一个。

## 二、机制要点（踩过的坑都在这里）

1. **两个相位**：按钮回调只置标志；结构性变更在 `BridgeLiftSystem`（`ModificationEnd` 相位）做（v1 的教训）。
2. **建网配方**：`CreationDefinition` + `NetCourse` + `OwnerDefinition` + `Updated`（+ 本轮 marker），
   排进 `ToolOutputBarrier`，`applyMode` 走 `Clear → None（等 Temp）→ Apply` 握手。
   预制件**按运行时名字解析**（index/version 只当线索）。
3. **站内子网**：`CreationDefinition.m_SubPrefab` 必须 **`Entity.Null`**，否则只建节点不建边
   （实测 33 Node / 0 Edge → 修好后 Edge 19 / Node 33）。详见 `maps\yilan\docs\铁路进图-调研.md` §11.4。
4. **接线**：节点筛选双门槛（轨道节点 + |Δy| ≤ 2.5 m）；挂接端**不带 `FreeHeight(128)`**（位置是绝对的）；
   连接开关 = `CoursePos.m_Entity`（不靠坐标重合）。见排障经验 §八 30–32。
5. **每个入口一套独立定时器**（v31 教训：⑧/⑦ 共用会把对方的 5 秒自查挤掉）。见 §八 33。
6. **清理只按自己的 marker**（本轮 marker / 原版 Temp / 永久实体三类分开数），**绝不按预制件全图清**。
7. 数据字段口径见 `maps\yilan\rail\README.md` §二（本图选择）与各 `*.json` 的 `note` 字段。

## 三、版本史（本仓库内）

- v1–v24：抬桥 + 街名 + 铁路线位/剖面/对外连接（详见 `当前状态.md` R4–R7、`archive\依兰-全程记录-2026-09-30.md`）。
- **v27**：⑦ 车站感知（站内跨距裁剪 + 端点挂接）；**v28**：⑧ 车站试放 + 按 `SubNet` 生成站内轨道。
- v30/v31：失败链（节点筛选只按横向距离、挂接端带 FreeHeight、⑧/⑦ 共用定时器）——见 `调研` §11.3。
- **v32**：定案（`m_SubPrefab = Entity.Null` ⇒ 站内轨道边生成；独立定时器）。
- **v33**：⑨ 站前路（铺公路 + 两端挂既有公路节点）——**未采用**，能力留档。
