// HarbinBridgeLift —— 干活的地方
//
// v2 修正（v1 导致游戏原生崩溃，见 Player.log 的 Native Crash Reporting）：
//   ① v1 对「没有 Game.Net.Elevation 组件」的节点直接调 SetComponentData<Elevation>。
//      实测扫描结果：命中的 1640 个节点里，有 Elevation 的是 0 个。
//      对缺失组件写数据的后果不是干净的托管异常，而是踩坏内存 → 之后 AddComponent
//      处原生崩溃。现在一律「有就 Set，没有就 Add」。
//   ② v1 照抄 Earth2Cities 的 MoveImportedRoads，在 UI 按钮回调里直接做结构变更。
//      那只搬位置（SetComponentData，非结构变更）尚且可以，AddComponent 不行。
//      现在按钮只排队，真正的活儿在 BridgeLiftSystem（ModificationEnd 相位）里做，
//      结构变更全部走 EntityCommandBuffer 批量提交。
//   ③ v1 用 new Entity{Index=..,Version=0} 重建 prefab entity 去查组件，
//      Version 不对 → HasComponent 全 false → 扫描报告里门槛全是 NaN。

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using Colossal.Mathematics;
using Game.Common;
using Game.Net;
using Game.Prefabs;
using Game.Tools;
using Unity.Collections;
using Unity.Entities;
using Unity.Mathematics;

namespace HarbinBridgeLift
{
    public static class BridgeLifter
    {
        // ---------------------------------------------------------------- 小工具

        /// <summary>节点"高出地面/水面多少米"。语义不对（负数或离谱大值）就当 0。</summary>
        private static float CurrentRel(EntityManager em, Entity e)
        {
            if (!em.HasComponent<Game.Net.Elevation>(e)) return 0f;
            float v = math.cmin(em.GetComponentData<Game.Net.Elevation>(e).m_Elevation);
            if (float.IsNaN(v) || v < 0f || v > 200f) return 0f;
            return v;
        }

        /// <summary>规范写法：有就 Set，没有就 Add。绝不能对缺失组件 SetComponentData。</summary>
        private static void ApplyElevation(EntityManager em, EntityCommandBuffer ecb, Entity e, float2 v)
        {
            if (em.HasComponent<Game.Net.Elevation>(e)) ecb.SetComponent(e, new Game.Net.Elevation(v));
            else ecb.AddComponent(e, new Game.Net.Elevation(v));
        }

        private static void ApplyUpdated(EntityManager em, EntityCommandBuffer ecb, Entity e)
        {
            if (!em.HasComponent<Updated>(e)) ecb.AddComponent<Updated>(e);
        }

        private sealed class Grid
        {
            private readonly Dictionary<long, List<int>> _cells = new Dictionary<long, List<int>>();
            private readonly float _cell;

            public Grid(BridgeLiftData d, float cell = 32f)
            {
                _cell = cell;
                for (int i = 0; i < d.Count; i++)
                {
                    long k = Key((int)Math.Floor(d.xs[i] / cell), (int)Math.Floor(d.zs[i] / cell));
                    if (!_cells.TryGetValue(k, out var list)) { list = new List<int>(4); _cells[k] = list; }
                    list.Add(i);
                }
            }

            private static long Key(int cx, int cz) => ((long)cx << 32) ^ (uint)cz;

            public float Max(BridgeLiftData d, float x, float z, float rad, out int idx)
            {
                idx = -1;
                float best = 0f;
                float r2 = rad * rad;
                int c0 = (int)Math.Floor((x - rad) / _cell), c1 = (int)Math.Floor((x + rad) / _cell);
                int r0 = (int)Math.Floor((z - rad) / _cell), r1 = (int)Math.Floor((z + rad) / _cell);
                for (int cx = c0; cx <= c1; cx++)
                {
                    for (int cz = r0; cz <= r1; cz++)
                    {
                        if (!_cells.TryGetValue(Key(cx, cz), out var list)) continue;
                        for (int n = 0; n < list.Count; n++)
                        {
                            int i = list[n];
                            float dx = x - d.xs[i], dz = z - d.zs[i];
                            float d2 = dx * dx + dz * dz;
                            if (d2 <= r2 && d.cs[i] > best) { best = d.cs[i]; idx = i; }
                        }
                    }
                }
                return best;
            }
        }

        private static EntityQuery QueryNodes(EntityManager em)
            => em.CreateEntityQuery(ComponentType.ReadWrite<Node>());

        private static long PosKey(float x, float z)
            => ((long)(int)Math.Round(x) << 32) ^ (uint)(int)Math.Round(z);

        private static EntityQuery QueryEdges(EntityManager em)
            => em.CreateEntityQuery(ComponentType.ReadWrite<Edge>(), ComponentType.ReadWrite<Curve>());

        private sealed class NodeScan
        {
            public int Total, RoadCapable;
            public bool UseRoadFilter;
            public NativeArray<Entity> Entities;
        }

        private static NodeScan OpenNodes(EntityManager em)
        {
            var s = new NodeScan();
            s.Entities = QueryNodes(em).ToEntityArray(Allocator.Temp);
            foreach (Entity e in s.Entities)
            {
                if (em.HasComponent<Temp>(e)) continue;
                s.Total++;
                if (em.HasComponent<Road>(e)) s.RoadCapable++;
            }
            s.UseRoadFilter = s.RoadCapable > 0;
            return s;
        }

        private static bool EligibleNode(EntityManager em, NodeScan s, Entity e)
        {
            if (em.HasComponent<Temp>(e)) return false;
            if (s.UseRoadFilter && !em.HasComponent<Road>(e)) return false;
            return true;
        }

        // ---------------------------------------------------------------- 扫描

        public static string Scan(World world, BridgeLiftData d, float radius)
        {
            var sb = new StringBuilder();
            EntityManager em = world.EntityManager;
            sb.AppendLine($"数据：{d.Count} 个点，结构 {d.StructureCount} 处，"
                          + $"海平面 {d.sea_level:F1} m，默认净空 {d.default_clearance:F1} m，"
                          + $"引道 {d.ramp_length:F0} m，配对半径 {radius:F2} m");

            TerrainSampler.Session ts = TerrainSampler.Open(world, out string why);
            sb.AppendLine($"地形采样：{(why == null ? "开启成功" : "不可用 → " + why)}");
            sb.AppendLine($"  采样器诊断：{TerrainSampler.Diag}");

            var grid = new Grid(d);
            NodeScan ns = OpenNodes(em);

            // 逐点配对诊断用：记下每个合格节点的位置（依兰 2026-09-29：12 点只配上 7 个，
            // 需要知道没配上的点离最近的游戏节点到底多远，才能定配对半径）
            var nodePos = new List<float3>(ns.Total);
            var nodeIdx = new List<int>(ns.Total);
            var nodePosY = new List<float>(ns.Total);

            int matched = 0, wouldChange = 0, elevPresent = 0, shown = 0;
            float elevMin = float.MaxValue, elevMax = float.MinValue, elevSum = 0f;

            foreach (Entity e in ns.Entities)
            {
                if (!EligibleNode(em, ns, e)) continue;
                Node n = em.GetComponentData<Node>(e);
                nodePos.Add(n.m_Position);
                nodeIdx.Add(e.Index);
                nodePosY.Add(n.m_Position.y);
                float c = grid.Max(d, n.m_Position.x, n.m_Position.z, radius, out int hit);
                if (hit < 0 || c <= 0.05f) continue;
                matched++;
                bool hasElev = em.HasComponent<Game.Net.Elevation>(e);
                float ev = 0f;
                if (hasElev)
                {
                    ev = math.cmin(em.GetComponentData<Game.Net.Elevation>(e).m_Elevation);
                    elevPresent++;
                    if (ev < elevMin) elevMin = ev;
                    if (ev > elevMax) elevMax = ev;
                    elevSum += ev;
                }
                bool ok = ts.TrySample(n.m_Position, out float terr);
                float baseY = ok ? math.max(terr, terr < d.sea_level ? d.sea_level : terr) : n.m_Position.y;
                float newY = baseY + c;
                if (math.abs(newY - n.m_Position.y) > 0.01f) wouldChange++;
                if (shown < 30)
                {
                    sb.AppendLine($"  [{shown + 1}] idx={e.Index} xz=({n.m_Position.x:F1},{n.m_Position.z:F1}) "
                                  + $"原Y={n.m_Position.y:F2} 地形={terr:F2}{(ok ? "" : "(采样失败)")} "
                                  + $"净空c={c:F2} → 目标Y={newY:F2} "
                                  + $"Elevation={(hasElev ? ev.ToString("F2") : "无")}");
                    shown++;
                }
            }
            ns.Entities.Dispose();

            // ---- 逐点配对诊断：每个高度场点离最近/次近游戏节点多远（只读，不改任何东西）
            sb.AppendLine($"逐点配对诊断（{d.Count} 个点，配对半径 {radius:F2} m）：");
            for (int i = 0; i < d.Count; i++)
            {
                float px = d.xs[i], pz = d.zs[i];
                int b1 = -1, b2 = -1;
                float d1 = float.MaxValue, d2 = float.MaxValue;
                for (int k = 0; k < nodePos.Count; k++)
                {
                    float dd = math.distance(new float2(px, pz), new float2(nodePos[k].x, nodePos[k].z));
                    if (dd < d1) { d2 = d1; b2 = b1; d1 = dd; b1 = k; }
                    else if (dd < d2) { d2 = dd; b2 = k; }
                }
                string s1 = b1 >= 0
                    ? $"idx={nodeIdx[b1]} ({nodePos[b1].x:F1},{nodePos[b1].z:F1}) d={d1:F2}m Y={nodePosY[b1]:F2}"
                    : "无节点";
                string s2 = b2 >= 0 ? $"次近 d={d2:F2}m" : "";
                sb.AppendLine($"  pt[{i}] ({px:F1},{pz:F1}) c={d.cs[i]:F2} {(d.cs[i] >= 9.99f ? "桥面" : "引道")} "
                              + $"→ 最近 {s1} {s2} {(d1 <= radius ? "[已配]" : "[未配!]")}");
            }

            // ---- 边：prefab 与「出桥墩门槛」
            // 注意 dictionary 的 key 用完整 Entity（带 Version），不能用 Index 重建
            EntityQuery qe = QueryEdges(em);
            NativeArray<Entity> edges = qe.ToEntityArray(Allocator.Temp);
            var prefabCount = new Dictionary<Entity, int>();
            int edgeTotal = 0, edgeRoad = 0;
            foreach (Entity e in edges)
            {
                if (em.HasComponent<Temp>(e)) continue;
                edgeTotal++;
                if (em.HasComponent<Road>(e)) edgeRoad++;
            }
            bool edgeRoadFilter = edgeRoad > 0;
            foreach (Entity e in edges)
            {
                if (em.HasComponent<Temp>(e)) continue;
                if (edgeRoadFilter && !em.HasComponent<Road>(e)) continue;
                if (!em.HasComponent<PrefabRef>(e)) continue;
                Entity pf = em.GetComponentData<PrefabRef>(e).m_Prefab;
                prefabCount.TryGetValue(pf, out int k);
                prefabCount[pf] = k + 1;
            }
            edges.Dispose();

            var pfSys = world.GetOrCreateSystemManaged<PrefabSystem>();
            sb.AppendLine($"路网：Node {ns.Total} 个（带 Road 的 {ns.RoadCapable}），"
                          + $"Edge {edgeTotal} 个（带 Road 的 {edgeRoad}）");
            sb.AppendLine($"      高度场命中 {matched} 个节点，其中 {wouldChange} 个需要改高度");
            sb.AppendLine($"      命中节点里已有 Game.Net.Elevation 的 {elevPresent} 个"
                          + (elevPresent > 0
                             ? $"，其 cmin 范围 [{elevMin:F2}, {elevMax:F2}]，均值 {elevSum / elevPresent:F2}"
                             : " —— 说明导入的路还没有任何高程组件，抬升时要 Add 而不是 Set"));
            sb.AppendLine($"      地形采样 hits={ts.Hits} misses={ts.Misses}");
            sb.AppendLine("边 prefab 阈值（RequireElevated 门槛 = m_ElevationLimit*2 + max(0, m_ElevationRange.min)）：");

            var list = new List<KeyValuePair<Entity, int>>(prefabCount);
            list.Sort((a, b) => b.Value.CompareTo(a.Value));
            for (int i = 0; i < list.Count && i < 20; i++)
            {
                Entity pf = list[i].Key;
                string name = "<?>";
                try { name = pfSys.GetPrefabName(pf); } catch { }
                bool hasLim = em.HasComponent<NetGeometryData>(pf);
                bool hasRange = em.HasComponent<PlaceableNetData>(pf);
                float lim = hasLim ? em.GetComponentData<NetGeometryData>(pf).m_ElevationLimit : float.NaN;
                Bounds1 r = hasRange ? em.GetComponentData<PlaceableNetData>(pf).m_ElevationRange : default;
                float thr = (float.IsNaN(lim) ? 0f : lim * 2f)
                            + (hasRange ? math.max(0f, r.min) : 0f);
                sb.AppendLine($"    {list[i].Value,5} 条  {name,-34} "
                              + $"NetGeometryData={hasLim} PlaceableNetData={hasRange} "
                              + $"elevLimit={(hasLim ? lim.ToString("F2") : "-")} "
                              + $"range={(hasRange ? $"[{r.min:F2},{r.max:F2}]" : "-")} → 门槛≈{thr:F2} m");
            }
            return sb.ToString();
        }

        // ---------------------------------------------------------------- 抬升

        public static string Lift(World world, BridgeLiftData d, float scale, float radius)
        {
            var sb = new StringBuilder();
            EntityManager em = world.EntityManager;
            var sw = System.Diagnostics.Stopwatch.StartNew();

            TerrainSampler.Session ts = TerrainSampler.Open(world, out string why);
            sb.AppendLine($"地形采样：{(why == null ? "OK" : "不可用 → " + why)}");

            var grid = new Grid(d);
            NodeScan ns = OpenNodes(em);

            // ---------- 阶段 1：只读，先算清楚要动谁、动到哪
            var nodeY = new Dictionary<Entity, float>();
            var nodeC = new Dictionary<Entity, float>();
            var list = new List<KeyValuePair<Entity, float>>();   // 保序，便于分帧/日志
            var backup = new BackupFile
            {
                stamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"),
                data_file = d.source_osm,
            };

            int matched = 0;
            foreach (Entity e in ns.Entities)
            {
                if (!EligibleNode(em, ns, e)) continue;
                Node n = em.GetComponentData<Node>(e);
                float raw = grid.Max(d, n.m_Position.x, n.m_Position.z, radius, out int hit);
                if (hit < 0) continue;
                float c = raw * scale;
                if (c <= 0.05f) continue;
                matched++;

                if (!ts.TrySample(n.m_Position, out float terr)) terr = n.m_Position.y;
                float baseY = math.max(terr, terr < d.sea_level ? d.sea_level : terr);
                float newY = baseY + c;

                bool hasElev = em.HasComponent<Game.Net.Elevation>(e);
                Game.Net.Elevation oldEl = hasElev ? em.GetComponentData<Game.Net.Elevation>(e) : default;
                backup.nodes.Add(new BackupEntry
                {
                    i = e.Index, x = n.m_Position.x, z = n.m_Position.z, y = n.m_Position.y,
                    hasElev = hasElev,
                    ex = hasElev ? oldEl.m_Elevation.x : 0f,
                    ey = hasElev ? oldEl.m_Elevation.y : 0f,
                });
                nodeY[e] = newY;
                nodeC[e] = c;
                list.Add(new KeyValuePair<Entity, float>(e, newY));
            }
            ns.Entities.Dispose();

            if (matched == 0)
            {
                sb.AppendLine("没有任何节点命中高度场 —— 什么都没改。");
                return sb.ToString();
            }
            if (matched > 200000)
            {
                sb.AppendLine($"命中 {matched} 个节点，超过 20 万的安全上限，已中止（防止误配数据把整张图抬了）。");
                return sb.ToString();
            }

            // ---------- 阶段 2：结构变更全部走 ECB，一次性提交
            var ecb = new EntityCommandBuffer(Allocator.TempJob);
            int moved = 0;
            foreach (var kv in list)
            {
                Entity e = kv.Key;
                Node n = em.GetComponentData<Node>(e);
                n.m_Position.y = kv.Value;
                ecb.SetComponent(e, n);
                float c = nodeC[e];
                ApplyElevation(em, ecb, e, new float2(c, c));
                ApplyUpdated(em, ecb, e);
                moved++;
            }
            ecb.Playback(em);
            ecb.Dispose();

            // ---------- 阶段 3：边跟着两端节点走
            var ecb2 = new EntityCommandBuffer(Allocator.TempJob);
            int touched = 0;
            EntityQuery qe = QueryEdges(em);
            NativeArray<Entity> edges = qe.ToEntityArray(Allocator.Temp);
            int edgeRoadCapable = 0;
            foreach (Entity e in edges)
                if (!em.HasComponent<Temp>(e) && em.HasComponent<Road>(e)) edgeRoadCapable++;
            bool edgeRoadFilter = edgeRoadCapable > 0;

            foreach (Entity e in edges)
            {
                if (em.HasComponent<Temp>(e)) continue;
                if (edgeRoadFilter && !em.HasComponent<Road>(e)) continue;
                Edge edge = em.GetComponentData<Edge>(e);
                bool cs = nodeY.TryGetValue(edge.m_Start, out float y0);
                bool ce = nodeY.TryGetValue(edge.m_End, out float y1);
                if (!cs && !ce) continue;
                if (!cs) y0 = em.GetComponentData<Node>(edge.m_Start).m_Position.y;
                if (!ce) y1 = em.GetComponentData<Node>(edge.m_End).m_Position.y;
                float r0 = cs ? nodeC[edge.m_Start] : CurrentRel(em, edge.m_Start);
                float r1 = ce ? nodeC[edge.m_End] : CurrentRel(em, edge.m_End);

                Curve curve = em.GetComponentData<Curve>(e);
                Bezier4x3 bz = curve.m_Bezier;
                Bezier4x3 nb = new Bezier4x3(
                    new float3(bz.a.x, y0, bz.a.z),
                    new float3(bz.b.x, y0 + (y1 - y0) / 3f, bz.b.z),
                    new float3(bz.c.x, y0 + (y1 - y0) * 2f / 3f, bz.c.z),
                    new float3(bz.d.x, y1, bz.d.z));
                curve.m_Bezier = nb;
                curve.m_Length = MathUtils.Length(nb);
                ecb2.SetComponent(e, curve);
                ApplyElevation(em, ecb2, e, new float2(r0, r1));
                ApplyUpdated(em, ecb2, e);
                touched++;
            }
            edges.Dispose();
            ecb2.Playback(em);
            ecb2.Dispose();

            backup.Save();
            sw.Stop();

            sb.AppendLine($"抬升完成：高度场命中 {matched} 个节点，改高度 {moved} 个，"
                          + $"联动边 {touched} 条（{sw.ElapsedMilliseconds} ms）");
            sb.AppendLine($"地形采样 hits={ts.Hits} misses={ts.Misses}");
            sb.AppendLine($"原值已备份到 {BackupFile.PathFor}（{backup.nodes.Count} 个节点）");
            return sb.ToString();
        }

        // ---------------------------------------------------------------- 批量街名

        /// <summary>解析 Game.UI.NameSystem 并对其 EndFrameBarrier 调 AllowUsage()。
        /// SetCustomName 内部经该 barrier（Game.EndFrameBarrier，world 系统实例）创建 ECB，
        /// 相位外调用不先 AllowUsage 会抛 "Trying to create EntityCommandBuffer when it's not allowed!"
        /// （v1 实测；v2 曾误对 world 里另一个 SafeCommandBufferSystem 实例 AllowUsage）。</summary>
        private static bool PrepareNameSystem(World world, out Game.UI.NameSystem nameSys, out string err)
        {
            err = null;
            nameSys = world.GetExistingSystemManaged<Game.UI.NameSystem>();
            if (nameSys == null) { err = "失败：Game.UI.NameSystem 不存在\n"; return false; }
            var bf = typeof(Game.UI.NameSystem).GetField("m_EndFrameBarrier",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            if (bf == null) { err = "失败：NameSystem.m_EndFrameBarrier 字段不存在（游戏版本变了？）\n"; return false; }
            object barrier = bf.GetValue(nameSys);
            var allow = barrier.GetType().GetMethod("AllowUsage");
            if (allow == null) { err = "失败：barrier.AllowUsage 方法不存在（游戏版本变了？）\n"; return false; }
            allow.Invoke(barrier, null);
            return true;
        }

        /// <summary>一条聚合拆出来的成员组（一个所有者）：名字 + 成员边。</summary>
        private sealed class AggGroup
        {
            public string Name;
            public readonly List<Entity> Edges = new List<Entity>();
        }

        /// <summary>批量轮的计数与统计（报告用）。</summary>
        private sealed class SplitStats
        {
            public int Owners, Clones, UnnamedOwners, ClearedEdges, NameOk;
            public int SplitAggregates, Downgraded, CloneFailed, MergedNoSplit;
            public readonly Dictionary<string, int> NameCounts = new Dictionary<string, int>();

            public void Count(string name)
            {
                NameCounts[name] = NameCounts.TryGetValue(name, out int c) ? c + 1 : 1;
            }
        }

        /// <summary>批量：把 68 的真名写进路网（v9 = 按连通性分组 + 多街名就地拆分 + 无名路）。
        /// 写法沿用已机主验收的单路试点序列（主 dll 3710B2BB…）：
        /// ① 名字写在**聚合实体**上；② 写前清该所有者**完整成员表**里所有边的自定义名（只清配对到的边会失败——v6 实测）；
        /// ③ 所有者补 Updated+BatchesUpdated；④ 成员边只给 BatchesUpdated、**绝不给 Updated**
        /// （给了 vanilla AggregateSystem 就会重算归属、把拆分抹掉——官方 advanced-road-naming 的核心禁忌）。
        /// 分组：成员边逐边配最近名字点 → 无名的边向相邻边扩散补齐 → 按节点连通性聚组；
        /// 一条聚合里出现多组不同街名 → **就地拆分**：克隆聚合实体、把该组成员边重挂到克隆
        /// （照抄官方 CreateAggregateClone / AssignAggregateEdges / SetAuthoritativeName，source-verified）；
        /// 没有名字证据的组写 `unnamedLabel`（默认「无名路」），不再留游戏花名。</summary>
        public static string SetNames(World world, StreetNames d)
        {
            var sb = new StringBuilder();
            sb.AppendLine("== 批量街名 v9（按聚合连通分组；多街名就地拆分；无名→" + d.unnamedLabel + "）==");
            EntityManager em = world.EntityManager;
            if (!PrepareNameSystem(world, out Game.UI.NameSystem nameSys, out string nerr)) return nerr;
            sb.AppendLine($"数据点 {d.Count} 个（配对半径 {d.radius:F2} m；组最小边数 {d.minComponentEdges}；"
                          + $"多街名拆分={d.splitMultiName}；无名标签「{d.unnamedLabel}」）");

            const float cell = 32f;
            var cells = new Dictionary<long, List<int>>();
            for (int i = 0; i < d.Count; i++)
            {
                long k = ((long)(int)Math.Floor(d.xs[i] / cell) << 32) ^ (uint)(int)Math.Floor(d.zs[i] / cell);
                if (!cells.TryGetValue(k, out var list)) { list = new List<int>(4); cells[k] = list; }
                list.Add(i);
            }
            float rad = d.radius, r2 = rad * rad;

            // ① 逐边配名（半径内最近名字点）+ 收集每条聚合的全部成员边
            var edgeNames = new Dictionary<Entity, string>();
            var aggRoad = new Dictionary<Entity, List<Entity>>();
            EntityQuery qe = QueryEdges(em);
            NativeArray<Entity> edges = qe.ToEntityArray(Allocator.Temp);
            int roadEdges = 0, assignedEdges = 0, noAgg = 0;
            foreach (Entity e in edges)
            {
                if (em.HasComponent<Temp>(e)) continue;
                if (!em.HasComponent<Road>(e)) continue;
                roadEdges++;
                Bezier4x3 bz = em.GetComponentData<Curve>(e).m_Bezier;
                float mx = (bz.a.x + bz.d.x) * 0.5f, mz = (bz.a.z + bz.d.z) * 0.5f;
                int best = -1; float bestD = r2;
                int c0 = (int)Math.Floor((mx - rad) / cell), c1 = (int)Math.Floor((mx + rad) / cell);
                int r0 = (int)Math.Floor((mz - rad) / cell), r1 = (int)Math.Floor((mz + rad) / cell);
                for (int cx = c0; cx <= c1; cx++)
                {
                    for (int cz = r0; cz <= r1; cz++)
                    {
                        if (!cells.TryGetValue(((long)cx << 32) ^ (uint)cz, out var list)) continue;
                        for (int n = 0; n < list.Count; n++)
                        {
                            int i2 = list[n];
                            float dx = mx - d.xs[i2], dz = mz - d.zs[i2];
                            float dd = dx * dx + dz * dz;
                            if (dd <= bestD) { bestD = dd; best = i2; }
                        }
                    }
                }
                Entity agg = em.HasComponent<Aggregated>(e)
                    ? em.GetComponentData<Aggregated>(e).m_Aggregate : Entity.Null;
                if (agg == Entity.Null || !em.Exists(agg)) { noAgg++; continue; }
                if (!aggRoad.TryGetValue(agg, out var lst)) { lst = new List<Entity>(); aggRoad[agg] = lst; }
                lst.Add(e);
                if (best < 0) continue;
                assignedEdges++;
                edgeNames[e] = d.names[best];
            }
            edges.Dispose();

            sb.AppendLine($"带 Road 边 {roadEdges}，配到名字的边 {assignedEdges}，聚合 {aggRoad.Count} 条"
                          + $"（{noAgg} 条边查不到聚合）");

            // ② 逐聚合分组 → 按组拆分/命名
            var st = new SplitStats();
            var splits = new List<string>();
            foreach (var kv in aggRoad)
            {
                Entity agg = kv.Key;
                List<Entity> members = kv.Value;
                if (em.HasBuffer<AggregateElement>(agg))
                {
                    var buf = em.GetBuffer<AggregateElement>(agg, true);
                    var all = new List<Entity>(buf.Length);
                    for (int i = 0; i < buf.Length; i++)
                    {
                        Entity m = buf[i].m_Edge;
                        if (m == Entity.Null || !em.Exists(m)) continue;
                        all.Add(m);
                    }
                    if (all.Count > 0) members = all;   // 成员表是权威全集（含这次没配到名字的边）
                }
                if (members.Count == 0) continue;
                List<AggGroup> groups = BuildGroups(em, members, edgeNames, d.unnamedLabel, d.minComponentEdges, st);
                if (!d.splitMultiName && groups.Count > 1)
                {
                    // 回滚位：不拆分 → 整条聚合取最大组的名字（v8 行为）
                    groups.Sort((a, b) => b.Edges.Count.CompareTo(a.Edges.Count));
                    for (int i = 1; i < groups.Count; i++) groups[0].Edges.AddRange(groups[i].Edges);
                    groups.RemoveRange(1, groups.Count - 1);
                    st.MergedNoSplit++;
                }
                ApplyGroups(em, nameSys, agg, groups, d.unnamedLabel, st);
                if (groups.Count > 1 && splits.Count < 60) splits.Add(DescribeGroups(agg, groups));
            }

            sb.AppendLine($"所有者：共 {st.Owners} 条（原聚合复用 {st.Owners - st.Clones} / 克隆 {st.Clones}）；"
                          + $"无名路所有者 {st.UnnamedOwners} 条；回读一致 {st.NameOk}/{st.Owners}；清边名 {st.ClearedEdges} 条");
            if (st.SplitAggregates > 0 || st.Downgraded > 0 || st.CloneFailed > 0 || st.MergedNoSplit > 0)
                sb.AppendLine($"拆分：{st.SplitAggregates} 条聚合被拆"
                              + (st.Downgraded > 0 ? $"；小组降级为无名 {st.Downgraded} 组" : "")
                              + (st.CloneFailed > 0 ? $"；克隆失败 {st.CloneFailed} 组（已并回原聚合）" : "")
                              + (st.MergedNoSplit > 0 ? $"；按配置未拆分 {st.MergedNoSplit} 条（取最大组名）" : ""));

            var top = new List<KeyValuePair<string, int>>(st.NameCounts);
            top.Sort((a, b) => b.Value.CompareTo(a.Value));
            sb.AppendLine($"-- 写名结果 {top.Count} 个名字（按所有者条数排序，前 25）--");
            for (int i = 0; i < top.Count && i < 25; i++)
                sb.AppendLine($"    {top[i].Value,3} 条  {top[i].Key}");

            if (splits.Count > 0)
            {
                sb.AppendLine("-- 被拆分的聚合（原本一条街名，现在按真实街名分给多个所有者）--");
                for (int i = 0; i < splits.Count; i++) sb.AppendLine("    " + splits[i]);
            }
            sb.AppendLine("下一步：存新档 → 目检各路牌（无名的应为「" + d.unnamedLabel + "」）→ 读档再目检（验收 = 名字对 + 读档还在）。");
            return sb.ToString();
        }

        // ---------------------------------------------------------------- 批量轮的分组/拆分/命名

        private static void AddNodeEdge(Dictionary<Entity, List<Entity>> map, Entity node, Entity edge)
        {
            if (node == Entity.Null) return;
            if (!map.TryGetValue(node, out var list)) { list = new List<Entity>(4); map[node] = list; }
            list.Add(edge);
        }

        private static void TallyNeighbors(Dictionary<Entity, List<Entity>> map, Entity node, Entity self,
            Dictionary<Entity, string> edgeNames, Dictionary<string, int> tally)
        {
            if (!map.TryGetValue(node, out var list)) return;
            for (int i = 0; i < list.Count; i++)
            {
                Entity e = list[i];
                if (e == self) continue;
                if (edgeNames.TryGetValue(e, out string n) && n != null)
                    tally[n] = tally.TryGetValue(n, out int c) ? c + 1 : 1;
            }
        }

        private static void PushSameName(Dictionary<Entity, List<Entity>> map, Entity node, Entity self,
            string want, Dictionary<Entity, string> edgeNames, string unnamed, HashSet<Entity> seen, List<Entity> stack)
        {
            if (!map.TryGetValue(node, out var list)) return;
            for (int i = 0; i < list.Count; i++)
            {
                Entity e = list[i];
                if (e == self || seen.Contains(e)) continue;
                string eff = edgeNames.TryGetValue(e, out string v) && v != null ? v : unnamed;
                if (eff != want) continue;
                seen.Add(e);
                stack.Add(e);
            }
        }

        /// <summary>把一条聚合的成员边聚成组：无名边先向相邻边扩散补齐，再按「同名 + 节点连通」分组。
        /// 组边数 &lt; minEdges 且不是无名组的，降级为无名（防一处误配把半条路改名）。</summary>
        private static List<AggGroup> BuildGroups(EntityManager em, List<Entity> members,
            Dictionary<Entity, string> edgeNames, string unnamed, int minEdges, SplitStats st)
        {
            var nodeEdges = new Dictionary<Entity, List<Entity>>();
            for (int i = 0; i < members.Count; i++)
            {
                Entity m = members[i];
                if (!em.HasComponent<Edge>(m)) continue;
                Edge ed = em.GetComponentData<Edge>(m);
                AddNodeEdge(nodeEdges, ed.m_Start, m);
                AddNodeEdge(nodeEdges, ed.m_End, m);
            }

            // 扩散补齐：无名边继承相邻边的名字（数轮内收敛）
            for (int pass = 0; pass < 4; pass++)
            {
                bool changed = false;
                for (int i = 0; i < members.Count; i++)
                {
                    Entity m = members[i];
                    if (edgeNames.ContainsKey(m) || !em.HasComponent<Edge>(m)) continue;
                    Edge ed = em.GetComponentData<Edge>(m);
                    var tally = new Dictionary<string, int>();
                    TallyNeighbors(nodeEdges, ed.m_Start, m, edgeNames, tally);
                    TallyNeighbors(nodeEdges, ed.m_End, m, edgeNames, tally);
                    string pick = null; int best = 0;
                    foreach (var p in tally) if (p.Value > best) { best = p.Value; pick = p.Key; }
                    if (pick != null) { edgeNames[m] = pick; changed = true; }
                }
                if (!changed) break;
            }

            // 分组：同名 + 连通
            var groups = new List<AggGroup>();
            var seen = new HashSet<Entity>();
            for (int i = 0; i < members.Count; i++)
            {
                Entity seed = members[i];
                if (!seen.Add(seed)) continue;
                string nm = edgeNames.TryGetValue(seed, out string sn) && sn != null ? sn : unnamed;
                var g = new AggGroup { Name = nm };
                var stack = new List<Entity>();     // List 当栈：CS2 引用里 System/mscorlib 各有一份 Stack<T>，直接用会 CS0433
                stack.Add(seed);
                while (stack.Count > 0)
                {
                    Entity cur = stack[stack.Count - 1];
                    stack.RemoveAt(stack.Count - 1);
                    g.Edges.Add(cur);
                    if (!em.HasComponent<Edge>(cur)) continue;
                    Edge ed = em.GetComponentData<Edge>(cur);
                    PushSameName(nodeEdges, ed.m_Start, cur, nm, edgeNames, unnamed, seen, stack);
                    PushSameName(nodeEdges, ed.m_End, cur, nm, edgeNames, unnamed, seen, stack);
                }
                groups.Add(g);
            }

            if (minEdges > 1)
            {
                for (int i = 0; i < groups.Count; i++)
                {
                    if (groups[i].Name != unnamed && groups[i].Edges.Count < minEdges)
                    {
                        groups[i].Name = unnamed;
                        st.Downgraded++;
                    }
                }
            }
            return groups;
        }

        /// <summary>按组把一条聚合拆成若干所有者：最大组留在原聚合上，其余组各克隆一个聚合实体；
        /// 成员关系双侧都改写；成员边只给 BatchesUpdated（绝不给 Updated，否则 vanilla 会把拆分抹掉）。</summary>
        private static void ApplyGroups(EntityManager em, Game.UI.NameSystem nameSys, Entity source,
            List<AggGroup> groups, string unnamed, SplitStats st)
        {
            if (groups.Count == 0) return;
            groups.Sort((a, b) => b.Edges.Count.CompareTo(a.Edges.Count));
            if (groups.Count > 1) st.SplitAggregates++;

            var owners = new List<Entity>();
            var picked = new List<AggGroup>();
            for (int i = 0; i < groups.Count; i++)
            {
                if (i == 0) { owners.Add(source); picked.Add(groups[i]); continue; }
                Entity clone = em.Instantiate(source);
                if (clone == Entity.Null || !em.Exists(clone) || !em.HasBuffer<AggregateElement>(clone))
                {
                    // 克隆失败：这一组并回原聚合（宁可名字不准，也不丢成员边）
                    st.CloneFailed++;
                    groups[0].Edges.AddRange(groups[i].Edges);
                    picked[0] = new AggGroup { Name = groups[0].Name };
                    picked[0].Edges.AddRange(groups[0].Edges);
                    owners[0] = source;
                    continue;
                }
                var cbuf = em.GetBuffer<AggregateElement>(clone);
                cbuf.Clear();
                owners.Add(clone);
                picked.Add(groups[i]);
                st.Clones++;
            }

            // 重挂成员（聚合成员表 + 边的 Aggregated 反向指针，两侧都要写）
            for (int i = 0; i < owners.Count; i++)
            {
                if (!em.HasBuffer<AggregateElement>(owners[i])) em.AddBuffer<AggregateElement>(owners[i]);
                var buf = em.GetBuffer<AggregateElement>(owners[i]);
                buf.Clear();
                List<Entity> es = picked[i].Edges;
                for (int k = 0; k < es.Count; k++)
                {
                    Entity e = es[k];
                    buf.Add(new AggregateElement { m_Edge = e });
                    if (em.HasComponent<Aggregated>(e))
                        em.SetComponentData(e, new Aggregated { m_Aggregate = owners[i] });
                    else
                        em.AddComponentData(e, new Aggregated { m_Aggregate = owners[i] });
                    EnsureRefreshTag<BatchesUpdated>(em, e);   // 边上绝不给 Updated
                }
            }

            // 命名：清该所有者完整成员表的边名 → 写聚合名 → 补刷新标记（= 已验收的单路序列）
            for (int i = 0; i < owners.Count; i++)
            {
                Entity owner = owners[i];
                string nm = picked[i].Name;
                var buf = em.GetBuffer<AggregateElement>(owner, true);
                for (int k = 0; k < buf.Length; k++)
                {
                    Entity e2 = buf[k].m_Edge;
                    if (e2 == Entity.Null || !em.Exists(e2)) continue;
                    if (nameSys.TryGetCustomName(e2, out _)) { nameSys.SetCustomName(e2, null); st.ClearedEdges++; }
                }
                nameSys.SetCustomName(owner, nm);
                EnsureRefreshTag<Updated>(em, owner);
                EnsureRefreshTag<BatchesUpdated>(em, owner);

                st.Owners++;
                st.Count(nm);
                if (nm == unnamed) st.UnnamedOwners++;
                bool has = nameSys.TryGetCustomName(owner, out string cn);
                if (has && cn == nm) st.NameOk++;
            }
        }

        private static string DescribeGroups(Entity agg, List<AggGroup> groups)
        {
            var t = new StringBuilder();
            t.Append("聚合 ").Append(agg.Index).Append('v').Append(agg.Version)
             .Append(" → ").Append(groups.Count).Append(" 组：");
            for (int i = 0; i < groups.Count && i < 6; i++)
                t.Append(i == 0 ? "" : "，").Append(groups[i].Name).Append('(').Append(groups[i].Edges.Count).Append(')');
            if (groups.Count > 6) t.Append("…");
            return t.ToString();
        }

        // ---------------------------------------------------------------- 铁路预制件枚举（只读）

        /// <summary>铁路轮第一步（只读，不改图）：枚举内容预制件里的铁路网预制件与 Train 类建筑预制件。
        /// 依据（2026-09-29 IL 实证）：铁路网预制件类 = Game.Prefabs.TrackPrefab : NetGeometryPrefab
        /// → 运行时生成 Game.Net.TrainTrack 标记；车站等建筑靠 Game.Prefabs.PublicTransport.m_TransportType
        /// == Train 识别（配 TransportStation / TransportDepot / TransportStop 判断是哪一类）。
        /// 预制件查找走官方公开 API：PrefabSystem.GetAvailableContentPrefabs() + TryGetEntity + HasComponent。
        /// 有了真名与实体 id，才按 E2C 的 CreateEdgeRequest 配方建轨（CreationDefinition + NetCourse）。</summary>
        public static string ListRailPrefabs(World world)
        {
            var sb = new StringBuilder();
            sb.AppendLine("== 铁路/车站预制件枚举（只读，不改任何东西）==");
            EntityManager em = world.EntityManager;
            Game.Prefabs.PrefabSystem ps = world.GetExistingSystemManaged<Game.Prefabs.PrefabSystem>();
            if (ps == null) { sb.AppendLine("失败：Game.Prefabs.PrefabSystem 不存在"); return sb.ToString(); }

            // 读预制件实体自己的组件类型名（不依赖"哪个是 struct 哪个是 class"的假设：
            // 泛型 HasComponent<T>(PrefabBase) 只吃 ECS 值类型，PublicTransport 之类是类，用不了）
            EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<Game.Prefabs.PrefabData>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            var trackLines = new List<string>();
            var stationLines = new List<string>();
            var nameLines = new List<string>();
            int total = arr.Length;
            foreach (Entity e in arr)
            {
                string nm;
                try { nm = ps.GetPrefabName(e) ?? "(无名)"; } catch { nm = "(取名失败)"; }
                var keep = new List<string>();
                bool track = false, station = false, depot = false, stop = false, hasNetGeom = false;
                NativeArray<ComponentType> cts = default;
                try
                {
                    cts = em.GetComponentTypes(e, Allocator.Temp);
                    for (int i = 0; i < cts.Length; i++)
                    {
                        string tn;
                        try { tn = cts[i].GetManagedType().Name; } catch { continue; }
                        if (tn.Contains("TrackData") || tn.Contains("TrackComposition") || tn.Contains("TrackLane")) track = true;
                        if (tn.Contains("TransportStationData")) station = true;
                        if (tn.Contains("TransportDepotData")) depot = true;
                        if (tn.Contains("TransportStopData")) stop = true;
                        if (tn.Contains("NetGeometryData")) hasNetGeom = true;
                        if (tn.Contains("Track") || tn.Contains("Lane") || tn.Contains("Net") ||
                            tn.Contains("Transport") || tn.Contains("Train") || tn.Contains("Build"))
                            keep.Add(tn);
                    }
                }
                finally { if (cts.IsCreated) cts.Dispose(); }

                string id = $"{e.Index}v{e.Version}";
                if (track)
                    trackLines.Add($"  {nm,-34} entity={id,-10} net几何={hasNetGeom} | {string.Join(",", keep)}");
                else if (station || depot || stop)
                    stationLines.Add($"  {nm,-34} entity={id,-10} 站={station} 库={depot} 点={stop} | {string.Join(",", keep)}");
                string low = nm.ToLowerInvariant();
                if (low.Contains("train") || low.Contains("rail") || low.Contains("track") ||
                    nm.Contains("火车") || nm.Contains("轨道") || nm.Contains("车站"))
                    nameLines.Add($"  {nm,-34} entity={id,-10} 轨道={track} 车站={station} 库={depot} 点={stop}");
            }
            arr.Dispose();

            sb.AppendLine($"预制件实体 {total} 个");
            sb.AppendLine("-- 候选铁路网预制件（组件里出现 TrackData/TrackComposition/TrackLane）--");
            if (trackLines.Count == 0) sb.AppendLine("  （无）");
            foreach (string s in trackLines) sb.AppendLine(s);
            sb.AppendLine("-- 候选车站/车库/站点建筑（组件里出现 TransportStation/Depot/StopData）--");
            if (stationLines.Count == 0) sb.AppendLine("  （无）");
            int sc = 0;
            foreach (string s in stationLines) { sb.AppendLine(s); if (++sc >= 80) { sb.AppendLine("  …（截断）"); break; } }
            sb.AppendLine("-- 名字含 train/rail/track/火车/轨道/车站 的（含非网预制件，供对照）--");
            int nc = 0;
            foreach (string s in nameLines) { sb.AppendLine(s); if (++nc >= 80) { sb.AppendLine("  …（截断）"); break; } }
            sb.AppendLine("下一步：确认铁路网预制件的名字/实体 id 后，才做「单段试建」（一轮只加一个变化）。");
            return sb.ToString();
        }

        // ---------------------------------------------------------------- 铁路单段试建（只加一段，自查）

        public sealed class RailTestData
        {
            public int prefabIndex = -1, prefabVersion = 0;
            public string prefabName = "";
            public readonly List<float[]> segments = new List<float[]>();
            /// <summary>v19：可选的高程模式。"terrain"（默认，逐端点采地形）或 "deck"（本文件所有端点用 deck_m）。
            /// 也可用 "elevations": [[y0,y1],…] 逐段显式给端点高程（过江桥面/引道坡用）。</summary>
            public string elevation_mode = "terrain";
            public float deck_m = 0f;
            public float water_level_m = 0f;
            /// <summary>v21：过水/高架段声明 = [段序号(1 起), 起点里程 m, 终点里程 m, 桥面绝对高程 m]。
            /// 该段在队列时被切成短课程、**逐节点**写入 桥面绝对高程−该点地形（FreeHeight 语义），
            /// 两侧按 ramp_m 做引道坡。里程 = 沿原段从起点算起。</summary>
            public readonly List<float[]> crossings = new List<float[]>();
            public float ramp_m = 250f;
            public float deck_step_m = 60f;
            public float ramp_step_m = 120f;
            /// <summary>v23：离已有网（公路等）多近就算"平面相交"，要在课程两端打 `DisableMerge(4096)`。
            /// 原版 `FindOverlapsJob::Execute` 的 overlap 检测是**纯 2D** 的：只要 XZ 上压住公路，
            /// 就当作平面相交 → 造出一个 type 不同的合并位置 → 该节点被强制成离地 0（贴地）→ 铁路扎进地里。
            /// `(startFlags & endFlags) & 4096 != 0` 时该作业直接 return（IL_002e 实读）= 我们能关掉它的开关。</summary>
            public float merge_m = 20f;
            public readonly List<float[]> elevations = new List<float[]>();
            public string raw_head = "";

            public static RailTestData Load(string path, out string err)
            {
                err = null;
                try
                {
                    string raw = File.ReadAllText(path, Encoding.UTF8);
                    var d = new RailTestData();
                    d.raw_head = raw.Length > 300 ? raw.Substring(0, 300) : raw;
                    // 预制件身份按**运行时名字**解析（v16）：记录里的 index/version 只当线索，
                    // 换游戏版本/内容包就失效，不能当固定身份（机主 2026-09-30 明确要求）。
                    Match mi = Regex.Match(raw, "\"prefab_index\"\\s*:\\s*(-?\\d+)");
                    Match mv = Regex.Match(raw, "\"prefab_version\"\\s*:\\s*(-?\\d+)");
                    Match mn = Regex.Match(raw, "\"prefab_name\"\\s*:\\s*\"([^\"]*)\"");
                    d.prefabIndex = mi.Success ? int.Parse(mi.Groups[1].Value, CultureInfo.InvariantCulture) : -1;
                    d.prefabVersion = mv.Success ? int.Parse(mv.Groups[1].Value, CultureInfo.InvariantCulture) : 0;
                    d.prefabName = mn.Success ? mn.Groups[1].Value : "";
                    if (d.prefabName.Length == 0)
                    {
                        err = "缺 prefab_name（v16 起只认运行时名字，不再用 index 当身份）";
                        return null;
                    }
                    // 注意：segments 是**嵌套数组**，不能用 "\[(.*?)\]" 取（非贪婪会在内层 ] 处截断，
                    // 2026-09-29 v11 就栽在这：解析出 0 段、什么都没建）。直接从 "segments" 往后扫 4 数数组。
                    int si = raw.IndexOf("\"segments\"", StringComparison.Ordinal);
                    string tail = si >= 0 ? raw.Substring(si) : "";
                    foreach (Match m in Regex.Matches(tail,
                             "\\[\\s*(-?\\d+\\.?\\d*)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*\\]"))
                    {
                        d.segments.Add(new[]
                        {
                            float.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture),
                            float.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture),
                            float.Parse(m.Groups[3].Value, CultureInfo.InvariantCulture),
                            float.Parse(m.Groups[4].Value, CultureInfo.InvariantCulture),
                        });
                    }
                    if (d.segments.Count == 0) { err = "segments 没解析出任何段"; return null; }

                    // v19：高程模式（全部可选；缺省 = 逐端点采地形，即 v17 行为）
                    Match mm = Regex.Match(raw, "\"elevation_mode\"\\s*:\\s*\"([^\"]*)\"");
                    d.elevation_mode = mm.Success ? mm.Groups[1].Value : "terrain";
                    Match md = Regex.Match(raw, "\"deck_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (md.Success) d.deck_m = float.Parse(md.Groups[1].Value, CultureInfo.InvariantCulture);
                    Match mw = Regex.Match(raw, "\"water_level_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (mw.Success) d.water_level_m = float.Parse(mw.Groups[1].Value, CultureInfo.InvariantCulture);
                    // v23：DisableMerge 判定半径（离已有网多近算平面相交）；缺省 20 m
                    Match mmg = Regex.Match(raw, "\"merge_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (mmg.Success) d.merge_m = float.Parse(mmg.Groups[1].Value, CultureInfo.InvariantCulture);

                    // v21：过水/高架段声明 [[段号,起里程,终里程,桥面绝对高程],…] + 坡长/步长
                    // 扫描范围限定在 "crossings" 与 "segments" 之间，免得把段数组当声明读进去。
                    int ci = raw.IndexOf("\"crossings\"", StringComparison.Ordinal);
                    int sIdx = raw.IndexOf("\"segments\"", StringComparison.Ordinal);
                    if (ci >= 0)
                    {
                        string ctail = (sIdx > ci) ? raw.Substring(ci, sIdx - ci) : raw.Substring(ci);
                        foreach (Match m in Regex.Matches(ctail,
                                 "\\[\\s*(-?\\d+)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*\\]"))
                        {
                            d.crossings.Add(new[]
                            {
                                float.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture),
                                float.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture),
                                float.Parse(m.Groups[3].Value, CultureInfo.InvariantCulture),
                                float.Parse(m.Groups[4].Value, CultureInfo.InvariantCulture),
                            });
                        }
                    }
                    Match mr = Regex.Match(raw, "\"ramp_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (mr.Success) d.ramp_m = float.Parse(mr.Groups[1].Value, CultureInfo.InvariantCulture);
                    Match mds = Regex.Match(raw, "\"deck_step_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (mds.Success) d.deck_step_m = float.Parse(mds.Groups[1].Value, CultureInfo.InvariantCulture);
                    Match mrs = Regex.Match(raw, "\"ramp_step_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (mrs.Success) d.ramp_step_m = float.Parse(mrs.Groups[1].Value, CultureInfo.InvariantCulture);
                    int ei = raw.IndexOf("\"elevations\"", StringComparison.Ordinal);                    if (ei >= 0)
                    {
                        string etail = raw.Substring(ei);
                        // v20：支持 null（= 该端点仍用地形采样），如 [[null,102.0],[102.0,null]]
                        foreach (Match m in Regex.Matches(etail, "\\[([^\\[\\]]*)\\]"))
                        {
                            string[] toks = m.Groups[1].Value.Split(',');
                            if (toks.Length < 2) continue;
                            var pair = new float[2];
                            for (int k = 0; k < 2; k++)
                            {
                                string tk = toks[k].Trim();
                                pair[k] = (tk.Length == 0 || tk == "null")
                                    ? float.NaN
                                    : float.Parse(tk, CultureInfo.InvariantCulture);
                            }
                            d.elevations.Add(pair);
                        }
                    }
                    return d;
                }
                catch (Exception ex) { err = ex.Message; return null; }
            }
        }

        /// <summary>单段试建（铁路轮第二步，一轮只加一个变化）。
        /// 配方逐字段照抄 E2C 出货版 OsmRoadToolSystem.CreateEdgeRequest（IL 实读 2026-09-29）：
        ///   NetCourse{Start=CoursePos(flags=IsFirst=1, m_CourseDelta=0), End=CoursePos(flags=IsLast=2, delta=1),
        ///             m_Curve=Bezier4x3(start, start+dir*len/3, end-dir*len/3, end),
        ///             m_Elevation=(start.y,end.y), m_Length=len, m_FixedIndex=-1}
        ///   CreationDefinition{m_Prefab=铁路预制件, 其余 Entity.Null, m_Flags=SubElevation(65536)}
        ///   OwnerDefinition{默认} + Updated{}
        /// 经 Game.SafeCommandBufferSystem（AllowUsage 后 CreateCommandBuffer）造实体。
        /// 本轮**先不碰工具系统**：先看原版生成系统是否直接消费这些定义实体；
        /// 报告在 last_rail_test.txt，2 秒后自查 TrainTrack 边数并把没被消费的定义实体清理掉。</summary>
        public static string RailTest(World world, out List<Entity> created, out int beforeEdges)
        {
            created = new List<Entity>();
            beforeEdges = 0;
            var sb = new StringBuilder();
            sb.AppendLine("== 铁路单段试建（定义实体路线，未激活工具）==");
            EntityManager em = world.EntityManager;
            string path = Path.Combine(BridgeLiftData.ModsDataFolder, "rail_test.json");
            RailTestData d = RailTestData.Load(path, out string derr);
            if (d == null) return $"失败：解析 {path} 出错 → {derr}\n";

            int before = CountTrackEdges(em);
            beforeEdges = before;
            sb.AppendLine($"测试前：TrainTrack 边 {before} 条；数据 {path}；段 {d.segments.Count} 条");

            // v16：预制件按运行时名字解析（index/version 只当线索）
            Entity prefab = ResolveRailPrefab(world, d, sb);
            if (prefab == Entity.Null)
            {
                sb.AppendLine("失败：解析不到铁路网预制件，未做任何改动。");
                return sb.ToString();
            }
            s_lastRailPrefab = prefab;
            s_resolvedPrefabName = d.prefabName;
            s_waterLevel = d.water_level_m;
            s_lastRailData = d;
            s_beforeRailEdges = CountEdgesOfPrefab(em, prefab, false);
            s_beforeTemp = CountTemp(em);

            var safecb = world.GetExistingSystemManaged<Game.SafeCommandBufferSystem>();
            if (safecb == null) return sb.AppendLine("失败：Game.SafeCommandBufferSystem 不存在").ToString();
            TerrainSampler.Session terrain = OpenTerrain(world, d, sb);
            if (terrain == null) return sb.AppendLine("未做任何改动。").ToString();
            safecb.AllowUsage();
            EntityCommandBuffer ecb = safecb.CreateCommandBuffer();
            QueueRailSegments(ecb, em, prefab, d, sb, created, terrain, null, 0f,
                Entity.Null, default, Entity.Null, default);
            sb.AppendLine("已入 ECB（帧末回放）。本按钮是**对照组**（不激活工具）；2 秒后做分类自查。");
            return sb.ToString();
        }

        /// <summary>本轮请求标记（v16）：我们排队的每个定义实体都带它。
        /// 自查时用它把「本轮请求」与「原版生成的临时网实体」分开数；清理只清带这个标记的实体
        /// （绝不按预制件清——同一预制件上可能是别的工具/玩家的对象）。</summary>
        public struct RailTestMarker : IComponentData
        {
            public int variant;
        }

        /// <summary>开一个地形采样会话，并用第一段起点做一次探针。不可用就返回 null + 原因。
        /// **宁可什么都不铺，也不要铺到地下**（v16 实测：y=0 的轨沉在地下 99 m，机主根本看不见）。</summary>
        internal static TerrainSampler.Session OpenTerrain(World world, RailTestData d, StringBuilder sb)
        {
            string why;
            TerrainSampler.Session s = TerrainSampler.Open(world, out why);
            float[] f = d.segments.Count > 0 ? d.segments[0] : null;
            float h = 0f;
            bool ok = f != null && s != null && s.TrySample(new float3(f[0], 0f, f[1]), out h);
            if (!ok)
            {
                sb.AppendLine("失败：地形采样不可用 → " + (why ?? TerrainSampler.Diag));
                return null;
            }
            sb.AppendLine($"地形采样就绪：探针点 ({f[0]:F0},{f[1]:F0}) 地形高度 = {h:F2} m");
            return s;
        }

        /// <summary>队列里的一个节点（v21）：沿段里程、地形、目标绝对高程、是否抬升。</summary>
        private sealed class RailNode
        {
            public float d;          // 沿段里程 m
            public float3 pos;       // 世界坐标（y = 实际写入课程的值）
            public float terr;       // 该点地形高度
            public float targetAbs;  // 目标**绝对**高程
            public bool elevated;    // 是否抬高（带 FreeHeight|ForceElevatedEdge）
            public string mode;      // deck / rampIn / rampOut / grade
        }

        private static float SampleTerrAt(TerrainSampler.Session t, float3 a, float3 dirxz, float dist, out bool ok)
        {
            float y = 0f;
            ok = t != null && t.TrySample(new float3(a.x + dirxz.x * dist, 0f, a.z + dirxz.z * dist), out y);
            return ok ? y : 0f;
        }

        /// <summary>v23：这条课程在 **XZ 平面**上是否压住了已有网（公路等）。
        /// 压住 → 两端都打 `DisableMerge(4096)`，原版 `FindOverlapsJob::Execute`（IL_002e：
        /// `(startFlags & endFlags) & 4096` 非零就直接 return）就不会把这里当成平面相交，
        /// 也就不会造出"被强制贴地"的合并节点 —— 这正是"跨公路从下面过/扎进地里"的根因。</summary>
        private static bool NearExistingNet(List<Bezier4x3> nets, List<float4> boxes, float3 p0, float3 p1, float range)
        {
            if (nets == null || nets.Count == 0) return false;
            float2 a0 = new float2(p0.x, p0.z);
            float2 a1 = new float2(p1.x, p1.z);
            float4 bb = new float4(math.min(a0.x, a1.x) - range, math.min(a0.y, a1.y) - range,
                                   math.max(a0.x, a1.x) + range, math.max(a0.y, a1.y) + range);
            float2 ab = a1 - a0;
            float l2 = math.dot(ab, ab);
            for (int i = 0; i < nets.Count; i++)
            {
                float4 e = boxes[i];
                if (e.z < bb.x || e.x > bb.z || e.w < bb.y || e.y > bb.w) continue;
                Bezier4x3 nb = nets[i];
                for (int k = 0; k <= 4; k++)
                {
                    float3 q = BezierAt(nb, k * 0.25f);
                    float2 q2 = new float2(q.x, q.z);
                    float d0 = math.distance(a0, q2);
                    if (d0 < range) return true;
                    float d1 = math.distance(a1, q2);
                    if (d1 < range) return true;
                    if (l2 > 1e-4f)
                    {
                        float t = math.clamp(math.dot(q2 - a0, ab) / l2, 0f, 1f);
                        if (math.distance(a0 + ab * t, q2) < range) return true;
                    }
                }
            }
            return false;
        }

        /// <summary>发一条铁路课程（组件与 E2C 出货版 CreateEdgeRequest 相同）。
        /// v27：`node0/node1` 非空时写进 CoursePos.m_Entity = **挂到既有节点**（官方开关，
        /// NetToolSystem 3546/3550 `m_StartPosition.m_Entity = edge.m_Start`），用于把正线接到站内轨道节点上。</summary>
        private static Entity EmitRailCourse(EntityCommandBuffer ecb, Entity prefab, List<Entity> created,
            float3 p0, float3 p1, int flags0, int flags1, Entity node0, Entity node1)
        {
            float len = math.distance(p0, p1);
            float3 dir = math.normalizesafe(p1 - p0, new float3(1f, 0f, 0f));
            float3 third = dir * (len / 3f);

            NetCourse course = default;
            course.m_StartPosition = BuildCoursePos(p0, 0f, flags0, node0);
            course.m_EndPosition = BuildCoursePos(p1, 1f, flags1, node1);
            course.m_Curve = new Bezier4x3(p0, p0 + third, p1 - third, p1);
            course.m_Elevation = new float2(p0.y, p1.y);
            course.m_Length = len;
            course.m_FixedIndex = -1;

            CreationDefinition cd = default;
            cd.m_Prefab = prefab;
            cd.m_SubPrefab = Entity.Null;
            cd.m_Owner = Entity.Null;
            cd.m_Original = Entity.Null;
            cd.m_Attached = Entity.Null;
            cd.m_RandomSeed = UnityEngine.Random.Range(int.MinValue, int.MaxValue);
            cd.m_Flags = (CreationFlags)65536;      // SubElevation（IL 实读的常量）

            Entity e = ecb.CreateEntity();
            ecb.AddComponent(e, cd);
            ecb.AddComponent(e, course);
            ecb.AddComponent(e, default(OwnerDefinition));
            ecb.AddComponent(e, default(Updated));
            ecb.AddComponent(e, new RailTestMarker { variant = 0 });
            created.Add(e);
            return e;
        }

        /// <summary>桥面有效绝对高程：`crossings` 第 4 个数为正 = 直接给绝对高程；
        /// 为负 = 只给**水面以上净空**（|值|），由两端地形自动抬高到 max(水面+净空, 两端地形+0.5)，
        /// 这样在不知道当地地面高度时也不会把桥修到岸里/水下（v21）。</summary>
        private static float EffectiveDeck(float[] c, List<float> knots, float[] knotTerr, float segLen, float waterLevel)
        {
            float v = c[3];
            if (v >= 0f) return v;
            float from = math.clamp(c[1], 0f, segLen);
            float to = math.clamp(c[2], from, segLen);
            float need = waterLevel + (-v);
            int i0 = knots.IndexOf(from), i1 = knots.IndexOf(to);
            float t0 = i0 >= 0 ? knotTerr[i0] : 0f;
            float t1 = i1 >= 0 ? knotTerr[i1] : 0f;
            return math.max(need, math.max(t0, t1) + 0.5f);
        }

        // ---------------------------------------------------------------- v27 车站接线（P2：正线接到站内轨道节点）
        //
        // 依据（maps\yilan\docs\铁路进图-调研.md §11，IL 实证）：
        //   · 「无轨道连接点」= ConnectionWarningSystem 对**轨道车道端点**的检查：端点节点上找不到
        //     TrackTypes 匹配的连接就打标（stationconn_warn_il.txt CheckNodeConnections）。
        //   · 官方"接到既有节点"的开关 = `CoursePos.m_Entity`（NetToolSystem 3546/3550）；
        //   · 节点不会在两次独立生成之间自动合并（NodeReductionSystem 只在工具生成过程中生效）
        //     ⇒ 必须**显式引用节点实体**，所以顺序必须先⑧放站（站内节点先存在）、后⑦铺轨。
        // 能成立的前提（强推论）：游戏能报这条警告 ⇒ 站内轨道车道已存在 ⇒ 站内节点可收集。

        /// <summary>站点的最终世界位姿：`yaw + yaw_offset_deg`，再沿建筑**本地 +Z 的世界方向**平移
        /// `lateral_m`（月台轨相对建筑原点有侧向距离，要让"某一条"月台轨正压在线路上就得平移建筑）。</summary>
        internal static void StationTransform(StationData d, StationData.St st, out float3 pos, out quaternion rot)
        {
            float yaw = st.yaw + (d != null ? d.yaw_offset_deg : 0f);
            rot = quaternion.RotateY(math.radians(yaw));
            float lat = d != null ? d.lateral_m : 0f;
            float3 fwd = math.rotate(rot, new float3(0f, 0f, 1f));
            pos = new float3(st.x + fwd.x * lat, st.y, st.z + fwd.z * lat);
        }

        /// <summary>站内轨道节点集合（从已放置车站建筑的 Game.Net.SubNet 缓冲里取）。</summary>
        public sealed class StationNodes
        {
            public readonly List<Entity> nodes = new List<Entity>();
            public readonly List<float3> positions = new List<float3>();
            public int buildings;
        }

        /// <summary>收集已放置车站的站内轨道节点，并**筛出"本站要服务的那条轨道"的两个端节点**：
        /// v28c 实测建筑的 `Game.Net.SubNet` 缓冲里既有 Node 也有 Edge（34 条里前 19 条是 Node），
        /// 两类都收；随后按"到我们线位的横向距离最小、且分居车站中心两侧"各挑 1 个——
        /// 这条即 `lateral_m` 指定的那条月台轨（依兰：local z=−8.5），避免误挂到第二/第三条轨上。
        /// `poly` = 我们线位的折线点（可为 null = 不筛）。</summary>
        internal static StationNodes CollectStationNodes(World world, EntityManager em, Entity stationPrefab, StringBuilder sb,
            List<float2> poly, List<float[]> elev)
        {
            var res = new StationNodes();
            if (stationPrefab == Entity.Null) return res;
            foreach (Entity b in BuildingsOfPrefab(em, stationPrefab, false, 64))
            {
                res.buildings++;
                if (!em.HasBuffer<Game.Net.SubNet>(b)) continue;
                DynamicBuffer<Game.Net.SubNet> subs = em.GetBuffer<Game.Net.SubNet>(b);
                for (int i = 0; i < subs.Length; i++)
                {
                    Entity n = subs[i].m_SubNet;
                    if (n == Entity.Null || !em.Exists(n)) continue;
                    if (em.HasComponent<Node>(n))
                    {
                        if (!res.nodes.Contains(n))
                        {
                            res.nodes.Add(n);
                            res.positions.Add(em.GetComponentData<Node>(n).m_Position);
                        }
                        continue;
                    }
                    if (!em.HasComponent<Edge>(n)) continue;
                    Edge ee = em.GetComponentData<Edge>(n);
                    for (int k = 0; k < 2; k++)
                    {
                        Entity nd = k == 0 ? ee.m_Start : ee.m_End;
                        if (nd == Entity.Null || !em.Exists(nd) || res.nodes.Contains(nd)) continue;
                        res.nodes.Add(nd);
                        res.positions.Add(em.HasComponent<Node>(nd) ? em.GetComponentData<Node>(nd).m_Position : default);
                    }
                }
            }

            // —— 筛选：只认**轨道节点**（自带 ConnectedEdge 里出现 TrackLane 车道）且**高程贴着轨面**（±2.5 m），
            //    再在其中取"到线位折线横向最近"的两个（车站两侧各一）。
            //    为什么必须加这两道：站内 33 个节点里混着隐形道路/步行道节点，y 从 15 到 114 都有；
            //    只按横向距离选（v30）会挂到 y≈111.4 甚至 y≈15 的节点上 → 线路末端被拽歪（机主 2026-09-30 实拍）。
            int all = res.nodes.Count;
            if (poly != null && poly.Count >= 2 && all > 2)
            {
                var lat = new List<float>(all);
                var arc = new List<float>(all);
                var lineY = new List<float>(all);
                var isTrack = new List<bool>(all);
                for (int i = 0; i < all; i++)
                {
                    float2 p = new float2(res.positions[i].x, res.positions[i].z);
                    float bestD = float.MaxValue, bestS = 0f, acc = 0f, bestY = res.positions[i].y;
                    for (int k = 0; k + 1 < poly.Count; k++)
                    {
                        float2 a = poly[k], b = poly[k + 1];
                        float2 ab = b - a;
                        float l2 = math.dot(ab, ab);
                        float t = l2 > 1e-4f ? math.clamp(math.dot(p - a, ab) / l2, 0f, 1f) : 0f;
                        float2 q = a + ab * t;
                        float dd = math.distance(p, q);
                        if (dd < bestD)
                        {
                            bestD = dd;
                            bestS = acc + t * math.sqrt(l2);
                            if (elev != null && k < elev.Count && elev[k] != null && elev[k].Length >= 2)
                                bestY = elev[k][0] + (elev[k][1] - elev[k][0]) * t;
                        }
                        acc += math.sqrt(l2);
                    }
                    lat.Add(bestD); arc.Add(bestS); lineY.Add(bestY);

                    // 是不是轨道节点：连着的边里有**火车轨道预制件**（名字含 Train Track）或**带 TrackLane 车道**的
                    bool track = false;
                    if (em.HasBuffer<ConnectedEdge>(res.nodes[i]))
                    {
                        DynamicBuffer<ConnectedEdge> ces = em.GetBuffer<ConnectedEdge>(res.nodes[i]);
                        for (int c = 0; c < ces.Length && !track; c++)
                        {
                            Entity e2 = ces[c].m_Edge;
                            if (e2 == Entity.Null || !em.Exists(e2)) continue;
                            if (em.HasComponent<Game.Prefabs.PrefabRef>(e2))
                            {
                                string pn = SafePrefabName(world, em.GetComponentData<Game.Prefabs.PrefabRef>(e2).m_Prefab);
                                if (pn != null && pn.Contains("Train Track")) { track = true; break; }
                            }
                            if (!em.HasBuffer<Game.Net.SubLane>(e2)) continue;
                            DynamicBuffer<Game.Net.SubLane> ls = em.GetBuffer<Game.Net.SubLane>(e2);
                            for (int j = 0; j < ls.Length; j++)
                            {
                                Entity lane = ls[j].m_SubLane;
                                if (lane != Entity.Null && em.Exists(lane) && em.HasComponent<Game.Net.TrackLane>(lane))
                                { track = true; break; }
                            }
                        }
                    }
                    isTrack.Add(track);
                }
                float sMin = float.MaxValue, sMax = float.MinValue;
                for (int i = 0; i < all; i++) { if (arc[i] < sMin) sMin = arc[i]; if (arc[i] > sMax) sMax = arc[i]; }
                float sMid = (sMin + sMax) * 0.5f;
                int iLo = -1, iHi = -1;
                float dLo = float.MaxValue, dHi = float.MaxValue;
                int cand = 0;
                for (int i = 0; i < all; i++)
                {
                    if (!isTrack[i]) continue;                                  // 必须是轨道节点
                    if (math.abs(res.positions[i].y - lineY[i]) > 2.5f) continue; // 高程必须贴轨面
                    cand++;
                    if (arc[i] <= sMid) { if (lat[i] < dLo) { dLo = lat[i]; iLo = i; } }
                    else { if (lat[i] < dHi) { dHi = lat[i]; iHi = i; } }
                }
                if (iLo >= 0 && iHi >= 0)
                {
                    Entity nLo = res.nodes[iLo], nHi = res.nodes[iHi];
                    float3 pLo = res.positions[iLo], pHi = res.positions[iHi];
                    res.nodes.Clear(); res.positions.Clear();
                    res.nodes.Add(nLo); res.positions.Add(pLo);
                    res.nodes.Add(nHi); res.positions.Add(pHi);
                    if (sb != null)
                        sb.AppendLine($"  站内节点筛选：全部 {all} 个 → 轨道节点且贴轨面(±2.5 m) 的 {cand} 个 → "
                                      + $"取**离线位最近**的两个：SW 侧 {nLo.Index}v{nLo.Version} (横距 {dLo:F2} m, y {pLo.y:F2}) / "
                                      + $"NE 侧 {nHi.Index}v{nHi.Version} (横距 {dHi:F2} m, y {pHi.y:F2})，跨距 {math.distance(pLo, pHi):F1} m");
                }
                else if (sb != null)
                {
                    sb.AppendLine($"  站内节点筛选：轨道且贴轨面的候选 {cand} 个，两侧没能各选出 1 个 → 本次不裁剪/不挂接"
                                  + "（检查 lateral_m 对不对、或线路与站内轨道的高差）");
                }
            }

            if (sb != null)
                sb.AppendLine($"  站内轨道节点收集：车站建筑 {res.buildings} 座 → 用于接线的节点 {res.nodes.Count} 个"
                              + (res.nodes.Count == 0
                                 ? "（**空**：本会话还没有车站 → 先点⑧放站，再点⑦铺轨才能接线）"
                                 : " → 铺轨时会被用来跳过站内跨距 + 挂接端点"));
            return res;
        }

        /// <summary>裁剪后要发的一段课程（含要挂的既有节点实体）。</summary>
        private sealed class CoursePart
        {
            public float3 p0, p1;
            public Entity n0 = Entity.Null, n1 = Entity.Null;
        }

        /// <summary>把一条课程按「站内轨道跨距」切成 0–2 段（结果填进 outParts，复用以免每课程分配）：
        /// · 跨距内的部分**丢掉**——那段交给车站自带轨道，否则正线与站内轨道重叠成双线；
        /// · 切口正好落在**站内节点**上，并把节点实体写进 `CoursePos.m_Entity`（官方"接到既有节点"开关，
        ///   NetToolSystem 3546/3550）→ 生成时与站内轨道共用节点 → 车道按 TrackTypes 接通 → 警告消失。
        /// 为什么必须"切"而不是"端点吸附"：我们的课程是 60 m 网格，站内轨道端点通常落在网格中间，
        /// 只按端点距离吸附（±3 m）大概率配不上，会把线留成断口。
        /// 站内节点为空（没放站）→ 原样一段返回，行为与 v26 完全一致。</summary>
        private static void StationSplitCourse(StationNodes st, float snapM, float3 p0, float3 p1, List<CoursePart> outParts)
        {
            outParts.Clear();
            var whole = new CoursePart { p0 = p0, p1 = p1 };
            if (st == null || st.nodes.Count < 2)
            {
                outParts.Add(whole);
                return;
            }
            // 横向容差：线路在站区是**弯的**（依兰实测站内 212 m 弦向与全线平均走向差 9.7°、最大偏离弦 11.4 m），
            // 3 m 的吸附容差根本够不着站内节点 → 放宽到 max(数据 snap_m, 25 m)。
            float tol = math.max(snapM > 0.1f ? snapM : 3f, 25f);
            // CollectStationNodes 已把节点筛成"本站要服务那条轨的两端"（离线位最近、分居两侧）
            float3 A = st.positions[0], B = st.positions[1];

            // ① 整段落在站内跨距里 → 交给车站自带轨道（= 把车站下方的轨截掉）
            float2 a2 = new float2(A.x, A.z), b2 = new float2(B.x, B.z);
            float2 ab = b2 - a2;
            float spanLen = math.length(ab);
            if (spanLen > 1f)
            {
                float2 ax = ab / spanLen;
                bool Inside(float3 p)
                {
                    float2 dp = new float2(p.x, p.z) - a2;
                    float t = math.dot(dp, ax);
                    float lat = math.abs(ax.x * dp.y - ax.y * dp.x);
                    return t > 2f && t < spanLen - 2f && lat < tol;
                }
                if (Inside(p0) && Inside(p1)) return;   // outParts 保持空 = 跳过本课程
            }

            // ② 站内两端节点在本课程上的投影 = 切点；切点位置用**节点自身坐标**，该端挂 m_Entity → 直接接上
            float2 c0 = new float2(p0.x, p0.z), c1 = new float2(p1.x, p1.z);
            float2 cv = c1 - c0;
            float clen = math.length(cv);
            if (clen < 0.5f) { outParts.Add(whole); return; }
            float2 cd = cv / clen;

            var cuts = new List<float>(2);
            var cutNodes = new List<Entity>(2);
            var cutPos = new List<float3>(2);
            for (int k = 0; k < 2; k++)
            {
                float3 P = k == 0 ? A : B;
                Entity PN = st.nodes[k];
                float2 dp = new float2(P.x, P.z) - c0;
                float t = math.dot(dp, cd);
                float lat = math.abs(cd.x * dp.y - cd.y * dp.x);
                if (lat > tol) continue;                    // 横向太远 → 不是本课程的切点
                if (t < -0.5f || t > clen + 0.5f) continue; // 不在本课程范围内
                cuts.Add(math.clamp(t, 0f, clen));
                cutNodes.Add(PN);
                cutPos.Add(P);
            }
            if (cuts.Count == 0) { outParts.Add(whole); return; }

            int lo = 0, hi = 0;
            for (int k = 1; k < cuts.Count; k++)
            {
                if (cuts[k] < cuts[lo]) lo = k;
                if (cuts[k] > cuts[hi]) hi = k;
            }
            // 跨距外的前段 [0, 切点] —— 末端就是站内节点（挂实体）
            if (cuts[lo] > 0.5f)
                outParts.Add(new CoursePart { p0 = p0, p1 = cutPos[lo], n0 = Entity.Null, n1 = cutNodes[lo] });
            // 跨距外的后段 [切点, clen] —— 起端是站内节点（挂实体）
            if (clen - cuts[hi] > 0.5f)
                outParts.Add(new CoursePart { p0 = cutPos[hi], p1 = p1, n0 = cutNodes[hi], n1 = Entity.Null });
            // 两段都不剩 = 整段都在跨距里 → outParts 空 = 跳过（交给站内轨道）
        }

        private static readonly List<CoursePart> s_courseParts = new List<CoursePart>();

        /// <summary>把 rail_test.json 的段排队成定义实体（⑥ 定义实体路线、⑦ 工具路线共用）。
        /// v16：每段一个定义实体，组件与 E2C 出货版 CreateEdgeRequest 相同（不加 Temp）。
        /// v17：端点高程取地形采样（不再用 y=0）。
        /// v21：**过水/高架段逐节点控高** —— `crossings` 声明 [段号, 起里程, 终里程, 桥面绝对高程]，
        ///   该段被切成短课程，每个节点写入 `目标绝对高程 − 该点地形`（FreeHeight 语义 = 相对地面），
        ///   两端按 `ramp_m` 做引道坡。v20 实测：只控两端时中间节点被地形拖下去（102→95.1，净空仅 +2.63 m），
        ///   所以桥面必须逐节点给值。</summary>
        internal static void QueueRailSegments(EntityCommandBuffer ecb, EntityManager em, Entity prefab,
            RailTestData d, StringBuilder sb, List<Entity> created, TerrainSampler.Session terrain,
            StationNodes station, float stationSnapM,
            Entity attachS, float3 attachSPos, Entity attachE, float3 attachEPos)
        {
            bool deckMode = string.Equals(d.elevation_mode, "deck", StringComparison.OrdinalIgnoreCase);
            sb.AppendLine($"  高程口径：模式={d.elevation_mode}"
                          + (deckMode ? $"，deck_m={d.deck_m:F2}" : "")
                          + $"，过水/高架声明 {d.crossings.Count} 处，引道坡 {d.ramp_m:F0} m，"
                          + $"桥面步长 {d.deck_step_m:F0} m / 坡步长 {d.ramp_step_m:F0} m"
                          + (d.water_level_m > 0f ? $"，水面 {d.water_level_m:F2} m" : ""));

            int hit = 0, miss = 0, courses = 0, nGrade = 0, nRamp = 0, nDeck = 0, explicitY = 0, nNoMerge = 0;
            float maxGrade = 0f, minClear = float.MaxValue;
            string worst = "";
            int stSkip = 0, stSnap = 0;              // v27 车站接线：跳过课程数 / 挂到站内节点的课程端数

            // v23：先把图上**已有网**的边曲线收起来（非 Temp），用来判断"这门课在 XZ 上压住了别的网"。
            // 原版 FindOverlapsJob 的 overlap 检测是纯 2D 的，压住就造平面相交 → 节点被强制贴地。
            var netCurves = new List<Bezier4x3>();
            var netBoxes = new List<float4>();
            {
                EntityQuery nq = em.CreateEntityQuery(ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<Curve>());
                NativeArray<Entity> narr = nq.ToEntityArray(Allocator.Temp);
                try
                {
                    foreach (Entity ne in narr)
                    {
                        if (em.HasComponent<Temp>(ne)) continue;
                        if (em.HasComponent<Game.Prefabs.PrefabRef>(ne)
                            && em.GetComponentData<Game.Prefabs.PrefabRef>(ne).m_Prefab == prefab) continue; // 不含本轮自己的
                        Bezier4x3 nb = em.GetComponentData<Curve>(ne).m_Bezier;
                        netCurves.Add(nb);
                        netBoxes.Add(new float4(
                            math.min(math.min(nb.a.x, nb.b.x), math.min(nb.c.x, nb.d.x)),
                            math.min(math.min(nb.a.z, nb.b.z), math.min(nb.c.z, nb.d.z)),
                            math.max(math.max(nb.a.x, nb.b.x), math.max(nb.c.x, nb.d.x)),
                            math.max(math.max(nb.a.z, nb.b.z), math.max(nb.c.z, nb.d.z))));
                    }
                }
                finally { narr.Dispose(); }
            }
            sb.AppendLine($"  已有网边曲线 {netCurves.Count} 条 → DisableMerge 判定半径 {d.merge_m:F0} m（压住就打标）");

            for (int si = 0; si < d.segments.Count; si++)
            {
                float[] s = d.segments[si];
                float3 a = new float3(s[0], 0f, s[1]);
                float3 b = new float3(s[2], 0f, s[3]);
                float3 flat = new float3(b.x - a.x, 0f, b.z - a.z);
                float segLen = math.length(flat);
                if (segLen < 0.5f) { sb.AppendLine($"  段 {si + 1} 长度过短（{segLen:F2} m），跳过"); continue; }
                float3 dirxz = flat / segLen;

                var segCross = new List<float[]>();
                foreach (float[] c in d.crossings) if ((int)c[0] == si + 1) segCross.Add(c);

                if (segCross.Count == 0)
                {
                    // ---- v22 路径：整段切成**短课程 + 逐节点控高**（= v21 那条已验证的路子）----
                    // 为什么废弃 v20 的「整段一条课程」：v7 实测，185–251 m 的长课程会被原版再细分，
                    // 细分节点的实际高度**不由我们写进去的值决定** —— 里程 5.5–6.85 km 那一整段
                    // 建成的节点全落在**地面上**（连 `Elevation` 组件都没有），两侧还拉出 20–57% 的急坡；
                    // 而 v21 的短课程（≤60 m，逐节点写 `目标绝对高程 − 该点地形`）逐点守住了桥面 103.00 m。
                    // 所以全线统一走短课程：每个节点都是课程端点，原版没有"自己决定中间高度"的余地。
                    float stepWant = d.deck_step_m > 1f ? d.deck_step_m : 60f;
                    int ndiv = math.max(1, (int)math.ceil(segLen / stepWant));
                    var ks = new List<RailNode>(ndiv + 1);
                    for (int k = 0; k <= ndiv; k++)
                    {
                        float dd = segLen * ((float)k / (float)ndiv);
                        bool okk;
                        float tv = SampleTerrAt(terrain, a, dirxz, dd, out okk);
                        if (okk) hit++; else miss++;
                        float tt = segLen > 0.1f ? dd / segLen : 0f;
                        bool exp = false;
                        float target = tv;
                        if (si < d.elevations.Count)
                        {
                            float v0 = d.elevations[si][0], v1 = d.elevations[si][1];
                            bool e0 = !float.IsNaN(v0), e1 = !float.IsNaN(v1);
                            if (e0 && e1) { target = v0 + (v1 - v0) * tt; exp = true; }
                            else if (e0) { target = v0; exp = true; }
                            else if (e1) { target = v1; exp = true; }
                        }
                        if (deckMode && d.deck_m > 0f) { target = d.deck_m; exp = true; }
                        var ndx = new RailNode();
                        ndx.d = dd;
                        ndx.terr = tv;
                        ndx.targetAbs = target;
                        ndx.elevated = exp && math.abs(target - tv) > 0.3f;
                        ndx.mode = ndx.elevated ? "explicit" : "grade";
                        ndx.pos = new float3(a.x + dirxz.x * dd,
                                             ndx.elevated ? target - tv : tv,
                                             a.z + dirxz.z * dd);
                        ks.Add(ndx);
                    }
                    bool anyExp = false;
                    for (int k = 0; k + 1 < ks.Count; k++)
                    {
                        RailNode n0 = ks[k], n1 = ks[k + 1];
                        StationSplitCourse(station, stationSnapM, n0.pos, n1.pos, s_courseParts);
                        if (s_courseParts.Count == 0) { stSkip++; continue; }   // 整段都在站内跨距里 → 交给站内轨道
                        foreach (CoursePart part in s_courseParts)
                        {
                            // v33：整条线的**首/末端点**挂到既有同类节点上（⑨ 站前路要接到既有村道）
                            if (attachS != Entity.Null && math.distance(part.p0, attachSPos) < 2.0f)
                            { part.n0 = attachS; part.p0 = attachSPos; }
                            if (attachE != Entity.Null && math.distance(part.p1, attachEPos) < 2.0f)
                            { part.n1 = attachE; part.p1 = attachEPos; }
                            bool near = NearExistingNet(netCurves, netBoxes, part.p0, part.p1, d.merge_m);
                            if (near) nNoMerge++;
                            int fl = near ? 4096 : 0;
                            // 挂到既有节点的那端：**位置是绝对坐标，标志只能留 IsFirst/IsLast**——
                            // 带 FreeHeight(128) 会被按"相对地面"再解释一次（v30 实测：线端被拽到 y≈15 m）。
                            int f0 = part.n0 != Entity.Null ? 1 : (1 | (n0.elevated ? 128 | 2048 : 0) | fl);
                            int f1 = part.n1 != Entity.Null ? 2 : (2 | (n1.elevated ? 128 | 2048 : 0) | fl);
                            Entity e = EmitRailCourse(ecb, prefab, created, part.p0, part.p1, f0, f1,
                                part.n0, part.n1);
                            courses++;
                            float ya0 = n0.elevated ? n0.targetAbs : n0.terr;
                            float ya1 = n1.elevated ? n1.targetAbs : n1.terr;
                            float hd = math.distance(new float3(part.p0.x, 0f, part.p0.z),
                                                     new float3(part.p1.x, 0f, part.p1.z));
                            float grade = hd > 0.1f ? (ya1 - ya0) / hd * 100f : 0f;
                            if (n0.elevated || n1.elevated)
                            {
                                anyExp = true;
                                if (math.abs(grade) > maxGrade) { maxGrade = math.abs(grade); worst = $"段{si + 1} {n0.d:F0}–{n1.d:F0} m"; }
                                nRamp++;
                            }
                            else nGrade++;
                            bool linked = part.n0 != Entity.Null || part.n1 != Entity.Null;
                            if (linked) stSnap++;
                            sb.AppendLine($"  课程 {courses}: 段{si + 1} {n0.d:F0}→{n1.d:F0} m，长 {hd:F0} m，"
                                          + $"模式 {(n0.mode == n1.mode ? n0.mode : n0.mode + "/" + n1.mode)}，"
                                          + $"地形 {n0.terr:F2}→{n1.terr:F2}，绝对 {ya0:F2}→{ya1:F2}，"
                                          + $"写入 {part.p0.y:F2}→{part.p1.y:F2}，坡度 {grade:+0.00;-0.00;0.00}%，实体 {e.Index}v{e.Version}"
                                          + (near ? "，[禁并]" : "")
                                          + (s_courseParts.Count > 1 ? "，[站内跨距裁剪]" : "")
                                          + (linked
                                             ? $"，[接站内节点 {(part.n0 != Entity.Null ? part.n0.Index + "v" + part.n0.Version : "-")}/{(part.n1 != Entity.Null ? part.n1.Index + "v" + part.n1.Version : "-")}]"
                                             : ""));
                        }
                    }
                    if (anyExp) explicitY++;
                    continue;
                }

                // ---- v21 路径：按声明分段，逐节点控高 ----
                var knots = new List<float> { 0f, segLen };
                foreach (float[] c in segCross)
                {
                    float from = math.clamp(c[1], 0f, segLen);
                    float to = math.clamp(c[2], from, segLen);
                    knots.Add(math.max(0f, from - d.ramp_m));
                    knots.Add(from);
                    knots.Add(to);
                    knots.Add(math.min(segLen, to + d.ramp_m));
                }
                knots.Sort();
                for (int i = knots.Count - 1; i > 0; i--) if (knots[i] - knots[i - 1] < 0.5f) knots.RemoveAt(i);

                var knotTerr = new float[knots.Count];
                for (int i = 0; i < knots.Count; i++)
                {
                    bool ok;
                    knotTerr[i] = SampleTerrAt(terrain, a, dirxz, knots[i], out ok);
                    if (ok) hit++; else miss++;
                }

                var nodes = new List<RailNode>();
                for (int i = 0; i + 1 < knots.Count; i++)
                {
                    float ka = knots[i], kb = knots[i + 1];
                    float mid = (ka + kb) * 0.5f;
                    string mode = "grade";
                    float deckHere = 0f, rampFrom = 0f, rampTo = 0f;
                    foreach (float[] c in segCross)
                    {
                        float from = math.clamp(c[1], 0f, segLen);
                        float to = math.clamp(c[2], from, segLen);
                        if (mid >= from && mid <= to) { mode = "deck"; deckHere = EffectiveDeck(c, knots, knotTerr, segLen, d.water_level_m); rampFrom = from; rampTo = to; break; }
                        if (mid > from - d.ramp_m && mid < from) { mode = "rampIn"; deckHere = EffectiveDeck(c, knots, knotTerr, segLen, d.water_level_m); rampFrom = math.max(0f, from - d.ramp_m); rampTo = from; break; }
                        if (mid > to && mid < to + d.ramp_m) { mode = "rampOut"; deckHere = EffectiveDeck(c, knots, knotTerr, segLen, d.water_level_m); rampFrom = to; rampTo = math.min(segLen, to + d.ramp_m); break; }
                    }
                    float outerTerr = 0f;
                    if (mode == "rampIn" || mode == "rampOut")
                    {
                        // 坡道外侧端点按"坡起点"的地形接地面
                        float outer = (mode == "rampIn") ? rampFrom : rampTo;
                        int idx = knots.IndexOf(outer);
                        if (idx < 0)
                        {
                            // 外侧端点不在 knot 列表里（被 clamp 掉）→ 现采一次
                            bool ok2;
                            float tv = SampleTerrAt(terrain, a, dirxz, outer, out ok2);
                            if (ok2) hit++; else miss++;
                            outerTerr = tv;
                        }
                        else outerTerr = knotTerr[idx];
                    }
                    int stepDiv = 1;
                    if (mode == "deck") stepDiv = math.max(1, (int)math.ceil((kb - ka) / math.max(20f, d.deck_step_m)));
                    else if (mode == "rampIn" || mode == "rampOut") stepDiv = math.max(1, (int)math.ceil((kb - ka) / math.max(20f, d.ramp_step_m)));

                    for (int k = (i == 0 ? 0 : 1); k <= stepDiv; k++)
                    {
                        float dd = ka + (kb - ka) * ((float)k / stepDiv);
                        float terr;
                        if (k == 0) terr = knotTerr[i];
                        else if (k == stepDiv) terr = knotTerr[i + 1];
                        else
                        {
                            bool ok3;
                            terr = SampleTerrAt(terrain, a, dirxz, dd, out ok3);
                            if (ok3) hit++; else miss++;
                        }
                        float target;
                        if (mode == "deck") target = deckHere;
                        else if (mode == "rampIn")
                        {
                            float t = math.clamp((dd - rampFrom) / math.max(1f, rampTo - rampFrom), 0f, 1f);
                            target = math.lerp(outerTerr, deckHere, t);
                        }
                        else if (mode == "rampOut")
                        {
                            float t = math.clamp((dd - rampFrom) / math.max(1f, rampTo - rampFrom), 0f, 1f);
                            target = math.lerp(deckHere, outerTerr, t);
                        }
                        else target = terr;

                        var nd = new RailNode();
                        nd.d = dd;
                        nd.terr = terr;
                        nd.targetAbs = target;
                        nd.elevated = math.abs(target - terr) > 0.3f;
                        nd.mode = nd.elevated ? mode : "grade";
                        nd.pos = new float3(a.x + dirxz.x * dd, nd.elevated ? target - terr : terr, a.z + dirxz.z * dd);
                        nodes.Add(nd);
                    }
                }

                for (int i = 0; i + 1 < nodes.Count; i++)
                {
                    RailNode n0 = nodes[i], n1 = nodes[i + 1];
                    StationSplitCourse(station, stationSnapM, n0.pos, n1.pos, s_courseParts);
                    if (s_courseParts.Count == 0) { stSkip++; continue; }
                    foreach (CoursePart part in s_courseParts)
                    {
                        if (attachS != Entity.Null && math.distance(part.p0, attachSPos) < 2.0f)
                        { part.n0 = attachS; part.p0 = attachSPos; }
                        if (attachE != Entity.Null && math.distance(part.p1, attachEPos) < 2.0f)
                        { part.n1 = attachE; part.p1 = attachEPos; }
                        bool near = NearExistingNet(netCurves, netBoxes, part.p0, part.p1, d.merge_m);
                        if (near) nNoMerge++;
                        int fl = near ? 4096 : 0;
                        // 挂到既有节点的那端：位置是绝对坐标，标志只能留 IsFirst/IsLast（不带 FreeHeight）
                        int g0 = part.n0 != Entity.Null ? 1 : (1 | (n0.elevated ? 128 | 2048 : 0) | fl);
                        int g1 = part.n1 != Entity.Null ? 2 : (2 | (n1.elevated ? 128 | 2048 : 0) | fl);
                        Entity e = EmitRailCourse(ecb, prefab, created, part.p0, part.p1, g0, g1,
                            part.n0, part.n1);
                        courses++;
                        float ya0 = n0.elevated ? n0.targetAbs : n0.terr;
                        float ya1 = n1.elevated ? n1.targetAbs : n1.terr;
                        float hd = math.distance(new float3(part.p0.x, 0f, part.p0.z),
                                                 new float3(part.p1.x, 0f, part.p1.z));
                        float grade = hd > 0.1f ? (ya1 - ya0) / hd * 100f : 0f;
                        if (n0.elevated || n1.elevated)
                        {
                            if (math.abs(grade) > maxGrade) { maxGrade = math.abs(grade); worst = $"段{si + 1} {n0.d:F0}–{n1.d:F0} m"; }
                            nRamp++;
                        }
                        else nGrade++;
                        if (n0.mode == "deck" || n1.mode == "deck")
                        {
                            nDeck++;
                            if (d.water_level_m > 0f)
                            {
                                float clr = math.min(ya0, ya1) - d.water_level_m;
                                if (clr < minClear) minClear = clr;
                            }
                        }
                        bool linked = part.n0 != Entity.Null || part.n1 != Entity.Null;
                        if (linked) stSnap++;
                        sb.AppendLine($"  课程 {courses}: 段{si + 1} {n0.d:F0}→{n1.d:F0} m，长 {hd:F0} m，"
                                      + $"模式 {(n0.mode == n1.mode ? n0.mode : n0.mode + "/" + n1.mode)}，"
                                      + $"地形 {n0.terr:F2}→{n1.terr:F2}，绝对 {ya0:F2}→{ya1:F2}，"
                                      + $"写入 {part.p0.y:F2}→{part.p1.y:F2}，坡度 {grade:+0.00;-0.00;0.00}%，实体 {e.Index}v{e.Version}"
                                      + (s_courseParts.Count > 1 ? "，[站内跨距裁剪]" : "")
                                      + (linked
                                         ? $"，[接站内节点 {(part.n0 != Entity.Null ? part.n0.Index + "v" + part.n0.Version : "-")}/{(part.n1 != Entity.Null ? part.n1.Index + "v" + part.n1.Version : "-")}]"
                                         : ""));
                    }
                }
                sb.AppendLine($"  — 段 {si + 1}：{nodes.Count - 1} 条课程（原段长 {segLen:F0} m，声明 {segCross.Count} 处）");
            }

            sb.AppendLine($"  汇总：课程 {courses} 条（贴地 {nGrade} / 抬高 {nRamp}，其中桥面课程 {nDeck}）；"
                          + $"最大坡 {maxGrade:F2}%{(worst.Length > 0 ? "（" + worst + "）" : "")}"
                          + (minClear < float.MaxValue ? $"；桥面最低离水面 {minClear:+0.00;-0.00;0.00} m" : "")
                          + $"；地形采样 命中 {hit} / 失败 {miss}"
                          + $"；打 DisableMerge 的课程 {nNoMerge} 条"
                          + (explicitY > 0 ? $"；另有 {explicitY} 段走整段显式高程" : "")
                          + (station != null && station.nodes.Count > 0
                             ? $"；**车站接线（v27）**：跳过/裁掉的站内跨距课程 {stSkip} 条、挂到站内节点的课程端 {stSnap} 个"
                               + $"（横向容差 = snap_m {stationSnapM:F1} m）"
                             : "；车站接线（v27）：本会话没有车站（先⑧后⑦），本次未做跳过/挂接"));
        }

        // ---------------------------------------------------------------- 预制件运行时解析（v16）

        private static string SafePrefabName(World world, Entity e)
        {
            try
            {
                var ps = world.GetExistingSystemManaged<Game.Prefabs.PrefabSystem>();
                if (ps == null) return "(无 PrefabSystem)";
                return ps.GetPrefabName(e) ?? "(无名)";
            }
            catch { return "(取名失败)"; }
        }

        /// <summary>按名字（必要时用记录里的 index 当线索）解析铁路网预制件实体。
        /// 要求名字精确匹配**且**预制件带 TrackData/TrackComposition/TrackLane（防同名非网预制件）。</summary>
        public static Entity ResolveRailPrefab(World world, RailTestData d, StringBuilder sb)
        {
            EntityManager em = world.EntityManager;

            // ① 记录里的 index/version 只当线索：名字必须对上，且必须是铁路网预制件
            if (d.prefabIndex >= 0)
            {
                Entity cand = new Entity { Index = d.prefabIndex, Version = d.prefabVersion };
                if (em.Exists(cand))
                {
                    string nm = SafePrefabName(world, cand);
                    bool track = LooksLikeTrackPrefab(em, cand);
                    sb.AppendLine($"记录里的预制件 {d.prefabIndex}v{d.prefabVersion} 存在：名字「{nm}」铁路网预制件={track}");
                    if (nm == d.prefabName && track)
                    {
                        sb.AppendLine("→ 与数据一致，采用它。");
                        return cand;
                    }
                    sb.AppendLine("→ 名字/类型与数据不符，不采用（index 不能当身份），改为按名字重新解析。");
                }
                else
                {
                    sb.AppendLine($"记录里的预制件 {d.prefabIndex}v{d.prefabVersion} 在本进程不存在（index 不能当身份）→ 按名字解析。");
                }
            }

            // ② 按名字 + TrackData 在运行时解析
            EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<Game.Prefabs.PrefabData>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            Entity found = Entity.Null;
            var trackNames = new List<string>();
            try
            {
                foreach (Entity e in arr)
                {
                    bool track = LooksLikeTrackPrefab(em, e);
                    if (!track) continue;
                    string nm = SafePrefabName(world, e);
                    trackNames.Add($"{nm}（{e.Index}v{e.Version}）");
                    if (found == Entity.Null && nm == d.prefabName)
                    {
                        found = e;
                        sb.AppendLine($"按名字解析到「{nm}」= {e.Index}v{e.Version}（含 TrackData 类组件）");
                    }
                }
            }
            finally { arr.Dispose(); }

            if (found == Entity.Null)
            {
                sb.AppendLine($"失败：运行时没有名字为「{d.prefabName}」的铁路网预制件。现存的铁路网预制件（前 12 个）：");
                for (int i = 0; i < trackNames.Count && i < 12; i++) sb.AppendLine("    " + trackNames[i]);
            }
            return found;
        }

        // ---- 工具路线（照 E2C：自建 ToolBaseSystem → 设为 activeTool → Clear/None/Apply 握手）----
        private static Game.Tools.ToolSystem s_toolSystem;
        private static Game.Tools.ToolBaseSystem s_prevTool;
        private static bool s_toolOwned;
        private static int s_beforeRailEdges;
        private static int s_beforeTemp;
        private static string s_resolvedPrefabName = "";
        private static float s_waterLevel;
        private static Entity s_lastRailPrefab = Entity.Null;
        /// <summary>v22：本轮排队的铁路数据。自查阶段用它把**实际建成的节点**按里程与期望绝对高程逐点对账
        ///（v7 的教训：抽样看 12 条边，看不出"整整 1.3 km 落到地面"这种整段性失败）。</summary>
        private static RailTestData s_lastRailData;

        /// <summary>⑦ 单段试建（E2C 同构）：先按运行时名字解析预制件，再把自建工具设为 activeTool；
        /// 定义实体由工具在 OnUpdate 里排进 **ToolOutputBarrier**，applyMode 走 Clear→None→Apply 握手。</summary>
        public static string RailTestTool(World world, out List<Entity> created, out int beforeEdges)
        {
            created = new List<Entity>();
            beforeEdges = 0;
            var sb = new StringBuilder();
            sb.AppendLine("== 铁路单段试建（E2C 同构：ToolOutputBarrier + Clear/None/Apply 握手）==");
            EntityManager em = world.EntityManager;
            string path = Path.Combine(BridgeLiftData.ModsDataFolder, "rail_test.json");
            RailTestData d = RailTestData.Load(path, out string derr);
            if (d == null) return $"失败：解析 {path} 出错 → {derr}\n";
            sb.AppendLine($"数据文件：{path}（段 {d.segments.Count} 条）");

            Entity prefab = ResolveRailPrefab(world, d, sb);
            if (prefab == Entity.Null) return sb.AppendLine("失败：解析不到铁路网预制件，未做任何改动。").ToString();
            s_lastRailPrefab = prefab;
            s_resolvedPrefabName = d.prefabName;
            s_waterLevel = d.water_level_m;
            s_lastRailData = d;

            s_beforeRailEdges = CountEdgesOfPrefab(em, prefab, false);
            s_beforeTemp = CountTemp(em);
            beforeEdges = s_beforeRailEdges;
            sb.AppendLine($"测试前：该预制件的永久边 {s_beforeRailEdges} 条；全图 Temp 实体 {s_beforeTemp} 个");

            // v27 车站接线（P2）：先⑧放站、后⑦铺轨时，把站内轨道节点交给铺轨逻辑
            // （跳过站内跨距 + 端点经 CoursePos.m_Entity 挂上）。没有车站/没点⑧ → 集合为空，行为与 v26 一致。
            StationNodes stNodes = null;
            float stSnapM = 0f;
            {
                string spath = Path.Combine(BridgeLiftData.ModsDataFolder, "rail_station.json");
                StationData sd = null;
                string sderr = null;
                if (File.Exists(spath)) sd = StationData.Load(spath, out sderr);
                if (sd == null)
                {
                    sb.AppendLine("  车站接线：没有可用的 rail_station.json"
                                  + (File.Exists(spath) ? "（解析失败：" + sderr + "）" : "") + " → 本次不做跳过/挂接");
                }
                else
                {
                    Entity stPrefab = ResolveStationPrefab(world, sd, sb);
                    // 线位折线（用于把站内节点筛成"离线位最近的两个"= 本站要服务那条轨的两端）
                    var poly = new List<float2>();
                    for (int i = 0; i < d.segments.Count; i++)
                    {
                        float[] sg = d.segments[i];
                        if (poly.Count == 0) poly.Add(new float2(sg[0], sg[1]));
                        poly.Add(new float2(sg[2], sg[3]));
                    }
                    stNodes = CollectStationNodes(world, em, stPrefab, sb, poly, d.elevations);
                    stSnapM = sd.snap_m;
                    if (!sd.trim_span)
                    {
                        sb.AppendLine("  车站接线：数据 `trim_span=false` → 正线照旧**穿过**车站（不裁剪、不挂接），"
                                      + "只靠原版 overlap 合并；回退用，不重编 mod。");
                        stNodes = null;
                    }
                }
            }

            try
            {
                s_toolSystem = world.GetExistingSystemManaged<Game.Tools.ToolSystem>();
                RailLayToolSystem tool = world.GetOrCreateSystemManaged<RailLayToolSystem>();
                var ps = world.GetExistingSystemManaged<Game.Prefabs.PrefabSystem>();
                if (ps != null)
                {
                    try { tool.TrySetPrefab(ps.GetPrefab<Game.Prefabs.PrefabBase>(prefab)); }
                    catch (Exception ex) { sb.AppendLine("  （PrefabBase 取值失败，不影响定义实体里的 m_Prefab：" + ex.GetType().Name + "）"); }
                }
                if (s_toolSystem != null)
                {
                    s_prevTool = s_toolSystem.activeTool;
                    s_toolSystem.activeTool = tool;
                    s_toolOwned = true;
                    sb.AppendLine($"已激活工具「{tool.toolID}」（原工具 "
                                  + (s_prevTool == null ? "(无)" : s_prevTool.toolID) + "）");
                }
                else sb.AppendLine("  （ToolSystem 取不到 → 退化成裸定义实体路线）");

                RailLayToolSystem.Wanted = true;
                RailLayToolSystem.Pending = d;
                RailLayToolSystem.PendingPrefab = prefab;
                RailLayToolSystem.PendingStationNodes = stNodes;
                RailLayToolSystem.PendingStationSnapM = stSnapM;
                RailLayToolSystem.PendingAttachStart = Entity.Null;    // ⑦ 铺轨不做首末端挂接
                RailLayToolSystem.PendingAttachEnd = Entity.Null;
                RailLayToolSystem.ResetRun();
                created = RailLayToolSystem.Created;
                sb.AppendLine("工具保持激活约 5 秒：applyMode 由它按 Clear→None→（等到 Temp 预览出现）→Apply 走。");
            }
            catch (Exception ex) { sb.AppendLine("异常：" + ex); }
            sb.AppendLine("随后自动还原原工具并做分类自查（只清本轮 marker，不按预制件乱清）。");
            return sb.ToString();
        }

        /// <summary>⑦ 的收尾：停工具、还原原工具，报告 Clear/None/Apply 握手结果，再做分类自查。</summary>
        public static string RailTestToolFinish(World world, List<Entity> created, int before)
        {
            var sb = new StringBuilder();
            sb.AppendLine("-- 5 秒后自查（工具路线 / E2C 握手）--");
            sb.AppendLine($"工具 OnUpdate 帧数 = {RailLayToolSystem.UpdateCount}（>0 = 工具确实在跑）");
            sb.AppendLine($"握手：排队完成 = {RailLayToolSystem.Queued}；"
                          + $"等到 Temp 预览并切 Apply = {RailLayToolSystem.ApplyFrames > 0}"
                          + (RailLayToolSystem.ApplyUpdateIndex >= 0 ? $"（第 {RailLayToolSystem.ApplyUpdateIndex} 帧）" : ""));
            sb.AppendLine($"Temp 计数：排队帧 = {RailLayToolSystem.TempCountAtQueue} → 切 Apply 时 = {RailLayToolSystem.TempCountAtApply}"
                          + "（后者更大 = 原版生成系统确实产出了预览实体）");
            sb.AppendLine($"applyMode 末态 = {RailLayToolSystem.LastApplyMode}；中止标志 = {RailLayToolSystem.Aborted}");
            if (RailLayToolSystem.QueueLog.Length > 0)
                sb.Append(RailLayToolSystem.QueueLog.ToString());
            RailLayToolSystem.Wanted = false;
            if (s_toolOwned && s_toolSystem != null)
            {
                try
                {
                    s_toolSystem.activeTool = s_prevTool;
                    sb.AppendLine("已还原原工具。");
                }
                catch (Exception ex) { sb.AppendLine("还原工具失败：" + ex.GetType().Name + " " + ex.Message); }
                s_toolOwned = false;
            }
            sb.AppendLine(RailTestVerify(world, before, created));
            return sb.ToString();
        }

        // ---------------------------------------------------------------- v33 站前路（⑨：NetCourse 铺公路 + 两端挂既有道路节点）

        /// <summary>预制件是不是公路网预制件：组件里出现 RoadData / CarLaneData。</summary>
        private static bool LooksLikeRoadPrefab(EntityManager em, Entity e)
        {
            NativeArray<ComponentType> cts = default;
            try
            {
                cts = em.GetComponentTypes(e, Allocator.Temp);
                for (int i = 0; i < cts.Length; i++)
                {
                    string tn;
                    try { tn = cts[i].GetManagedType().Name; } catch { continue; }
                    if (tn.Contains("RoadData") || tn.Contains("CarLaneData")) return true;
                }
            }
            catch { }
            finally { if (cts.IsCreated) cts.Dispose(); }
            return false;
        }

        /// <summary>按名字解析公路网预制件（照 ResolveRailPrefab 的两段式：index 只当线索）。</summary>
        public static Entity ResolveRoadPrefab(World world, RailTestData d, StringBuilder sb)
        {
            EntityManager em = world.EntityManager;
            if (d.prefabIndex >= 0)
            {
                Entity cand = new Entity { Index = d.prefabIndex, Version = d.prefabVersion };
                if (em.Exists(cand))
                {
                    string nm = SafePrefabName(world, cand);
                    bool road = LooksLikeRoadPrefab(em, cand);
                    sb.AppendLine($"记录里的预制件 {d.prefabIndex}v{d.prefabVersion} 存在：名字「{nm}」公路网预制件={road}");
                    if (nm == d.prefabName && road) { sb.AppendLine("→ 与数据一致，采用它。"); return cand; }
                }
            }
            EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<Game.Prefabs.PrefabData>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            Entity found = Entity.Null;
            var names = new List<string>();
            try
            {
                foreach (Entity e in arr)
                {
                    if (!LooksLikeRoadPrefab(em, e)) continue;
                    string nm = SafePrefabName(world, e);
                    if (names.Count < 40) names.Add(nm);
                    if (found == Entity.Null && nm == d.prefabName) found = e;
                }
            }
            finally { arr.Dispose(); }
            if (found == Entity.Null)
            {
                sb.AppendLine($"失败：运行时没有名字为「{d.prefabName}」的公路网预制件。现有候选（前 20）：");
                for (int i = 0; i < names.Count && i < 20; i++) sb.AppendLine("    " + names[i]);
            }
            else sb.AppendLine($"按名字解析到公路预制件「{d.prefabName}」= {found.Index}v{found.Version}");
            return found;
        }

        /// <summary>找离 p 最近的**既有永久公路节点**（带 Road、无 Temp）；超出 maxDist 返回 Null。</summary>
        public static Entity FindNearestRoadNode(World world, EntityManager em, float3 p, float maxDist,
            out float3 pos, out float dist)
        {
            pos = default;
            dist = float.MaxValue;
            Entity best = Entity.Null;
            EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<Node>(), ComponentType.ReadOnly<Road>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            try
            {
                foreach (Entity n in arr)
                {
                    if (em.HasComponent<Temp>(n)) continue;
                    float3 q2 = em.GetComponentData<Node>(n).m_Position;
                    float dd = math.distance(new float2(q2.x, q2.z), new float2(p.x, p.z));
                    if (dd < dist) { dist = dd; pos = q2; best = n; }
                }
            }
            finally { arr.Dispose(); }
            if (best == Entity.Null || dist > maxDist) { dist = -1f; return Entity.Null; }
            return best;
        }

        private static string s_resolvedRoadName = "";
        private static int s_beforeRoadEdges;

        /// <summary>⑨ 站前路：按 rail_road.json 铺一条公路，并把**首/末端点**挂到最近的既有公路节点上
        /// （官方"接既有节点"开关 = `CoursePos.m_Entity`，`NetToolSystem` 3546/3550；不靠坐标重合）。</summary>
        public static string StationRoadTool(World world, out List<Entity> created, out int beforeEdges)
        {
            created = new List<Entity>();
            beforeEdges = 0;
            var sb = new StringBuilder();
            sb.AppendLine("== 站前路铺设（⑨：NetCourse 铺公路 + 两端挂既有道路节点）==");
            EntityManager em = world.EntityManager;
            string path = Path.Combine(BridgeLiftData.ModsDataFolder, "rail_road.json");
            RailTestData d = RailTestData.Load(path, out string derr);
            if (d == null) return $"失败：解析 {path} 出错 → {derr}\n";
            sb.AppendLine($"数据文件：{path}（段 {d.segments.Count} 条，预制件「{d.prefabName}」）");

            Entity prefab = ResolveRoadPrefab(world, d, sb);
            if (prefab == Entity.Null) return sb.AppendLine("失败：解析不到公路预制件，未做任何改动。").ToString();

            s_beforeRoadEdges = CountEdgesOfPrefab(em, prefab, false);
            beforeEdges = s_beforeRoadEdges;
            s_resolvedRoadName = d.prefabName;
            sb.AppendLine($"测试前：该公路预制件的永久边 {s_beforeRoadEdges} 条");

            float[] f0 = d.segments[0];
            float[] fl = d.segments[d.segments.Count - 1];
            float3 pStart = new float3(f0[0], 0f, f0[1]);
            float3 pEnd = new float3(fl[2], 0f, fl[3]);
            float3 ap, bp;
            float ad, bd;
            Entity nStart = FindNearestRoadNode(world, em, pStart, 40f, out ap, out ad);
            sb.AppendLine(nStart == Entity.Null
                ? $"  首端 ({pStart.x:F1},{pStart.z:F1}) 40 m 内没有既有公路节点 → 该端不挂接"
                : $"  首端挂接：既有公路节点 {nStart.Index}v{nStart.Version} 距 {ad:F2} m（({ap.x:F1},{ap.y:F2},{ap.z:F1})）");
            Entity nEnd = FindNearestRoadNode(world, em, pEnd, 40f, out bp, out bd);
            sb.AppendLine(nEnd == Entity.Null
                ? $"  末端 ({pEnd.x:F1},{pEnd.z:F1}) 40 m 内没有既有公路节点 → 该端不挂接"
                : $"  末端挂接：既有公路节点 {nEnd.Index}v{nEnd.Version} 距 {bd:F2} m（({bp.x:F1},{bp.y:F2},{bp.z:F1})）");

            try
            {
                s_toolSystem = world.GetExistingSystemManaged<Game.Tools.ToolSystem>();
                RailLayToolSystem tool = world.GetOrCreateSystemManaged<RailLayToolSystem>();
                var ps = world.GetExistingSystemManaged<Game.Prefabs.PrefabSystem>();
                if (ps != null)
                {
                    try { tool.TrySetPrefab(ps.GetPrefab<Game.Prefabs.PrefabBase>(prefab)); } catch { }
                }
                if (s_toolSystem != null)
                {
                    s_prevTool = s_toolSystem.activeTool;
                    s_toolSystem.activeTool = tool;
                    s_toolOwned = true;
                }
                RailLayToolSystem.Wanted = true;
                RailLayToolSystem.Pending = d;
                RailLayToolSystem.PendingPrefab = prefab;
                RailLayToolSystem.PendingStationNodes = null;      // 公路不做站内裁剪
                RailLayToolSystem.PendingStationSnapM = 0f;
                RailLayToolSystem.PendingAttachStart = nStart;
                RailLayToolSystem.PendingAttachStartPos = ap;
                RailLayToolSystem.PendingAttachEnd = nEnd;
                RailLayToolSystem.PendingAttachEndPos = bp;
                RailLayToolSystem.ResetRun();
                created = RailLayToolSystem.Created;
                sb.AppendLine("工具保持激活约 5 秒（applyMode Clear→None→等 Temp→Apply）。");
            }
            catch (Exception ex) { sb.AppendLine("异常：" + ex); }
            sb.AppendLine("随后自动还原原工具并自查（只清本轮 marker）。");
            return sb.ToString();
        }

        /// <summary>⑨ 收尾自查：公路永久边前后对比 + 清理。</summary>
        public static string StationRoadFinish(World world, List<Entity> created, int before)
        {
            var sb = new StringBuilder();
            sb.AppendLine("-- 5 秒后自查（站前路）--");
            sb.AppendLine($"工具 OnUpdate 帧数 = {RailLayToolSystem.UpdateCount}；排队 = {RailLayToolSystem.Queued}；"
                          + $"切 Apply = {RailLayToolSystem.ApplyFrames > 0}"
                          + (RailLayToolSystem.ApplyUpdateIndex >= 0 ? $"（第 {RailLayToolSystem.ApplyUpdateIndex} 帧）" : ""));
            if (RailLayToolSystem.QueueLog.Length > 0) sb.Append(RailLayToolSystem.QueueLog.ToString());
            RailLayToolSystem.Wanted = false;
            if (s_toolOwned && s_toolSystem != null)
            {
                try { s_toolSystem.activeTool = s_prevTool; sb.AppendLine("已还原原工具。"); }
                catch (Exception ex) { sb.AppendLine("还原工具失败：" + ex.GetType().Name); }
                s_toolOwned = false;
            }
            EntityManager em = world.EntityManager;
            int now = CountEdgesOfPrefab(em, RailLayToolSystem.PendingPrefab, false);
            sb.AppendLine($"永久公路边（预制件「{s_resolvedRoadName}」）：{s_beforeRoadEdges} → {now}"
                          + $"（Δ {now - s_beforeRoadEdges:+0;-0;0}）");
            int killed = DestroyMarked(em);
            sb.AppendLine($"已清理本轮定义实体 {killed} 个（只按 RailTestMarker）。");
            sb.AppendLine(now > s_beforeRoadEdges
                ? "结论：**站前路已铺出永久边**；与既有村道是否连通以机主目检为准（六层证据 4≠5）。"
                : "结论：没有新增永久公路边 → 看上面的预制件解析与握手数据。");
            return sb.ToString();
        }

        private static CoursePos BuildCoursePos(float3 pos, float courseDelta, int flags, Entity node)
            => BuildCoursePos(pos, courseDelta, flags, node, quaternion.identity);

        /// <summary>v28：带节点朝向的重载——站内子网课程的 CoursePos 朝向照官方用
        /// `NetUtils.GetNodeRotation(切线, 建筑旋转)`（stconn2_objtoolbase_createsubnet_il.txt:304/385）。</summary>
        private static CoursePos BuildCoursePos(float3 pos, float courseDelta, int flags, Entity node, quaternion rot)
        {
            CoursePos cp = default;
            cp.m_Entity = node;                 // v27：非空 = 这个端点挂到既有节点（站内轨道节点）
            cp.m_Position = pos;
            cp.m_Rotation = rot;
            cp.m_Elevation = new float2(pos.y);
            cp.m_CourseDelta = courseDelta;
            cp.m_SplitPosition = 0f;
            cp.m_Flags = (CoursePosFlags)flags;
            cp.m_ParentMesh = -1;
            return cp;
        }

        /// <summary>数一遍「TrainTrack 标记的边」——仅作对照项（TrainTrack 究竟挂边还是挂车道，本轮用 dump 实测确认）。</summary>
        public static int CountTrackEdges(EntityManager em)
        {
            EntityQuery q = em.CreateEntityQuery(
                ComponentType.ReadOnly<TrainTrack>(), ComponentType.ReadOnly<Edge>());
            return q.CalculateEntityCount();
        }

        // ---------------------------------------------------------------- 分类诊断（v16 修正口径）
        //
        // v15 的两条错误口径（机主 2026-09-30 指出，已撤）：
        //   ① 把「带 Temp 的定义实体消失」当成「生成成功」——它只是被别的清理路径删掉了；
        //   ② Temp+NetCourse 的计数把「我们自己造的定义实体」也算进了「原版生成的预览」。
        // 现在三类分开数：本轮请求（RailTestMarker）/ 原版临时网实体（Temp 且无我们的 marker）/
        // 永久铁路（Edge 且无 Temp 且预制件 = 运行时解析出的那个）。清理只按 marker，绝不按预制件。

        private static int CountQuery(EntityManager em, params ComponentType[] types)
        {
            EntityQuery q = em.CreateEntityQuery(types);
            return q.CalculateEntityCount();
        }

        private static int CountTemp(EntityManager em)
            => CountQuery(em, ComponentType.ReadOnly<Temp>());

        /// <summary>数「预制件 == 指定预制件」的边。withTemp=false 只数永久边，true 只数带 Temp 的预览边。</summary>
        private static int CountEdgesOfPrefab(EntityManager em, Entity prefab, bool withTemp)
        {
            if (prefab == Entity.Null) return 0;
            EntityQuery q = em.CreateEntityQuery(
                ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<Game.Prefabs.PrefabRef>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            int n = 0;
            try
            {
                foreach (Entity e in arr)
                {
                    if (em.GetComponentData<Game.Prefabs.PrefabRef>(e).m_Prefab != prefab) continue;
                    if (em.HasComponent<Temp>(e) == withTemp) n++;
                }
            }
            finally { arr.Dispose(); }
            return n;
        }

        private static Entity FirstEdgeOfPrefab(EntityManager em, Entity prefab, bool withTemp)
        {
            if (prefab == Entity.Null) return Entity.Null;
            EntityQuery q = em.CreateEntityQuery(
                ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<Game.Prefabs.PrefabRef>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            Entity found = Entity.Null;
            try
            {
                foreach (Entity e in arr)
                {
                    if (em.GetComponentData<Game.Prefabs.PrefabRef>(e).m_Prefab != prefab) continue;
                    if (em.HasComponent<Temp>(e) != withTemp) continue;
                    found = e; break;
                }
            }
            finally { arr.Dispose(); }
            return found;
        }

        private static Entity FirstEdgeWithRoad(EntityManager em)
        {
            EntityQuery q = em.CreateEntityQuery(
                ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<Road>(),
                ComponentType.ReadOnly<Game.Prefabs.PrefabRef>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            Entity found = Entity.Null;
            try
            {
                foreach (Entity e in arr) { if (!em.HasComponent<Temp>(e)) { found = e; break; } }
            }
            finally { arr.Dispose(); }
            return found;
        }

        /// <summary>列出该预制件的铁路边（最多 max 条），用于逐条 dump 与连接判定。</summary>
        private static List<Entity> RailEdgesOfPrefab(EntityManager em, Entity prefab, bool withTemp, int max)
        {
            var list = new List<Entity>();
            if (prefab == Entity.Null) return list;
            EntityQuery q = em.CreateEntityQuery(
                ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<Game.Prefabs.PrefabRef>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            try
            {
                foreach (Entity e in arr)
                {
                    if (em.GetComponentData<Game.Prefabs.PrefabRef>(e).m_Prefab != prefab) continue;
                    if (em.HasComponent<Temp>(e) != withTemp) continue;
                    list.Add(e);
                    if (list.Count >= max) break;
                }
            }
            finally { arr.Dispose(); }
            return list;
        }

        /// <summary>第二关的判定（v19 修正）：连续铁路要**共享节点实体**，而不只是坐标重合。
        /// v18 那版把「该节点处 Composition 的 start/end 条目不同」当成"只是几何相接"——**那是错的**：
        /// `Composition.m_Edge` 才是"同一套断面/composition"的判据（同网应相等），
        /// 而 m_StartNode/m_EndNode 是同一 composition 里的不同节点侧条目，本来就可以不同。
        /// 本版给出三条硬证据：① 共用节点实体 ② 节点 `ConnectedEdge` 缓冲里确实列着这些边
        /// ③ 节点上的**轨道车道**（带 TrackLane/TrainTrack 的车道）及其 LaneConnection。</summary>
        private static void DumpRailConnectivity(EntityManager em, List<Entity> rails, StringBuilder sb)
        {
            var nodeEdges = new Dictionary<Entity, List<Entity>>();
            foreach (Entity e in rails)
            {
                if (!em.HasComponent<Edge>(e)) continue;
                Edge ed = em.GetComponentData<Edge>(e);
                for (int k = 0; k < 2; k++)
                {
                    Entity n = k == 0 ? ed.m_Start : ed.m_End;
                    if (n == Entity.Null) continue;
                    List<Entity> l;
                    if (!nodeEdges.TryGetValue(n, out l)) { l = new List<Entity>(); nodeEdges[n] = l; }
                    l.Add(e);
                }
            }

            sb.AppendLine("-- 轨道连接判定（共享的是**节点实体**，不是坐标重合）--");
            int junctions = 0, trueLinks = 0;
            foreach (KeyValuePair<Entity, List<Entity>> kv in nodeEdges)
            {
                List<Entity> group = kv.Value;
                if (group.Count < 2) continue;
                junctions++;
                var ids = new List<string>();
                var compEdges = new List<string>();
                foreach (Entity e in group)
                {
                    ids.Add($"{e.Index}v{e.Version}");
                    compEdges.Add(em.HasComponent<Composition>(e)
                        ? $"{em.GetComponentData<Composition>(e).m_Edge.Index}v{em.GetComponentData<Composition>(e).m_Edge.Version}"
                        : "无");
                }
                bool sameComp = true;
                for (int i = 1; i < compEdges.Count; i++) if (compEdges[i] != compEdges[0]) sameComp = false;

                var connIds = new List<string>();
                bool allListed = true;
                if (em.HasBuffer<ConnectedEdge>(kv.Key))
                {
                    var buf = em.GetBuffer<ConnectedEdge>(kv.Key);
                    for (int i = 0; i < buf.Length && i < 8; i++) connIds.Add($"{buf[i].m_Edge.Index}v{buf[i].m_Edge.Version}");
                    foreach (Entity e in group)
                    {
                        bool found = false;
                        for (int i = 0; i < buf.Length; i++) if (buf[i].m_Edge == e) { found = true; break; }
                        if (!found) allListed = false;
                    }
                }
                else allListed = false;

                int nodeLanes = 0, trackLanes = 0, withConn = 0;
                if (em.HasBuffer<Game.Net.SubLane>(kv.Key))
                {
                    var subs = em.GetBuffer<Game.Net.SubLane>(kv.Key);
                    nodeLanes = subs.Length;
                    for (int i = 0; i < subs.Length; i++)
                    {
                        Entity lane = subs[i].m_SubLane;
                        if (lane == Entity.Null || !em.Exists(lane)) continue;
                        bool isTrack = em.HasComponent<Game.Net.TrackLane>(lane) || em.HasComponent<TrainTrack>(lane);
                        if (!isTrack) continue;
                        trackLanes++;
                        if (em.HasComponent<LaneConnection>(lane)) withConn++;
                    }
                }

                bool real = sameComp && allListed;
                if (real) trueLinks++;
                sb.AppendLine($"  节点 {kv.Key.Index}v{kv.Key.Version} 被 {group.Count} 条铁路边共用：{string.Join(" / ", ids.ToArray())}");
                sb.AppendLine($"    Composition.m_Edge：{string.Join(" / ", compEdges.ToArray())}"
                              + (sameComp ? "  → 同一套 composition" : "  → 不同 composition"));
                sb.AppendLine($"    节点 ConnectedEdge 缓冲（{connIds.Count}）：{string.Join(" / ", connIds.ToArray())}"
                              + (allListed ? "  → 这些边确实挂在该节点上（拓扑连接）" : "  → 有边没列进来（拓扑未连）"));
                sb.AppendLine($"    节点子车道 {nodeLanes} 条，其中轨道车道 {trackLanes} 条、带 LaneConnection 的 {withConn} 条");
                sb.AppendLine($"    判定：{(real ? "**真连接**（共享节点 + 拓扑登记）" : "未达真连接")}；节点 TrainTrack={em.HasComponent<TrainTrack>(kv.Key)}");
            }
            if (junctions == 0)
                sb.AppendLine("  （没有任何被 ≥2 条铁路边共用的节点 → 还不构成连续铁路）");
            else
                sb.AppendLine($"  小结：共用节点 {junctions} 个，其中真连接 {trueLinks} 个");
        }

        /// <summary>对外连接诊断（2026-09-30 机主："这一侧怎么还到黄线外面去了，另一侧是不是没连接外部，
        /// 两段都要接外面啊"）。官方口径：网络**铺到可玩区边缘**才生成对外连接
        /// （Map Creation: Outside Connections）。原版机制（IL 实读 `outsideconn_il.txt`）：
        /// `Game.Net.OutsideConnectionSystem/UpdateOutsideConnectionsJob::Execute` 只处理带
        /// `Game.Objects.OutsideConnection` 的**建筑实例**（预制件如「Road/Train Outside Connection - Twoway」，
        /// 带 `Game.Prefabs.OutsideConnectionData`）——由它把建筑挂到的那个网节点补上
        /// `Game.Net.OutsideConnection`、并建连接车道（`TryCreateLane`）。所以"连没连上外面"的直接证据
        /// 就是**网节点上有没有 `Game.Net.OutsideConnection`**。
        /// 本段只读，打印三件事：① 图上现有对外连接建筑有几处、什么预制件、在哪；
        /// ② 网络节点上被标了 `Game.Net.OutsideConnection` 的有几个、是铁路还是公路、在哪；
        /// ③ 本预制件铁路网的**终端节点**（只接 1 条铁路边）位置 + 组件 + 有没有那个标记。</summary>
        private static void DumpOutsideConnections(World world, EntityManager em, StringBuilder sb)
        {
            sb.AppendLine("-- 对外连接诊断（铁路两端连没连上外面）--");
            int objAll = CountQuery(em, ComponentType.ReadOnly<Game.Objects.OutsideConnection>());
            int netAll = CountQuery(em, ComponentType.ReadOnly<Game.Net.OutsideConnection>());
            int netNodeAll = CountQuery(em, ComponentType.ReadOnly<Game.Net.OutsideConnection>(), ComponentType.ReadOnly<Node>());
            int netNodeRail = CountQuery(em, ComponentType.ReadOnly<Game.Net.OutsideConnection>(),
                ComponentType.ReadOnly<Node>(), ComponentType.ReadOnly<TrainTrack>());
            int netNodeRoad = CountQuery(em, ComponentType.ReadOnly<Game.Net.OutsideConnection>(),
                ComponentType.ReadOnly<Node>(), ComponentType.ReadOnly<Road>());
            int netEdgeAll = CountQuery(em, ComponentType.ReadOnly<Game.Net.OutsideConnection>(), ComponentType.ReadOnly<Edge>());
            sb.AppendLine($"  ① 对外连接**建筑**（Game.Objects.OutsideConnection）：{objAll} 个");
            {
                EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<Game.Objects.OutsideConnection>());
                NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
                try
                {
                    int shown = 0;
                    foreach (Entity o in arr)
                    {
                        if (shown++ >= 8) { sb.AppendLine($"     …（共 {arr.Length} 个，其余略）"); break; }
                        string nm = "(无 PrefabRef)";
                        if (em.HasComponent<Game.Prefabs.PrefabRef>(o))
                            nm = SafePrefabName(world, em.GetComponentData<Game.Prefabs.PrefabRef>(o).m_Prefab);
                        string pos = "?";
                        if (em.HasComponent<Game.Objects.Transform>(o))
                        {
                            float3 p = em.GetComponentData<Game.Objects.Transform>(o).m_Position;
                            pos = $"({p.x:F1},{p.y:F1},{p.z:F1})";
                        }
                        string owner = em.HasComponent<Owner>(o) ? $"{em.GetComponentData<Owner>(o).m_Owner.Index}v{em.GetComponentData<Owner>(o).m_Owner.Version}" : "无";
                        sb.AppendLine($"     {nm}  {pos}  Owner={owner}");
                    }
                }
                finally { arr.Dispose(); }
            }
            sb.AppendLine($"  ② 网上的对外连接标记（Game.Net.OutsideConnection）：共 {netAll} 个实体"
                          + $"（节点 {netNodeAll}：铁路 {netNodeRail} / 公路 {netNodeRoad}；边 {netEdgeAll}）");
            {
                EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<Game.Net.OutsideConnection>(),
                    ComponentType.ReadOnly<Node>());
                NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
                try
                {
                    int shown = 0;
                    foreach (Entity n in arr)
                    {
                        if (shown++ >= 8) { sb.AppendLine($"     …（共 {arr.Length} 个节点，其余略）"); break; }
                        float3 p = em.GetComponentData<Game.Net.Node>(n).m_Position;
                        string nm = em.HasComponent<Game.Prefabs.PrefabRef>(n)
                            ? SafePrefabName(world, em.GetComponentData<Game.Prefabs.PrefabRef>(n).m_Prefab) : "(无)";
                        sb.AppendLine($"     节点 {n.Index}v{n.Version} ({p.x:F1},{p.y:F1},{p.z:F1})"
                                      + $" 铁路={em.HasComponent<TrainTrack>(n)} 公路={em.HasComponent<Road>(n)} 预制件={nm}");
                    }
                }
                finally { arr.Dispose(); }
            }

            List<Entity> rails = RailEdgesOfPrefab(em, s_lastRailPrefab, false, 4096);
            if (rails.Count == 0) { sb.AppendLine("  ③ （图上没有永久铁路边）"); return; }
            var deg = new Dictionary<Entity, List<Entity>>();
            foreach (Entity e in rails)
            {
                if (!em.HasComponent<Edge>(e)) continue;
                Edge ed = em.GetComponentData<Edge>(e);
                for (int k = 0; k < 2; k++)
                {
                    Entity n = k == 0 ? ed.m_Start : ed.m_End;
                    if (n == Entity.Null) continue;
                    List<Entity> l;
                    if (!deg.TryGetValue(n, out l)) { l = new List<Entity>(); deg[n] = l; }
                    l.Add(e);
                }
            }
            var ends = new List<Entity>();
            foreach (KeyValuePair<Entity, List<Entity>> kv in deg) if (kv.Value.Count == 1) ends.Add(kv.Key);
            sb.AppendLine($"  ③ 铁路网：边 {rails.Count} 条 / 节点 {deg.Count} 个；"
                          + $"**终端节点（只接 1 条铁路边）{ends.Count} 个**"
                          + (ends.Count == 2 ? "（单线网络应为 2）" : "（异常：单线网络应有且仅有 2 个）"));
            int railMarked = 0;
            foreach (Entity n in deg.Keys) if (em.HasComponent<Game.Net.OutsideConnection>(n)) railMarked++;
            foreach (Entity n in ends)
            {
                float3 p = em.HasComponent<Game.Net.Node>(n) ? em.GetComponentData<Game.Net.Node>(n).m_Position : default;
                int ce = em.HasBuffer<ConnectedEdge>(n) ? em.GetBuffer<ConnectedEdge>(n).Length : -1;
                sb.AppendLine($"     终端节点 {n.Index}v{n.Version} 位置=({p.x:F1},{p.y:F1},{p.z:F1})"
                              + $"  OutsideConnection={em.HasComponent<Game.Net.OutsideConnection>(n)}"
                              + $"  TrainTrack={em.HasComponent<TrainTrack>(n)}  ConnectedEdge={ce}");
                sb.AppendLine($"       组件：{CompList(em, n)}");
            }
            sb.AppendLine($"  铁路节点里带 Game.Net.OutsideConnection 的 {railMarked} 个 → "
                          + (railMarked >= 2 ? "**两端都成了对外连接**"
                             : railMarked == 1 ? "只有一端成了对外连接（另一端是死头）"
                             : "**一端都没成 = 铁路对外连接没生成**（按 IL，需要图边有 Train Outside Connection 建筑）"));
        }

        /// <summary>v29 诊断（只读）：**车站站内轨道节点的连接实况** —— 我们的正线到底挂上没有。
        /// 判据来自 `ConnectionWarningSystem/CheckNodeConnections` 的语义：站内节点上若只有"本建筑自己的边"
        /// （都带 `Game.Common.Owner`）就是未连接；出现**无 Owner 的"外来"边**（= 我们的正线）才算接上。
        /// 这条 dump 也能直接解释「无轨道连接点」还剩几个、挂在哪些节点上。</summary>
        private static void DumpStationConnectivity(World world, EntityManager em, StringBuilder sb)
        {
            string sp = Path.Combine(BridgeLiftData.ModsDataFolder, "rail_station.json");
            if (!File.Exists(sp)) return;
            StationData sd = StationData.Load(sp, out _);
            if (sd == null) return;
            Entity prefab = ResolveStationPrefab(world, sd, new StringBuilder());   // 安静解析
            if (prefab == Entity.Null) return;

            sb.AppendLine("-- 车站接线实况（v29 诊断：站内节点上有没有「外来边」= 我们的正线）--");
            int nodes = 0, withForeign = 0, ownOnly = 0;
            foreach (Entity b in BuildingsOfPrefab(em, prefab, false, 64))
            {
                if (!em.HasBuffer<Game.Net.SubNet>(b)) continue;
                DynamicBuffer<Game.Net.SubNet> subs = em.GetBuffer<Game.Net.SubNet>(b);
                for (int i = 0; i < subs.Length; i++)
                {
                    Entity n = subs[i].m_SubNet;
                    if (n == Entity.Null || !em.Exists(n) || !em.HasComponent<Node>(n)) continue;
                    nodes++;
                    int own = 0, foreign = 0, trackLanes = 0;
                    var ids = new List<string>();
                    if (em.HasBuffer<ConnectedEdge>(n))
                    {
                        DynamicBuffer<ConnectedEdge> ces = em.GetBuffer<ConnectedEdge>(n);
                        for (int k = 0; k < ces.Length; k++)
                        {
                            Entity e = ces[k].m_Edge;
                            if (e == Entity.Null || !em.Exists(e)) continue;
                            bool hasOwner = em.HasComponent<Game.Common.Owner>(e);
                            if (hasOwner) own++; else foreign++;
                            if (em.HasBuffer<Game.Net.SubLane>(e))
                            {
                                DynamicBuffer<Game.Net.SubLane> ls = em.GetBuffer<Game.Net.SubLane>(e);
                                for (int j = 0; j < ls.Length; j++)
                                {
                                    Entity lane = ls[j].m_SubLane;
                                    if (lane != Entity.Null && em.Exists(lane)
                                        && em.HasComponent<Game.Net.TrackLane>(lane)) trackLanes++;
                                }
                            }
                            if (ids.Count < 6)
                            {
                                string pf = em.HasComponent<Game.Prefabs.PrefabRef>(e)
                                    ? SafePrefabName(world, em.GetComponentData<Game.Prefabs.PrefabRef>(e).m_Prefab)
                                    : "(无)";
                                ids.Add($"{pf}{(hasOwner ? "[Owner]" : "[外来]")}");
                            }
                        }
                    }
                    if (foreign > 0) withForeign++; else ownOnly++;
                    float3 p = em.GetComponentData<Node>(n).m_Position;
                    sb.AppendLine($"    站内节点 {n.Index}v{n.Version} ({p.x:F1},{p.y:F2},{p.z:F1})："
                                  + $"ConnectedEdge {own + foreign} 条（本建筑 {own} / 外来 {foreign}），轨道车道 {trackLanes} 条"
                                  + (ids.Count > 0 ? "；边：" + string.Join(" / ", ids.ToArray()) : ""));
                }
            }
            sb.AppendLine($"    小结：站内节点 {nodes} 个；**有外来边（正线挂上）的 {withForeign} 个**，只有自家边的 {ownOnly} 个"
                          + (withForeign > 0
                             ? " → 接线成立（原版 overlap 合并 或 v27 显式挂接 至少一条生效）"
                             : " → 一个都没挂上（正线只是几何重合，拓扑上没接）"));
        }

        /// <summary>只清「本轮我们造的」定义实体（带 RailTestMarker）。绝不按预制件清——
        /// 同一预制件上可能有别的工具/玩家的对象（机主 2026-09-30 明确要求）。</summary>
        private static int DestroyMarked(EntityManager em)
        {
            EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<RailTestMarker>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            int n = 0;
            try { foreach (Entity e in arr) { em.DestroyEntity(e); n++; } }
            finally { arr.Dispose(); }
            return n;
        }

        /// <summary>实体上所有组件类型名（buffer 标 [buffer]），按名排序，用于公路/铁路结构对照。</summary>
        private static string CompList(EntityManager em, Entity e)
        {
            var names = new List<string>();
            NativeArray<ComponentType> cts = default;
            try
            {
                cts = em.GetComponentTypes(e, Allocator.Temp);
                for (int i = 0; i < cts.Length; i++)
                {
                    string tn;
                    try { tn = cts[i].GetManagedType().Name; } catch { continue; }
                    if (cts[i].IsBuffer) tn += "[buffer]";
                    names.Add(tn);
                }
            }
            catch { }
            finally { if (cts.IsCreated) cts.Dispose(); }
            names.Sort();
            return string.Join(",", names);
        }

        /// <summary>预制件是不是「铁路网预制件」：组件里出现 TrackData/TrackComposition/TrackLane。</summary>
        private static bool LooksLikeTrackPrefab(EntityManager em, Entity e)
        {
            NativeArray<ComponentType> cts = default;
            try
            {
                cts = em.GetComponentTypes(e, Allocator.Temp);
                for (int i = 0; i < cts.Length; i++)
                {
                    string tn;
                    try { tn = cts[i].GetManagedType().Name; } catch { continue; }
                    if (tn.Contains("TrackData") || tn.Contains("TrackComposition") || tn.Contains("TrackLane")) return true;
                }
            }
            catch { }
            finally { if (cts.IsCreated) cts.Dispose(); }
            return false;
        }

        private static void DumpNode(EntityManager em, Entity n, StringBuilder sb, string tag)
        {
            if (n == Entity.Null || !em.Exists(n)) { sb.AppendLine($"    {tag}：(空/不存在)"); return; }
            sb.AppendLine($"    {tag} {n.Index}v{n.Version}：{CompList(em, n)}");
            if (em.HasComponent<Node>(n))
            {
                Node nd = em.GetComponentData<Node>(n);
                sb.AppendLine($"      位置=({nd.m_Position.x:F1},{nd.m_Position.y:F1},{nd.m_Position.z:F1})");
            }
            if (em.HasComponent<Game.Objects.Transform>(n))
            {
                Game.Objects.Transform tf = em.GetComponentData<Game.Objects.Transform>(n);
                sb.AppendLine($"      Transform=({tf.m_Position.x:F1},{tf.m_Position.y:F1},{tf.m_Position.z:F1})");
            }
            int ce = em.HasBuffer<ConnectedEdge>(n) ? em.GetBuffer<ConnectedEdge>(n).Length : -1;
            int cn = em.HasBuffer<ConnectedNode>(n) ? em.GetBuffer<ConnectedNode>(n).Length : -1;
            int sl = em.HasBuffer<Game.Net.SubLane>(n) ? em.GetBuffer<Game.Net.SubLane>(n).Length : -1;
            sb.AppendLine($"      缓冲：ConnectedEdge={ce} ConnectedNode={cn} SubLane={sl}；Temp={em.HasComponent<Temp>(n)}");
        }

        /// <summary>Bezier4x3 上取点（自己在代码里算，不依赖 MathUtils 的重载）。</summary>
        private static float3 BezierAt(Bezier4x3 b, float t)
        {
            float u = 1f - t;
            float w0 = u * u * u, w1 = 3f * u * u * t, w2 = 3f * u * t * t, w3 = t * t * t;
            return new float3(
                w0 * b.a.x + w1 * b.b.x + w2 * b.c.x + w3 * b.d.x,
                w0 * b.a.y + w1 * b.b.y + w2 * b.c.y + w3 * b.d.y,
                w0 * b.a.z + w1 * b.b.z + w2 * b.c.z + w3 * b.d.z);
        }

        private static void DumpEdge(EntityManager em, World world, Entity e, StringBuilder sb, string tag)
        {
            // 实例实体没有预制件名：得先取 PrefabRef.m_Prefab 再取名（v19 修正；之前打印的是 Entity(x:y)）
            string pfName = "(无 PrefabRef)";
            if (em.HasComponent<Game.Prefabs.PrefabRef>(e))
                pfName = SafePrefabName(world, em.GetComponentData<Game.Prefabs.PrefabRef>(e).m_Prefab);
            sb.AppendLine($"  [{tag}] 边 {e.Index}v{e.Version} 预制件「{pfName}」");
            sb.AppendLine($"    组件：{CompList(em, e)}");
            if (em.HasComponent<Edge>(e))
            {
                Edge ed = em.GetComponentData<Edge>(e);
                sb.AppendLine($"    Edge: start={ed.m_Start.Index}v{ed.m_Start.Version}  end={ed.m_End.Index}v{ed.m_End.Version}");
            }
            if (em.HasComponent<Curve>(e))
            {
                Curve c = em.GetComponentData<Curve>(e);
                sb.AppendLine($"    Curve: 长度={c.m_Length:F2} m  起点=({c.m_Bezier.a.x:F1},{c.m_Bezier.a.y:F1},{c.m_Bezier.a.z:F1})"
                              + $"  终点=({c.m_Bezier.d.x:F1},{c.m_Bezier.d.y:F1},{c.m_Bezier.d.z:F1})");
                float y0 = c.m_Bezier.a.y;
                float y25 = BezierAt(c.m_Bezier, 0.25f).y;
                float y50 = BezierAt(c.m_Bezier, 0.5f).y;
                float y75 = BezierAt(c.m_Bezier, 0.75f).y;
                float y100 = c.m_Bezier.d.y;
                float minY = Math.Min(Math.Min(y0, y25), Math.Min(y50, Math.Min(y75, y100)));
                string elev = em.HasComponent<Elevation>(e)
                    ? $"有({em.GetComponentData<Elevation>(e).m_Elevation.x:F2},{em.GetComponentData<Elevation>(e).m_Elevation.y:F2})"
                    : "无";
                sb.AppendLine($"    纵断面 y(t=0/.25/.5/.75/1)：{y0:F2} / {y25:F2} / {y50:F2} / {y75:F2} / {y100:F2}；"
                              + $"坡度 {(c.m_Length > 0.1f ? (y100 - y0) / c.m_Length * 100f : 0f):+0.00;-0.00;0.00}%；"
                              + $"Elevation组件={elev}"
                              + (s_waterLevel > 0f ? $"；最低点离水面({s_waterLevel:F1} m) = {minY - s_waterLevel:+0.00;-0.00;0.00} m" : ""));
            }
            if (em.HasComponent<Elevation>(e))
            {
                float2 el = em.GetComponentData<Elevation>(e).m_Elevation;
                sb.AppendLine($"    Elevation: ({el.x:F2},{el.y:F2})");
            }
            if (em.HasComponent<Aggregated>(e))
                sb.AppendLine($"    Aggregated.m_Aggregate={em.GetComponentData<Aggregated>(e).m_Aggregate.Index}v{em.GetComponentData<Aggregated>(e).m_Aggregate.Version}");
            if (em.HasComponent<Composition>(e))
            {
                Composition cp = em.GetComponentData<Composition>(e);
                sb.AppendLine($"    Composition: edge={cp.m_Edge.Index} start={cp.m_StartNode.Index} end={cp.m_EndNode.Index}");
            }
            sb.AppendLine($"    几何：EdgeGeometry={em.HasComponent<EdgeGeometry>(e)} StartNodeGeometry={em.HasComponent<StartNodeGeometry>(e)} EndNodeGeometry={em.HasComponent<EndNodeGeometry>(e)}");
            sb.AppendLine($"    标记：Temp={em.HasComponent<Temp>(e)} Updated={em.HasComponent<Updated>(e)} "
                          + $"Road={em.HasComponent<Road>(e)} TrainTrack={em.HasComponent<TrainTrack>(e)} TramTrack={em.HasComponent<TramTrack>(e)} "
                          + $"Owner={em.HasComponent<Game.Common.Owner>(e)} Native={em.HasComponent<Game.Common.Native>(e)}");

            if (em.HasBuffer<Game.Net.SubLane>(e))
            {
                var subs = em.GetBuffer<Game.Net.SubLane>(e);
                sb.AppendLine($"    SubLane {subs.Length} 条");
                int shown = 0;
                for (int i = 0; i < subs.Length; i++)
                {
                    Entity lane = subs[i].m_SubLane;
                    if (lane == Entity.Null || !em.Exists(lane)) { sb.AppendLine($"      lane[{i}] 失效"); continue; }
                    if (shown >= 4) { sb.AppendLine("      …（其余车道略）"); break; }
                    shown++;
                    sb.AppendLine($"      lane[{i}] {lane.Index}v{lane.Version}：{CompList(em, lane)}");
                    if (em.HasComponent<Game.Net.TrackLane>(lane))
                    {
                        Game.Net.TrackLane tl = em.GetComponentData<Game.Net.TrackLane>(lane);
                        sb.AppendLine($"        TrackLane: 限速={tl.m_SpeedLimit:F1} 曲率={tl.m_Curviness:F2} flags={tl.m_Flags}");
                    }
                    if (em.HasComponent<EdgeLane>(lane))
                    {
                        EdgeLane le = em.GetComponentData<EdgeLane>(lane);
                        sb.AppendLine($"        EdgeLane: delta=({le.m_EdgeDelta.x:F3},{le.m_EdgeDelta.y:F3}) 连起={le.m_ConnectedStartCount} 连终={le.m_ConnectedEndCount}");
                    }
                    if (em.HasComponent<NodeLane>(lane))
                    {
                        NodeLane nl = em.GetComponentData<NodeLane>(lane);
                        sb.AppendLine($"        NodeLane: 宽偏=({nl.m_WidthOffset.x:F3},{nl.m_WidthOffset.y:F3}) flags={nl.m_Flags} 共享起={nl.m_SharedStartCount} 共享终={nl.m_SharedEndCount}");
                    }
                }

                // v19：把**轨道车道**单独找出来（前面只抽样了前 4 条子车道，未必是轨道）
                int trackTotal = 0, trackShown = 0;
                for (int i = 0; i < subs.Length; i++)
                {
                    Entity lane = subs[i].m_SubLane;
                    if (lane == Entity.Null || !em.Exists(lane)) continue;
                    if (!em.HasComponent<Game.Net.TrackLane>(lane) && !em.HasComponent<Game.Net.TrainTrack>(lane)) continue;
                    trackTotal++;
                    if (trackShown >= 4) continue;
                    trackShown++;
                    sb.AppendLine($"      轨道车道[{i}] {lane.Index}v{lane.Version}：{CompList(em, lane)}");
                    if (em.HasComponent<Game.Net.TrackLane>(lane))
                    {
                        Game.Net.TrackLane tl2 = em.GetComponentData<Game.Net.TrackLane>(lane);
                        sb.AppendLine($"        TrackLane: 限速={tl2.m_SpeedLimit:F1} 曲率={tl2.m_Curviness:F2} flags={tl2.m_Flags}");
                    }
                    sb.AppendLine($"        LaneConnection={em.HasComponent<LaneConnection>(lane)}");
                }
                sb.AppendLine($"    → 该边上的轨道车道共 {trackTotal} 条（带 TrackLane 或 TrainTrack）");
            }

            if (em.HasComponent<Edge>(e))
            {
                Edge ed = em.GetComponentData<Edge>(e);
                DumpNode(em, ed.m_Start, sb, "起点节点");
                DumpNode(em, ed.m_End, sb, "终点节点");
            }
        }

        /// <summary>试建后的分类自查 + 结构 dump。清理只按我们的 marker。</summary>
        /// <summary>v22：把**实际建成的**铁路节点按线路里程排序，与数据里的期望绝对高程逐点对账。
        /// 起因（v7 实测）：抽样看 12 条边完全看不出"里程 5.5–6.85 km 整整 1.3 km 落到地面"，
        /// 汇总还写着「贴地 0 / 抬高 61；最大坡 0.00%」。这份表让整段性失败一眼可见。</summary>
        private static string RailNodeReconcile(World world, EntityManager em)
        {
            var sb = new StringBuilder();
            sb.AppendLine("-- 全量节点对账（实际建成的铁路节点 vs 数据里的期望绝对高程）--");
            if (s_lastRailPrefab != Entity.Null)
            {
                bool hasLim = em.HasComponent<NetGeometryData>(s_lastRailPrefab);
                bool hasRange = em.HasComponent<PlaceableNetData>(s_lastRailPrefab);
                float lim = hasLim ? em.GetComponentData<NetGeometryData>(s_lastRailPrefab).m_ElevationLimit : float.NaN;
                Bounds1 r = hasRange ? em.GetComponentData<PlaceableNetData>(s_lastRailPrefab).m_ElevationRange : default;
                float thr = (float.IsNaN(lim) ? 0f : lim * 2f) + (hasRange ? math.max(0f, r.min) : 0f);
                sb.AppendLine($"  铁路预制件「{s_resolvedPrefabName}」：m_ElevationLimit={(hasLim ? lim.ToString("F2") : "-")} "
                              + $"m_ElevationRange={(hasRange ? $"[{r.min:F2},{r.max:F2}]" : "-")} → 抬高门槛≈{thr:F2} m");
            }
            RailTestData d = s_lastRailData;
            if (d == null || d.segments.Count == 0) { sb.AppendLine("  （没有本轮数据，跳过）"); return sb.ToString(); }

            int ns = d.segments.Count;
            var cum = new float[ns + 1];
            for (int i = 0; i < ns; i++)
                cum[i + 1] = cum[i] + math.distance(new float3(d.segments[i][0], 0f, d.segments[i][1]),
                                                    new float3(d.segments[i][2], 0f, d.segments[i][3]));

            var seen = new HashSet<Entity>();
            var rows = new List<float[]>();     // [里程, 实测y, 期望y, 有Elevation=1/无=0, Elevation.x, 离中线, 离公路, 公路y]
            var roads = new List<Entity>();
            {
                EntityQuery rq = em.CreateEntityQuery(ComponentType.ReadOnly<Edge>(),
                                                      ComponentType.ReadOnly<Road>(), ComponentType.ReadOnly<Curve>());
                NativeArray<Entity> ra = rq.ToEntityArray(Allocator.Temp);
                try { foreach (Entity re in ra) if (!em.HasComponent<Temp>(re)) roads.Add(re); }
                finally { ra.Dispose(); }
            }
            var rails = RailEdgesOfPrefab(em, s_lastRailPrefab, false, 100000);
            foreach (Entity e in rails)
            {
                if (!em.HasComponent<Edge>(e)) continue;
                Edge ed = em.GetComponentData<Edge>(e);
                AddNodeRow(em, ed.m_Start, seen, rows, d, cum, roads);
                AddNodeRow(em, ed.m_End, seen, rows, d, cum, roads);
            }
            sb.AppendLine($"  建成铁路边 {rails.Count} 条 → 节点 {rows.Count} 个（期望节点数 ≈ {ns + 1} 起）；"
                          + $"图上公路（非 Temp，含曲线）{roads.Count} 条");
            if (rows.Count == 0) return sb.ToString();
            rows.Sort((x, y2) => x[0].CompareTo(y2[0]));

            sb.AppendLine("   里程    实测y    期望y      Δ      Elevation  离中线  离公路  公路y");
            foreach (float[] w in rows)
            {
                string el = w[3] > 0.5f ? w[4].ToString("F2") : "无(贴地)";
                bool bad = w[2] > -1e8f && math.abs(w[1] - w[2]) > 1f;
                string mark = bad ? "  ←偏" : "";
                sb.AppendLine($"  {w[0],6:F0} {w[1],8:F2} "
                              + (w[2] > -1e8f ? $"{w[2],8:F2} {w[1] - w[2],+7:F2}" : "     --     --")
                              + $"  {el,-9} {w[5],5:F1} {w[6],7:F1} {w[7],7:F1}{mark}");
            }

            int matched = 0, off = 0, atGrade = 0, noExp = 0, nearRoad = 0, gradeNearRoad = 0;
            float worstAbs = 0f, worstAt = 0f;
            var offRange = new List<float[]>();      // 连续偏差点分组 [起里程,终里程,最小Δ,最大Δ,个数]
            foreach (float[] w in rows)
            {
                if (w[3] < 0.5f) atGrade++;
                bool near = w[6] < 25f;
                if (near) nearRoad++;
                if (w[2] <= -1e8f) { noExp++; continue; }
                float dd = w[1] - w[2];
                bool bad = math.abs(dd) > 1f;
                if (near && bad) gradeNearRoad++;
                if (!bad) matched++;
                else
                {
                    off++;
                    if (math.abs(dd) > worstAbs) { worstAbs = math.abs(dd); worstAt = w[0]; }
                    if (offRange.Count > 0 && w[0] - offRange[offRange.Count - 1][1] < 200f)
                    {
                        float[] g = offRange[offRange.Count - 1];
                        g[1] = w[0];
                        g[2] = math.min(g[2], dd);
                        g[3] = math.max(g[3], dd);
                        g[4] += 1f;
                    }
                    else offRange.Add(new[] { w[0], w[0], dd, dd, 1f });
                }
            }
            sb.AppendLine($"  对账：合上（|Δ|≤1 m）{matched} 个 / 偏差 {off} 个"
                          + (noExp > 0 ? $" / 无期望 {noExp} 个" : "")
                          + $"；其中**没有 Elevation 组件（=原版当成贴地）** {atGrade} 个");
            sb.AppendLine($"  与公路的关系：离最近公路 <25 m 的节点 {nearRoad} 个，其中偏差的 {gradeNearRoad} 个"
                          + $"（>25 m 的节点里偏差 {off - gradeNearRoad} 个）");
            if (off > 0)
            {
                sb.AppendLine($"  最大偏差 {worstAbs:F2} m（里程 {worstAt:F0} m）；偏差分段：");
                foreach (float[] g in offRange)
                {
                    if (g[4] < 1f) continue;
                    sb.AppendLine($"    里程 {g[0]:F0}–{g[1]:F0} m：{g[4]:F0} 个节点，Δ {g[2]:+0.0;-0.0;0.0}…{g[3]:+0.0;-0.0;0.0} m");
                }
            }
            else sb.AppendLine("  → 逐点合上：数据里写的绝对高程就是建成的高度。");
            return sb.ToString();
        }

        /// <summary>把一个铁路节点投影到本轮线位上，产出对账行（已见过的节点跳过）。</summary>
        private static void AddNodeRow(EntityManager em, Entity n, HashSet<Entity> seen, List<float[]> rows,
            RailTestData d, float[] cum, List<Entity> roads)
        {
            if (n == Entity.Null || !em.Exists(n) || !seen.Add(n)) return;
            if (!em.HasComponent<Node>(n)) return;
            Node nd = em.GetComponentData<Node>(n);
            float px = nd.m_Position.x, pz = nd.m_Position.z;
            float best = float.MaxValue, bestChain = 0f;
            for (int i = 0; i < d.segments.Count; i++)
            {
                float x0 = d.segments[i][0], z0 = d.segments[i][1];
                float dx = d.segments[i][2] - x0, dz = d.segments[i][3] - z0;
                float l2 = dx * dx + dz * dz;
                float t = l2 > 1e-6f ? ((px - x0) * dx + (pz - z0) * dz) / l2 : 0f;
                t = math.clamp(t, 0f, 1f);
                float qx = x0 + dx * t, qz = z0 + dz * t;
                float dd = math.distance(new float2(px, pz), new float2(qx, qz));
                if (dd < best) { best = dd; bestChain = cum[i] + t * (cum[i + 1] - cum[i]); }
            }
            // 期望绝对高程：段内线性（与排队时同一口径）
            float expY = float.NaN;
            for (int i = 0; i < d.segments.Count; i++)
            {
                if (bestChain > cum[i + 1] + 1e-3f) continue;
                if (i >= d.elevations.Count) break;
                float v0 = d.elevations[i][0], v1 = d.elevations[i][1];
                if (float.IsNaN(v0) && float.IsNaN(v1)) break;
                if (float.IsNaN(v0)) v0 = v1;
                if (float.IsNaN(v1)) v1 = v0;
                float t = (cum[i + 1] - cum[i]) > 0.01f ? (bestChain - cum[i]) / (cum[i + 1] - cum[i]) : 0f;
                expY = v0 + (v1 - v0) * math.clamp(t, 0f, 1f);
                break;
            }
            bool hasEl = em.HasComponent<Elevation>(n);
            float elx = hasEl ? em.GetComponentData<Elevation>(n).m_Elevation.x : 0f;

            // 离最近公路边多远 + 那条公路在最近点的高度（v22：判定"是不是被公路拽到地面"）
            float bestRoad = float.MaxValue, bestRoadY = 0f;
            float2 p2 = new float2(px, pz);
            foreach (Entity re in roads)
            {
                if (!em.HasComponent<Curve>(re)) continue;
                Bezier4x3 rb = em.GetComponentData<Curve>(re).m_Bezier;
                for (int k = 0; k <= 6; k++)
                {
                    float3 q = BezierAt(rb, k / 6f);
                    float dd = math.distance(p2, new float2(q.x, q.z));
                    if (dd < bestRoad) { bestRoad = dd; bestRoadY = q.y; }
                }
            }
            rows.Add(new[] { bestChain, nd.m_Position.y, float.IsNaN(expY) ? -1e9f : expY, hasEl ? 1f : 0f, elx, best,
                             bestRoad == float.MaxValue ? -1f : bestRoad, bestRoadY });
        }

        public static string RailTestVerify(World world, int before, List<Entity> created)
        {
            var sb = new StringBuilder();
            EntityManager em = world.EntityManager;
            sb.AppendLine("-- 分类自查（三类分开数，清理只按 marker）--");

            int marker = CountQuery(em, ComponentType.ReadOnly<RailTestMarker>());
            int tempAll = CountTemp(em);
            int tempEdge = CountQuery(em, ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Edge>());
            int tempNode = CountQuery(em, ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Node>());
            int tempLane = CountQuery(em, ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Lane>());
            int tempAgg = CountQuery(em, ComponentType.ReadOnly<Temp>(), ComponentType.ReadOnly<Aggregate>());
            int permRail = CountEdgesOfPrefab(em, s_lastRailPrefab, false);
            int tempRail = CountEdgesOfPrefab(em, s_lastRailPrefab, true);
            int trackEdges = CountQuery(em, ComponentType.ReadOnly<TrainTrack>(), ComponentType.ReadOnly<Edge>());
            int roadEdges = CountQuery(em, ComponentType.ReadOnly<Road>(), ComponentType.ReadOnly<Edge>());

            sb.AppendLine($"① 本轮请求的定义实体（RailTestMarker）：{marker} 个");
            sb.AppendLine($"② 原版生成的临时网实体（Temp；与本轮定义实体分开数）：总 {tempAll} 个"
                          + $"（Edge {tempEdge} / Node {tempNode} / Lane {tempLane} / Aggregate {tempAgg}）");
            sb.AppendLine($"③ 永久铁路（Edge 且无 Temp 且预制件 = 运行时解析的「{s_resolvedPrefabName}」）："
                          + $"{s_beforeRailEdges} → {permRail}（Δ {permRail - s_beforeRailEdges:+0;-0;0}）；"
                          + $"同预制件仍是 Temp 的边 {tempRail} 条");
            sb.AppendLine($"   对照：全图 Edge+TrainTrack = {trackEdges} 条；Edge+Road = {roadEdges} 条");

            sb.AppendLine("-- 结构 dump（第一关的结构证据）--");
            var rails = RailEdgesOfPrefab(em, s_lastRailPrefab, false, 12);
            Entity railEdge = rails.Count > 0 ? rails[0] : Entity.Null;
            if (rails.Count > 0)
            {
                foreach (Entity e in rails) DumpEdge(em, world, e, sb, "永久铁路");
                DumpRailConnectivity(em, rails, sb);
            }
            else sb.AppendLine("  （图上没有永久铁路边）");
            Entity railTemp = FirstEdgeOfPrefab(em, s_lastRailPrefab, true);
            if (railTemp != Entity.Null) DumpEdge(em, world, railTemp, sb, "临时铁路(预览)");
            Entity roadEdge = FirstEdgeWithRoad(em);
            if (roadEdge != Entity.Null) DumpEdge(em, world, roadEdge, sb, "对照·公路");
            if (railEdge == Entity.Null && railTemp == Entity.Null)
                sb.AppendLine("  （图上没有任何本预制件的边；公路对照仍给出，用于结构差集）");

            sb.Append(RailNodeReconcile(world, em));
            DumpOutsideConnections(world, em, sb);

            DumpStationConnectivity(world, em, sb);
            int killed = DestroyMarked(em);
            sb.AppendLine($"已清理本轮定义实体 {killed} 个（只按 marker，不动任何网实体）。");

            if (permRail > s_beforeRailEdges)
                sb.AppendLine("结论：**永久铁路边已建成** —— 第一关的「短轨实际出现并成为永久实体」达成，坐标/连接/车道见上面 dump。");
            else if (tempRail > 0 || tempEdge > 0 || tempNode > 0 || tempLane > 0)
                sb.AppendLine("结论：原版**生成**了临时网实体，但没落实成永久边 → 断点在本报告上文的 applyMode 握手数据里。");
            else
                sb.AppendLine("结论：连临时网实体都没生成 → 定义实体没被 GenerateNodesSystem/GenerateEdgesSystem 消费"
                              + "（看定义实体组件、ECB 来源是否 ToolOutputBarrier、工具是否被 ToolSystem 接受）。");
            return sb.ToString();
        }

        // ---------------------------------------------------------------- 车站试放（S2 车站轮 2026-09-30，对象分支第一次实测）
        //
        // 配方出处（三条证据，缺一不可）：
        //   ① 官方对象放置代码：反编译 NetToolSystem.CreateCourseObject（archive\decompiled\tool\
        //      NetToolSystem.cs:2872-2902）——无 owner 的独立对象 = CreationDefinition{m_Prefab}
        //      + ObjectDefinition{m_ParentMesh=-1, m_Position, m_Rotation, m_Local*=同值} + Updated，
        //      **不加 OwnerDefinition、不设 CreationFlags**（网的 SubElevation 是网专用，EmitRailCourse 才用）。
        //   ② 消费侧：GenerateNodesSystem.m_DefinitionQuery = All{CreationDefinition,Updated}
        //      Any{NetCourse, ObjectDefinition}——同一查询、同一生成系统，两分支通吃。
        //   ③ 落实侧：对象版的 Temp→永久由 Game.Tools.ApplyObjectsSystem/HandleTempEntitiesJob 负责
        //      （maps\yilan\rail\il\tools_addcomponent.txt IL 实读），与 ApplyNetSystem 平行，
        //      所以 Clear→None→（等 Temp）→Apply 握手与铁路轮完全同构（StationPlaceToolSystem）。

        /// <summary>车站轮的本轮请求标记：与 RailTestMarker **分开**（S3 同会话先⑦后⑧，
        /// 两轮的计数/清理互不污染；清理只按各自的 marker，绝不按预制件清）。</summary>
        public struct StationMarker : IComponentData
        {
            public int variant;
        }

        public sealed class StationData
        {
            public int prefabIndex = -1, prefabVersion = 0;
            public string prefabName = "";
            public float water_level_m = 0f;
            /// <summary>v27：正线端点吸附到站内轨道节点的容差（米）。超出就不要硬接（避免把线拽歪）。</summary>
            public float snap_m = 3f;
            /// <summary>v28：站台轴向补偿角（度）。建筑本地 +Z 未必是站台长轴——预制件自带站内轨道曲线的
            /// 本地方向才是。数据里给这个偏移，就不必为重摆站台重编 mod（报告会算出建议值）。
            /// 依兰实测（2026-09-30）：月台轨（Double Train Track）沿 **local X**，故取 −90°。</summary>
            public float yaw_offset_deg = 0f;
            /// <summary>v28：横向偏移（米，沿建筑本地 +Z 的世界方向）。月台轨相对建筑原点有侧向距离
            /// （依兰实测 local z = −8.5 / −24 / −40），要让**某一条**月台轨正压在线路上就得平移建筑。
            /// 8.5 → 最近那条；24 → 中间；40 → 最远那条。</summary>
            public float lateral_m = 0f;
            /// <summary>v29：正线在站内是否"裁剪"（true = 跳过站内跨距并把两端挂到站内节点；
            /// false = 照旧穿过车站，只靠原版 overlap 合并）。留作回退开关，不必重编 mod。</summary>
            public bool trim_span = true;
            public string raw_head = "";

            public sealed class St
            {
                public string name = "";
                public float x, z, yaw;      // yaw：世界 +Z 起算、朝 +X 为正（右手系绕 +Y）
                public float y = float.NaN;  // NaN = 放置时现场采地形（绝对高程）
            }
            public readonly List<St> stations = new List<St>();

            public static StationData Load(string path, out string err)
            {
                err = null;
                try
                {
                    string raw = File.ReadAllText(path, Encoding.UTF8);
                    var d = new StationData();
                    d.raw_head = raw.Length > 300 ? raw.Substring(0, 300) : raw;
                    // 预制件身份按**运行时名字**解析（v16 纪律）：index/version 只当线索
                    Match mi = Regex.Match(raw, "\"prefab_index\"\\s*:\\s*(-?\\d+)");
                    Match mv = Regex.Match(raw, "\"prefab_version\"\\s*:\\s*(-?\\d+)");
                    Match mn = Regex.Match(raw, "\"prefab_name\"\\s*:\\s*\"([^\"]*)\"");
                    d.prefabIndex = mi.Success ? int.Parse(mi.Groups[1].Value, CultureInfo.InvariantCulture) : -1;
                    d.prefabVersion = mv.Success ? int.Parse(mv.Groups[1].Value, CultureInfo.InvariantCulture) : 0;
                    d.prefabName = mn.Success ? mn.Groups[1].Value : "";
                    if (d.prefabName.Length == 0)
                    {
                        err = "缺 prefab_name（v16 起只认运行时名字，不再用 index 当身份）";
                        return null;
                    }
                    Match mw = Regex.Match(raw, "\"water_level_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (mw.Success) d.water_level_m = float.Parse(mw.Groups[1].Value, CultureInfo.InvariantCulture);
                    Match ms = Regex.Match(raw, "\"snap_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (ms.Success) d.snap_m = float.Parse(ms.Groups[1].Value, CultureInfo.InvariantCulture);
                    Match myo = Regex.Match(raw, "\"yaw_offset_deg\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (myo.Success) d.yaw_offset_deg = float.Parse(myo.Groups[1].Value, CultureInfo.InvariantCulture);
                    Match mlm = Regex.Match(raw, "\"lateral_m\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                    if (mlm.Success) d.lateral_m = float.Parse(mlm.Groups[1].Value, CultureInfo.InvariantCulture);
                    Match mts = Regex.Match(raw, "\"trim_span\"\\s*:\\s*(true|false)");
                    if (mts.Success) d.trim_span = mts.Groups[1].Value == "true";
                    // stations: [["依兰站", x, z, yaw(, y)?], …]——嵌套数组用「从字段名往后扫」的
                    // 同款办法（v11 教训：不能用非贪婪 \[(.*?)\]，会在内层 ] 截断）
                    int si = raw.IndexOf("\"stations\"", StringComparison.Ordinal);
                    string tail = si >= 0 ? raw.Substring(si) : "";
                    foreach (Match m in Regex.Matches(tail,
                             "\\[\\s*\"([^\"]*)\"\\s*,\\s*(-?\\d+\\.?\\d*)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*(?:,\\s*(-?\\d+\\.?\\d*))?\\s*\\]"))
                    {
                        var st = new St();
                        st.name = m.Groups[1].Value;
                        st.x = float.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture);
                        st.z = float.Parse(m.Groups[3].Value, CultureInfo.InvariantCulture);
                        st.yaw = float.Parse(m.Groups[4].Value, CultureInfo.InvariantCulture);
                        st.y = m.Groups[5].Success
                            ? float.Parse(m.Groups[5].Value, CultureInfo.InvariantCulture)
                            : float.NaN;
                        d.stations.Add(st);
                    }
                    if (d.stations.Count == 0) { err = "stations 没解析出任何站点（格式 [\"名\",x,z,yaw(,y)?]）"; return null; }
                    return d;
                }
                catch (Exception ex) { err = ex.Message; return null; }
            }
        }

        /// <summary>预制件是不是「车站建筑预制件」：组件里出现 TransportStationData
        /// （⑤ 报告的分类口径同款；名字本身由调用方精确匹配）。</summary>
        private static bool LooksLikeStationPrefab(EntityManager em, Entity e)
        {
            NativeArray<ComponentType> cts = default;
            try
            {
                cts = em.GetComponentTypes(e, Allocator.Temp);
                for (int i = 0; i < cts.Length; i++)
                {
                    string tn;
                    try { tn = cts[i].GetManagedType().Name; } catch { continue; }
                    if (tn.Contains("TransportStationData")) return true;
                }
            }
            catch { }
            finally { if (cts.IsCreated) cts.Dispose(); }
            return false;
        }

        /// <summary>按名字解析车站建筑预制件（照 ResolveRailPrefab 的两段式：index 只当线索）。</summary>
        public static Entity ResolveStationPrefab(World world, StationData d, StringBuilder sb)
        {
            EntityManager em = world.EntityManager;

            if (d.prefabIndex >= 0)
            {
                Entity cand = new Entity { Index = d.prefabIndex, Version = d.prefabVersion };
                if (em.Exists(cand))
                {
                    string nm = SafePrefabName(world, cand);
                    bool st = LooksLikeStationPrefab(em, cand);
                    sb.AppendLine($"记录里的预制件 {d.prefabIndex}v{d.prefabVersion} 存在：名字「{nm}」车站建筑预制件={st}");
                    if (nm == d.prefabName && st)
                    {
                        sb.AppendLine("→ 与数据一致，采用它。");
                        return cand;
                    }
                    sb.AppendLine("→ 名字/类型与数据不符，不采用（index 不能当身份），改为按名字重新解析。");
                }
                else sb.AppendLine($"记录里的预制件 {d.prefabIndex}v{d.prefabVersion} 在本进程不存在 → 按名字解析。");
            }

            EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<Game.Prefabs.PrefabData>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            Entity found = Entity.Null;
            var stNames = new List<string>();
            try
            {
                foreach (Entity e in arr)
                {
                    if (!LooksLikeStationPrefab(em, e)) continue;
                    string nm = SafePrefabName(world, e);
                    stNames.Add($"{nm}（{e.Index}v{e.Version}）");
                    if (found == Entity.Null && nm == d.prefabName)
                    {
                        found = e;
                        sb.AppendLine($"按名字解析到「{nm}」= {e.Index}v{e.Version}（含 TransportStationData）");
                    }
                }
            }
            finally { arr.Dispose(); }

            if (found == Entity.Null)
            {
                sb.AppendLine($"失败：运行时没有名字为「{d.prefabName}」的车站建筑预制件。现存候选（前 16 个）：");
                for (int i = 0; i < stNames.Count && i < 16; i++) sb.AppendLine("    " + stNames[i]);
            }
            return found;
        }

        /// <summary>数「预制件 == 指定预制件」的建筑实例。withTemp=false 只数永久，true 只数 Temp 预览。</summary>
        private static int CountBuildingsOfPrefab(EntityManager em, Entity prefab, bool withTemp)
        {
            if (prefab == Entity.Null) return 0;
            EntityQuery q = em.CreateEntityQuery(
                ComponentType.ReadOnly<Game.Buildings.Building>(), ComponentType.ReadOnly<Game.Prefabs.PrefabRef>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            int n = 0;
            try
            {
                foreach (Entity e in arr)
                {
                    if (em.GetComponentData<Game.Prefabs.PrefabRef>(e).m_Prefab != prefab) continue;
                    if (em.HasComponent<Temp>(e) == withTemp) n++;
                }
            }
            finally { arr.Dispose(); }
            return n;
        }

        private static List<Entity> BuildingsOfPrefab(EntityManager em, Entity prefab, bool withTemp, int max)
        {
            var list = new List<Entity>();
            if (prefab == Entity.Null) return list;
            EntityQuery q = em.CreateEntityQuery(
                ComponentType.ReadOnly<Game.Buildings.Building>(), ComponentType.ReadOnly<Game.Prefabs.PrefabRef>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            try
            {
                foreach (Entity e in arr)
                {
                    if (em.GetComponentData<Game.Prefabs.PrefabRef>(e).m_Prefab != prefab) continue;
                    if (em.HasComponent<Temp>(e) != withTemp) continue;
                    list.Add(e);
                    if (list.Count >= max) break;
                }
            }
            finally { arr.Dispose(); }
            return list;
        }

        // ---- 工具路线（与 ⑦ 平行的一套 statics，互不污染） ----
        private static Game.Tools.ToolSystem s_stToolSystem;
        private static Game.Tools.ToolBaseSystem s_stPrevTool;
        private static bool s_stToolOwned;
        private static string s_resolvedStationName = "";
        private static Entity s_lastStationPrefab = Entity.Null;
        private static StationData s_lastStationData;
        private static int s_beforeStationBuildings;
        private static List<Entity> s_beforeStationSet;
        /// <summary>本会话由本按钮放出的车站建筑（幂等重放时只清这些，绝不全图按预制件清）。</summary>
        private static readonly List<Entity> s_placedStations = new List<Entity>();

        /// <summary>⑧ 车站试放：解析预制件 → 采 y → 激活对象工具（E2C 同构握手）。</summary>
        public static string StationTool(World world, out List<Entity> created, out int beforeBuildings)
        {
            created = new List<Entity>();
            beforeBuildings = 0;
            var sb = new StringBuilder();
            sb.AppendLine("== 车站试放（对象分支：ObjectDefinition + E2C 握手）==");
            EntityManager em = world.EntityManager;
            string path = Path.Combine(BridgeLiftData.ModsDataFolder, "rail_station.json");
            StationData d = StationData.Load(path, out string derr);
            if (d == null) return $"失败：解析 {path} 出错 → {derr}\n";
            sb.AppendLine($"数据文件：{path}（车站 {d.stations.Count} 座，预制件「{d.prefabName}”）");

            Entity prefab = ResolveStationPrefab(world, d, sb);
            if (prefab == Entity.Null) return sb.AppendLine("失败：解析不到车站建筑预制件，未做任何改动。").ToString();
            s_lastStationPrefab = prefab;
            s_resolvedStationName = d.prefabName;
            s_lastStationData = d;

            // 幂等重放：先清掉本会话上次由本按钮放出的站（横向偏移不贴合 → 改数据重点，同会话可重复）
            int removed = 0;
            for (int i = s_placedStations.Count - 1; i >= 0; i--)
            {
                Entity old = s_placedStations[i];
                if (old != Entity.Null && em.Exists(old)) { em.DestroyEntity(old); removed++; }
            }
            s_placedStations.Clear();
            if (removed > 0) sb.AppendLine($"幂等重放：已清掉本会话上次放置的车站建筑 {removed} 座（只清自己记录的实体）。");

            // y 高程：数据显式给了就用数据的，否则现场采地形（绝对值；站点在陆地，无需水面口径）
            TerrainSampler.Session terrain = TerrainSampler.Open(world, out string why);
            foreach (StationData.St st in d.stations)
            {
                if (!float.IsNaN(st.y)) { sb.AppendLine($"  站点「{st.name}」y 用数据显式值 {st.y:F2} m"); continue; }
                float h;
                if (terrain == null || !terrain.TrySample(new float3(st.x, 0f, st.z), out h))
                {
                    return sb.AppendLine($"失败：站点「{st.name}」({st.x:F1},{st.z:F1}) 地形采样不可用 → "
                                         + (why ?? TerrainSampler.Diag) + "，未做任何改动。").ToString();
                }
                st.y = h;
                sb.AppendLine($"  站点「{st.name}」({st.x:F1},{st.z:F1}) 地形高度 = {h:F2} m（建筑 y 采地形绝对值）");
            }

            s_beforeStationSet = BuildingsOfPrefab(em, prefab, false, 4096);
            s_beforeStationBuildings = s_beforeStationSet.Count;
            beforeBuildings = s_beforeStationBuildings;
            sb.AppendLine($"测试前：该预制件的永久建筑 {s_beforeStationBuildings} 座；"
                          + $"yaw 口径 = 世界 +Z 起算、朝 +X 为正。");

            try
            {
                s_stToolSystem = world.GetExistingSystemManaged<Game.Tools.ToolSystem>();
                StationPlaceToolSystem tool = world.GetOrCreateSystemManaged<StationPlaceToolSystem>();
                var ps = world.GetExistingSystemManaged<Game.Prefabs.PrefabSystem>();
                if (ps != null)
                {
                    try { tool.TrySetPrefab(ps.GetPrefab<Game.Prefabs.PrefabBase>(prefab)); }
                    catch (Exception ex) { sb.AppendLine("  （PrefabBase 取值失败，不影响定义实体里的 m_Prefab：" + ex.GetType().Name + "）"); }
                }
                if (s_stToolSystem != null)
                {
                    s_stPrevTool = s_stToolSystem.activeTool;
                    s_stToolSystem.activeTool = tool;
                    s_stToolOwned = true;
                    sb.AppendLine($"已激活工具「{tool.toolID}」（原工具 "
                                  + (s_stPrevTool == null ? "(无)" : s_stPrevTool.toolID) + "）");
                }
                else sb.AppendLine("  （ToolSystem 取不到 → 退化成裸定义实体路线）");

                StationPlaceToolSystem.Wanted = true;
                StationPlaceToolSystem.Pending = d;
                StationPlaceToolSystem.PendingPrefab = prefab;
                StationPlaceToolSystem.ResetRun();
                created = StationPlaceToolSystem.Created;
                sb.AppendLine("工具保持激活约 5 秒：applyMode 由它按 Clear→None→（等到 Temp 预览出现）→Apply 走。");
            }
            catch (Exception ex) { sb.AppendLine("异常：" + ex); }
            sb.AppendLine("随后自动还原原工具并做分类自查（只清本轮 StationMarker，不动任何建筑）。");
            return sb.ToString();
        }

        /// <summary>⑧ 的收尾：停工具、还原原工具，报告握手结果，再做分类自查。</summary>
        public static string StationToolFinish(World world, List<Entity> created, int before)
        {
            var sb = new StringBuilder();
            sb.AppendLine("-- 5 秒后自查（对象分支 / E2C 握手）--");
            sb.AppendLine($"工具 OnUpdate 帧数 = {StationPlaceToolSystem.UpdateCount}（>0 = 工具确实在跑）");
            sb.AppendLine($"握手：排队完成 = {StationPlaceToolSystem.Queued}；"
                          + $"等到 Temp 预览并切 Apply = {StationPlaceToolSystem.ApplyFrames > 0}"
                          + (StationPlaceToolSystem.ApplyUpdateIndex >= 0 ? $"（第 {StationPlaceToolSystem.ApplyUpdateIndex} 帧）" : ""));
            sb.AppendLine($"Temp 计数：排队帧 = {StationPlaceToolSystem.TempCountAtQueue} → 切 Apply 时 = {StationPlaceToolSystem.TempCountAtApply}");
            sb.AppendLine($"applyMode 末态 = {StationPlaceToolSystem.LastApplyMode}");
            if (StationPlaceToolSystem.QueueLog.Length > 0)
                sb.Append(StationPlaceToolSystem.QueueLog.ToString());
            StationPlaceToolSystem.Wanted = false;
            if (s_stToolOwned && s_stToolSystem != null)
            {
                try
                {
                    s_stToolSystem.activeTool = s_stPrevTool;
                    sb.AppendLine("已还原原工具。");
                }
                catch (Exception ex) { sb.AppendLine("还原工具失败：" + ex.GetType().Name + " " + ex.Message); }
                s_stToolOwned = false;
            }
            sb.Append(StationVerify(world, before, created));
            return sb.ToString();
        }

        /// <summary>排车站定义实体（官方配方逐字段照抄 NetToolSystem.CreateCourseObject 的无 owner 分支）。</summary>
        internal static void QueueStationObjects(EntityCommandBuffer ecb, Entity prefab,
            StationData d, StringBuilder sb, List<Entity> created)
        {
            foreach (StationData.St st in d.stations)
            {
                quaternion rot = quaternion.RotateY(math.radians(st.yaw + d.yaw_offset_deg));
                float3 pos = new float3(st.x, st.y, st.z);
                StationTransform(d, st, out pos, out rot);

                CreationDefinition cd = default;
                cd.m_Prefab = prefab;
                cd.m_SubPrefab = Entity.Null;
                cd.m_Owner = Entity.Null;
                cd.m_Original = Entity.Null;
                cd.m_Attached = Entity.Null;
                cd.m_RandomSeed = UnityEngine.Random.Range(int.MinValue, int.MaxValue);
                cd.m_Flags = 0;   // 官方对象放置不设 flags（SubElevation 是网专用，见 EmitRailCourse）

                ObjectDefinition od = default;
                od.m_ParentMesh = -1;
                od.m_Position = pos;
                od.m_Rotation = rot;
                od.m_LocalPosition = pos;   // 官方无 owner 分支：local = world 原样
                od.m_LocalRotation = rot;

                Entity e = ecb.CreateEntity();
                ecb.AddComponent(e, cd);
                ecb.AddComponent(e, od);
                ecb.AddComponent(e, default(Updated));
                ecb.AddComponent(e, new StationMarker { variant = 0 });
                created.Add(e);
                sb.AppendLine($"  车站「{st.name}」定义实体 {e.Index}v{e.Version}：位置 ({pos.x:F1},{pos.y:F2},{pos.z:F1})，"
                              + $"yaw {st.yaw:F1}°（+Z 起算），预制件「{d.prefabName}」{prefab.Index}v{prefab.Version}");
            }
        }

        /// <summary>S3+ 只读诊断：车站建筑的**站内轨道**（Game.Net.SubNet/SubLane）与正线节点的对账。
        /// 为什么需要：游戏弹「无轨道连接点」。IL 已定位判据（maps\yilan\rail\il\stationconn_warn_il.txt，
        /// `Game.Net.ConnectionWarningSystem/CheckOwnersJob::CheckNodeConnections`）：节点子车道里
        /// **带 TrackLane** 且 `EdgeLane.m_EdgeDelta` 命中该端点(0/1)的车道，若在 `nodeConnections`
        /// 里查不到 `(conn.m_TrackTypes & lanePrefab.m_TrackTypes) == lanePrefab.m_TrackTypes` 的连接，
        /// 就在该车道端点打 `TrafficConfigurationData.m_TrackConnectionNotification` 图标。
        /// 所以要把三个数打出来：①站内轨道车道的端点世界坐标 ②它的 TrackTypes（来自车道预制件的
        /// TrackLaneData）③它与正线节点/车道的最近距离与类型是否匹配。</summary>
        private static void DumpStationSubNet(World world, EntityManager em, Entity building, StringBuilder sb)
        {
            sb.AppendLine("      — 站内轨道对账（SubNet / SubLane / TrackTypes）—");
            if (!em.HasBuffer<Game.Net.SubNet>(building))
            {
                sb.AppendLine("      （建筑上没有 Game.Net.SubNet buffer → 站内无网，警告应来自别处）");
                return;
            }
            var roots = new List<Entity>();
            DynamicBuffer<Game.Net.SubNet> subNets = em.GetBuffer<Game.Net.SubNet>(building);
            int nNode = 0, nEdge = 0, nOther = 0;
            for (int i = 0; i < subNets.Length; i++)
            {
                Entity e0 = subNets[i].m_SubNet;
                if (e0 == Entity.Null || !em.Exists(e0)) continue;
                if (em.HasComponent<Edge>(e0)) nEdge++;
                else if (em.HasComponent<Node>(e0)) nNode++;
                else nOther++;
            }
            sb.AppendLine($"      SubNet 条目 {subNets.Length} 个：**Edge(真轨道边) {nEdge} / Node {nNode} / 其它 {nOther}**"
                          + (nEdge == 0 ? "  ⚠️ 一条边都没有 = 站内轨道没生成成网（正线无从连接）" : "  ✓ 站内轨道边已生成"));
            for (int i = 0; i < subNets.Length; i++)
            {
                Entity n = subNets[i].m_SubNet;
                if (n == Entity.Null || !em.Exists(n)) { sb.AppendLine($"        [{i}] 失效/空"); continue; }
                roots.Add(n);
                string pf = em.HasComponent<Game.Prefabs.PrefabRef>(n)
                    ? SafePrefabName(world, em.GetComponentData<Game.Prefabs.PrefabRef>(n).m_Prefab) : "(无预制件)";
                sb.AppendLine($"        [{i}] {n.Index}v{n.Version} 预制件「{pf}」Temp={em.HasComponent<Temp>(n)}");
                sb.AppendLine($"            组件：{CompList(em, n)}");
                if (em.HasComponent<Curve>(n))
                {
                    Curve c = em.GetComponentData<Curve>(n);
                    sb.AppendLine($"            Curve 长 {c.m_Length:F2} m  a=({c.m_Bezier.a.x:F2},{c.m_Bezier.a.y:F2},{c.m_Bezier.a.z:F2})"
                                  + $" d=({c.m_Bezier.d.x:F2},{c.m_Bezier.d.y:F2},{c.m_Bezier.d.z:F2})");
                }
                if (em.HasComponent<Edge>(n))
                {
                    Edge ee = em.GetComponentData<Edge>(n);
                    sb.AppendLine($"            Edge start={ee.m_Start.Index}v{ee.m_Start.Version} end={ee.m_End.Index}v{ee.m_End.Version}");
                    DumpNode(em, ee.m_Start, sb, "站内起节点");
                    DumpNode(em, ee.m_End, sb, "站内终节点");
                }
                DumpTrackLanes(world, em, n, sb, "            ");
            }

            sb.AppendLine("      — 正线（永久 TrainTrack 边）节点对照 —");
            var lineNodes = new List<Entity>();
            var lineNodePos = new List<float3>();
            EntityQuery rq = em.CreateEntityQuery(
                ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<TrainTrack>(),
                ComponentType.ReadOnly<Curve>(), ComponentType.ReadOnly<Game.Prefabs.PrefabRef>());
            NativeArray<Entity> rarr = rq.ToEntityArray(Allocator.Temp);
            try
            {
                foreach (Entity re in rarr)
                {
                    if (em.HasComponent<Temp>(re)) continue;
                    Edge ee = em.GetComponentData<Edge>(re);
                    for (int k = 0; k < 2; k++)
                    {
                        Entity nd = k == 0 ? ee.m_Start : ee.m_End;
                        if (nd == Entity.Null || !em.Exists(nd) || lineNodes.Contains(nd)) continue;
                        lineNodes.Add(nd);
                        float3 p = em.HasComponent<Node>(nd) ? em.GetComponentData<Node>(nd).m_Position : default;
                        lineNodePos.Add(p);
                    }
                }
            }
            finally { rarr.Dispose(); }
            sb.AppendLine($"      正线永久节点 {lineNodes.Count} 个；逐个与站内轨道端点比距离（≤3 个最近）：");

            // 站内端点：取 SubNet 边的 Curve 两端（若有）
            var stEnds = new List<float3>();
            foreach (Entity n in roots)
            {
                if (!em.HasComponent<Curve>(n)) continue;
                Curve c = em.GetComponentData<Curve>(n);
                stEnds.Add(c.m_Bezier.a);
                stEnds.Add(c.m_Bezier.d);
            }
            for (int i = 0; i < stEnds.Count; i++)
            {
                float3 p = stEnds[i];
                var order = new List<int>();
                for (int j = 0; j < lineNodes.Count; j++) order.Add(j);
                order.Sort((x, y) => math.distance(new float2(p.x, p.z), new float2(lineNodePos[x].x, lineNodePos[x].z))
                    .CompareTo(math.distance(new float2(p.x, p.z), new float2(lineNodePos[y].x, lineNodePos[y].z))));
                sb.AppendLine($"        站内端点[{i}] ({p.x:F2},{p.y:F2},{p.z:F2})：");
                for (int k = 0; k < order.Count && k < 3; k++)
                {
                    int j = order[k];
                    float d = math.distance(p, lineNodePos[j]);
                    sb.AppendLine($"           ↔ 正线节点 {lineNodes[j].Index}v{lineNodes[j].Version}"
                                  + $" 距离 {d:F2} m（含 y；平面 {math.distance(new float2(p.x, p.z), new float2(lineNodePos[j].x, lineNodePos[j].z)):F2} m）");
                }
                if (lineNodes.Count == 0) sb.AppendLine("           （正线无永久节点）");
            }
        }

        /// <summary>把一个网实体上的**轨道车道**逐条打出来（含 EdgeLane 端点、TrackTypes、Connected 计数）。</summary>
        private static void DumpTrackLanes(World world, EntityManager em, Entity net, StringBuilder sb, string indent)
        {
            if (!em.HasBuffer<Game.Net.SubLane>(net)) { sb.AppendLine(indent + "（无 SubLane buffer）"); return; }
            DynamicBuffer<Game.Net.SubLane> lanes = em.GetBuffer<Game.Net.SubLane>(net);
            int total = 0, track = 0, withEdge = 0;
            for (int i = 0; i < lanes.Length; i++)
            {
                Entity lane = lanes[i].m_SubLane;
                if (lane == Entity.Null || !em.Exists(lane)) continue;
                total++;
                if (!em.HasComponent<Game.Net.TrackLane>(lane)) continue;
                track++;
                string types = "?";
                if (em.HasComponent<Game.Prefabs.PrefabRef>(lane))
                {
                    Entity lp = em.GetComponentData<Game.Prefabs.PrefabRef>(lane).m_Prefab;
                    if (em.HasComponent<Game.Prefabs.TrackLaneData>(lp))
                        types = em.GetComponentData<Game.Prefabs.TrackLaneData>(lp).m_TrackTypes.ToString();
                }
                var tl = em.GetComponentData<Game.Net.TrackLane>(lane);
                string edge = "";
                if (em.HasComponent<EdgeLane>(lane))
                {
                    EdgeLane el = em.GetComponentData<EdgeLane>(lane);
                    withEdge++;
                    edge = $" EdgeDelta=({el.m_EdgeDelta.x:F2},{el.m_EdgeDelta.y:F2}) 连起={el.m_ConnectedStartCount} 连终={el.m_ConnectedEndCount}";
                }
                string pos = "";
                if (em.HasComponent<Curve>(lane))
                {
                    Curve c = em.GetComponentData<Curve>(lane);
                    pos = $" a=({c.m_Bezier.a.x:F2},{c.m_Bezier.a.y:F2},{c.m_Bezier.a.z:F2}) d=({c.m_Bezier.d.x:F2},{c.m_Bezier.d.y:F2},{c.m_Bezier.d.z:F2})";
                }
                sb.AppendLine($"{indent}轨道车道 {lane.Index}v{lane.Version} TrackTypes={types} Flags={tl.m_Flags}{edge}{pos}");
            }
            sb.AppendLine($"{indent}（该网子车道 {total} 条，其中轨道车道 {track} 条、带 EdgeLane 端点信息 {withEdge} 条）");
        }

        /// <summary>v28（P1，官方 `CreateSubNet` 同构）：按车站预制件自带的站内轨道数据
        /// （`Game.Prefabs.SubNet`：`m_Prefab` = 轨道网预制件、`m_Curve` = **建筑本地**曲线）发定义实体，
        /// 让原版把站台轨道真正生成出来。
        /// 为什么必须补这一步：v26 诊断实测——只发建筑的 ObjectDefinition 时，建筑的 `Game.Net.SubNet`
        /// 缓冲是 **0 条**（只有外壳、没有站内轨道），所以站台不成形、也无法与正线接线。
        /// 逐字段对照 `Game.Tools.ObjectToolBaseSystem/CreateDefinitionsJob::CreateSubNet`：
        /// `CreationDefinition{m_Prefab=子网预制件, m_SubPrefab=车站预制件}` + `NetCourse{曲线 = LocalToWorld(本地曲线)}`。
        /// 顺带报告**本地曲线方向 = 站台真实轴向**，并给出建议 `yaw_offset_deg`
        /// （世界 yaw = 本地 yaw + 建筑 yaw，故偏移 = −本地 yaw）。</summary>
        internal static void QueueStationSubNets(World world, EntityCommandBuffer ecb, EntityManager em, Entity prefab,
            StationData d, StringBuilder sb, List<Entity> created)
        {
            if (prefab == Entity.Null || d == null) return;
            if (!em.HasBuffer<Game.Prefabs.SubNet>(prefab))
            {
                sb.AppendLine("  站内轨道（v28/P1）：该预制件没有 Game.Prefabs.SubNet 缓冲 → 生成不了站台轨道；"
                              + "需改走「按 Station Side 轨道预制件手工建轨」的路子");
                return;
            }
            DynamicBuffer<Game.Prefabs.SubNet> subs = em.GetBuffer<Game.Prefabs.SubNet>(prefab);
            sb.AppendLine($"  站内轨道（v28/P1）：预制件「{d.prefabName}」带 Game.Prefabs.SubNet 条目 {subs.Length} 条");
            for (int i = 0; i < subs.Length; i++)
            {
                Game.Prefabs.SubNet sn = subs[i];
                float3 la = sn.m_Curve.a, ld = sn.m_Curve.d;
                float localYaw = math.degrees(math.atan2(ld.x - la.x, ld.z - la.z));
                float want = -localYaw;
                while (want > 180f) want -= 360f;
                while (want < -180f) want += 360f;
                string npf = sn.m_Prefab == Entity.Null ? "(空预制件)" : SafePrefabName(world, sn.m_Prefab);
                sb.AppendLine($"    [{i}] 轨道预制件「{npf}」 本地曲线 a=({la.x:F2},{la.y:F2},{la.z:F2}) "
                              + $"d=({ld.x:F2},{ld.y:F2},{ld.z:F2}) 长 {math.distance(la, ld):F2} m");
                sb.AppendLine($"        本地轴向 yaw {localYaw:F1}° → 建议 yaw_offset_deg ≈ {want:F1}°"
                              + $"（当前用 {d.yaw_offset_deg:F1}°）");
                if (sn.m_Prefab == Entity.Null) continue;

                for (int k = 0; k < d.stations.Count; k++)
                {
                    StationData.St st = d.stations[k];
                    float3 pos = new float3(st.x, st.y, st.z);
                    quaternion rot = quaternion.RotateY(math.radians(st.yaw + d.yaw_offset_deg));
                    StationTransform(d, st, out pos, out rot);
                    Bezier4x3 cur = Game.Objects.ObjectUtils.LocalToWorld(pos, rot, sn.m_Curve);
                    float3 tan = math.normalizesafe(cur.d - cur.a, new float3(0f, 0f, 1f));
                    quaternion nrot = Game.Net.NetUtils.GetNodeRotation(tan, rot);

                    NetCourse course = default;
                    course.m_StartPosition = BuildCoursePos(cur.a, 0f, 1, Entity.Null, nrot);
                    course.m_EndPosition = BuildCoursePos(cur.d, 1f, 2, Entity.Null, nrot);
                    course.m_Curve = cur;
                    course.m_Elevation = new float2(cur.a.y, cur.d.y);
                    course.m_Length = math.distance(cur.a, cur.d);
                    course.m_FixedIndex = -1;

                    CreationDefinition cd = default;
                    cd.m_Prefab = sn.m_Prefab;
                    // v32：`m_SubPrefab` **必须留空**。官方 CreateSubNet 只在"对象带 EditorContainer"时才把它
                    // 设成容器预制件（stconn2_objtoolbase_updatesubnets_il.txt:592-603）；我们的程序化对象没有
                    // EditorContainer，设了它会让生成系统按"对象的子网"处理、去找 LocalNodeCache/LocalTransformCache
                    // 等上下文——结果**只生成节点、不生成边**（v31 实测：站内 33 条全是 Node、0 条 Edge）。
                    cd.m_SubPrefab = Entity.Null;
                    cd.m_Owner = Entity.Null;
                    cd.m_Original = Entity.Null;
                    cd.m_Attached = Entity.Null;
                    cd.m_RandomSeed = UnityEngine.Random.Range(int.MinValue, int.MaxValue);
                    cd.m_Flags = 0;               // 官方在非"清除/移动"分支不设标志（stconn2_objtoolbase_updatesubnets_il.txt:573-580）

                    // v28 关键：**必须给 OwnerDefinition**——官方在 owner 非空时加它
                    // （stconn2_objtoolbase_createsubnet_il.txt:282-290、updatesubnets_il:582-591）；
                    // 有 owner 才会被 SubNetReferencesSystem 登记进建筑的 Game.Net.SubNet 缓冲
                    // （stconn2_subnetrefs_il.txt:128-160），站内轨道才算"属于车站"。
                    OwnerDefinition od = default;
                    od.m_Prefab = prefab;
                    od.m_Position = pos;
                    od.m_Rotation = rot;

                    Entity e = ecb.CreateEntity();
                    ecb.AddComponent(e, cd);
                    ecb.AddComponent(e, course);
                    ecb.AddComponent(e, od);
                    ecb.AddComponent(e, default(Updated));
                    ecb.AddComponent(e, new StationMarker { variant = 1 });
                    created.Add(e);
                    sb.AppendLine($"        → 站「{st.name}」子网定义实体 {e.Index}v{e.Version}："
                                  + $"世界 a=({cur.a.x:F1},{cur.a.y:F2},{cur.a.z:F1}) d=({cur.d.x:F1},{cur.d.y:F2},{cur.d.z:F1})");
                }
            }
        }

        private static int DestroyStationMarked(EntityManager em)
        {
            EntityQuery q = em.CreateEntityQuery(ComponentType.ReadOnly<StationMarker>());
            NativeArray<Entity> arr = q.ToEntityArray(Allocator.Temp);
            int n = 0;
            try { foreach (Entity e in arr) { em.DestroyEntity(e); n++; } }
            finally { arr.Dispose(); }
            return n;
        }

        /// <summary>⑧ 的分类自查：三类分开数（本轮 StationMarker / Temp 实体 / 永久车站建筑），
        /// 新建筑逐座 dump 期望 vs 实际（位置偏差、yaw 差、离最近轨道中线距离、高程），清理只按 marker。</summary>
        public static string StationVerify(World world, int before, List<Entity> created)
        {
            var sb = new StringBuilder();
            EntityManager em = world.EntityManager;
            sb.AppendLine("-- 分类自查（对象分支；三类分开数，清理只按本轮 StationMarker）--");

            int marker = CountQuery(em, ComponentType.ReadOnly<StationMarker>());
            int tempAll = CountTemp(em);
            int tempBuilding = CountQuery(em, ComponentType.ReadOnly<Temp>(),
                ComponentType.ReadOnly<Game.Buildings.Building>());
            int permStations = CountBuildingsOfPrefab(em, s_lastStationPrefab, false);
            int tempStations = CountBuildingsOfPrefab(em, s_lastStationPrefab, true);

            sb.AppendLine($"① 本轮请求的定义实体（StationMarker）：{marker} 个");
            sb.AppendLine($"② 原版临时实体（Temp，与①分开数）：总 {tempAll} 个，其中 Temp+Building {tempBuilding} 个");
            sb.AppendLine($"③ 永久车站建筑（Building 且无 Temp 且预制件=运行时解析的「{s_resolvedStationName}」）："
                          + $"{s_beforeStationBuildings} → {permStations}（Δ {permStations - s_beforeStationBuildings:+0;-0;0}）；"
                          + $"同预制件仍是 Temp 的建筑 {tempStations} 座");

            // 新建筑逐座 dump：期望 vs 实际 + 离最近轨道中线距离（S3 的核心观察项）
            var nowSet = BuildingsOfPrefab(em, s_lastStationPrefab, false, 4096);
            var fresh = new List<Entity>();
            foreach (Entity e in nowSet)
                if (s_beforeStationSet == null || !s_beforeStationSet.Contains(e)) fresh.Add(e);
            if (fresh.Count == 0) sb.AppendLine("  （没有新增的永久车站建筑）");

            int ki = 0;
            foreach (Entity b in fresh)
            {
                ki++;
                sb.AppendLine($"  [{ki}] 建筑实体 {b.Index}v{b.Version}：{CompList(em, b)}");
                DumpStationSubNet(world, em, b, sb);
                bool hasT = em.HasComponent<Game.Objects.Transform>(b);
                float3 p = hasT ? em.GetComponentData<Game.Objects.Transform>(b).m_Position : default;
                float yawActual = float.NaN;
                if (hasT)
                {
                    float3 fwd = math.rotate(em.GetComponentData<Game.Objects.Transform>(b).m_Rotation, new float3(0f, 0f, 1f));
                    yawActual = math.degrees(math.atan2(fwd.x, fwd.z));
                }

                StationData.St want = null; float bestD = float.MaxValue;
                if (s_lastStationData != null)
                    foreach (StationData.St st in s_lastStationData.stations)
                    {
                        float dd = math.distance(new float2(st.x, st.z), new float2(p.x, p.z));
                        if (dd < bestD) { bestD = dd; want = st; }
                    }
                if (want != null)
                {
                    float3 wpos; quaternion wrot;
                    StationTransform(s_lastStationData, want, out wpos, out wrot);
                    float wantYaw = want.yaw + (s_lastStationData != null ? s_lastStationData.yaw_offset_deg : 0f);
                    float dx = p.x - wpos.x, dz = p.z - wpos.z;
                    sb.AppendLine($"      期望「{want.name}」({wpos.x:F1},{wpos.z:F1}) yaw {wantYaw:F1}°，y(采地形/显式) {want.y:F2}"
                                  + $"（数据 x/z {want.x:F1},{want.z:F1} + yaw 偏移 {want.yaw:F1}°"
                                  + (s_lastStationData != null && math.abs(s_lastStationData.lateral_m) > 0.01f
                                     ? $"，横向平移 {s_lastStationData.lateral_m:F1} m" : "") + "）");
                    sb.AppendLine($"      实际位置=({p.x:F1},{p.y:F2},{p.z:F1})；平面偏差 |Δ|={math.length(new float2(dx, dz)):F2} m"
                                  + $"（dx {dx:+0.0;-0.0}，dz {dz:+0.0;-0.0}）");
                    if (!float.IsNaN(yawActual))
                    {
                        float dyaw = yawActual - wantYaw;
                        while (dyaw > 180f) dyaw -= 360f;
                        while (dyaw < -180f) dyaw += 360f;
                        sb.AppendLine($"      实际 yaw={yawActual:F1}°（期望 {wantYaw:F1}°，差 {dyaw:+0.0;-0.0}°；差≈180° = 背向）");
                    }
                    TerrainSampler.Session ts = TerrainSampler.Open(world, out _);
                    string wlev = s_lastStationData != null && s_lastStationData.water_level_m > 0f
                        ? $"，水面 {s_lastStationData.water_level_m:F1} m" : "";
                    if (ts != null && ts.TrySample(new float3(p.x, 0f, p.z), out float th))
                        sb.AppendLine($"      高程：建筑 y={p.y:F2} m，地形 {th:F2} m（差 {p.y - th:+0.00;-0.00} m）{wlev}");
                    else
                        sb.AppendLine($"      高程：建筑 y={p.y:F2} m（地形采样失败）{wlev}");
                }

                // 离最近**永久铁路边**中线的平面距离（S3 铺轨后此项是"站台贴轨"的直接证据）
                float bestRail = float.MaxValue; float3 bestPt = default;
                EntityQuery rq = em.CreateEntityQuery(
                    ComponentType.ReadOnly<Edge>(), ComponentType.ReadOnly<TrainTrack>(),
                    ComponentType.ReadOnly<Curve>(), ComponentType.ReadOnly<Game.Prefabs.PrefabRef>());
                NativeArray<Entity> rarr = rq.ToEntityArray(Allocator.Temp);
                try
                {
                    foreach (Entity re in rarr)
                    {
                        if (em.HasComponent<Temp>(re)) continue;
                        Bezier4x3 cb = em.GetComponentData<Curve>(re).m_Bezier;
                        for (int k = 0; k <= 8; k++)
                        {
                            float3 q = BezierAt(cb, k / 8f);
                            float dd = math.distance(new float2(q.x, q.z), new float2(p.x, p.z));
                            if (dd < bestRail) { bestRail = dd; bestPt = q; }
                        }
                    }
                }
                finally { rarr.Dispose(); }
                sb.AppendLine(bestRail < float.MaxValue
                    ? $"      离最近轨道中线 {bestRail:F1} m（该处轨面高程 {bestPt.y:F2} m；站台是否贴轨以机主目检为准）"
                    : "      图上没有永久铁路边（S3 应先点⑦铺轨，再点⑧放站）");
                s_placedStations.Add(b);
            }

            int killed = DestroyStationMarked(em);
            sb.AppendLine($"已清理本轮定义实体 {killed} 个（只按 StationMarker；车站建筑本身保留）。");

            if (permStations > s_beforeStationBuildings)
                sb.AppendLine("结论：**永久车站建筑已生成** —— 位置/朝向/离轨距离见上；画面是否正确待机主目检（六层证据 4≠5）。");
            else if (tempStations > 0 || tempBuilding > 0)
                sb.AppendLine("结论：原版生成了**临时**对象实体，但没落实成永久建筑 → 断点在 applyMode 握手 / ApplyObjectsSystem 侧。");
            else
                sb.AppendLine("结论：连临时对象都没生成 → 定义实体没被 GenerateNodesSystem 对象分支消费"
                              + "（看组件组合 / ObjectDefinition 字段 / ECB 是不是 ToolOutputBarrier）。");
            return sb.ToString();
        }

        // ---------------------------------------------------------------- 单路试点（街名轮）

        /// <summary>EnsureRefreshTag：缺才加，幂等（照抄 advanced-road-naming@559e72c 的同名模式，
        /// 旧区研究库 guide-integration/how-to/recipes/aggregate-topology-mutation.md，source-verified）。</summary>
        private static void EnsureRefreshTag<T>(EntityManager em, Entity e) where T : struct, IComponentData
        {
            if (!em.HasComponent<T>(e)) em.AddComponent<T>(e);
        }

        /// <summary>只处理一条路（targetName）：清子边旧名 → 写聚合实体名 → 手工补刷新标记。
        /// 做法照抄成熟命名 mod advanced-road-naming（全部 source-verified）：
        /// ① 标签挂在聚合实体上（Game.Net.Aggregate+LabelMaterial，AggregateMeshSystem 调
        ///    GetRenderedLabelName 绘制，取 TryGetCustomName(聚合)）——名字必须写聚合实体；
        /// ② 写名前先 SetCustomName(edge, null) 清掉子边自定义名（"让标签只活在一个所有者"；
        ///    base9 残留的 1000 个边名是干扰源）；该调用对无名的边是幂等空操作；
        /// ③ 聚合写名后必须手工补 Updated+BatchesUpdated（SetCustomName 的 ECB 只加
        ///    CustomName+BatchesUpdated，缺 Updated 标签重建不触发）——v5 就栽在这；
        /// ④ 边上只允许 BatchesUpdated（SetCustomName(null) 的 ECB 自带），绝不能加 Updated，
        ///    否则 vanilla AggregateSystem 重新合并聚合归属。</summary>
        public static string SetSingleName(World world, StreetNames d, string targetName)
        {
            var sb = new StringBuilder();
            sb.AppendLine($"== 单路试点：\"{targetName}\" ==");
            EntityManager em = world.EntityManager;
            Game.UI.NameSystem nameSys = world.GetExistingSystemManaged<Game.UI.NameSystem>();
            if (nameSys == null) { return "失败：Game.UI.NameSystem 不存在\n"; }
            // 与 v3 相同：SetCustomName 内部经 NameSystem 持有的 Game.EndFrameBarrier
            //（OnCreate IL = World.GetOrCreateSystemManaged，即 world 系统实例）创建 ECB，
            // 相位外调用须先 AllowUsage，否则抛 "EntityCommandBuffer not allowed"。
            var bf = typeof(Game.UI.NameSystem).GetField("m_EndFrameBarrier",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            if (bf == null) return "失败：NameSystem.m_EndFrameBarrier 字段不存在\n";
            object barrier = bf.GetValue(nameSys);
            var allow = barrier.GetType().GetMethod("AllowUsage");
            if (allow == null) return "失败：barrier.AllowUsage 方法不存在\n";
            allow.Invoke(barrier, null);

            const float cell = 32f;
            var cells = new Dictionary<long, List<int>>();
            int pts = 0;
            for (int i = 0; i < d.Count; i++)
            {
                if (d.names[i] != targetName) continue;
                pts++;
                long k = ((long)(int)Math.Floor(d.xs[i] / cell) << 32) ^ (uint)(int)Math.Floor(d.zs[i] / cell);
                if (!cells.TryGetValue(k, out var list)) { list = new List<int>(4); cells[k] = list; }
                list.Add(i);
            }
            if (pts == 0) { sb.AppendLine($"数据文件里没有名为 \"{targetName}\" 的点"); return sb.ToString(); }
            sb.AppendLine($"数据点 {pts} 个（半径 {d.radius:F2} m）");

            float r2 = d.radius * d.radius;
            EntityQuery qe = QueryEdges(em);
            NativeArray<Entity> edges = qe.ToEntityArray(Allocator.Temp);

            var hitEdges = new List<Entity>();
            var aggs = new List<Entity>();
            int matched = 0;
            sb.AppendLine("-- 改名前采样 --");
            foreach (Entity e in edges)
            {
                if (em.HasComponent<Temp>(e)) continue;
                if (!em.HasComponent<Road>(e)) continue;
                Bezier4x3 bz = em.GetComponentData<Curve>(e).m_Bezier;
                float mx = (bz.a.x + bz.d.x) * 0.5f, mz = (bz.a.z + bz.d.z) * 0.5f;
                int best = -1; float bestD = r2;
                int c0 = (int)Math.Floor((mx - d.radius) / cell), c1 = (int)Math.Floor((mx + d.radius) / cell);
                int r0 = (int)Math.Floor((mz - d.radius) / cell), r1 = (int)Math.Floor((mz + d.radius) / cell);
                for (int cx = c0; cx <= c1; cx++)
                {
                    for (int cz = r0; cz <= r1; cz++)
                    {
                        if (!cells.TryGetValue(((long)cx << 32) ^ (uint)cz, out var list)) continue;
                        for (int n = 0; n < list.Count; n++)
                        {
                            int i2 = list[n];
                            float dx = mx - d.xs[i2], dz = mz - d.zs[i2];
                            float dd = dx * dx + dz * dz;
                            if (dd <= bestD) { bestD = dd; best = i2; }
                        }
                    }
                }
                if (best < 0) continue;
                matched++;
                hitEdges.Add(e);
                Entity agg = em.HasComponent<Aggregated>(e)
                    ? em.GetComponentData<Aggregated>(e).m_Aggregate : Entity.Null;
                if (agg != Entity.Null && !aggs.Contains(agg)) aggs.Add(agg);
                string en = nameSys.TryGetCustomName(e, out string cn) ? cn : "(无)";
                sb.AppendLine($"  边 {e.Index}v{e.Version} 中点=({mx:F0},{mz:F0}) 自定义名={en}"
                              + " 聚合=" + (agg == Entity.Null ? "(无 Aggregated)" : $"{agg.Index}v{agg.Version}"));
            }
            edges.Dispose();
            sb.AppendLine($"匹配路段 {matched}，去重聚合道路 {aggs.Count} 条");

            foreach (Entity a in aggs)
            {
                string an = nameSys.TryGetCustomName(a, out string cn) ? cn : "(无)";
                string rn;
                try { rn = nameSys.GetRenderedLabelName(a); }
                catch (Exception ex) { rn = "(取渲染名失败 " + ex.GetType().Name + ")"; }
                sb.AppendLine($"  聚合 {a.Index}v{a.Version} 自定义名={an} 标签渲染名=\"{rn}\""
                              + $" Updated={em.HasComponent<Updated>(a)} BatchesUpdated={em.HasComponent<BatchesUpdated>(a)}");
            }
            if (aggs.Count == 0)
            {
                sb.AppendLine("没有可写名的聚合实体（匹配路段都缺 Game.Net.Aggregated？）");
                return sb.ToString();
            }
            if (aggs.Count > 1)
                sb.AppendLine($"该名下命中 {aggs.Count} 条聚合道路：长街被路口拆成多段聚合属正常，全部写同名");

            // ① 清子边旧名——照抄正式版 advanced-road-naming 的 SetAuthoritativeName：
            //    遍历聚合自己的 AggregateElement 成员表（全部成员边），不是只清配对到的边；
            //    SetCustomName(null) = 字典 Remove + ECB Remove<CustomName>+Add<BatchesUpdated>，幂等
            int cleared = 0;
            foreach (Entity a in aggs)
            {
                if (!em.HasBuffer<AggregateElement>(a)) continue;
                var buf = em.GetBuffer<AggregateElement>(a, true);
                for (int i = 0; i < buf.Length; i++)
                {
                    Entity e2 = buf[i].m_Edge;
                    if (e2 == Entity.Null || !em.Exists(e2)) continue;
                    if (nameSys.TryGetCustomName(e2, out _)) { nameSys.SetCustomName(e2, null); cleared++; }
                }
            }
            sb.AppendLine($"清子边旧名 {cleared}（按 {aggs.Count} 条聚合的完整成员表）");

            // ② 写聚合实体名
            foreach (Entity a in aggs) nameSys.SetCustomName(a, targetName);

            // ③ 手工补刷新标记（v5 缺的关键一步）
            foreach (Entity a in aggs)
            {
                EnsureRefreshTag<Updated>(em, a);
                EnsureRefreshTag<BatchesUpdated>(em, a);
            }

            // ④ 改名后回读
            sb.AppendLine("-- 改名后回读 --");
            int renamed = 0;
            foreach (Entity a in aggs)
            {
                bool has = nameSys.TryGetCustomName(a, out string cn);
                string rn;
                try { rn = nameSys.GetRenderedLabelName(a); }
                catch (Exception ex) { rn = "(取渲染名失败 " + ex.GetType().Name + ")"; }
                if (has && cn == targetName) renamed++;
                sb.AppendLine($"  聚合 {a.Index}v{a.Version} 自定义名={(has ? cn : "(无)")} 标签渲染名=\"{rn}\""
                              + $" Updated={em.HasComponent<Updated>(a)} BatchesUpdated={em.HasComponent<BatchesUpdated>(a)}");
            }
            sb.AppendLine($"完成：目标聚合 {aggs.Count} 条，实际写名 {renamed}，清边名 {cleared}。");
            return sb.ToString();
        }

        // ---------------------------------------------------------------- 还原

        public static string Restore(World world)
        {
            var sb = new StringBuilder();
            EntityManager em = world.EntityManager;
            BackupFile bf = BackupFile.Load(out string err);
            if (bf == null) { sb.AppendLine("还原失败：" + err); return sb.ToString(); }

            var map = new Dictionary<int, BackupEntry>(bf.nodes.Count);
            var posMap = new Dictionary<long, BackupEntry>(bf.nodes.Count);
            foreach (BackupEntry b in bf.nodes)
            {
                map[b.i] = b;
                posMap[PosKey(b.x, b.z)] = b;
            }

            var restY = new Dictionary<Entity, float>();
            var restR = new Dictionary<Entity, float>();
            int n = 0, skipped = 0, byPos = 0;

            NodeScan ns = OpenNodes(em);
            var targets = new List<BackupEntry>();
            var ents = new List<Entity>();
            foreach (Entity e in ns.Entities)
            {
                if (!EligibleNode(em, ns, e)) continue;
                Node nd = em.GetComponentData<Node>(e);

                // 先按 Entity.Index 找；跨存档索引可能变，再用位置兜底
                BackupEntry b;
                if (!map.TryGetValue(e.Index, out b))
                {
                    int cx = (int)Math.Round(nd.m_Position.x), cz = (int)Math.Round(nd.m_Position.z);
                    float bestD = 1.5f;
                    for (int dx = -1; dx <= 1; dx++)
                    {
                        for (int dz = -1; dz <= 1; dz++)
                        {
                            if (!posMap.TryGetValue(PosKey(cx + dx, cz + dz), out BackupEntry cand)) continue;
                            float d = math.distance(new float2(nd.m_Position.x, nd.m_Position.z),
                                                    new float2(cand.x, cand.z));
                            if (d < bestD) { bestD = d; b = cand; }
                        }
                    }
                    if (b == null) continue;
                    byPos++;
                }
                else if (math.abs(nd.m_Position.x - b.x) > 1.5f || math.abs(nd.m_Position.z - b.z) > 1.5f)
                {
                    skipped++;
                    continue;
                }

                targets.Add(b);
                ents.Add(e);
                restY[e] = b.y;
                restR[e] = b.hasElev ? math.cmin(new float2(b.ex, b.ey)) : 0f;
                n++;
            }
            ns.Entities.Dispose();

            var ecb = new EntityCommandBuffer(Allocator.TempJob);
            for (int k = 0; k < targets.Count; k++)
            {
                Entity e = ents[k];
                BackupEntry b = targets[k];
                Node nd = em.GetComponentData<Node>(e);
                nd.m_Position.y = b.y;
                ecb.SetComponent(e, nd);
                if (b.hasElev)
                    ApplyElevation(em, ecb, e, new float2(b.ex, b.ey));
                else if (em.HasComponent<Game.Net.Elevation>(e))
                    ecb.RemoveComponent<Game.Net.Elevation>(e);
                ApplyUpdated(em, ecb, e);
            }
            ecb.Playback(em);
            ecb.Dispose();

            var ecb2 = new EntityCommandBuffer(Allocator.TempJob);
            int touched = 0;
            EntityQuery qe = QueryEdges(em);
            NativeArray<Entity> edges = qe.ToEntityArray(Allocator.Temp);
            int edgeRoadCapable = 0;
            foreach (Entity e in edges)
                if (!em.HasComponent<Temp>(e) && em.HasComponent<Road>(e)) edgeRoadCapable++;
            bool edgeRoadFilter = edgeRoadCapable > 0;

            foreach (Entity e in edges)
            {
                if (em.HasComponent<Temp>(e)) continue;
                if (edgeRoadFilter && !em.HasComponent<Road>(e)) continue;
                Edge edge = em.GetComponentData<Edge>(e);
                bool cs = restY.TryGetValue(edge.m_Start, out float y0);
                bool ce = restY.TryGetValue(edge.m_End, out float y1);
                if (!cs && !ce) continue;
                if (!cs) y0 = em.GetComponentData<Node>(edge.m_Start).m_Position.y;
                if (!ce) y1 = em.GetComponentData<Node>(edge.m_End).m_Position.y;
                float r0 = cs ? restR[edge.m_Start] : 0f;
                float r1 = ce ? restR[edge.m_End] : 0f;

                Curve curve = em.GetComponentData<Curve>(e);
                Bezier4x3 bz = curve.m_Bezier;
                Bezier4x3 nb = new Bezier4x3(
                    new float3(bz.a.x, y0, bz.a.z),
                    new float3(bz.b.x, y0 + (y1 - y0) / 3f, bz.b.z),
                    new float3(bz.c.x, y0 + (y1 - y0) * 2f / 3f, bz.c.z),
                    new float3(bz.d.x, y1, bz.d.z));
                curve.m_Bezier = nb;
                curve.m_Length = MathUtils.Length(nb);
                ecb2.SetComponent(e, curve);
                if (r0 == 0f && r1 == 0f)
                {
                    if (em.HasComponent<Game.Net.Elevation>(e)) ecb2.RemoveComponent<Game.Net.Elevation>(e);
                }
                else ApplyElevation(em, ecb2, e, new float2(r0, r1));
                ApplyUpdated(em, ecb2, e);
                touched++;
            }
            edges.Dispose();
            ecb2.Playback(em);
            ecb2.Dispose();

            sb.AppendLine($"还原完成：恢复 {n} 个节点（按位置兜底匹配 {byPos} 个，位置对不上跳过 {skipped} 个），"
                          + $"联动边 {touched} 条");
            sb.AppendLine($"备份时间 {bf.stamp}，来源数据 {bf.data_file}");
            return sb.ToString();
        }
    }
}
