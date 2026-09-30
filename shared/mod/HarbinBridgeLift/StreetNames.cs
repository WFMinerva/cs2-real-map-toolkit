// HarbinBridgeLift —— 批量街名数据（shared/pipeline/68_export_street_names.py 产出）
//
// 为什么要有这个：Earth2Cities 不读 OSM name 标签（dll 字符串表只有 highway/ref），
// 导入的路全拿 CS2 主题默认花名。游戏官方批量入口 = Game.UI.NameSystem.SetCustomName
// （Game.dll 公开方法；NameSystem 自带 Serialize/Deserialize → 自定义名进存档）。
// 数据 = 每条带名 way 的逐段中点世界坐标 + 名字；mod 按边 Bezier 中点配对后写名。

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace HarbinBridgeLift
{
    public class StreetNames
    {
        public float radius = 6f;
        /// <summary>分组参数（68 从 maps/&lt;map&gt;.config.json 的 street_names 段带入，不写死在 mod 里）：
        /// 一条聚合的成员边按「最近名字点」逐边配名 → 无名边向相邻边扩散补齐 → 按节点连通性聚组；
        /// 组边数 &lt; minComponentEdges 的判为无名（防一处误配改名半条路）；</summary>
        public int minComponentEdges = 2;
        /// <summary>完全没有名字证据的组写这个标签（机主要求：实在没名字的就是「无名路」，不留游戏花名）。</summary>
        public string unnamedLabel = "无名路";
        /// <summary>一条聚合里出现多组不同街名时是否就地拆分（克隆聚合实体+重挂成员边）。
        /// 关掉 = 整条聚合取最大组的名字（v8 行为，作为拆分出问题时的回滚位）。</summary>
        public bool splitMultiName = true;
        public float[] xs = new float[0];
        public float[] zs = new float[0];
        public string[] names = new string[0];
        public int Count => xs.Length;

        public static string DefaultPath =>
            Path.Combine(BridgeLiftData.ModsDataFolder, "street_names.json");

        /// <summary>极简解析："points":[[x,z,"名字"],...] 与 "radius":n。不引 JSON 库，mod 依赖保持最小。</summary>
        public static StreetNames Load(string path, out string err)
        {
            err = null;
            try
            {
                string raw = File.ReadAllText(path, Encoding.UTF8);
                int i = raw.IndexOf("\"points\"", StringComparison.Ordinal);
                if (i < 0) { err = "缺 points 字段"; return null; }
                int br = raw.IndexOf('[', i);
                if (br < 0) { err = "points 不是数组"; return null; }
                int depth = 0, j = br;
                for (; j < raw.Length; j++)
                {
                    if (raw[j] == '[') depth++;
                    else if (raw[j] == ']') { depth--; if (depth == 0) { j++; break; } }
                }
                string body = raw.Substring(br, j - br);
                var xs = new List<float>(); var zs = new List<float>(); var ns = new List<string>();
                foreach (Match m in Regex.Matches(body,
                             "\\[\\s*(-?\\d+\\.?\\d*)\\s*,\\s*(-?\\d+\\.?\\d*)\\s*,\\s*\"([^\"]*)\"\\s*\\]"))
                {
                    xs.Add(float.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture));
                    zs.Add(float.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture));
                    ns.Add(m.Groups[3].Value);
                }
                var d = new StreetNames { xs = xs.ToArray(), zs = zs.ToArray(), names = ns.ToArray() };
                Match mr = Regex.Match(raw, "\"radius\"\\s*:\\s*(-?\\d+\\.?\\d*)");
                if (mr.Success) d.radius = float.Parse(mr.Groups[1].Value, CultureInfo.InvariantCulture);
                Match mv = Regex.Match(raw, "\"min_component_edges\"\\s*:\\s*(\\d+)");
                if (mv.Success) d.minComponentEdges = int.Parse(mv.Groups[1].Value, CultureInfo.InvariantCulture);
                Match mu = Regex.Match(raw, "\"unnamed_label\"\\s*:\\s*\"([^\"]*)\"");
                if (mu.Success && mu.Groups[1].Value.Length > 0) d.unnamedLabel = mu.Groups[1].Value;
                Match ms = Regex.Match(raw, "\"split_multi_name\"\\s*:\\s*(true|false)");
                if (ms.Success) d.splitMultiName = ms.Groups[1].Value == "true";
                if (d.Count == 0) { err = "points 没解析出任何点"; return null; }
                return d;
            }
            catch (Exception ex) { err = ex.Message; return null; }
        }
    }
}
