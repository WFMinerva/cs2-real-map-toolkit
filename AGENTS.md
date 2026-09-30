# AGENTS.md · AI Agent 操作规程（本项目通用）

> 这份文件是给**接手本项目的 AI Agent**（以及愿意照此协作的人）看的。
> 它记录的不是"代码怎么写"，而是**这类工作该怎么推进才不出事**——本项目就是在这些规则下跑通的。

## 角色分工

机主（人）在对话里提要求、**做游戏内操作与验收**、拍关键决策；**其余（查证、制作、修改、读日志、
对账、部署）全部由 Agent 完成**。凡 Agent 自己能读到的（日志/报告/哈希/文件状态）不推给人看；
需要人时，明确列出"**只验证什么**"或给出少量选项。

## 新会话先读什么（按序）

1. `README.md` —— 本仓是什么、三条最关键结论、**已证伪的弯路清单**。
2. `docs/制作路线.md` —— 完整制作路线与各阶段通过标准。
3. `shared/docs/排障经验.md` —— **动 mod / E2C / 存档 / 铁路之前必读**（六层证据链 + 实测坑）。
4. `shared/mod/HarbinBridgeLift/README.md` —— mod 的按钮表、建网配方、版本史。
5. 要做某张图：`maps/<map_id>/` 下的 config 与 docs；要改铁路另有 `maps/<map_id>/rail/README.md`。

## 操作边界（红线）

1. **游戏存档先备份再动手**：备份放游戏扫描目录之外；改前记录 SHA256。
2. **一轮只验证一个变化**（机制改动一轮只动一处）；**没进游戏验证过的，一律叫「候选版本」**，
   不得说"完成/安全"。
3. **起步基线 = 最新验收基线**（以项目状态文档为准）；候选图只能游戏内「另存为」产生
   （手工复制 `.cok` 会 cid 碰撞被去重）。
4. **部署成功 ≠ 编译成功**：mod 构建后必须在游戏关闭时复制并**核对哈希**才算部署；
   数据文件（`ModsData\*.json`）改动无需重启，重载地图即生效。
5. **区分六层证据**：编译 / 部署 / 加载 / 实体生成 / 画面正确 / 存档后仍有效——**互相不能推论**；
   API 回读全绿、字符串进了存档都不是显示证据，画面与存档以人目检/读档复核为准。
6. **建网诊断三类分开数**（本轮 marker 定义实体 / 原版 Temp 网实体 / 永久网络）；
   **清理只按自己的 marker，绝不按预制件全图清**。
7. **机主说停就停**：停止期间不调用工具、不写文件，只汇报状态与所缺决定。
8. **不加硬编码**：地图范围、数据路径、目标地图身份、阈值一律进 `maps/<map_id>/<map_id>.config.json`
   或命令行；脚本必须配置驱动；某张图的具体数值（水位、margin、剖面参数）是**该图选择**，不是普遍规则。
9. **优先官方机制与已验证实现**；缺可靠做法时，先写明具体缺口、停下来一起研究，不擅自发明。
10. **不发布未获授权的产物**：不把游戏资产、反编译产物、第三方 mod 代码放进本仓或发布物里
    （见 `NOTICE.md`）。

## 工具速记

- Python：`python -m venv .venv` → `pip install -r requirements.txt`；控制台打印中文前
  `set PYTHONIOENCODING=utf-8`（或 `$env:PYTHONIOENCODING='utf-8'`）。
- 管线脚本：`python shared/pipeline/<脚本>.py --map <map_id>`（清单见 `shared/pipeline/README.md`）。
- 铁路工具链：`tools/rail_*.py`（线位 / 剖面 / 边界 / 交叉；用法见各文件头注与 `maps/<id>/rail/README.md`）。
- Mod 构建：官方 **Modding Toolchain** 编译 `shared/mod/HarbinBridgeLift`（Windows 下
  `pwsh -File shared/scripts/build_mod.ps1`）；部署脚本 `shared/scripts/deploy_mod_when_closed.ps1`。
- 诊断优先级：**先读 mod 自己的报告文件**（`ModsData\HarbinBridgeLift\last_*.txt`），再看游戏
  `Logs\` 下的日志，最后才猜。
