using System;
using System.Collections.Generic;
using System.IO;
using HarbinBridgeLift;

internal static class Program
{
    private static int Main(string[] args)
    {
        string path = args.Length > 0
            ? args[0]
            : @"<old-workspace>\out\mod_input\bridges_lift.json";

        Console.WriteLine("== 解析 " + path + " ==");

        // 先手工复现一遍扫描，看清中间量
        {
            string raw0 = File.ReadAllText(path);
            int pi = raw0.IndexOf("\"points\"", StringComparison.Ordinal);
            int open = pi < 0 ? -1 : raw0.IndexOf('[', pi);
            Console.WriteLine($"  诊断: 长度={raw0.Length} points@{pi} open@{open}");
            if (open >= 0) Console.WriteLine("  诊断: open 之后 70 字符 = " +
                raw0.Substring(open, Math.Min(70, raw0.Length - open)).Replace("\n", "\\n"));
        }

        BridgeLiftData d = BridgeLiftData.Load(path, out string err);
        if (d == null)
        {
            Console.WriteLine("失败: " + err);
            return 1;
        }

        int fail = 0;
        void Check(string what, bool ok, string detail)
        {
            Console.WriteLine($"  [{(ok ? "OK  " : "FAIL")}] {what}  {detail}");
            if (!ok) fail++;
        }

        Check("点数", d.Count == 2705, $"读到 {d.Count}，期望 2705");
        Check("结构数", d.StructureCount == 190, $"读到 {d.StructureCount}，期望 190");
        Check("海平面", Math.Abs(d.sea_level - 113.5f) < 0.01f, d.sea_level.ToString("F2"));
        Check("默认净空", Math.Abs(d.default_clearance - 10f) < 0.01f, d.default_clearance.ToString("F2"));
        Check("引道长度", Math.Abs(d.ramp_length - 160f) < 0.01f, d.ramp_length.ToString("F0"));
        Check("配对半径", Math.Abs(d.match_radius - 2.5f) < 0.01f, d.match_radius.ToString("F2"));
        Check("来源", d.source_osm == "overpass_roads.osm", d.source_osm);

        float maxC = 0f, minC = float.MaxValue;
        double sum = 0;
        for (int i = 0; i < d.Count; i++)
        {
            if (d.cs[i] > maxC) maxC = d.cs[i];
            if (d.cs[i] < minC) minC = d.cs[i];
            sum += d.cs[i];
        }
        Check("净空上界=10", Math.Abs(maxC - 10f) < 0.01f, maxC.ToString("F3"));
        Check("净空下界>0.1", minC > 0.1f, minC.ToString("F3"));
        Console.WriteLine($"  净空均值 {sum / d.Count:F3}");

        // 抽查前 3 个点：用另一条独立代码路径从源文件里抠出来对照
        var ci = System.Globalization.CultureInfo.InvariantCulture;
        string raw = File.ReadAllText(path);
        int pk = raw.IndexOf("\"points\":[", StringComparison.Ordinal);
        var expect = new List<float[]>();
        if (pk >= 0)
        {
            int i = pk + 10;
            while (expect.Count < 3 && i < raw.Length)
            {
                int e = raw.IndexOf(']', i);
                if (e < 0) break;
                string[] p = raw.Substring(i + 1, e - i - 1).Split(',');
                expect.Add(new[]
                {
                    float.Parse(p[0], ci), float.Parse(p[1], ci), float.Parse(p[2], ci)
                });
                i = raw.IndexOf('[', e);
                if (i < 0) break;
            }
        }
        Check("独立路径也抠到 3 个点", expect.Count == 3, "抠到 " + expect.Count);
        for (int i = 0; i < expect.Count && i < d.Count; i++)
        {
            bool ok = Math.Abs(expect[i][0] - d.xs[i]) < 1e-3
                      && Math.Abs(expect[i][1] - d.zs[i]) < 1e-3
                      && Math.Abs(expect[i][2] - d.cs[i]) < 1e-3;
            Check($"抽查第 {i + 1} 个点", ok,
                $"独立 [{expect[i][0]},{expect[i][1]},{expect[i][2]}]  解析 [{d.xs[i]},{d.zs[i]},{d.cs[i]}]");
        }

        // ---- 备份往返
        Console.WriteLine("\n== 备份读写往返 ==");
        var bf = new BackupFile { stamp = "1999-01-01 00:00:00", data_file = "overpass_roads.osm" };
        var rnd = new Random(12345);
        for (int i = 0; i < 500; i++)
        {
            bf.nodes.Add(new BackupEntry
            {
                i = 1000 + i * 7,
                x = (float)(rnd.NextDouble() * 14000 - 7000),
                z = (float)(rnd.NextDouble() * 14000 - 7000),
                y = (float)(100 + rnd.NextDouble() * 90),
                hasElev = (i % 3 == 0),
                ex = (float)(rnd.NextDouble() * 20),
                ey = (float)(rnd.NextDouble() * 20),
            });
        }
        string backupPath = BackupFile.PathFor;
        Console.WriteLine("  备份路径 " + backupPath);
        bf.Save();
        BackupFile back = BackupFile.Load(out string err2);
        Check("备份能读回", back != null, err2 ?? "");
        if (back != null)
        {
            Check("条数一致", back.nodes.Count == bf.nodes.Count, $"{back.nodes.Count} vs {bf.nodes.Count}");
            int bad = 0;
            for (int i = 0; i < bf.nodes.Count; i++)
            {
                BackupEntry a = bf.nodes[i], b = back.nodes[i];
                if (a.i != b.i || Math.Abs(a.x - b.x) > 1e-3 || Math.Abs(a.z - b.z) > 1e-3
                    || Math.Abs(a.y - b.y) > 1e-3 || a.hasElev != b.hasElev
                    || Math.Abs(a.ex - b.ex) > 1e-3 || Math.Abs(a.ey - b.ey) > 1e-3) bad++;
            }
            Check("逐条数值一致", bad == 0, $"不一致 {bad} 条");
            Console.WriteLine("  stamp=" + back.stamp + "  data=" + back.data_file);
        }
        try { File.Delete(backupPath); Console.WriteLine("  已清理测试备份文件"); } catch { }

        Console.WriteLine(fail == 0 ? "\n全部通过 ✅" : $"\n{fail} 项失败 ❌");
        return fail == 0 ? 0 : 1;
    }
}
