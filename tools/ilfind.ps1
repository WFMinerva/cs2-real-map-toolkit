# ilfind.ps1 —— 在程序集里按**操作数正则**找方法：哪些方法引用了某个类型/方法/字段。
# 用途：回答「谁给定义实体补了 X 组件」「谁调用了 Y」这类结构问题，只读。
# 用法：
#   pwsh -File tools\ilfind.ps1 -Assembly <dll> -Pattern 'AddComponent<Game\.Net\.Edge>' -TypeFilter '^Game\.(Tools|Net)\.'
param(
  [Parameter(Mandatory=$true)][string]$Assembly,
  [Parameter(Mandatory=$true)][string]$Pattern,
  [string]$TypeFilter = '.',
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
      if (-not $m.HasBody) { continue }
      $matched = New-Object System.Collections.Generic.List[string]
      foreach ($i in $m.Body.Instructions) {
        if ($null -eq $i.Operand) { continue }
        $op = "$($i.Operand)"
        if ($op -match $Pattern) { $matched.Add("    IL_" + $i.Offset.ToString("x4") + "  " + $i.OpCode.Name + " " + $op) }
      }
      if ($matched.Count -gt 0) {
        $hit++
        $lines.Add("==== $($tt.FullName)::$($m.Name)  ($($matched.Count) 处)")
        foreach ($l in $matched) { $lines.Add($l) }
      }
    }
  }
}
$lines.Insert(0, "（命中方法 $hit 个，模式 $Pattern）")
if ($Out) { $lines | Set-Content -Path $Out -Encoding utf8 } else { $lines | ForEach-Object { Write-Output $_ } }
Write-Output "（命中方法 $hit 个）→ $Out"
