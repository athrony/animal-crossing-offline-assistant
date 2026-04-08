param(
    [string]$PythonVersion = "3.11"
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
        throw "步骤失败：$Label (exit code: $LASTEXITCODE)"
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appPath = Join-Path $scriptRoot "app.py"
$distPath = Join-Path $scriptRoot "dist"
$buildPath = Join-Path $scriptRoot "build"
$cachePath = Join-Path $scriptRoot "offline_cache"

if (-not (Test-Path $appPath)) {
    throw "找不到 app.py：$appPath"
}

Invoke-Step -Label "检查 Python" -Command @("py", "-$PythonVersion", "--version")

try {
    Invoke-Step -Label "检查 PyInstaller" -Command @("py", "-$PythonVersion", "-m", "PyInstaller", "--version")
}
catch {
    Invoke-Step -Label "安装 PyInstaller" -Command @("py", "-$PythonVersion", "-m", "pip", "install", "-r", (Join-Path $scriptRoot "requirements-build.txt"))
}

if (Test-Path $distPath) {
    Remove-Item -LiteralPath $distPath -Recurse -Force
}

if (Test-Path $buildPath) {
    Remove-Item -LiteralPath $buildPath -Recurse -Force
}

Invoke-Step -Label "开始打包 EXE" -Command @(
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
    throw "打包完成后没有找到 EXE：$exePath"
}

if (Test-Path $cachePath) {
    $distCachePath = Join-Path $distPath "offline_cache"
    if (Test-Path $distCachePath) {
        Remove-Item -LiteralPath $distCachePath -Recurse -Force
    }
    Copy-Item -LiteralPath $cachePath -Destination $distCachePath -Recurse
}

Write-Host ""
Write-Host "打包成功：$exePath"
if (Test-Path $cachePath) {
    Write-Host "已复制离线缓存：$(Join-Path $distPath 'offline_cache')"
}
