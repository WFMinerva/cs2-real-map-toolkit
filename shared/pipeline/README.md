# shared/pipeline —— 配置驱动的制作管线

用法：`<root>\.venv\Scripts\python.exe shared\pipeline\<脚本>.py --map <map_id>`（默认 harbin）。
所有地图参数读 `maps/<id>/<id>.config.json`；机器参数读 `config/game.json`。
完整制作路线（每阶段的输入/产出/通过标准/机主决策点）见根 `README.md` 第三节；
实测坑见 `shared\docs\排障经验.md`。

| 脚本 | 作用 |
|---|---|
| `cs2map\config.py` | 配置加载/路径解析 |
| `cs2map\geo.py` | densify/重投影/runs/高度图读取/山体阴影（公共库） |
| `10_fetch_dem.py` | 下载 Copernicus GLO-30 tile + 框内高程统计 |
| `11_estimate_sea_level.py` | DEM 尖峰法估水面高程（是数据内估计，非实测） |
| `12_extract_water.py` | 从 Geofabrik 图层提框内水体面（外环/内环） |
| `14_build_mask.py` | UTM 网格水体掩膜 + DEM 尖峰交叉验证 |
| `16_build_heightmap.py` | 可玩区高度图（**社区标准岸线模型**，现行；06/17 路线已废） |
| `18_build_world.py` | 世界图 57.344 km（画外挖河道；`--no-deploy` 可关部署） |
| `20_preview_heightmap.py` | 验图：拉伸灰度+山体阴影 |
| `22_deploy_heightmaps.py` | 部署高度图+回读校验 |
| `24_river_edges.py` | 河道穿可玩区四边位置（日后放 Border River/Water Source 用） |
| `26_trim_roads.py` | 路网裁剪+伪经纬度坐标补偿 → E2C 用 .osm（干线连边口径 = config `roads_connector`） |
| `28_export_bridge_lift.py` | 抬桥高度场导出（**必须用 roads_bounds**，排障 §5；`densify_step_m` 兜细分插节点） |
| `30_deploy_osm_to_e2c.py` | 部署 .osm 到 E2C 缓存（自动备份；前置：关游戏改 `OSMImportMod.coc`，排障 §3） |
| `54_build_resource_maps.py` | 官方资源图 256²（地下水代理/矿油留空/森林指引不能导入） |
| `64_fetch_climate_nasa.py` | NASA POWER 逐日 10 年取数存档（config `climate` 块） |
| `65_climate_table.py` | 气候填值表（日历四季；**--map harbin 为回归锚**，9 项×4 季全过才许出表） |
| `66_make_climate_doc.py` | 气候填值对照表 DOCX/PDF（复用 65.compute 单源；需 bundled Python 的 python-docx） |
| `67_verify_save_climate.py` | 读存档 MapMetadata 自校验气候（只读；temperatureRange 按"派生包围"判） |
| `68_export_street_names.py` | 批量街名数据：带名 way 段中点→真名（E2C 不读 name 的补法；配 mod「批量街名」） |
| `69_build_fertile_worldcover.py` | 肥沃图换源：ESA WorldCover 10 m COG 按窗直读（先候选+对照，机主批准后 `--deploy`） |
| `90_range_preview.py` | 范围预览渲染（提案用；机主确认前不做地形） |
| `92_extract_layers.py` | 从分省 zip 抽 shapefile 图层到本图 `data/osm_hlj` |
| `96_fetch_roads_overpass.py` | Overpass 直取路网原始响应 → 26 的 FULL 输入（类白名单 = config `roads_overpass`） |
| `97_fetch_rail_overpass.py` | 同上，铁路/车站（config `railways` 块；OSM 整段缺画时换影像判读） |
| `98_fetch_imagery_mosaic.py` | Esri World Imagery 镶嵌图（判读/描摹底图；产物进 `products/imagery/` 当候选） |

## 已知限制与前置检查（换图必读；"跨图直接复用"按本表逐项打折）

> 原则：**宁可显式停止，不静默用错数据**。2026-09-30 起 92/14/16/90 已带停止条件；
> 未修的限制在此列明检查办法。

| 脚本 | 已知限制 | 前置检查 / 停止条件 |
|---|---|---|
| `90` | 底图完全依赖 region_shp 三图层 | 三图层全缺 → **停止**并提示先跑 92；**选错省的包**只会画出"不像当地"的底图，需人工看图判别（config `region_zip` 显式化后已大幅降低该风险） |
| `10` | 只按 config `dem.tiles` 列表下载 | 不校验瓦片是否覆盖全框（覆盖缺口要到 14 才被拦）；跨瓦片图把 tiles 列全即可 |
| `92` | 源包与输出目录**曾经写死黑龙江/固定目录**（已修） | 源 = config `region_zip`；未设且 `data_common` 有多个包 → **停止**并列出候选；单包 → 放行但打印提醒。输出 = config `region_shp` |
| `11` | 候选值是 DEM 数据内估计 | **不自动写 config**；必须人工核对 `sea_level_estimate.json`（尖峰占比 + OSM 交叉验证）后填 `sea_level_m`。多瓦片自动合并，无限制 |
| `12` | 只抽 `water_a` 面（线状水归 18） | 框 = config `water.extract_bbox_swnu`，新图要重设 |
| `14` | **只读 `dem.tiles[0]` 一块**（未扩展） | `sea_level_m` 为空 → **停止**（提示 11 → 填值 → 再来）；DEM 对可玩区覆盖 <99.5% → **停止**（跨瓦片图需先合并 DEM 成单景，或等工具扩展）。哈尔滨双瓦片属"playable 整块在第一块内"的合法情形 |
| `16` | 依赖 sea 与 14 的 mask_npz | `sea_level_m` 为空 → **停止** |
| `24` | 依赖 sea 与已生成的 playable_png | 顺序正确则前置自然满足；sea 为空或无图会在读取处报错（无专用提示） |
| `26` | 干线连边口径 = config `roads_connector`（无该块 = 全收边，哈尔滨行为） | 换图要重定 `match_highway`/`boundary_margin_m`；E2C 生效四至 = `roads_bounds` |
| `28` | 高度换算对四至敏感 | **必须用 `roads_bounds`（六位）**，用 4 位圆整 bounds 会偏 ~4 m（排障 §5） |
| `30` | E2C 缓存是全局一份 | 前置：**关游戏**改 `OSMImportMod.coc` 四至（排障 §3）；两图切换互改 |
| `54` | OSM 口径（地下水代理、矿油留空） | OSM 土地覆盖缺口是常态（排障 §23）；肥沃缺数据换 69 |
| `65` | 回归锚**固定 = harbin** | 出任何图的表前 `--map harbin` 回归必须 9 项×4 季全过（口径锁） |
| `69` | **单 WorldCover 瓦片**（config `tile`，或按框西南角推导） | 框跨 WorldCover 瓦片边界（3° 网格）时窗口出界**无检查**——用前比对读出窗口 shape 与预期，异常即停 |
| `96`/`97` | Overpass 镜像可达性、OSM 覆盖缺口 | endpoints 顺序重试；97 取回 0 way ≠ 无铁路，先大范围对照再换 98 影像判读（排障 §23） |
| `98` | Esri World Imagery 可用性 | 仅作判读/描摹底图，不进游戏 |

铁路线位→铺轨数据的工具链（`tools/rail_*.py`）不在本目录，索引见 `maps/yilan/rail/README.md`
（方法通用、参数按图重定）。

复现验证：`shared\scripts\verify_harbin_reproduction.py`；
差异诊断：`shared\scripts\diag_migration_diffs.py`（`bridges [path]` 可单查抬桥 JSON）。
