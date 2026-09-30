# deploy_mod_when_closed.ps1 —— 把 bin\Release 的 6 个产物部署到游戏 Mods 目录。
# 为什么单独有这一步：游戏运行时 `dotnet build` 的复制步骤会撞 MSB3231
# （无法移除目录 …HarbinBridgeLift_win_x86_64.dll: Access denied），但**编译产物已经生成**；
# 所以先构建、再由本脚本等 Cities2 退出后复制并逐文件核对哈希。全程只读源、只写目标 Mods 目录。
# 用法：
#   pwsh -File shared\scripts\deploy_mod_when_closed.ps1 -Tag "v24 端点跨边+对外连接诊断"
#   pwsh -File shared\scripts\deploy_mod_when_closed.ps1 -Tag "..." -NoWait      # 游戏在跑就直接退出（返回码 2）
#   pwsh -File shared\scripts\deploy_mod_when_closed.ps1 -Tag "..." -TimeoutMin 90
param(
  [string]$Tag = "",
  [int]$TimeoutMin = 60,
  [switch]$NoWait
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$game = Get-Content (Join-Path $root 'config\game.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$src = Join-Path $root 'shared\mod\HarbinBridgeLift\bin\Release'
$dst = Join-Path $game.user_data 'Mods\HarbinBridgeLift'
$log = Join-Path $game.user_data 'ModsData\HarbinBridgeLift\deploy_log.txt'
$files = @('HarbinBridgeLift.dll', 'HarbinBridgeLift.pdb',
           'HarbinBridgeLift_win_x86_64.dll', 'HarbinBridgeLift_win_x86_64.pdb',
           'HarbinBridgeLift_mac_x86_64.bundle', 'HarbinBridgeLift_linux_x86_64.so')

function Hash16($p) { if (Test-Path $p) { (Get-FileHash $p -Algorithm SHA256).Hash.Substring(0, 16) } else { '-' } }
function AllMatch() {
  foreach ($f in $files) { if (-not (Test-Path (Join-Path $dst $f))) { return $false }
                           if ((Hash16 (Join-Path $src $f)) -ne (Hash16 (Join-Path $dst $f))) { return $false } }
  return $true
}
function Append($line) {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
  Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $line) -Encoding UTF8
}

if (AllMatch) { Write-Output "已是最新（6 个文件哈希一致），无需复制。"; Append "$Tag  已是最新，跳过"; exit 0 }

$running = @(Get-Process -Name Cities2 -ErrorAction SilentlyContinue).Count -gt 0
if ($running -and $NoWait) { Write-Output "Cities2 正在运行（-NoWait）→ 未复制。"; exit 2 }
if ($running) {
  Write-Output "等 Cities2 退出…（最多 $TimeoutMin 分钟）"
  Append "WAIT 等 Cities2 退出…（$Tag）"
  $deadline = (Get-Date).AddMinutes($TimeoutMin)
  while (@(Get-Process -Name Cities2 -ErrorAction SilentlyContinue).Count -gt 0) {
    if ((Get-Date) -gt $deadline) { Write-Output "超时：Cities2 仍在运行，未复制。"; Append "$Tag  超时未复制"; exit 3 }
    Start-Sleep -Seconds 5
  }
  Start-Sleep -Seconds 2
}

foreach ($f in $files) { Copy-Item (Join-Path $src $f) (Join-Path $dst $f) -Force }
$ok = $true
foreach ($f in $files) {
  $m = (Hash16 (Join-Path $src $f)) -eq (Hash16 (Join-Path $dst $f))
  if (-not $m) { $ok = $false }
  Append ("{0}  {1}  {2}  match={3}" -f $Tag, $f, (Hash16 (Join-Path $src $f)), $m)
  Write-Output ("{0}  {1}  match={2}" -f $f, (Hash16 (Join-Path $src $f)), $m)
}
if ($ok) { Write-Output "部署完成：6/6 哈希一致。" } else { Write-Output "部署异常：有文件哈希不一致！"; exit 4 }
