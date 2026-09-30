// HarbinBridgeLift —— 铁路铺设工具
//
// v16（2026-09-30）**照 E2C 出货版 OsmRoadToolSystem 的时序 1:1 复刻**，这是本轮的关键修正。
// 依据（三条独立证据）：
//   ① Game.dll IL：GenerateEdgesSystem.m_DefinitionQuery = desc0{All: CreationDefinition+NetCourse+Updated}
//      ∪ desc1{All: Updated, Any: Node|Edge, None: Deleted}；GenerateNodesSystem.m_DefinitionQuery =
//      {All: CreationDefinition+Updated, Any: NetCourse|ObjectDefinition}；ApplyNetSystem.m_TempQuery =
//      {All: Temp, Any: Node|Edge|Lane|Aggregate}，且 ApplyNetSystem **没有任何 ToolSystem/applyMode 字段**。
//   ② Game.Tools.ToolOutputBarrier : SafeCommandBufferSystem，而 ApplyNetSystem 持有的正是这个 barrier
//      （ApplyNetSystem.m_ToolOutputBarrier）——定义实体必须排进**这个** barrier 才会被回放给生成系统。
//   ③ E2C 出货版 OsmRoadToolSystem（archive/decompiled/e2c，对应已跑通的公路导入）实际做的事：
//        OnCreate:                applyMode = Clear(2)
//        OnUpdate 每帧开头:        applyMode = Clear(2)
//        有活时的排队帧:            applyMode = None(0)，定义实体写进 ToolOutputBarrier 的 ECB
//        等到 tempQuery(WithAll<Temp>) 非空: applyMode = Apply(1)
//      —— 即 applyMode 是有状态的握手，不是「一直 Apply」。
//
// v15 的两个偏差（就是短轨不出边的根因）：用了 Game.SafeCommandBufferSystem 而不是 ToolOutputBarrier；
// 且 applyMode 直接顶到 Apply、没有 Clear/None 与「等 Temp 出现」这一步。
using System;
using System.Collections.Generic;
using System.Text;
using Game.Prefabs;
using Game.Tools;
using Unity.Entities;
using Unity.Jobs;
using Unity.Mathematics;
using UnityEngine.Scripting;

namespace HarbinBridgeLift
{
    public partial class RailLayToolSystem : ToolBaseSystem
    {
        // ---------------- 外部闸门与数据（按钮 / BridgeLiftSystem 写） ----------------
        public static volatile bool Wanted;
        public static BridgeLifter.RailTestData Pending;
        public static Entity PendingPrefab = Entity.Null;
        /// <summary>v27 车站接线：本会话已放置车站的**站内轨道节点**（先⑧放站、后⑦铺轨才非空）。
        /// 铺轨时用它们跳过站内跨距、并把正线端点经 CoursePos.m_Entity 挂上（见 BridgeLifter.StationCourseAdjust）。</summary>
        public static BridgeLifter.StationNodes PendingStationNodes;
        public static float PendingStationSnapM;
        /// <summary>v33：整个铺线的**首/末端点**要挂到的既有节点（⑨ 站前路接既有村道用）。</summary>
        public static Entity PendingAttachStart = Entity.Null;
        public static Entity PendingAttachEnd = Entity.Null;
        public static float3 PendingAttachStartPos;
        public static float3 PendingAttachEndPos;

        // ---------------- 握手状态（自查报告用，逐帧可读） ----------------
        public static bool Queued;              // 定义实体是否已排进 ToolOutputBarrier
        public static bool WaitingForTemp;      // 是否在等原版生成出 Temp 预览实体
        public static bool Aborted;             // 地形采样不可用等前置条件不满足 → 本轮中止
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
        private static TerrainSampler.Session s_terrain;

        /// <summary>每次点按钮前重置本轮状态（含地形采样会话，地形可能已被编辑过）。</summary>
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
            s_terrain = null;
            Created.Clear();
            QueueLog.Clear();
        }

        public override string toolID => "HarbinBridgeLift RailLay";

        [Preserve]
        protected override void OnCreate()
        {
            base.OnCreate();
            _prefab = null;
            // E2C：tempQuery = WithAll<Temp>()（就是「原版有没有生成出预览」的判据）
            _tempQuery = GetEntityQuery(ComponentType.ReadOnly<Temp>());
            SetMode(ApplyMode.Clear);        // E2C OnCreate 行 90
        }

        public override PrefabBase GetPrefab() => _prefab;

        public override bool TrySetPrefab(PrefabBase prefab)
        {
            _prefab = prefab;
            return true;
        }

        // 无参 OnUpdate() 在 ToolBaseSystem 里是 sealed，只能重写 JobHandle 版（E2C 同）
        protected override JobHandle OnUpdate(JobHandle inputDeps)
        {
            UpdateCount++;
            if (!Wanted)
            {
                SetMode(ApplyMode.Clear);    // 空闲态
                return inputDeps;
            }

            SetMode(ApplyMode.Clear);        // E2C 行 154：每帧先回空闲态

            if (!Queued && Pending != null && PendingPrefab != Entity.Null)
            {
                SetMode(ApplyMode.None);     // E2C 行 165：排队帧
                try
                {
                    ToolOutputBarrier barrier = World.GetOrCreateSystemManaged<ToolOutputBarrier>();
                    if (barrier == null)
                    {
                        QueueLog.AppendLine("失败：ToolOutputBarrier 取不到");
                    }
                    else
                    {
                        // v17：先拿地形采样器；拿不到就中止（宁可不动图，也不要把轨铺到地下）
                        if (s_terrain == null) s_terrain = BridgeLifter.OpenTerrain(World, Pending, QueueLog);
                        if (s_terrain == null)
                        {
                            Queued = true;
                            Aborted = true;
                            QueueLog.AppendLine("已中止：地形采样不可用，本轮没有排队任何定义实体。");
                            return inputDeps;
                        }
                        barrier.AllowUsage();
                        EntityCommandBuffer ecb = barrier.CreateCommandBuffer();
                        BridgeLifter.QueueRailSegments(ecb, World.EntityManager, PendingPrefab,
                                                       Pending, QueueLog, Created, s_terrain,
                                                       PendingStationNodes, PendingStationSnapM,
                                                       PendingAttachStart, PendingAttachStartPos,
                                                       PendingAttachEnd, PendingAttachEndPos);
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
                    SetMode(ApplyMode.Apply);   // E2C 行 188：预览出现了，交给 ApplyNetSystem 落实
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
