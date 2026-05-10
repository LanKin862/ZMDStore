param(
  [switch]$SingleFileBridge,
  [switch]$SkipTauriBuild
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$publishRoot = Join-Path $root "dist_portable"
$publishDir = Join-Path $publishRoot "ZMDStoreDesktop"
$resourceDir = Join-Path $publishDir "_up_"
$bridgeDist = Join-Path $root "build_portable\bridge_dist"
$bridgeWork = Join-Path $root "build_portable\bridge_build"
$specDir = Join-Path $root "build_portable"
$releaseExe = Join-Path $root "src-tauri\target\release\zmdstore_desktop.exe"

if (-not $SkipTauriBuild) {
  npm run tauri:build
}

if (-not (Test-Path $releaseExe)) {
  throw "Tauri release exe was not found: $releaseExe"
}

if (Test-Path $publishDir) {
  Remove-Item -LiteralPath $publishDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $resourceDir | Out-Null
New-Item -ItemType Directory -Force -Path $specDir | Out-Null

$commonPyInstallerArgs = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--name", "tauri_bridge",
  "--distpath", $bridgeDist,
  "--workpath", $bridgeWork,
  "--specpath", $specDir,
  "--hidden-import", "auto_click",
  "--hidden-import", "cv2",
  "--hidden-import", "numpy",
  "--hidden-import", "PIL",
  "--hidden-import", "mss",
  "--hidden-import", "pyautogui",
  "--hidden-import", "pygetwindow",
  "--exclude-module", "PySide6",
  "--exclude-module", "matplotlib",
  "--exclude-module", "torch",
  "--exclude-module", "scipy",
  "--exclude-module", "pandas"
)

if ($SingleFileBridge) {
  $pyInstallerArgs = $commonPyInstallerArgs + @("--onefile", "tauri_bridge.py")
  python @pyInstallerArgs
  Copy-Item -LiteralPath (Join-Path $bridgeDist "tauri_bridge.exe") -Destination $resourceDir -Force
} else {
  $pyInstallerArgs = $commonPyInstallerArgs + @("--onedir", "--contents-directory", ".", "tauri_bridge.py")
  python @pyInstallerArgs
  Copy-Item -Path (Join-Path $bridgeDist "tauri_bridge\*") -Destination $resourceDir -Recurse -Force
}

Copy-Item -LiteralPath $releaseExe -Destination (Join-Path $publishDir "ZMDStoreDesktop.exe") -Force
Copy-Item -LiteralPath "item" -Destination $resourceDir -Recurse -Force
Copy-Item -LiteralPath "region" -Destination $resourceDir -Recurse -Force
Copy-Item -LiteralPath "public" -Destination $resourceDir -Recurse -Force

@"
ZMDStoreDesktop portable package

Run: ZMDStoreDesktop.exe
Resource directory: _up_
Python bridge runtime is bundled. No separate Python or pip install is required.

Build mode:
- SingleFileBridge=$SingleFileBridge
"@ | Set-Content -Path (Join-Path $publishDir "README.txt") -Encoding UTF8

Write-Host "Portable package written to: $publishDir"
Write-Host "Distribute the whole ZMDStoreDesktop folder."
