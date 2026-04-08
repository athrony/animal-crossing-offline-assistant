param(
    [string]$PythonVersion = "3.11",
    [string]$CsvPath = "items.csv"
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

Invoke-Step -Label "Build SQLite database" -Command @(
    "py",
    "-$PythonVersion",
    $dbBuilderPath,
    "--csv",
    $CsvPath,
    "--output-dir",
    $dataPath
)

if (Test-Path $distPath) {
    Remove-Item -LiteralPath $distPath -Recurse -Force
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
        Remove-Item -LiteralPath $distDataPath -Recurse -Force
    }
    Copy-Item -LiteralPath $dataPath -Destination $distDataPath -Recurse
}

Write-Host ""
Write-Host "Build complete: $exePath"
if (Test-Path $dataPath) {
    Write-Host "Bundled data copied to: $(Join-Path $distPath 'data')"
}
