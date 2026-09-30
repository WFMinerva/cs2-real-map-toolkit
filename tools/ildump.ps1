# ildump.ps1 —— 用 Cecil 把指定程序集里匹配类型的 IL 摘要成"调用了哪些方法 + 字符串常量"。
# 用法：
#   pwsh -File tools\ildump.ps1 -Assembly <dll> -TypeFilter 'NetTool|MinimalRoad|Cloning' [-OnlyGame]
# 输出：每个类型/方法 → 去重后的 call/callvirt/newobj 目标（默认只看 Game./Colossal./Unity./全局）与 ldstr 常量。
param(
  [Parameter(Mandatory=$true)][string]$Assembly,
  [Parameter(Mandatory=$true)][string]$TypeFilter,
  [switch]$OnlyGame,
  [switch]$Fields
)
$ErrorActionPreference = 'Stop'
$managed = '<CS2_INSTALL>\Cities2_Data\Managed'
[Reflection.Assembly]::LoadFrom("$managed\Colossal.Mono.Cecil.dll") | Out-Null
$mod = [Colossal.Mono.Cecil.ModuleDefinition]::ReadModule($Assembly)

function Show-Type($t) {
  Write-Output "==== $($t.FullName) ===="
  if ($Fields) {
    foreach ($f in $t.Fields) { Write-Output ("  field {0} {1}" -f $f.FieldType.Name, $f.Name) }
  }
  foreach ($m in $t.Methods) {
    $calls = New-Object System.Collections.Generic.List[string]
    $strs  = New-Object System.Collections.Generic.List[string]
    if ($m.HasBody) {
      foreach ($i in $m.Body.Instructions) {
        $op = $i.OpCode.Name
        if ($op -match '^call|^callvirt|^newobj|^ldftn') {
          $operand = "$($i.Operand)"
          if (-not $OnlyGame -or $operand -match '^(Game|Colossal|Unity|OSMImportMod)') {
            $calls.Add("$op $operand")
          }
        } elseif ($op -eq 'ldstr') {
          $strs.Add([string]$i.Operand)
        }
      }
    }
    Write-Output "  -- $($m.Name)($($m.Parameters.Count) 参)"
    ($calls | Select-Object -Unique) | ForEach-Object { Write-Output "       $_" }
    if ($strs.Count) { Write-Output ("       ldstr: " + (($strs | Select-Object -Unique) -join ' | ')) }
  }
}

$hit = 0
foreach ($t in $mod.Types) {
  if ($t.FullName -match $TypeFilter) { Show-Type $t; $hit++ }
  foreach ($n in $t.NestedTypes) { if ($n.FullName -match $TypeFilter) { Show-Type $n; $hit++ } }
}
Write-Output "（匹配类型 $hit 个）"
