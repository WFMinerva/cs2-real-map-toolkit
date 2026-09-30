// HarbinBridgeLift —— 设置面板（三个按钮就是全部交互）

using System;
using System.IO;
using Colossal.IO.AssetDatabase;
using Game;
using Game.Modding;
using Game.Settings;
using Game.UI.Widgets;
using Unity.Entities;

namespace HarbinBridgeLift
{
    [FileLocation("HarbinBridgeLift")]
    [SettingsUIGroupOrder(new[] { "BridgeLift" })]
    [SettingsUIShowGroupName(new[] { "BridgeLift" })]
    public class Setting : ModSetting
    {
        public const string kSection = "Main";
        public const string kGroup = "BridgeLift";

        private string _dataFilePath = "";
        private float _clearanceScale = 1f;
        private float _matchRadius = 2.5f;
        private string _diagRoadName = "中央大街";
        private string _lastStatus = "尚未运行";

        public Setting(IMod mod) : base(mod) { }

        public override void SetDefaults()
        {
            DataFilePath = "";
            ClearanceScale = 1f;
            MatchRadius = 2.5f;
            DiagRoadName = "中央大街";
            LastStatus = "尚未运行";
        }

        /// <summary>留空就用默认位置 ModsData\HarbinBridgeLift\bridges_lift.json</summary>
        [SettingsUITextInput]
        [SettingsUISection(kSection, kGroup)]
        public string DataFilePath
        {
            get => _dataFilePath;
            set { _dataFilePath = value ?? ""; }
        }

        /// <summary>整体净空倍率：1.0 = 数据里的 10 m</summary>
        [SettingsUISlider(min = 0.2f, max = 2f, step = 0.05f, scalarMultiplier = 1f, unit = "floatSingleFraction")]
        [SettingsUISection(kSection, kGroup)]
        public float ClearanceScale
        {
            get => _clearanceScale;
            set { _clearanceScale = value; }
        }

        /// <summary>节点配对半径（米）</summary>
        [SettingsUISlider(min = 0.5f, max = 10f, step = 0.25f, scalarMultiplier = 1f, unit = "floatSingleFraction")]
        [SettingsUISection(kSection, kGroup)]
        public float MatchRadius
        {
            get => _matchRadius;
            set { _matchRadius = value; }
        }

        /// <summary>单路试点要写真名的路名（须与 street_names.json 里的名字一致）。</summary>
        [SettingsUITextInput]
        [SettingsUISection(kSection, kGroup)]
        public string DiagRoadName
        {
            get => _diagRoadName;
            set { _diagRoadName = value ?? "中央大街"; }
        }

        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool ScanBridges
        {
            set { Enqueue(LiftRequest.Scan); }
        }

        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool LiftBridges
        {
            set { Enqueue(LiftRequest.Lift); }
        }

        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool RestoreBridges
        {
            set { Enqueue(LiftRequest.Restore); }
        }

        /// <summary>按 street_names.json 把 OSM 真实街名批量写进路网（官方 NameSystem.SetCustomName）。</summary>
        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool SetStreetNames
        {
            set { Enqueue(LiftRequest.Names); }
        }

        /// <summary>单路试点：只把 DiagRoadName 一条路的名字写到聚合实体（标签真正读取的对象）。</summary>
        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool SetNameSingle
        {
            set { Enqueue(LiftRequest.NameSingle); }
        }

        /// <summary>只读：枚举铁路网预制件（TrackPrefab）与 Train 类建筑预制件，名字+实体 id 进报告。</summary>
        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool ListRailPrefabs
        {
            set { Enqueue(LiftRequest.RailPrefabs); }
        }

        /// <summary>单段试建：按 ModsData 的 rail_test.json 建 1 段铁轨，2 秒后自查并清理未消费的定义实体。</summary>
        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool RailTestSegment
        {
            set { Enqueue(LiftRequest.RailTest); }
        }

        /// <summary>工具路线试建：自建 ToolBaseSystem 设为 activeTool + applyMode=Apply 后再排队同一段。</summary>
        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool RailTestWithTool
        {
            set { Enqueue(LiftRequest.RailTestTool); }
        }

        /// <summary>车站试放（对象分支）：按 ModsData 的 rail_station.json 放火车站建筑，
        /// ObjectDefinition + E2C 握手，5 秒后分类自查（位置/朝向/离轨距离）。重复点 = 幂等重放。</summary>
        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool PlaceStation
        {
            set { Enqueue(LiftRequest.Station); }
        }

        /// <summary>⑨ 站前路：按 ModsData 的 rail_road.json 铺一条公路（默认 Small Road），
        /// 高程跟地形，首/末端点挂到最近的既有公路节点上。</summary>
        [SettingsUIButton]
        [SettingsUISection(kSection, kGroup)]
        public bool PlaceStationRoad
        {
            set { Enqueue(LiftRequest.StationRoad); }
        }

        [SettingsUITextInput]
        [SettingsUISection(kSection, kGroup)]
        public string LastStatus
        {
            get => _lastStatus;
            set { _lastStatus = value ?? ""; }
        }

        public static string ReportPath =>
            Path.Combine(BridgeLiftData.ModsDataFolder, "last_report.txt");

        /// <summary>每种操作各留一份，互不覆盖，方便事后核对</summary>
        public static string ReportPathFor(int mode)
        {
            string name = mode == 1 ? "last_scan.txt"
                        : mode == 2 ? "last_lift.txt"
                        : mode == 4 ? "last_names.txt"
                        : mode == 5 ? "last_single_name.txt"
                        : mode == 6 ? "last_rail_prefabs.txt"
                        : mode == 7 ? "last_rail_test.txt"
                        : mode == 8 ? "last_rail_tool_test.txt"
                        : mode == 9 ? "last_station_test.txt"
                        : mode == 10 ? "last_road_test.txt"
                        : "last_restore.txt";
            return Path.Combine(BridgeLiftData.ModsDataFolder, name);
        }

        /// <summary>
        /// 按钮只排队。真正的读写在 BridgeLiftSystem（ModificationEnd 相位）里做 ——
        /// 在 UI 回调里做 ECS 结构变更会原生崩溃（v1 实测）。
        /// </summary>
        private void Enqueue(LiftRequest kind)
        {
            BridgeLiftSystem.Pending = (int)kind;
            LastStatus = "已排队：" + kind + "（本帧稍后执行，看日志/报告文件）";
            Mod.log.Info($"[HarbinBridgeLift] queued {kind}");
        }

        public void SetStatusFromReport(string report)
        {
            LastStatus = FirstLine(report);
        }

        private static string FirstLine(string s)
        {
            string[] lines = s.Split('\n');
            for (int i = lines.Length - 1; i >= 0; i--)
            {
                string t = lines[i].Trim();
                if (t.Length > 0 && !t.StartsWith("==")) return t.Length > 160 ? t.Substring(0, 160) : t;
            }
            return "完成（详见报告）";
        }

        public static void WriteReport(string report, int mode)
        {
            try
            {
                Directory.CreateDirectory(BridgeLiftData.ModsDataFolder);
                File.WriteAllText(ReportPath, report);
                File.WriteAllText(ReportPathFor(mode), report);
            }
            catch (Exception ex)
            {
                Mod.log.Warn("写报告失败: " + ex.Message);
            }
            Mod.log.Info("[HarbinBridgeLift]\n" + report);
        }
    }
}
