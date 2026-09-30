// TypeDump —— 读游戏程序集元数据，输出结构体字段
//
// 用法：
//   TypeDump <managedDir> <assemblyName> <mode> [pattern...]
//
//   mode = fields   列出匹配类型的字段（含嵌套类型的展开）
//   mode = find     列出名字匹配 pattern 的所有类型（全程序集搜索）
//
// 例：
//   TypeDump "...\Managed" Game fields Game.Net.NetData Game.Net.Composition
//   TypeDump "...\Managed" Game find *Composition* *Archetype*

using System.Reflection;
using System.Runtime.InteropServices;

if (args.Length < 4)
{
    Console.Error.WriteLine("用法: TypeDump <managedDir> <assembly> <fields|find> <pattern...>");
    return 2;
}

string managedDir = args[0];
string asmName = args[1];
string mode = args[2].ToLowerInvariant();
string[] patterns = args.Skip(3).ToArray();

// 只用游戏 Managed 目录：里面自带完整 .NET Framework mscorlib，
// 混入 .NET 10 运行时目录会造成同名程序集冲突（mscorlib / netstandard）。
var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
foreach (var p in Directory.GetFiles(managedDir, "*.dll"))
    map[Path.GetFileNameWithoutExtension(p)] = p;

using var mlc = new MetadataLoadContext(new SimpleResolver(map), coreAssemblyName: "mscorlib");

Assembly asm;
try
{
    asm = mlc.LoadFromAssemblyPath(Path.Combine(managedDir, asmName + ".dll"));
}
catch (Exception ex)
{
    Console.Error.WriteLine($"加载 {asmName}.dll 失败: {ex.Message}");
    return 1;
}

Type[] allTypes;
try
{
    allTypes = asm.GetTypes();
}
catch (ReflectionTypeLoadException ex)
{
    allTypes = ex.Types.Where(t => t != null).ToArray()!;
    Console.Error.WriteLine($"(警告: 部分类型无法加载，已跳过 {ex.LoaderExceptions?.Length ?? 0} 个)");
}

static bool Match(string name, string[] pats)
{
    foreach (var p in pats)
    {
        if (p.Contains('*'))
        {
            var rx = "^" + System.Text.RegularExpressions.Regex.Escape(p).Replace("\\*", ".*") + "$";
            if (System.Text.RegularExpressions.Regex.IsMatch(name, rx)) return true;
        }
        else
        {
            if (name == p || name.EndsWith("." + p, StringComparison.Ordinal)) return true;
        }
    }
    return false;
}

if (mode == "find")
{
    foreach (var t in allTypes.Where(t => Match(t.FullName ?? t.Name, patterns)).OrderBy(t => t.FullName))
        Console.WriteLine($"{t.FullName}   [{(t.IsValueType ? "struct" : t.IsInterface ? "interface" : t.IsEnum ? "enum" : "class")}]  {t.Assembly.GetName().Name}");
    return 0;
}

// ---- fields 模式 ----
static string Pretty(Type t)
{
    if (t == null) return "?";
    if (t.IsArray) return Pretty(t.GetElementType()!) + "[]";
    if (!t.IsGenericType) return t.Name;
    var n = t.Name;
    int tick = n.IndexOf('`');
    if (tick > 0) n = n.Substring(0, tick);
    return n + "<" + string.Join(", ", t.GetGenericArguments().Select(Pretty)) + ">";
}

foreach (var pat in patterns)
{
    var hits = allTypes.Where(t => Match(t.FullName ?? t.Name, new[] { pat })).ToList();

    if (hits.Count == 0)
    {
        Console.WriteLine($"### {pat} —— 未找到");
        Console.WriteLine();
        continue;
    }

    foreach (var t in hits.OrderBy(t => t.FullName))
    {
        Console.WriteLine($"### {t.FullName}   [{(t.IsValueType ? "struct" : t.IsEnum ? "enum" : "class")}]  size={SafeSize(t)}");

        // 接口决定它在 ECS 里的角色：IComponentData / IBufferElementData / ICleanupComponentData
        var ifaces = t.GetInterfaces().Select(i => Pretty(i)).Where(n => n.Contains("I") && (n.Contains("Component") || n.Contains("Buffer") || n.Contains("Element"))).ToList();
        if (ifaces.Count > 0)
            Console.WriteLine($"      接口: {string.Join(", ", ifaces)}");
        else if (t.IsValueType && !t.IsEnum)
            Console.WriteLine("      接口: (无 ECS 接口 —— 是纯数据/嵌套结构)");

        if (t.IsEnum)
        {
            foreach (var f in t.GetFields(BindingFlags.Public | BindingFlags.Static))
                Console.WriteLine($"      {f.Name}");
            Console.WriteLine();
            continue;
        }

        var fields = t.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        if (fields.Length == 0)
            Console.WriteLine("      (无实例字段)");

        foreach (var f in fields)
        {
            string acc = f.IsPublic ? "public" : f.IsPrivate ? "private" : "protected";
            string off = "";
            try { off = $"  @0x{Marshal.OffsetOf(t, f.Name).ToInt64():X}"; } catch { }
            Console.WriteLine($"      {acc} {Pretty(f.FieldType),-42} {f.Name}{off}");
        }

        // 公开方法（含继承来的），建路要调哪个 API 靠这个确定
        var methods = t.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly)
                        .Where(m => !m.IsSpecialName)
                        .OrderBy(m => m.Name).ToList();
        if (methods.Count > 0)
        {
            Console.WriteLine("      ---- 方法 ----");
            foreach (var m in methods)
            {
                string ps = string.Join(", ", m.GetParameters().Select(p => Pretty(p.ParameterType) + " " + p.Name));
                Console.WriteLine($"      {(m.IsStatic ? "static " : "")}{Pretty(m.ReturnType)} {m.Name}({ps})");
            }
        }
        Console.WriteLine();
    }
}

return 0;

static string SafeSize(Type t)
{
    try { return Marshal.SizeOf(t).ToString(); } catch { return "?"; }
}

/// <summary>按简单名解析，一个名字只对应一个文件，避免 PathAssemblyResolver 的同名冲突。</summary>
sealed class SimpleResolver : MetadataAssemblyResolver
{
    private readonly Dictionary<string, string> _map;
    public SimpleResolver(Dictionary<string, string> map) => _map = map;

    public override Assembly Resolve(MetadataLoadContext context, AssemblyName assemblyName)
    {
        string key = assemblyName.Name!;
        if (_map.TryGetValue(key, out var path))
            return context.LoadFromAssemblyPath(path);
        return null!;   // 解析不到就交给上下文报错，不静默吞掉
    }
}
