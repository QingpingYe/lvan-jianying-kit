# 剪映 MCP 启动器：解析运行时与剪映路径，然后把 stdio 交给 MCP 服务器。
# 这个脚本会被 Codex 作为 MCP server 进程直接拉起，不要在里面等待用户输入。
param([string]$Module = 'jianying_tool.mcp_server')

$ErrorActionPreference = 'Stop'

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'

$pluginRoot = Split-Path -Parent $PSScriptRoot
# 插件自带的模块（mcp_ext）在插件根目录下，靠 PYTHONPATH 才找得到
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$pluginRoot;$env:PYTHONPATH" } else { $pluginRoot }

function Get-JianyingKitRuntime {
    param([string]$PluginRoot)
    $candidates = @()
    if ($env:JIANYING_KIT_HOME) { $candidates += $env:JIANYING_KIT_HOME }
    $candidates += (Join-Path $PluginRoot '.jianying-kit')
    if ($env:USERPROFILE) { $candidates += (Join-Path $env:USERPROFILE '.jianying-kit') }
    foreach ($dir in $candidates) {
        $py = Join-Path $dir '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $py) { return $dir }
    }
    if ($env:JIANYING_KIT_PYTHON -and (Test-Path -LiteralPath $env:JIANYING_KIT_PYTHON)) {
        return (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $env:JIANYING_KIT_PYTHON)))
    }
    return $null
}

function Import-JianyingPaths {
    param([string]$RuntimeDir, [string]$PluginRoot)
    $files = @(
        (Join-Path $RuntimeDir 'jianying-paths.json'),
        (Join-Path $PluginRoot 'jianying-paths.json')
    )
    foreach ($file in $files) {
        if (-not (Test-Path -LiteralPath $file)) { continue }
        try { $paths = [System.IO.File]::ReadAllText($file) | ConvertFrom-Json } catch { continue }
        if (-not $paths) { continue }
        foreach ($name in @('JIANYING_INSTALL_ROOT', 'JY_INSTALL_DIR', 'JIANYING_DRAFT_ROOT')) {
            $value = $paths.$name
            if ($value -and -not (Test-Path "Env:$name")) { Set-Item -Path "Env:$name" -Value $value }
        }
        return
    }
}

$runtimeDir = Get-JianyingKitRuntime -PluginRoot $pluginRoot
if ($runtimeDir) {
    Import-JianyingPaths -RuntimeDir $runtimeDir -PluginRoot $pluginRoot
    $python = Join-Path $runtimeDir '.venv\Scripts\python.exe'
} elseif ($env:JIANYING_KIT_PYTHON) {
    $python = $env:JIANYING_KIT_PYTHON
} else {
    Write-Error @"
[jianying-kit] Runtime is not ready: no .venv found.
Run the plugin setup skill in Codex, or do it manually:
  powershell -ExecutionPolicy Bypass -File "$pluginRoot\install.ps1" -RuntimeRoot "$env:USERPROFILE\.jianying-kit"
"@
    exit 127
}

$probe = & $python -c "import importlib; importlib.import_module('$Module')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error @"
MCP module is not importable with this interpreter: $Module ($python)
Run the plugin setup skill to reinstall dependencies, or do it manually:
  powershell -ExecutionPolicy Bypass -File "$pluginRoot\install.ps1" -RuntimeRoot "$runtimeDir"
Last error: $probe
"@
    exit 1
}

& $python -m $Module
exit $LASTEXITCODE
