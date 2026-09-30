# ildump_il.ps1 —— 用 Cecil 打印匹配类型的**原始 IL**（逐条 opcode + 操作数），用于看查询/结构构造。
# 与 ildump.ps1（只看调用与字符串）互补：这个把方法体原样摊开，能看清 WithAll<T>/AddComponent<T> 的泛型实参。
# 用法：
#   pwsh -File tools\ildump_il.ps1 -Assembly <dll> -TypeFilter 'GenerateEdgesSystem' -MethodFilter '__AssignQueries|OnCreate'
#   -Limit <n>  每个方法最多打印 n 条指令（默认 400，避免超长）
param(
  [Parameter(Mandatory=$true)][string]$Assembly,
  [Parameter(Mandatory=$true)][string]$TypeFilter,
  [string]$MethodFilter = '.',
  [int]$Limit = 400,
  [string]$Out
)
$ErrorActionPreference = 'Stop'
$managed = '<CS2_INSTALL>\Cities2_Data\Managed'
[Reflection.Assembly]::LoadFrom("$managed\Colossal.Mono.Cecil.dll") | Out-Null
$mod = [Colossal.Mono.Cecil.ModuleDefinition]::ReadModule($Assembly)

$lines = New-Object System.Collections.Generic.List[string]
$hit = 0
foreach ($t in $mod.Types) {
  $all = @($t) + @($t.NestedTypes)
  foreach ($tt in $all) {
    if ($tt.FullName -notmatch $TypeFilter) { continue }
    foreach ($m in $tt.Methods) {
      if ($m.Name -notmatch $MethodFilter) { continue }
      $hit++
      $lines.Add("")
      $lines.Add("==== $($tt.FullName)::$($m.Name) ($($m.Parameters.Count) 参) ====")
      if (-not $m.HasBody) { $lines.Add("     (无方法体)"); continue }
      $n = 0
      foreach ($i in $m.Body.Instructions) {
        $op = $i.OpCode.Name
        $operand = ''
        if ($null -ne $i.Operand) { $operand = " $($i.Operand)" }
        $lines.Add(("  IL_{0:x4}  {1}{2}" -f $i.Offset, $op, $operand))
        if (++$n -ge $Limit) { $lines.Add("  …（截断，共 $($m.Body.Instructions.Count) 条）"); break }
      }
    }
  }
}
$lines.Insert(0, "（匹配方法 $hit 个）")
if ($Out) { $lines | Set-Content -Path $Out -Encoding utf8 } else { $lines | ForEach-Object { Write-Output $_ } }
Write-Output "（匹配方法 $hit 个）→ $Out"
