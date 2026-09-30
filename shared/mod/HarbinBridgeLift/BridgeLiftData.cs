// HarbinBridgeLift —— 数据模型：抬升高度场 + 原值备份
//
// 刻意不用任何 JSON 库（Newtonsoft 在游戏里能不能解析到没验证过，不值得赌一次往返）：
//   读   —— 手写扫描器，只认 105_export_bridge_data.py 生成的固定格式
//   写   —— 行式纯文本，好读也好查

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace HarbinBridgeLift
{
    /// <summary>抬升高度场：一堆 (世界X, 世界Z, 该点净空m)。</summary>
    public sealed class BridgeLiftData
    {
        public float sea_level = 113.5f;
        public float default_clearance = 10f;
        public float ramp_length = 160f;
        public float match_radius = 2.5f;
        public string source_osm = "";
        public string generated_by = "";
        /// <summary>结构真实数量（structure_names 是去重过的名字表，长度不等于它）</summary>
        public int structure_count;
        public string[] structure_names = new string[0];

        public float[] xs = new float[0];
        public float[] zs = new float[0];
        public float[] cs = new float[0];

        public int Count => xs.Length;
        public int StructureCount => structure_count > 0 ? structure_count : structure_names.Length;

        public const string FileName = "bridges_lift.json";

        public static string ModsDataFolder
        {
            get
            {
                string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                return Path.GetFullPath(Path.Combine(local, "..", "LocalLow", "Colossal Order",
                    "Cities Skylines II", "ModsData", "HarbinBridgeLift"));
            }
        }

        public static string DefaultPath => Path.Combine(ModsDataFolder, FileName);

        public static string Resolve(string overridePath, out List<string> tried)
        {
            tried = new List<string>();
            var cands = new List<string>();
            if (!string.IsNullOrWhiteSpace(overridePath)) cands.Add(overridePath.Trim());
            cands.Add(DefaultPath);
            if (!string.IsNullOrEmpty(Mod.InstallFolder))
            {
                cands.Add(Path.Combine(Mod.InstallFolder, FileName));
                cands.Add(Path.GetFullPath(Path.Combine(Mod.InstallFolder, "..", "..",
                    "ModsData", "HarbinBridgeLift", FileName)));
            }
            foreach (string c in cands)
            {
                tried.Add(c);
                if (File.Exists(c)) return c;
            }
            return null;
        }

        // ------------------------------------------------------------ 扫描器

        private static int IndexOfKey(string s, string key)
        {
            return s.IndexOf("\"" + key + "\"", StringComparison.Ordinal);
        }

        private static float ReadFloat(string s, string key, float fallback)
        {
            int i = IndexOfKey(s, key);
            if (i < 0) return fallback;
            i = s.IndexOf(':', i);
            if (i < 0) return fallback;
            i++;
            int j = i;
            while (j < s.Length && (char.IsDigit(s[j]) || s[j] == '-' || s[j] == '+' || s[j] == '.'
                                    || s[j] == 'e' || s[j] == 'E')) j++;
            float v;
            return float.TryParse(s.Substring(i, j - i), NumberStyles.Float,
                CultureInfo.InvariantCulture, out v) ? v : fallback;
        }

        private static string ReadString(string s, string key)
        {
            int i = IndexOfKey(s, key);
            if (i < 0) return "";
            int a = s.IndexOf('"', i + key.Length + 2);
            if (a < 0) return "";
            int b = s.IndexOf('"', a + 1);
            return b < 0 ? "" : s.Substring(a + 1, b - a - 1);
        }

        private static string[] ReadStringArray(string s, string key)
        {
            int i = s.IndexOf("\"" + key + "\":[", StringComparison.Ordinal);
            if (i < 0) return new string[0];
            int a = i + key.Length + 3;
            int depth = 0, b = a;
            for (; b < s.Length; b++)
            {
                if (s[b] == '[') depth++;
                else if (s[b] == ']') { depth--; if (depth == 0) break; }
            }
            string body = s.Substring(a + 1, Math.Max(0, b - a - 1));
            var list = new List<string>();
            int p = 0;
            while (true)
            {
                int q1 = body.IndexOf('"', p);
                if (q1 < 0) break;
                int q2 = body.IndexOf('"', q1 + 1);
                if (q2 < 0) break;
                list.Add(body.Substring(q1 + 1, q2 - q1 - 1));
                p = q2 + 1;
            }
            return list.ToArray();
        }

        public static BridgeLiftData Load(string path, out string error)
        {
            error = null;
            try
            {
                string s = File.ReadAllText(path);
                var d = new BridgeLiftData
                {
                    sea_level = ReadFloat(s, "sea_level", 113.5f),
                    default_clearance = ReadFloat(s, "default_clearance", 10f),
                    ramp_length = ReadFloat(s, "ramp_length", 160f),
                    match_radius = ReadFloat(s, "match_radius", 2.5f),
                    structure_count = (int)ReadFloat(s, "structure_count", 0f),
                    source_osm = ReadString(s, "source_osm"),
                    generated_by = ReadString(s, "generated_by"),
                    structure_names = ReadStringArray(s, "structure_names"),
                };

                // 注意：counts 里也有个 "points" 子键，所以必须连冒号方括号一起匹配
                const string PointsKey = "\"points\":[";
                int pk = s.IndexOf(PointsKey, StringComparison.Ordinal);
                if (pk < 0) { error = "找不到顶层 points 数组（\"points\":[）"; return null; }
                int open = pk + PointsKey.Length - 1;

                var xs = new List<float>(4096);
                var zs = new List<float>(4096);
                var cs = new List<float>(4096);
                int i = open + 1;
                while (i < s.Length)
                {
                    while (i < s.Length && (s[i] == ' ' || s[i] == ',' || s[i] == '\n'
                                            || s[i] == '\r' || s[i] == '\t')) i++;
                    if (i >= s.Length) break;
                    if (s[i] == ']') break;          // 外层数组结束
                    if (s[i] != '[') { i++; continue; }
                    int end = s.IndexOf(']', i);
                    if (end < 0) break;
                    string[] parts = s.Substring(i + 1, end - i - 1).Split(',');
                    if (parts.Length >= 3)
                    {
                        float x, z, c;
                        if (float.TryParse(parts[0], NumberStyles.Float, CultureInfo.InvariantCulture, out x)
                            && float.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out z)
                            && float.TryParse(parts[2], NumberStyles.Float, CultureInfo.InvariantCulture, out c)
                            && c > 0f)
                        {
                            xs.Add(x); zs.Add(z); cs.Add(c);
                        }
                    }
                    i = end + 1;
                }
                if (xs.Count == 0) { error = "points 里没解析出任何点"; return null; }
                d.xs = xs.ToArray();
                d.zs = zs.ToArray();
                d.cs = cs.ToArray();
                if (d.match_radius <= 0f) d.match_radius = 2.5f;
                return d;
            }
            catch (Exception ex)
            {
                error = ex.GetType().Name + ": " + ex.Message;
                return null;
            }
        }
    }

    // ------------------------------------------------------------------ 备份

    public sealed class BackupEntry
    {
        public int i;
        public float x, z, y;
        public bool hasElev;
        public float ex, ey;
    }

    public sealed class BackupFile
    {
        public string stamp = "";
        public string data_file = "";
        public List<BackupEntry> nodes = new List<BackupEntry>();

        private const string Header = "HarbinBridgeLift-backup v1";

        public static string PathFor =>
            Path.Combine(BridgeLiftData.ModsDataFolder, "lift_backup.txt");

        public void Save()
        {
            Directory.CreateDirectory(BridgeLiftData.ModsDataFolder);
            var sb = new StringBuilder();
            sb.AppendLine(Header);
            sb.AppendLine("stamp " + stamp);
            sb.AppendLine("data " + data_file);
            sb.AppendLine("count " + nodes.Count);
            var ci = CultureInfo.InvariantCulture;
            foreach (BackupEntry b in nodes)
            {
                sb.Append(b.i.ToString(ci)).Append(' ')
                  .Append(b.x.ToString("R", ci)).Append(' ')
                  .Append(b.z.ToString("R", ci)).Append(' ')
                  .Append(b.y.ToString("R", ci)).Append(' ')
                  .Append(b.hasElev ? '1' : '0').Append(' ')
                  .Append(b.ex.ToString("R", ci)).Append(' ')
                  .Append(b.ey.ToString("R", ci)).Append('\n');
            }
            File.WriteAllText(PathFor, sb.ToString());
        }

        public static BackupFile Load(out string error)
        {
            error = null;
            try
            {
                if (!File.Exists(PathFor)) { error = "没有备份文件 " + PathFor; return null; }
                string[] lines = File.ReadAllLines(PathFor);
                if (lines.Length == 0 || !lines[0].StartsWith(Header))
                {
                    error = "备份文件头不对：" + PathFor;
                    return null;
                }
                var b = new BackupFile();
                var ci = CultureInfo.InvariantCulture;
                for (int k = 1; k < lines.Length; k++)
                {
                    string ln = lines[k];
                    if (ln.Length == 0) continue;
                    if (ln.StartsWith("stamp ")) { b.stamp = ln.Substring(6); continue; }
                    if (ln.StartsWith("data ")) { b.data_file = ln.Substring(5); continue; }
                    if (ln.StartsWith("count ")) continue;
                    string[] p = ln.Split(' ');
                    if (p.Length < 7) continue;
                    float x, z, y, ex, ey; int i;
                    if (!int.TryParse(p[0], NumberStyles.Integer, ci, out i)) continue;
                    if (!float.TryParse(p[1], NumberStyles.Float, ci, out x)) continue;
                    if (!float.TryParse(p[2], NumberStyles.Float, ci, out z)) continue;
                    if (!float.TryParse(p[3], NumberStyles.Float, ci, out y)) continue;
                    if (!float.TryParse(p[5], NumberStyles.Float, ci, out ex)) continue;
                    if (!float.TryParse(p[6], NumberStyles.Float, ci, out ey)) continue;
                    b.nodes.Add(new BackupEntry
                    {
                        i = i, x = x, z = z, y = y,
                        hasElev = p[4] == "1", ex = ex, ey = ey,
                    });
                }
                if (b.nodes.Count == 0) { error = "备份文件里没有节点行"; return null; }
                return b;
            }
            catch (Exception ex2)
            {
                error = ex2.GetType().Name + ": " + ex2.Message;
                return null;
            }
        }
    }
}
