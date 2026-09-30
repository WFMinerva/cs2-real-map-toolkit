# cs2-real-map-toolkit · 把真实地理数据做成《城市天际线 2》地图

> Real-world geodata → playable **Cities: Skylines II** maps: Copernicus DEM terrain & water, real
> OpenStreetMap roads with real street names, **real railways and a real train station built by the
> companion code mod in this repo**, climate and resource maps — then verified and published.
> **本仓不重新分发任何游戏资产或第三方代码。**
>
> 成果：黑龙江**依兰县**图（含哈佳铁路与依兰站）已发布到 PDX Mods **id 161349**；哈尔滨图为其前身。

---

## 一、如果你是 AI Agent / LLM，先读这段

这个仓库是**一条被实际跑通过、并被人类验收过的完整路径**，同时是一份**失败清单**——目的是让你少走我们走过的弯路。

**阅读顺序**：`docs/制作路线.md`（完整路线与阶段通过标准）→ `shared/docs/排障经验.md`（**血泪坑清单，必读**）
→ `maps/yilan/docs/铁路进图-调研.md`（程序化建网/建站的逆向结论与配方）→
`maps/yilan/rail/il/stconn2_report.md`（车站接线机制）→ `shared/mod/HarbinBridgeLift/README.md`（mod 按钮与机制）。

**三条最关键结论**（先记住这三条，能省你几天）：

1. **现成工具只解决一半**：路网可以靠第三方 mod **Earth2Cities（E2C）** 从 OSM 导入；
   但**真实铁路线位与火车站必须自己写 mod**——本仓 `shared/mod/HarbinBridgeLift` 就是为此写的
   （程序化建网 + 放建筑 + 接线）。
2. **游戏内的网可以用 ECS 定义实体程序化生成**（本仓已验证的配方）：
   `CreationDefinition` + `NetCourse` + `OwnerDefinition` + `Updated` → 排进 `ToolOutputBarrier` →
   `applyMode` 走 `Clear → None（等 Temp）→ Apply`。**车站内部轨道**要按建筑预制件的
   `Game.Prefabs.SubNet` 逐条建，且 **`m_SubPrefab` 必须是 `Entity.Null`**（填建筑预制件只出节点不出边）。
   与既有网络相连只能靠 **`CoursePos.m_Entity` 显式挂接**，不要指望坐标重合自动合并。
3. **游戏侧必须由人验收**：编译 / 部署 / 加载 / 实体生成 / 画面正确 / 存档后仍有效 = **六层证据，互相不能推论**。
   API 回读全绿、字符串进了存档，都不等于画面对。每轮只改一个机制、只请人验一个变化。

## 二、已证伪 / 别走的弯路（我们真的踩过）

| 弯路 | 后果 | 正解 |
|---|---|---|
| 只发 `ObjectDefinition` 就想放车站 | 只有外壳：站内 `SubNet` **0 条** | 按预制件 `SubNet` 逐条发子网定义 |
| 站内子网 `m_SubPrefab` 填建筑预制件 | **33 Node / 0 Edge**，站内轨道根本没建出来 | `m_SubPrefab = Entity.Null` |
| 挂接端点带 `FreeHeight(128)` | 绝对坐标被当成"相对地面"，高程飞掉（114.51 → 15.07） | 挂接端只留 `IsFirst/IsLast` |
| 站内节点只按"横向距离最近"筛 | 挂到同位置的**隐形道路节点**，正线被拽歪 | 双门槛：轨道节点 **且** \|Δy\| ≤ 2.5 m |
| 指望新铺的线与旧网自动合并 | 节点不跨批次合并（合并系统只在工具期生效） | `CoursePos.m_Entity` 显式指向既有节点 |
| 手工复制 `.cok` 当候选版本 | cid 碰撞 → 地图列表里被去重挤掉 | 只能游戏内「另存为」 |
| 用 OSM 水面判"桥在水下" / 用判读点连直线定线位 | 结论错 / 线位翻山（25% 坡） | 用**游戏高度图**判水、**逐段剖面**校验线位 |
| 把车站放在 14 m 路基上，再硬接站前道路 | 站前与地面差 15 m，越修越复杂 | 改剖面让站区走**地面线**（±A 取平 + ≤2.2% 过渡） |
| 发布前才想配缩略图 | 选择器只认"截图目录"，空目录直接抛异常卡死 | 先在编辑器配 Preview/Thumbnail；空目录时用"从磁盘选择" |
| 在游戏运行时 build mod | 复制步报 MSB3231，且会**删掉部署目录 6 个文件中的 5 个** | 构建后必须重新部署并核对 6/6 哈希 |

## 三、这套路径做什么（制作路线一览）

| 阶段 | 内容 | 关键手段 |
|---|---|---|
| 0 | 环境与配置 | `config/game.json`（游戏路径 / 用户数据 / E2C 缓存，见 `config/game.example.json`） |
| 1 | 取数 | Copernicus DEM GLO-30、Overpass/Geofabrik OSM、NASA POWER 气候 |
| 2 | 地形与水位 | 高度图（可玩区 14 km / 世界图 57 km）+ Sea Level 即全图水面机制 |
| 3 | 路网导入 | 裁到图边 + 坐标补偿 → 交给 **E2C** 导入（含高速对外连接） |
| 4 | 抬桥 | 离线算抬升量 → mod「抬桥」按钮（净空校验） |
| 5 | 气候 / 资源图 | 照表填值（脚本校验）、地下水/矿/油/土地覆盖资源图 |
| 6 | 街名 | OSM 名字点 → 连通性分组 → mod「批量街名」（含"无名路"） |
| 7 | 铁路 | 手绘红线 → 逐段剖面（≤2.5% 坡）→ mod 铺轨（对外连接、过路贴地、站内裁剪挂接） |
| 8 | 车站 | mod「车站试放」：建筑 + 站内轨道 + 与正线接线（**先⑧后⑦**） |
| 9 | 站前道路 | mod 铺路（地形模式或显式高程 + 两端挂既有路口） |
| 10 | 发布 | 官方 checklist 4 项 + Preview/Thumbnail + 中英文标题描述 + 署名 |

每阶段的**脚本、通过标准、已知限制**见 [`docs/制作路线.md`](docs/制作路线.md)；
坑与实测见 [`shared/docs/排障经验.md`](shared/docs/排障经验.md)。

## 四、仓库结构（刻意保留原工作区布局，使文档中的路径引用直接生效）

```
shared/pipeline/             离线制作管线（配置驱动：python shared/pipeline/<脚本>.py --map <id>）
shared/mod/HarbinBridgeLift/ 自研 C# mod：抬桥 / 批量街名 / 铺轨 / 车站试放 / 站前路
                             （按钮表、建网配方、版本史见其 README.md）
shared/scripts/              mod 构建与部署、跨工作区迁移与复现校验脚本
shared/tools/                ParseTest / TypeDump（程序集类型与预制件枚举辅助）
tools/                       离线工具：铁路线位·剖面·边界·交叉、.cok 探针、IL 抓取脚本
shared/docs/                 排障经验、道路导入官方配方、E2C 操作卡
maps/yilan/ , maps/harbin/   两个案例：docs（过程与结论）+ 关键数据（线位/铺轨/车站/config）
                             —— 不含任何地图产物、存档或原始数据
docs/制作路线.md              完整制作路线（含各阶段通过标准与限制）
```

## 五、复现前提

- **Cities: Skylines II** + 官方 **Modding Toolchain**（编译 `shared/mod/` 下的 mod）
- **Python 3.11+**：`python -m venv .venv` → `pip install -r requirements.txt`
- **Earth2Cities（E2C）**：路网导入这一步用它（**第三方 mod，本仓不含其代码**，见 `NOTICE.md`）
- 图幅 bbox 与本机路径：把 `config/game.example.json` 复制为 `config/game.json` 填自己的路径；
  各图 config（`maps/<id>/<id>.config.json`）可参考依兰/哈尔滨两份

## 六、许可与署名

- **代码**（`shared/pipeline/`、`shared/mod/`、`shared/scripts/`、`shared/tools/`、`tools/`）：**MIT**，见 [`LICENSE`](LICENSE)
- **文档与数据**（`shared/docs/`、`maps/*/docs/`、`maps/*/rail/*.json`、`docs/`）：**CC BY-SA 4.0**，见 [`LICENSE-docs`](LICENSE-docs)
- 数据来源署名、第三方致谢、"本仓不含游戏资产/反编译产物"声明：见 [`NOTICE.md`](NOTICE.md)

---

## English summary (for search & LLM retrieval)

A reproducible, human-verified path from **real geodata to a playable Cities: Skylines II map**:
Copernicus DEM heightmaps and water, OpenStreetMap roads trimmed to the map edge and imported with
**Earth2Cities**, real street names, **a companion C# mod that programmatically builds railways and a
real train station** using ECS definition entities (`CreationDefinition` + `NetCourse` +
`OwnerDefinition` + `Updated` through `ToolOutputBarrier`), climate and resource maps, then publishing
via the official checklist. The repo also documents **confirmed dead ends** (station sub-nets with
`m_SubPrefab` set, `FreeHeight` on attached endpoints, node filtering by lateral distance only, relying
on coordinate-based node merging, copying `.cok` files by hand, …) so that other humans and AI agents
do not repeat them. **No game assets, no decompiled code, no third-party mod code are redistributed.**

*Keywords: Cities Skylines 2 map modding, real world map, OSM import, DEM heightmap, railway, train
station, ECS net creation, Earth2Cities alternative, open source map pipeline.*
