# build_mod.ps1 —— 从本工作区构建 HarbinBridgeLift 并部署到游戏 Mods 目录。
# 用法:
#   pwsh -File build_mod.ps1
# 部署目标说明（2026-09-29 实测）: ModPostProcessor 直读【用户级】CSII_LOCALMODSPATH，
# 进程级覆盖无效，所以本脚本不支持临时改部署目录；验证构建产物请直接比对
# bin\Release 与游戏 Mods\HarbinBridgeLift 的哈希。
# 依据: config/game.json + 官方 Modding Toolchain（CSII_* 环境变量，进程级设置）。
# 注意: csproj 直接读【用户级】CSII_TOOLPATH 环境变量；本机已持久化设置。
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # shared/scripts -> workshop root
$game = Get-Content (Join-Path $root 'config\game.json') -Raw -Encoding UTF8 | ConvertFrom-Json

$env:CSII_INSTALLATIONPATH   = $game.game_install
$env:CSII_USERDATAPATH       = $game.user_data
$env:CSII_LOCALMODSPATH      = Join-Path $game.user_data 'Mods'
$env:CSII_UNITYMODPROJECTPATH = Join-Path $game.user_data '.cache\Modding\UnityModsProject'
$env:CSII_MODPOSTPROCESSORPATH = Join-Path $game.game_install 'Cities2_Data\Content\Game\.ModdingToolchain\ModPostProcessor\ModPostProcessor.exe'
$env:CSII_PATHSET            = 'Build'

$proj = Join-Path $root 'shared\mod\HarbinBridgeLift\HarbinBridgeLift.csproj'
Write-Output "project : $proj"
Write-Output "deploy  : $env:CSII_LOCALMODSPATH"
dotnet build $proj -c Release
if ($LASTEXITCODE -ne 0) { throw "dotnet build failed: $LASTEXITCODE" }
Write-Output '--- bin\Release ---'
Get-ChildItem (Join-Path $root 'shared\mod\HarbinBridgeLift\bin\Release') -File | ForEach-Object {
  '{0}  {1,9:N0} B  {2}' -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.Substring(0,16), $_.Length, $_.Name }
