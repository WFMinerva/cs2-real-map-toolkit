# ildump_types.ps1 —— 用 Cecil 列程序集里匹配类型的「基类 / 接口 / 字段」，供结构对照用（只读）。
# 与 ildump.ps1（列 IL 调用/字符串）互补：这个只看类型形状，不反编译方法体。
# 用法：
#   pwsh -File tools\ildump_types.ps1 -Assembly <dll> -TypeFilter '^Game\.Net\.' -Out <txt>
#   -FieldsOnly      只列实现了 IBufferElementData / IComponentData 的类型（组件与缓冲）
#   -Interfaces a,b  只列接口名包含其中之一的类型
param(
  [Parameter(Mandatory=$true)][string]$Assembly,
  [Parameter(Mandatory=$true)][string]$TypeFilter,
  [string]$Out,
  [switch]$FieldsOnly,
  [string[]]$Interfaces
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
    $ifs = @()
    foreach ($i in $tt.Interfaces) { $ifs += $i.InterfaceType.Name }
    if ($FieldsOnly -and ($ifs -notcontains 'IBufferElementData') -and ($ifs -notcontains 'IComponentData')) { continue }
    if ($Interfaces) {
      $keep = $false
      foreach ($f in $Interfaces) { if ($ifs -contains $f) { $keep = $true } }
      if (-not $keep) { continue }
    }
    $hit++
    $base = if ($tt.BaseType) { $tt.BaseType.Name } else { '' }
    $lines.Add("==== $($tt.FullName)  : $base  [$([string]::Join(',', $ifs))]  size=$($tt.ClassSize)")
    foreach ($f in $tt.Fields) {
      $cv = ''
      if ($f.HasConstant) { $cv = " = $($f.Constant)" }
      $lines.Add("     $($f.FieldType.Name)  $($f.Name)$cv")
    }
  }
}
$lines.Insert(0, "（匹配类型 $hit 个）")
if ($Out) { $lines | Set-Content -Path $Out -Encoding utf8 } else { $lines | ForEach-Object { Write-Output $_ } }
Write-Output "（匹配类型 $hit 个）→ $Out"
