// HarbinBridgeLift —— 真正执行的系统
//
// 为什么要有这个系统：v1 在 UI 按钮回调里直接对 ECS 做结构变更（AddComponent），
// 结果原生崩溃（Player.log 的栈：EntityManager.AddComponent ← BridgeLifter.Lift）。
// 现在按钮只置一个标志，实际的读写在 ModificationEnd 相位里做 —— 这是 CS2 mod
// 改路网的标准位置，也是 Earth2Cities 自己注册导路系统用的那个相位。

using System;
using System.Collections.Generic;
using UnityEngine.Scripting;
using Game;
using Unity.Entities;

namespace HarbinBridgeLift
{
    public enum LiftRequest
    {
        None = 0,
        Scan = 1,
        Lift = 2,
        Restore = 3,
        Names = 4,
        NameSingle = 5,
        RailPrefabs = 6,
        RailTest = 7,
        RailTestTool = 8,
        Station = 9,
        StationRoad = 10,
    }

    public partial class BridgeLiftSystem : GameSystemBase
    {
        /// <summary>按钮回调只写这里，OnUpdate 取走并清空。</summary>
        public static volatile int Pending = (int)LiftRequest.None;

        // 铁路单段试建的自查状态（约 5 秒后回读边数，并清理没被消费的定义实体）
        // v31：**⑧ 车站与 ⑦ 铁路各有独立定时器**——共用一个时，机主点得快（<5 s）会把前一个的自查挤掉
        //（2026-09-30 实测：⑧报告只剩初始段、站内轨道对账整段缺失）。
        private static int _railFollowLeft;
        private static int _railBeforeEdges;
        private static List<Entity> _railCreated;
        private static string _railReport;
        private static int _railFollowMode = 7;

        private static int _stFollowLeft;
        private static int _stBeforeEdges;
        private static List<Entity> _stCreated;
        private static string _stReport;

        // v33：⑨ 站前路也独立一个定时器
        private static int _rdFollowLeft;
        private static int _rdBeforeEdges;
        private static List<Entity> _rdCreated;
        private static string _rdReport;

        /// <summary>配对半径 = max(面板滑条, 数据文件声明)。
        /// 取 max 的理由：数据侧知道自己这张图游戏节点的实际偏移（依兰 2026-09-29 实测
        /// 0.4–2.4 m，2.5 m 半径时 12 点只配上 7 个 → 桥面中段与南岸引道漏抬）；
        /// 滑条保留机主手动覆盖权（调更大以滑条为准）。哈尔滨数据声明 2.5 = 行为不变。</summary>
        private static float Radius(Setting st, BridgeLiftData data)
            => Math.Max(st != null ? st.MatchRadius : 0f, data.match_radius);

        [Preserve]
        protected override void OnCreate()
        {
            base.OnCreate();
            Mod.log.Info("[HarbinBridgeLift] BridgeLiftSystem ready");
        }

        [Preserve]
        protected override void OnUpdate()
        {
            int req = Pending;
            if (req != (int)LiftRequest.None)
            {
            Pending = (int)LiftRequest.None;

            string report;
            try
            {
                World world = World;
                Setting st = Mod.Settings;

                if (world == null || !world.IsCreated)
                {
                    report = "失败：World 不可用\n";
                }
                else if (req == (int)LiftRequest.Restore)
                {
                    report = "== 还原 ==\n" + BridgeLifter.Restore(world);
                }
                else if (req == (int)LiftRequest.Names)
                {
                    string np = StreetNames.DefaultPath;
                    StreetNames sn = StreetNames.Load(np, out string serr);
                    report = sn == null
                        ? $"失败：解析街名数据 {np} 出错 → {serr}\n"
                        : "== 批量街名 ==\n数据文件：" + np + "\n" + BridgeLifter.SetNames(world, sn);
                }
                else if (req == (int)LiftRequest.RailPrefabs)
                {
                    report = BridgeLifter.ListRailPrefabs(world);
                }
                else if (req == (int)LiftRequest.RailTest)
                {
                    report = BridgeLifter.RailTest(world, out List<Entity> created, out int beforeEdges);
                    _railCreated = created;
                    _railBeforeEdges = beforeEdges;
                    _railFollowMode = 7;
                }
                else if (req == (int)LiftRequest.RailTestTool)
                {
                    report = BridgeLifter.RailTestTool(world, out List<Entity> created, out int beforeEdges);
                    _railCreated = created;
                    _railBeforeEdges = beforeEdges;
                    _railFollowMode = 8;
                }
                else if (req == (int)LiftRequest.Station)
                {
                    report = BridgeLifter.StationTool(world, out List<Entity> created, out int beforeEdges);
                    _stCreated = created;
                    _stBeforeEdges = beforeEdges;
                }
                else if (req == (int)LiftRequest.StationRoad)
                {
                    report = BridgeLifter.StationRoadTool(world, out List<Entity> created, out int beforeEdges);
                    _rdCreated = created;
                    _rdBeforeEdges = beforeEdges;
                }
                else if (req == (int)LiftRequest.NameSingle)
                {
                    string target = st != null && !string.IsNullOrEmpty(st.DiagRoadName) ? st.DiagRoadName : "";
                    if (target.Length == 0)
                    {
                        report = "失败：单路试点路名为空（设置面板 DiagRoadName 里填，如 中央大街）\n";
                    }
                    else
                    {
                        string np = StreetNames.DefaultPath;
                        StreetNames sn = StreetNames.Load(np, out string serr);
                        report = sn == null
                            ? $"失败：解析街名数据 {np} 出错 → {serr}\n"
                            : BridgeLifter.SetSingleName(world, sn, target);
                    }
                }
                else
                {
                    string path = BridgeLiftData.Resolve(st != null ? st.DataFilePath : "", out var tried);
                    if (path == null)
                    {
                        var sb = new System.Text.StringBuilder();
                        sb.AppendLine("失败：找不到高度场数据文件。试过这些路径：");
                        foreach (string t in tried) sb.AppendLine("  " + t);
                        report = sb.ToString();
                    }
                    else
                    {
                        BridgeLiftData data = BridgeLiftData.Load(path, out string err);
                        if (data == null)
                        {
                            report = $"失败：解析 {path} 出错 → {err}\n";
                        }
                        else if (req == (int)LiftRequest.Scan)
                        {
                            report = "== 扫描（只读，不改任何东西）==\n数据文件：" + path + "\n"
                                     + BridgeLifter.Scan(world, data, Radius(st, data));
                        }
                        else
                        {
                            float scale = st != null ? st.ClearanceScale : 1f;
                            float radius = Radius(st, data);
                            report = $"== 抬升 ==\n数据文件：{path}\n"
                                     + $"净空倍率 {scale:F2}，配对半径 {radius:F2} m\n"
                                     + BridgeLifter.Lift(world, data, scale, radius);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                report = "异常：\n" + ex;
                Mod.log.Error("HarbinBridgeLift run failed: " + ex);
            }

            Setting.WriteReport(report, req);
            Setting st2 = Mod.Settings;
            if (st2 != null) st2.SetStatusFromReport(report);
            if (req == (int)LiftRequest.RailTest || req == (int)LiftRequest.RailTestTool)
            {
                if (_railCreated != null)
                {
                    _railReport = report;
                    // v22：全线切成短课程后课程数从 61 涨到 ~225，留足生成时间（原 120 帧≈2 秒）
                    _railFollowLeft = 300;      // 约 5 秒
                }
            }
            if (req == (int)LiftRequest.Station && _stCreated != null)
            {
                _stReport = report;
                _stFollowLeft = 300;            // 约 5 秒（与 ⑦ 独立，互不挤占）
            }
            if (req == (int)LiftRequest.StationRoad && _rdCreated != null)
            {
                _rdReport = report;
                _rdFollowLeft = 300;            // 约 5 秒（独立定时器）
            }
            }   // end if (req != None)

            // 铁路自查（⑦ 工具路线 / ⑥ 对照组）——与 ⑧ 独立定时器
            if (_railFollowLeft > 0)
            {
                _railFollowLeft--;
                if (_railFollowLeft == 0 && _railCreated != null)
                {
                    string v;
                    try
                    {
                        v = _railFollowMode == 8
                            ? BridgeLifter.RailTestToolFinish(World, _railCreated, _railBeforeEdges)
                            : BridgeLifter.RailTestVerify(World, _railBeforeEdges, _railCreated);
                    }
                    catch (Exception ex) { v = "自查异常：" + ex; }
                    string full = (_railReport ?? "") + "\n" + v;
                    Setting.WriteReport(full, _railFollowMode);
                    Setting st3 = Mod.Settings;
                    if (st3 != null) st3.SetStatusFromReport(v);
                    Mod.log.Info("[HarbinBridgeLift] rail test verify:\n" + v);
                    _railCreated = null;
                }
            }

            // 车站自查（⑧）——独立定时器，点得快也不会被 ⑦ 挤掉
            if (_stFollowLeft > 0)
            {
                _stFollowLeft--;
                if (_stFollowLeft == 0 && _stCreated != null)
                {
                    string v;
                    try { v = BridgeLifter.StationToolFinish(World, _stCreated, _stBeforeEdges); }
                    catch (Exception ex) { v = "自查异常：" + ex; }
                    string full = (_stReport ?? "") + "\n" + v;
                    Setting.WriteReport(full, 9);
                    Setting st4 = Mod.Settings;
                    if (st4 != null) st4.SetStatusFromReport(v);
                    Mod.log.Info("[HarbinBridgeLift] station verify:\n" + v);
                    _stCreated = null;
                }
            }

            // ⑨ 站前路自查——独立定时器
            if (_rdFollowLeft > 0)
            {
                _rdFollowLeft--;
                if (_rdFollowLeft == 0 && _rdCreated != null)
                {
                    string v;
                    try { v = BridgeLifter.StationRoadFinish(World, _rdCreated, _rdBeforeEdges); }
                    catch (Exception ex) { v = "自查异常：" + ex; }
                    string full = (_rdReport ?? "") + "\n" + v;
                    Setting.WriteReport(full, 10);
                    Setting st5 = Mod.Settings;
                    if (st5 != null) st5.SetStatusFromReport(v);
                    Mod.log.Info("[HarbinBridgeLift] station road verify:\n" + v);
                    _rdCreated = null;
                }
            }
        }
    }
}
