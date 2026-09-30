# stconn2 —— 车站「无轨道连接」接线机制 逆向报告

日期：2026-09-30（本会话）　证据根：`<repo>\maps\yilan\rail\il\stconn2_*.txt`
Game.dll = `<CS2_INSTALL>\Cities2_Data\Managed\Game.dll`（下称 Game.dll）
所有 IL 文件为本会话新抓，**未覆盖任何既有文件**（前缀 `stconn2_`）。

---

## 0. 结论摘要（≤12 行）

1. **接线只有一条官方通路：实体身份共享**。一条边连到网络里，唯一的方式是它的 `Game.Net.Edge.m_Start/m_End` **就是**那个既有 node 实体；CS2 **不按坐标合并**新建永久节点与既有节点（`GenerateNodesSystem/NodeKey`、`GenerateEdgesSystem/NodeMapKey` 都以 `m_Original`/`coursePos.m_Entity` 为准）。
2. 生成侧判定写在 `GenerateEdgesSystem::TryGetNode`：`isPermanent && m_NodeData.HasComponent(coursePos.m_Entity)` → 直接复用 `coursePos.m_Entity` 当节点（`GenerateEdgesSystem.cs:1256-1262`）。所以 **我们的铺轨课程只要把 `NetCourse.m_StartPosition.m_Entity` 指到车站站内轨道端点 node，就真的接上了**。
3. 车站的站内轨道来自**预制件数据**：`Game.Prefabs.ObjectSubNets.m_SubNets[].m_NetPrefab` + `m_BezierCurve`，由 `ObjectInitializeSystem::OnUpdate` 烘成预制件实体上的 `Game.Prefabs.SubNet` buffer（`stconn2_objinitsys_onupdate_il.txt:1042-1171`）。
4. **站内轨道的 net 课程定义只有工具会发**：`ObjectToolBaseSystem/CreateDefinitionsJob::UpdateSubNets → CreateSubNet`（`stconn2_subnet_refs_prefabs.txt:117-158`）。我们的 `StationPlaceToolSystem : ToolBaseSystem`（`shared/mod/HarbinBridgeLift/StationPlaceToolSystem.cs:26`）只写 `CreationDefinition{ObjectDefinition}`，**不发 sub-net 定义** → 车站很可能根本没有站内轨道实体。
5. 建筑实例的 `Game.Net.SubNet` buffer **不需要我们写**：`Game.Net.SubNetReferencesSystem`（查询 `All{Created,Owner} Any{Node,Edge}`）自动把「`Owner.m_Owner == 建筑` 的 net 节点/边」登记进建筑 buffer（`stconn2_subnetrefs_il.txt:3-160`）。→ 这是最省事的「给建筑造站内轨道」入口。
6. 放置时的「吸附」（`Game.Prefabs.SubNet.m_Snapping`）**只是整体平移/旋转建筑**，不建连接（`stconn2_objtool_snapjob_il.txt:215-271, 341-433`）。
7. 「Station Side」预制件名在**任何托管 DLL 里都搜不到**（ASCII+UTF-16 全量扫描，`stconn2_stationside_refs.txt`）→ 引用只可能存在于预制件资产数据里的**实体引用**，即 `ObjectSubNets.m_NetPrefab`（未确认，见 §5）。
8. `PlaceableObjectData.m_SubReplacementType` 只有 `None/Tree`（Game.Net.SubReplacementType），**没有**「把重叠边换成站内边」的官方升级通路 → 候选方向 (a) **无证据**。
9. **推荐方案（排序见 §4）**：① 先做**一轮测量**（把车站实例的 SubNet/Edge/Node/SubLane + 我们正线的 Edge/Node 全量打出来）；② 按测量结果二选一 —— 有站内轨道 → 让正线课程端点 `CoursePos.m_Entity = 站内 node`；无站内轨道 → 由 mod 自己按 §4-B 的官方配方补发站内 sub-net 课程（`OwnerDefinition` = 车站）。
10. 「在站内留缺口靠坐标重合接通」= **无效**，别浪费一轮（§4-D）。

---

## Q1. 玩家把火车站放到既有轨道上时，谁完成「接线」？

### 1.1 官方通路逐段证据

**(A) 对象定义 → 站内轨道课程定义（只有工具做）**

* `Game.Tools.ObjectToolBaseSystem/CreateDefinitionsJob::UpdateObject` → `UpdateSubNets(...)`：
  `stconn2_subnet_refs_prefabs.txt:117-120`
* `UpdateSubNets` 从**预制件实体**的 `Game.Prefabs.SubNet` buffer 读，逐条建课程：
  `stconn2_objtoolbase_updatesubnets_il.txt:21-46`（`m_PrefabSubNets.HasBuffer(prefab)` / `get_Item`）、
  `:159-196`（对每条 `NetUtils::GetSubNet(...)` 后调 `CreateSubNet(...)`）。
* `CreateSubNet` 发的定义实体长这样（这就是「官方配方」）：
  `stconn2_objtoolbase_createsubnet_il.txt:265-290`（`CreateEntity` → `AddComponent<CreationDefinition>` → `AddComponent<Updated>` → 若 `ownerDefinition.m_Prefab != Null` 再 `AddComponent<OwnerDefinition>`）、
  `:104-234`（`NetCourse.m_Curve = ObjectUtils::LocalToWorld(parentTransform, curve)`：**站内轨道曲线是建筑局部坐标 → 世界**）、
  `:458-500`（`m_StartPosition.m_Flags |= 1 (IsFirst)`、`m_EndPosition.m_Flags |= 2 (IsLast)`）、
  `:505-521`（`ownerDefinition.m_Prefab == Null` 时两端再 `|= 128 (FreeHeight)`）、
  `:522-526`（`AddComponent<NetCourse>`）。
  `CoursePosFlags`/`CreationFlags`/`TempFlags` 取值见 `stconn2_enums.txt`。
* 注意 `CreateSubNet` **从不设置 `CoursePos.m_Entity`** → 站内轨道端点永远是**新建节点**，不与既有网络共享实体。

**(B) 生成侧：节点复用的唯一开关就是 `CoursePos.m_Entity`**

* 反编译证据 `archive/decompiled/GenerateEdgesSystem.cs:1256-1262`：
  ```
  private bool TryGetNode(CoursePos coursePos, bool isPermanent, bool isEditor, out Entity node) {
      if (isPermanent && m_NodeData.HasComponent(coursePos.m_Entity)) { node = coursePos.m_Entity; return true; }
  ```
* `archive/decompiled/GenerateEdgesSystem.cs:1294`：`Edge.m_Start/End = TryGetNode(start/end)`；任一端取不到 → **该边根本不创建**。
* `archive/decompiled/GenerateEdgesSystem.cs:1310`：两端都指向既有实体且 `ConnectionExists(start,end)` → 直接返回（防重复）。
* `NodeMapKey`（`GenerateEdgesSystem.cs:44-81`）：非永久阶段也以 `coursePos.m_Entity` 为键 → **临时阶段同样能挂到既有节点上**（`TryGetNode:1265-1285` 命中非 temp 项即返回该实体）。
* 反向证据（**不按坐标合并**）：
  `archive/decompiled/tool/GenerateNodesSystem.cs:146-175` `NodeKey.Equals`：任一方 `m_Original != Null` 就只比实体；否则才比**完全相等**的 `m_Position`。
  `:1035-1079` `CollectUpdatesJob`：合并只发生在**同 key**（=同一既有实体 or 两个全新建且坐标逐一相等）的两条 UpdateData 之间。
  `:983-1014` `AddNode`：`CreationFlags.Permanent` 且 `coursePos.m_Entity` 已有 `Node` → 只 `Enqueue(updateOnly)` 后 **return**（不建新节点）。

**(C) 建筑实例 `Game.Net.SubNet` 由系统自动维护（不用手写）**

* `Game.Net.SubNetReferencesSystem::OnCreate` 查询：`All{Created, Owner} Any{Node, Edge}`（`stconn2_subnetrefs_il.txt:3-60`）；
  `UpdateSubNetReferencesJob::Execute`：对每条新建的带 `Owner` 的 Node/Edge，`new SubNet(entity)` → `TryAddUniqueValue(owner 的 SubNet buffer)`（`:86-120` temp 分支、`:128-160` 永久分支）；被删则 `RemoveValue`（`:188-215`）。
* 元素构造点只有 3 处（`stconn2_subnet_ctor_sites.txt`）：`SubNetReferencesSystem`（建）、`Serialization.SubNetSystem`（读档）、`ApplyNetSystem/PatchTempReferencesJob`（temp→permanent 改挂）。
* `ApplyNetSystem/PatchTempReferencesJob::Execute`：Apply 时把 `Temp.m_Original` 换进 `Edge.m_Start/End`（`stconn2_patchtemprefs_il.txt:176-221`）、重挂 `ConnectedEdge`（`:240-392`）、并**在旧 owner 的 SubNet buffer 里删旧边、在新 owner 里加新边**（`:510-620`）。这就是「Apply 后真正落地」的那一步。

**(D) 放置时 `SnapJob` 干了什么（以及没干什么）**

* `ObjectToolSystem/SnapJob::Execute`：仅当 `m_Snap & 1` 且预制件有 `Game.Prefabs.SubNet` buffer 时，遍历每条 `SubNet`，看 `m_Snapping.x/.y`；对每个需要吸附的端点，用 `ObjectUtils.LocalToWorld(hitPos, rotation, curve.a/.d)` 算世界位置，建 ±8×tol 的 Bounds2，然后 `m_NetSearchTree.Iterate(NetIterator)`（`stconn2_objtool_snapjob_il.txt:215-271` 取 buffer + 算 tol，`:341-433` 起点吸附，`:434-509` 终点吸附）。
  吸附容差 = `clamp(|curve.a.xz| * 0.02, 2, 4)` 米（`IL_02d2-IL_0332`，即**建筑原点到端点距离的 2%**，用于容忍旋转误差）。
* 它把结果写进 `ControlPoint.m_HitPosition/m_Rotation` + `m_SubSnapPoints` → **只影响建筑的落位/朝向**；`m_ParentSubNets` 相关的 `ControlPoint.m_OriginalEntity` 只在「ServiceUpgrade/扩展建筑贴着既有边」的分支里被赋成**既有边实体**（`IL_0989-0b15`），随后走 `NetToolSystem::CreatePath`/`AddControlPoints`（`IL_0bfd-0d15`）。
* 该分支的前提 `PlaceableObjectData.m_SubReplacementType != None`，而 `Game.Net.SubReplacementType` 只有 `None/Tree`（`gamenet_types.txt:806-814`）→ **这是「清树」通路，不是铺轨/接轨通路**。`ObjectToolSystem::UpdateSubReplacementDefinitions` 也是同一族（`stconn2_objtool_subreplace_il.txt`，它 schedule 的是 `NetToolSystem/CreateDefinitionsJob` 且 `Mode=5`）。

### 1.2 结论（Q1）

* **(a) 「生成时把重叠既有边升级/替换成 Station Side」：无证据。** Game.dll 里没有任何代码引用这两个预制件名（§Q2），`CreationFlags.Upgrade` 只出现在工具驱动的课程生成里（`GenerateEdgesSystem.cs:1396-1403` 把 `CreationFlags.Upgrade` 翻成 `TempFlags.Upgrade`），没有任何「建筑放置 → 自动升级脚下网」的调用链。
* **(b) 「节点合并」：不是通用机制。** 唯一会「移动/合并节点」的是 `Game.Tools.NodeReductionSystem`，且它**只处理 `Temp.m_Original != Null` 的临时节点**、且要求该节点恰好 2 条同预制件的**临时边**：`stconn2_nodereduction_all_il.txt:348-366`（`Temp::m_Original == Null → return false`）、`:362-366`（`TempFlags & 770 != 0 → return false`）、`:307-337`（`FindCandidatesJob::Execute`）。永久几何之间没有合并系统。
* **(c) 真正的接线路径 = 从「网」这一侧**：玩家画的轨道课程端点**吸附到车站站内轨道的端点节点**上 → `ControlPoint.m_OriginalEntity` = 该 node → 课程 `CoursePos.m_Entity` = 该 node → `TryGetNode` 复用该实体 → 新边的 `Edge.m_Start/End` 就是车站的节点 → 节点 `ConnectedEdge` 同时含站内边与外来边 → 真连通（+ `PatchTempReferencesJob` 在 Apply 时把引用换成永久实体）。
  → 也就是说：**官方不需要「升级既有边」，需要的是「新边与站内边共用 node 实体」。**

### 1.3 我们目前的位置（推断，需一轮实测确认）

`StationPlaceToolSystem` 直接往 `ToolOutputBarrier` 写 `CreationDefinition{m_Prefab=车站} + ObjectDefinition + Updated`（`shared/mod/HarbinBridgeLift/StationPlaceToolSystem.cs:105-119`，类继承 `ToolBaseSystem`，非 `ObjectToolBaseSystem`）。发 sub-net 定义的方法只存在于 `ObjectToolBaseSystem/CreateDefinitionsJob`（`stconn2_subnet_refs_prefabs.txt:110-120`）。
⇒ **推断：我们的车站实例上 `Game.Net.SubNet` buffer 很可能是空的（没有站内轨道实体）**。
反证/对照：`Game.Prefabs.BuildingPrefab::RefreshArchetype` 只在预制件带 `BuildingUpgradeElement` 时把 `Game.Net.SubNet` 加进原型（`stconn2_buildingprefab_refresharch_il.txt:12-27`）——即使有 buffer，也得有人往里写边。
（这条与「警告已出现」并不矛盾：见下段，警告也可能来自**正线自己的节点**。）

### 1.4 警告到底是哪一个（补充，纠正既有结论的覆盖面）

`ConnectionWarningSystem/CheckOwnersJob::Execute`（`stationconn_warn_il.txt:1228` 起）有**两条**入口：

* `IL_0013-003b`：owner **有 `ConnectedEdge` buffer（= 是个 node）** → `UpdateNodeConnectionWarnings(node, isNativeMapTile)`（`stationconn_warn_il.txt:1241-1261`）。
* `IL_0194-019b`：owner 是 **Building** → `UpdateSubnetConnectionWarnings(building)`（`stationconn_warn_il.txt:1420-1422`）。

两条最终都会调 `CheckNodeConnections`：

* 节点路径：`stconn2_warn_chain_il.txt:315-322`（`FillNodeConnections(SubLane…)` + `FillNodeConnections(ConnectedEdge…, node)` → `CheckNodeConnections(...,allConnected,false)`）。
* 建筑路径：`stconn2_warn_chain_il.txt:197-209`（同样两段 Fill + `CheckNodeConnections(..., 1)`）。
  进这一段的**前置门槛**在 `:157-195`：`SubNet` 里每条边，只要节点 `ConnectedEdge` 中存在**至少一条没有 `Game.Common.Owner` 的边**（即「外来边」）才继续；全是本建筑自己的子边就跳过（`ldloc V_14; brfalse` → 下一条 sub net）。
  ⇒ 若车站**完全没有站内轨道**（`SubNet` 为空），这条建筑路径直接不执行 → **警告只可能来自 §1.4 第一条（节点路径，即我们正线自己的节点）**。
  ⇒ 若车站有站内轨道、且站内节点上出现了外来边，才会走建筑路径并因**轨道类型不匹配/无连接**报警。

> 这一条决定了修复方向，必须先测量（§4-①）。

---

## Q2. `… - Station Side` 预制件在哪被用？谁引用它们？

**硬结论（可复现）：托管 DLL 里没有这两个预制件的名字。**

* 扫描命令（本会话执行）：对 `Cities2_Data\Managed\*.dll` 逐个 `ReadAllBytes`，用 `Encoding.Unicode` 与 `Encoding.ASCII` 双编码 `Contains` 搜 `Station Side` / `StationSide` / `Train Station` / `Track - Station` → **零命中**（含 Colossal.*、Game.dll、Unity.*）。
* 同法用 Cecil 按操作数正则搜 `Station Side|StationSide|StationTrack|StationPrefab` → **0 个方法命中**：`stconn2_stationside_refs.txt`、`stconn2_stationside_ident.txt`。
* ⇒ 预制件之间是**实体引用**（资产里序列化的 Entity 指向），不是按名字查表。所以「谁引用」只能从预制件资产或运行时预制件实体图里读。

**代码侧能做到的排查（已做完，均为负结果/结构性排除）：**

* `Game.Prefabs.TransportStationData` / `PublicTransportStationData` / `CargoTransportStationData` 的字段里**没有**任何轨道网预制件槽位（只有 Comfort/Loading/Refuel 类型与 EMPTY 标记）：
  `stconn2_types_prefabs_all.txt:657-666`、`gamenet_types.txt` 同名段。
  ⇒ 排除「车站数据里直接存站内轨道预制件」这一形式。
* `Game.Prefabs.NetSubObjects` / `NetSubObjectInfo`（`m_Object` 是 **ObjectPrefab**，是「网挂物体」，方向相反）→ 排除。
  `archive/decompiled/game-subobj/Game.Prefabs.NetSubObjects.decompiled.cs:8-28`、`Game.Prefabs.NetSubObjectInfo.decompiled.cs:8-33`。
* `Game.Prefabs.AuxiliaryNets` / `AuxiliaryNetInfo{m_Prefab, m_Position, m_InvertWhen}` 是「沿网并行铺另一张网」（电线/水管族），不是站内轨道：
  `stconn2_types_prefabs_all.txt:4311-4317`、`stconn2_auxiliarynets_il.txt:44-49`（`GetArchetypeComponents` 给**网实例**加 `Game.Net.SubNet`）。

**唯一符合条件的字段（结构推定）：**

* `Game.Prefabs.ObjectSubNets.m_SubNets[i].m_NetPrefab`（类型 `Game.Prefabs.NetPrefab`）——建筑预制件声明自己站内轨道用哪张网：`stconn2_types_prefabs_all.txt:5022-5030`；烘制过程把 `m_NetPrefab` 直接写进 `Game.Prefabs.SubNet.m_Prefab`：`stconn2_objinitsys_onupdate_il.txt:1057-1078`（`IL_0c57 m_NetPrefab` → `IL_0c8b stfld SubNet::m_Prefab`）。
* ⇒ **推定：TrainStation01 的 `ObjectSubNets` 条目就指向 `Double Train Track - Station Side` / `Twoway Train Track - Station Side`（站内月台轨），这就是这两个预制件存在的唯一理由**（它们不出现在玩家菜单里，因为只能被预制件引用）。
* 反向工具也印证这套数据是「实例子网 ↔ 预制件子网」的双向映射：`Game.Tools.ApplyPrefabsSystem::ListObjectSubNets`（读实例 `Game.Net.SubNet` → 重建 `ObjectSubNetInfo[]`，含 `m_NetPrefab`/`m_BezierCurve`/`m_NodeIndex`/`m_ParentMesh`/`m_Upgrades`）与 `UpdateObjectSubNets`（写回预制件组件）：`stconn2_subnet_refs_prefabs.txt:5-42`。

**未确认部分与下一步（见 §5-①）**：无法离线证明 TrainStation01 的 `ObjectSubNets[i].m_NetPrefab` 就是那两个预制件；也无法证明「Station Side」不是别的机制（例如某种聚合/样式变体）在用。这两条都需要**运行时预制件实体 dump**（读预制件实体的 `Game.Prefabs.SubNet` buffer，打印每条 `m_Prefab` → `PrefabRef`/名字；再全表扫一遍哪些预制件的 SubNet 指向这两个实体）。

---

## Q3. 站内轨道几何从哪读；预制件/实例两侧字段结构

### 3.1 预制件侧（**可以离线算出站内轨道端点世界坐标**）

| 类型 | 接口 | 字段（`stconn2_types_prefabs_all.txt` 行号） |
|---|---|---|
| `Game.Prefabs.ObjectSubNets` : ComponentBase | 挂在**建筑/物体预制件**上 | `NetInvertMode m_InvertWhen`；`ObjectSubNetInfo[] m_SubNets`（`:5028-5030`） |
| `Game.Prefabs.ObjectSubNetInfo` : Object | 数据元素（**编辑器授权数据**） | `NetPrefab m_NetPrefab`；`Bezier4x3 m_BezierCurve`；`int2 m_NodeIndex`；`int2 m_ParentMesh`；`NetPieceRequirements[] m_Upgrades`（`:5022-5027`） |
| `Game.Prefabs.SubNet` : IBufferElementData | **预制件实体上的烘制结果** | `Entity m_Prefab`；`Bezier4x3 m_Curve`；`int2 m_NodeIndex`；`int2 m_ParentMesh`；`NetInvertMode m_InvertMode`；`CompositionFlags m_Upgrades`；`bool2 m_Snapping`（`:4026-4033`） |
| `Game.Prefabs.SubLane` : IBufferElementData | 同上（车道） | `Entity m_Prefab`；`Bezier4x3 m_Curve`；`int2 m_NodeIndex`；`int2 m_ParentMesh`（`:4034-4038`） |

**烘制链路（ObjectSubNets → SubNet buffer）**：`Game.Prefabs.ObjectInitializeSystem::OnUpdate`
`stconn2_objinitsys_onupdate_il.txt:1042`（`GetComponent<ObjectSubNets>()`）、`:1062`（`m_NetPrefab`）、`:1078/1086`（`m_Prefab`/`m_NodeIndex`）、`:1081-1082`（`m_BezierCurve`→`m_Curve`）、`:1089-1090`（`m_InvertWhen`→`m_InvertMode`）、`:1093-1094`（`m_ParentMesh`）、`:1104-1137`（`m_Upgrades`，不支持的状态会打日志 `ObjectSubNets ({0}[{1}]) upgrade state not supported: {2}`，`:1147`）、`:1171`（`DynamicBuffer<SubNet>::Add`）。
随后 `InitializeSubNetsJob::Execute` 只做**节点索引表规模校正**（`stconn2_objinit_subnets_job_il.txt:36-155`，按 `m_NodeIndex.x/.y` 补 0 占位）。

**端点坐标的算法（决定我们能否离线算）**：`ObjectToolBaseSystem/CreateDefinitionsJob::UpdateSubNets`
`stconn2_objtoolbase_updatesubnets_il.txt:31-38` 先按 `2 * SubNet.Length` 开 `NativeList<float4>`；
`:46-128` 对第 i 条 SubNet：**在 `m_NodeIndex.x` 槽上累加 `float4(curve.a, 1)`，在 `m_NodeIndex.y` 槽上累加 `float4(curve.d, 1)`**（`m_NodeIndex` 分量 `< 0` 就跳过，`:52-55`、`:86-92`）；
`:129-156` 全部除以 `w`（累加计数）→ **同一 `m_NodeIndex` 的多条子边端点取平均，得到唯一的局部节点位置**；
`:159-196` 用 `NetUtils::GetSubNet(...)` 取（可能按左右行 invert 过的）SubNet，再调 `CreateSubNet(...)`。
`CreateSubNet` 里：`nodeIndex.x >= 0` 时把 `nodePositions[nodeIndex.x]` 经 `ObjectUtils::LocalToWorld(parentTransform, …)` 转世界（并在需要时采样水面高度）作为 `CoursePos.m_Position`（`stconn2_objtoolbase_createsubnet_il.txt:322-371` 起点、`:403-452` 终点）；`< 0` 时用曲线端点本身（`:227-234`/`:372-377`）。
另：`GetSubNet` 会按 `NetInvertMode` 与 `NetGeometryData.m_Flags & 32768` 决定整体反转（曲线 `MathUtils::Invert` + `m_NodeIndex.yx` + `m_ParentMesh.yx` + 左右行修正）：`stconn2_netutils_getsubnet_il.txt:9-45`。

⇒ **离线可行**：给定建筑世界变换 + 预制件实体上的 `Game.Prefabs.SubNet` buffer（运行时读得到），即可算出每条站内轨道边的世界曲线与**端点世界坐标**（同 index 求平均）。注意 `m_ParentMesh` 分量 ≥ 0 时坐标是相对某个 sub-mesh 的（`CreateSubNet:236-248`），车站站内轨道一般是 `-1`，但仍要按 `-1` 校验。

### 3.2 实例侧（建筑实例上这几个 buffer/组件的元素结构）

| 类型 | 接口 | 字段 | 证据 |
|---|---|---|---|
| `Game.Net.SubNet` | IBufferElementData, IEquatable, IEmptySerializable | `Entity m_SubNet` | `gamenet_types.txt:873-874` |
| `Game.Net.SubLane` | IBufferElementData | `Entity m_SubLane`；`PathMethod m_PathMethods` | `gamenet_types.txt:875-877` |
| `Game.Net.ConnectedEdge` | IBufferElementData（**挂在 node 上**） | `Entity m_Edge` | `gamenet_types.txt:865-866` |
| `Game.Net.ConnectedNode` | IBufferElementData（挂在 edge 上，子节点/曲线交点） | `Entity m_Node`；`Single m_CurvePosition` | `gamenet_types.txt:867-869` |
| `Game.Net.Edge` | IComponentData | `Entity m_Start`；`Entity m_End` | `archive/decompiled/game-net/Game.Net.Edge.decompiled.cs:6-11` |
| `Game.Net.Node` | IComponentData | `float3 m_Position`；`quaternion m_Rotation` | `archive/decompiled/game-net/Game.Net.Node.decompiled.cs:8-12` |
| `Game.Net.EdgeLane` | IComponentData | `float2 m_EdgeDelta`；`Byte m_ConnectedStartCount`；`Byte m_ConnectedEndCount` | `gamenet_types.txt:888-891` |
| `Game.Net.NodeLane` | IComponentData | `float2 m_WidthOffset`；`NodeLaneFlags m_Flags`；`Byte m_SharedStartCount/SharedEndCount` | `gamenet_types.txt:892-896` |
| `Game.Net.LocalConnect` / `LocalConnectData` | 组件 / 预制件 | `LocalConnectFlags m_Flags`、`Layer m_Layers`、`Bounds1 m_HeightRange`、`Single m_SearchDistance` | `gamenet_types.txt:870`；`stconn2_types_localnode.txt`（`LocalConnectData` 段） |
| `Game.Tools.LocalNodeCache` | IBufferElementData（**定义实体上**，区域工具用） | `float3 m_Position`；`Int32 m_ParentMesh` | `stconn2_types_localnode.txt` |

其它相关：`Game.Net.SubReplacement`（`Entity m_Prefab; SubReplacementType m_Type; SubReplacementSide m_Side; AgeMask m_AgeMask`，`gamenet_types.txt:801-805`）、`Game.Net.Upgraded`（`CompositionFlags m_Flags`，`gamenet_types.txt:799-800`）、`Game.Net.TrackTypes`=`{None=0,Train=1,Tram=2,Subway=4}`、`Game.Net.Layer`（`TrainTrack=64`、`TramTrack=1024`、`SubwayTrack=2048`）、`CoursePosFlags`/`CreationFlags`/`TempFlags` 全表见 `stconn2_enums.txt`。

实例侧自动维护关系（重要）：
`Owner`（`Game.Common.Owner.m_Owner`）**是**把边/节点归属到建筑的唯一钥匙 → `SubNetReferencesSystem` 自动填建筑的 `SubNet`（§Q1-C）。
`Game.Prefabs.BuildingPrefab::RefreshArchetype` 只在预制件有 `BuildingUpgradeElement` 时给建筑原型加 `Game.Net.SubNet`（`stconn2_buildingprefab_refresharch_il.txt:12-27`）。

---

## Q4. 最小程序化接线方案（排序：证据强度 / 风险）

### ① 先做一轮「测量」（**必须**，0 代码风险）
把下面这些打全（一次 update 里 dump 到 `ModsData`）：
* 车站建筑实体：`Game.Net.SubNet` 长度与每条 `m_SubNet`；对每条：`Game.Net.Edge{m_Start,m_End}`、`Game.Common.Owner`（确认归属）、`Game.Prefabs.PrefabRef.m_Prefab` 的名字、`Game.Net.SubLane` 列表 + 每条 lane 的 `PrefabRef`→`Game.Prefabs.TrackLaneData.m_TrackTypes`、两端 `Node.m_Position`、节点 `ConnectedEdge`（列出每条边的 owner，判断有没有「外来边」）。
* 我们正线：每条边的 `Edge`、两端 `Node.m_Position`、`ConnectedEdge`、`SubLane` 的 `TrackLaneData.m_TrackTypes`、以及该处 `ConnectionWarningSystem` 图标挂在哪个实体上。
**判读**：车站 `SubNet` 为空 → 走 ②-B；非空但站内节点只有自己的边 → 走 ②-A（把正线端点指过去）；已连上但 `TrackTypes` 不同 → 换网/换站内网预制件（回到 Q2 的预制件 dump）。
（证据强度：高——全部字段已定位到具体类型/行号；风险：无。）

### ②-A 让正线课程端点直接复用车站站内 node（**首选实现**）
在生成接站那一段课程时，把 `NetCourse` 的 `m_StartPosition.m_Entity`（或 `m_EndPosition.m_Entity`）设为站内轨道端点 node 实体，并把该 CoursePos 的 `m_Position/m_Rotation/m_Elevation/m_CourseDelta` 填成该 node 的实际值（`m_CourseDelta` 取 0 或 1 视边方向）。
* 证据：`GenerateEdgesSystem.cs:1256-1262`（永久阶段直接复用实体）、`:1265-1285`（临时阶段按 `NodeMapKey.m_OriginalEntity` 命中并返回既有实体）、`:1294`（边端点即取得）、`:1310`（已连通则跳过）；Apply 阶段重挂引用：`stconn2_patchtemprefs_il.txt:176-392`。
* 证据强度：**最高**（这是玩家用铺轨工具接站时走的同一段代码）。
* 风险：中。① 需要课程在 `CreationFlags.Permanent` 阶段被处理（我们的 Clear→None→Apply 握手已满足），否则要走 `NodeMap` 分支；② 节点几何/车道连接（`NodeLane`/`EdgeLane`/`LaneConnection`）由 `Updated` 驱动的系统重算——**未在游戏内验证**；③ 若站内 node 不存在（②-①判读为空）则本方案不适用。

### ②-B 由 mod 按官方配方补发「站内 sub-net 课程」（车站没有站内轨道时的正解）
对预制件 `Game.Prefabs.SubNet` 的每条，发一个定义实体：
`CreationDefinition{ m_Prefab = SubNet.m_Prefab, m_SubPrefab = Entity.Null, m_Flags = Permanent（+需要时 SubElevation 等）, m_RandomSeed }`
`+ NetCourse{ m_Curve = ObjectUtils.LocalToWorld(buildingTransform, SubNet.m_Curve), m_StartPosition/m_EndPosition { m_Position = 上面 §3.1 的平均节点世界坐标（或 curve.a/.d）, m_Rotation = NetUtils.GetNodeRotation(切线, 建筑旋转), m_CourseDelta = 0/1, m_Elevation, m_ParentMesh = SubNet.m_ParentMesh 分量, m_Entity = 我们正线的 node 实体 } , m_FixedIndex = -1 }`
`+ OwnerDefinition{ m_Prefab = 车站建筑预制件实体, … } + Updated`。
* 证据：`stconn2_objtoolbase_createsubnet_il.txt` 全篇（官方 CreateSubNet 逐条对齐：`m_Prefab/m_SubPrefab/RandomSeed` `:12-21`、曲线 LocalToWorld `:42-56`、节点位置来自 nodePositions `:322-371`、旋转 `NetUtils.GetNodeRotation(StartTangent/EndTangent)` `:291-305`/`:378-386`、`CourseDelta 0/1` `:306-309`/`:387-390`、`ParentMesh` `:317-321`、`Flags IsFirst|IsLast(+FreeHeight)` `:458-521`、`AddComponent<NetCourse>` `:522-526`、`OwnerDefinition` `:281-290`）。
* 收益：建筑的 `Game.Net.SubNet` 会被 `SubNetReferencesSystem` 自动填好（`stconn2_subnetrefs_il.txt:86-160`），不需要手写任何 buffer。
* 证据强度：高（等于照抄官方工具配方）。风险：中高（字段多、坐标/父网格/左右行 invert 容易错；需要一轮小场景验证）。

### ③ 手工写图（最后手段）
把正线边 `Edge.m_Start` 改成站内 node、往该 node 的 `ConnectedEdge` 里加正线边、给 node/边打 `Updated`，指望 `GeometrySystem` / `CompositionSelectSystem` / `LaneConnectionSystem` / `LaneReferencesSystem` 重算节点几何与车道连接。
* 证据强度：弱。只有「`Updated` 驱动重算」这一层是间接证据（`GenerateNodesSystem/GenerateEdgesSystem` 都会打 Temp/Created/Updated，`GenerateEdgesSystem.cs:1253`；`NodeReductionSystem` 查询也是 `All{Temp,Node,Updated}`，`stconn2_nodereduction_all_il.txt:20-56`）；**没有**找到任何系统「只靠 `Updated` 重建 `LaneConnection`/`NodeLane`」的直接证据。
* 风险：高（很可能要连 `NodeLane`/`EdgeLane`/`LaneConnection` 一起手工修，容易制造隐形断轨）。

### ④ 「站内铺轨留缺口，靠坐标重合自动接通」——**无效，别做**
* `GenerateNodesSystem/NodeKey.Equals`（`GenerateNodesSystem.cs:146-175`）与 `GenerateEdgesSystem/NodeMapKey.Equals`（`GenerateEdgesSystem.cs:60-81`）：只要一方是既有实体就按实体比，**不比坐标**；两侧都是新节点时才比「完全相等」的坐标，且那两个节点必须在同一帧的更新队列里（`CollectUpdatesJob`，`GenerateNodesSystem.cs:1035-1079`）。
* `NodeReductionSystem` 不适用：`CanMove` 先要求 `Temp.m_Original != Null`（`stconn2_nodereduction_all_il.txt:348-359`），且要恰好 2 条**同预制件的临时边**（`:362-366` 起）。
* 所以正线只是「穿过车站、位置对得上」永远不会连；这也解释了当前的现象。

### ⑤ 「发 Upgrade 定义把站内重叠边换成 Station Side」——证据最弱，降级
* `CreationFlags.Upgrade` 机制本身存在（`GenerateEdgesSystem.cs:1396-1403` → `TempFlags.Upgrade`；`Temp::m_Original` 指向被替换的原边），程序上是能做「替换」的，**但**：
  * 没有任何证据表明游戏在「放置车站」时会做这种替换（§Q1）；
  * 「Station Side」是**站内轨道自己的预制件**这一解释更自洽（§Q2），替换重叠边并不能让站内节点与正线共用 node 实体 —— 也就是**换了预制件仍然不连通**，除非同时做 ②-A。
* 证据强度：低。风险：高（改坏既有永久边）。

---

## 5. 明确「未确认」的部分 + 下一步该抓什么

1. **未确认：`… - Station Side` 到底被谁引用。** 只能通过运行时预制件实体图确认。
   下一步：在 mod 里做一次 dump —— 遍历所有带 `Game.Prefabs.SubNet` buffer 的预制件实体，打印 `PrefabRef`/名字（或 `Game.Prefabs.PrefabData` 索引）；重点看 `TrainStation01` 预制件的 `SubNet` 每条 `m_Prefab` 是否 = 那两个 Station Side 实体；再反向扫「哪些预制件的 SubNet 指向 Station Side」。工具：`Game.Prefabs.PrefabSystem`（`archive/decompiled/game-prefab/Game.Prefabs.PrefabSystem.decompiled.cs`）取实体。
2. **未确认：我们的车站实例上 `Game.Net.SubNet` 是否为空。** §1.3 是**推断**（依据：发 sub-net 定义的代码只在工具里）。必须实测（§4-①）。
3. **未确认：警告图标具体挂在哪个实体。** `TrafficConfigurationData.m_TrackConnectionNotification` 的两条入口（节点路径 / 建筑路径）都能触发，本文只证明了两条入口的**前置条件**（`stationconn_warn_il.txt:1241-1261` 与 `:1420-1422`、`stconn2_warn_chain_il.txt:157-209`）。要确认得在 `UpdateConnectionWarnings` 的 `IconCommandBuffer` 写入点打日志，或直接在游戏里选中建筑看图标挂点。
4. **未确认：`TrackTypes` 是否真的匹配。** `CheckNodeConnections` 的判据（`(conn.m_TrackTypes & lane.m_TrackTypes) == lane.m_TrackTypes`）需要两侧 lane 的 `Game.Prefabs.TrackLaneData.m_TrackTypes` 实测值：正线 Double Train Track - Twoway vs 站内（Station Side）网预制件。这决定要不要换网预制件。
5. **未确认：`Updated` 是否足以让 `LaneConnection`/`NodeLane`/`EdgeLane` 重建**（决定方案 ③ 可行性）。下一步：在 Game.dll 里定位写 `Game.Net.LaneConnection`/`NodeLane` 的系统及其查询（本次未抓；`ConnectionWarningSystem` 只**读**这些 buffer）。
6. **未确认：`m_ParentMesh >= 0` 时站内轨道端点是否还相对某个 sub-mesh**（影响 §3.1 的离线坐标算法）。需实测 TrainStation01 的 SubNet 各条 `m_ParentMesh` 分量。

---

## 附：本会话新建证据文件（均在 `maps\yilan\rail\il\`，前缀 `stconn2_`）

抓取/判定类：
`stconn2_stationside_refs.txt`、`stconn2_stationside_ident.txt`（均为 0 命中）、`stconn2_types_station.txt`、`stconn2_types_prefabs_all.txt`(1663 类型)、`stconn2_objtoolsystem_types.txt`、`stconn2_objtool_nested_types.txt`、`stconn2_types_localnode.txt`、`stconn2_enums.txt`、`stconn2_localnodecache_refs.txt`、`stconn2_subnet_refs_prefabs.txt`(86 方法)、`stconn2_subnet_refs_other.txt`、`stconn2_subnet_writers.txt`、`stconn2_subnet_ctor_sites.txt`、`stconn2_netlaneinfo_refs.txt`。

IL 类：
`stconn2_objtoolbase_createsubnet_il.txt`、`stconn2_objtoolbase_updatesubnets_il.txt`、`stconn2_netutils_getsubnet_il.txt`、`stconn2_objinit_subnets_job_il.txt`、`stconn2_objinitsys_onupdate_il.txt`、`stconn2_objtool_snapjob_il.txt`、`stconn2_objtool_subreplace_il.txt`、`stconn2_auxiliarynets_il.txt`、`stconn2_buildingprefab_refresharch_il.txt`、`stconn2_buildinginit_connreq_job_il.txt`、`stconn2_netobjectinit_checkownerlanes_il.txt`、`stconn2_subnetrefs_il.txt`、`stconn2_patchtemprefs_il.txt`、`stconn2_warn_subnetconn_il.txt`、`stconn2_warn_chain_il.txt`、`stconn2_nodereduction_all_il.txt`、`stconn2_nodereduction_onupdate_il.txt`。
