// HarbinBridgeLift —— 车站放置工具（S2 车站轮 2026-09-30）
//
// 与 RailLayToolSystem 同构（E2C 出货版 OsmRoadToolSystem 的 Clear→None→（等 Temp）→Apply 握手，
// 已在铁路轮 v16–v24 验证），唯一差别：定义实体走**对象分支** ——
//   CreationDefinition{m_Prefab=车站建筑预制件} + ObjectDefinition{m_ParentMesh=-1, m_Position,
//   m_Rotation, m_Local*=同值} + Updated   （官方配方 = 反编译 NetToolSystem.CreateCourseObject，
//   archive\decompiled\tool\NetToolSystem.cs:2872-2902，无 owner 分支不加 OwnerDefinition、无 CreationFlags）
// 消费侧证据：GenerateNodesSystem.m_DefinitionQuery = All{CreationDefinition,Updated} Any{NetCourse,
// ObjectDefinition}（同查询两分支通吃）；对象的 Temp→永久由 Game.Tools.ApplyObjectsSystem/
// HandleTempEntitiesJob 落实（maps\yilan\rail\il\tools_addcomponent.txt），与 ApplyNetSystem 平行，
// 所以 applyMode 握手照旧。
//
// 为什么单独一个类而不是塞进 RailLayToolSystem：⑦ 是已验收的铁路路径，S3 要在**同一个会话**里
// 先⑦后⑧，两个机制的 marker/状态必须互不污染（排障 §6：一轮只验证一个变化、分类分开数）。
using System;
using System.Collections.Generic;
using System.Text;
using Game.Prefabs;
using Game.Tools;
using Unity.Entities;
using Unity.Jobs;
using UnityEngine.Scripting;

namespace HarbinBridgeLift
{
    public partial class StationPlaceToolSystem : ToolBaseSystem
    {
        // ---------------- 外部闸门与数据（按钮 / BridgeLiftSystem 写） ----------------
        public static volatile bool Wanted;
        public static BridgeLifter.StationData Pending;
        public static Entity PendingPrefab = Entity.Null;

        // ---------------- 握手状态（自查报告用，逐帧可读） ----------------
        public static bool Queued;
        public static bool WaitingForTemp;
        public static bool Aborted;
        public static int UpdateCount;
        public static int ApplyFrames;
        public static int TempCountAtQueue = -1;
        public static int TempCountAtApply = -1;
        public static int ApplyUpdateIndex = -1;
        public static ApplyMode LastApplyMode = ApplyMode.None;
        public static readonly List<Entity> Created = new List<Entity>();
        public static readonly StringBuilder QueueLog = new StringBuilder();

        private PrefabBase _prefab;
        private EntityQuery _tempQuery;

        public static void ResetRun()
        {
            Queued = false;
            WaitingForTemp = false;
            Aborted = false;
            UpdateCount = 0;
            ApplyFrames = 0;
            ApplyUpdateIndex = -1;
            TempCountAtQueue = -1;
            TempCountAtApply = -1;
            Created.Clear();
            QueueLog.Clear();
        }

        public override string toolID => "HarbinBridgeLift StationPlace";

        [Preserve]
        protected override void OnCreate()
        {
            base.OnCreate();
            _prefab = null;
            _tempQuery = GetEntityQuery(ComponentType.ReadOnly<Temp>());
            SetMode(ApplyMode.Clear);
        }

        public override PrefabBase GetPrefab() => _prefab;

        public override bool TrySetPrefab(PrefabBase prefab)
        {
            _prefab = prefab;
            return true;
        }

        protected override JobHandle OnUpdate(JobHandle inputDeps)
        {
            UpdateCount++;
            if (!Wanted)
            {
                SetMode(ApplyMode.Clear);    // 空闲态
                return inputDeps;
            }

            SetMode(ApplyMode.Clear);        // 每帧先回空闲态（E2C 行 154）

            if (!Queued && Pending != null && PendingPrefab != Entity.Null)
            {
                SetMode(ApplyMode.None);     // 排队帧（E2C 行 165）
                try
                {
                    ToolOutputBarrier barrier = World.GetOrCreateSystemManaged<ToolOutputBarrier>();
                    if (barrier == null)
                    {
                        QueueLog.AppendLine("失败：ToolOutputBarrier 取不到");
                    }
                    else
                    {
                        // y 高程在 StationTool（ModificationEnd 相位）里已采好地形写进 Pending；
                        // 这里只排队定义实体，不做采样（宁可不动图，也不把建筑放到地下/悬空）。
                        barrier.AllowUsage();
                        EntityCommandBuffer ecb = barrier.CreateCommandBuffer();
                        BridgeLifter.QueueStationObjects(ecb, PendingPrefab, Pending, QueueLog, Created);
                        // v28（P1）：再按预制件的站内轨道数据发子网定义实体——否则只有建筑外壳、
                        // 站内 SubNet 为 0 条（v26 诊断实测），站台不成形也无法与正线接线。
                        BridgeLifter.QueueStationSubNets(World, ecb, World.EntityManager, PendingPrefab,
                                                        Pending, QueueLog, Created);
                        Queued = true;
                        WaitingForTemp = true;
                        TempCountAtQueue = _tempQuery.CalculateEntityCount();
                    }
                }
                catch (Exception ex)
                {
                    QueueLog.AppendLine("工具内排队异常：" + ex);
                }
            }

            if (WaitingForTemp)
            {
                int n = _tempQuery.CalculateEntityCount();
                if (n > TempCountAtQueue)
                {
                    SetMode(ApplyMode.Apply);   // 预览出现了，交给 ApplyObjectsSystem/ApplyNetSystem 落实
                    WaitingForTemp = false;
                    ApplyFrames++;
                    ApplyUpdateIndex = UpdateCount;
                    TempCountAtApply = n;
                }
            }

            return inputDeps;
        }

        private void SetMode(ApplyMode m)
        {
            applyMode = m;
            LastApplyMode = m;
        }
    }
}
