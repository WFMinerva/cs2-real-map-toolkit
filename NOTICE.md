# NOTICE · 数据来源、第三方致谢与范围声明

## 1. 本仓**不包含**什么（重要）

- **不含**《城市天际线 2》（Cities: Skylines II）及其 DLC 的游戏文件、程序集、资源或**反编译产物**。
  mod 只以**源码**形式发布，使用者需自行用官方 **Modding Toolchain** 编译；本仓不附带任何游戏 DLL。
- **不含**第三方 mod 的代码或二进制（例如 Earth2Cities / `OSMImportMod`）。
- **不含**从游戏程序集抓取的 IL dump、类型/预制件清单原始导出。
- **不含**游戏存档（`.cok`）、地图产物（高度图/预览图）、DEM/OSM 原始数据包、商业影像（如 Esri World Imagery）。
- 文档中的逆向互操作结论（方法名、组件名、行为规律）是为**互操作性**而做的实测记录，用于帮助他人以
  官方机制完成同类工作；如权利人认为不妥，请开 issue，我们会调整或移除。

## 2. 数据来源与署名

| 数据 | 来源 | 许可 / 署名要求 |
|---|---|---|
| 高程 | Copernicus DEM GLO-30（AWS 开放数据） | © DLR / Airbus Defence and Space (COPERNICUS) |
| 水系 / 路网 / 行政 / 街名 | OpenStreetMap（Geofabrik 分省提取 / Overpass API） | © OpenStreetMap contributors，**ODbL** |
| 土地覆盖（肥沃度资源图） | ESA WorldCover 10 m 2021 v200（AWS 开放数据） | CC BY 4.0，署名 ESA WorldCover project |
| 气候 | NASA POWER | NASA 开放数据 |
| 影像（仅制作期判读用，**不再分发**） | Esri World Imagery 等 | 各自服务条款 |

## 3. 第三方致谢

- **Earth2Cities – Real World Cities Import**（作者 BussianRananana）：路网从 OSM 导入游戏这一步由它完成；
  本仓的建网实现参考了其"用原版工具管线创建网络"的思路，但**代码为独立实现**，不含其任何源码或二进制。
- **Cities: Skylines II Modding Toolchain**（Colossal Order 官方）：编译与部署 mod 依赖它。
- 社区岸线/水深模型思路，以及 `docs/` 中引用到的所有公开资料。

## 4. 免责声明

本项目为个人学习与创作性质的制图工具链。使用者需自行确保对所用数据源、游戏模组与最终发布内容的合规性
（含署名与相同方式共享义务）。作者不对因使用本仓内容产生的任何后果负责。
