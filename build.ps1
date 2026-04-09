param(
    [string]$PythonVersion = "3.11",
    [string]$CsvPath = (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "items.csv"),
    [string]$NhseRoot = (Join-Path ([Environment]::GetFolderPath("MyDocuments")) ".tmp_nhse")
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

function Invoke-Step {
    param(
        [string]$Label,
        [string[]]$Command
    )

    Write-Host "==> $Label"
    $commandName = $Command[0]
    $commandArgs = @()
    if ($Command.Length -gt 1) {
        $commandArgs = $Command[1..($Command.Length - 1)]
    }

    & $commandName @commandArgs

    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Label (exit code: $LASTEXITCODE)"
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appPath = Join-Path $scriptRoot "app.py"
$dbBuilderPath = Join-Path $scriptRoot "build_database.py"
$distPath = Join-Path $scriptRoot "dist"
$buildPath = Join-Path $scriptRoot "build"
$dataPath = Join-Path $scriptRoot "data"
$seedDbPath = Join-Path $scriptRoot "legacy_seed.db"

if (-not (Test-Path $appPath)) {
    throw "Missing app.py: $appPath"
}

if (-not (Test-Path $dbBuilderPath)) {
    throw "Missing build_database.py: $dbBuilderPath"
}

Invoke-Step -Label "Check Python" -Command @("py", "-$PythonVersion", "--version")

try {
    Invoke-Step -Label "Check PyInstaller" -Command @("py", "-$PythonVersion", "-m", "PyInstaller", "--version")
}
catch {
    Invoke-Step -Label "Install PyInstaller" -Command @("py", "-$PythonVersion", "-m", "pip", "install", "-r", (Join-Path $scriptRoot "requirements-build.txt"))
}

$dbCommand = @(
    "py",
    "-$PythonVersion",
    $dbBuilderPath,
    "--csv",
    $CsvPath,
    "--nhse-root",
    $NhseRoot,
    "--output-dir",
    $dataPath
)
if (Test-Path $seedDbPath) {
    $dbCommand += @("--seed-db", $seedDbPath)
}
Invoke-Step -Label "Build SQLite database" -Command $dbCommand

$databasePath = Join-Path $dataPath "animal_crossing_offline.db"
$env:ACA_DATA_PATH = $dataPath
$env:ACA_DATABASE_PATH = $databasePath
$env:ACA_MIRROR_HINT = Join-Path $HOME "Documents\.tmp_pattern_dump_index"
$patternBootstrap = @"
import os
from pathlib import Path
from tool_support import sync_pattern_mirror
from pattern_support import PatternRepository

base = Path(os.environ["ACA_DATA_PATH"])
mirror = sync_pattern_mirror(base, Path(os.environ["ACA_MIRROR_HINT"]))
repo = PatternRepository(Path(os.environ["ACA_DATABASE_PATH"]))

try:
    print(repo.refresh_site_index())
except Exception as ex:
    print("skip_remote_index:", ex)

print(repo.refresh_local_mirror_index(mirror))
"@
$patternBootstrapPath = Join-Path $scriptRoot ".tmp_preload_patterns.py"
Set-Content -LiteralPath $patternBootstrapPath -Value $patternBootstrap -Encoding UTF8
Invoke-Step -Label "Preload pattern index and local mirror" -Command @(
    "py",
    "-$PythonVersion",
    $patternBootstrapPath
)
if (Test-Path $patternBootstrapPath) {
    Remove-Item -LiteralPath $patternBootstrapPath -Force
}

if (Test-Path $distPath) {
    Get-ChildItem -LiteralPath $distPath -Force | Remove-Item -Recurse -Force
}

if (Test-Path $buildPath) {
    Remove-Item -LiteralPath $buildPath -Recurse -Force
}

Invoke-Step -Label "Package EXE" -Command @(
    "py",
    "-$PythonVersion",
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name",
    "ItemsBilingualViewer",
    "--distpath",
    $distPath,
    "--workpath",
    $buildPath,
    "--specpath",
    $scriptRoot,
    $appPath
)

$exePath = Join-Path $distPath "ItemsBilingualViewer.exe"
if (-not (Test-Path $exePath)) {
    throw "EXE not found after build: $exePath"
}

if (Test-Path $dataPath) {
    $distDataPath = Join-Path $distPath "data"
    if (Test-Path $distDataPath) {
        Get-ChildItem -LiteralPath $distDataPath -Force | Remove-Item -Recurse -Force
    }
    else {
        New-Item -ItemType Directory -Path $distDataPath -Force | Out-Null
    }
    Get-ChildItem -LiteralPath $dataPath -Force | Copy-Item -Destination $distDataPath -Recurse -Force
}

Write-Host ""
Write-Host "Build complete: $exePath"
if (Test-Path $dataPath) {
    Write-Host "Bundled data copied to: $(Join-Path $distPath 'data')"
}
