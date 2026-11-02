# 插件结构与脚本自检（只读，不写任何文件）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\validate_plugin.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$fail = 0

function Check-Json {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host "MISS  $Path" -ForegroundColor Red
        $script:fail++
        return
    }
    try {
        Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json | Out-Null
        Write-Host "OK    JSON  $Path" -ForegroundColor Green
    } catch {
        Write-Host "FAIL  JSON  $Path : $($_.Exception.Message)" -ForegroundColor Red
        $script:fail++
    }
}

function Check-PsSyntax {
    param([string]$Path)
    $errors = $null
    $ast = $null
    [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$ast, [ref]$errors) | Out-Null
    if ($errors -and $errors.Count) {
        Write-Host "FAIL  PS    $Path" -ForegroundColor Red
        $errors | Select-Object -First 5 | ForEach-Object { Write-Host "      $($_.Message) (line $($_.Extent.StartLineNumber))" -ForegroundColor Red }
        $script:fail++
    } else {
        Write-Host "OK    PS    $Path" -ForegroundColor Green
    }
}

Write-Host "插件自检：$root"
Check-Json (Join-Path $root '.codex-plugin\plugin.json')
Check-Json (Join-Path $root '.mcp.json')
Check-Json (Join-Path $root '.agents\plugins\marketplace.json')
Check-Json (Join-Path $root 'examples\shotlist.example.json')
Check-Json (Join-Path $root 'examples\templatefill.example.json')
Check-Json (Join-Path $root 'examples\roughcut-grouping.example.json')
Check-Json (Join-Path $root 'examples\roughcut.example.json')

# 模板批量出片的文件与 MCP 接线
foreach ($file in @('tools\jy_template.py', 'tools\template_fill.py', 'tools\jy_roughcut.py', 'tools\roughcut.py', 'mcp_ext\server.py')) {
    $path = Join-Path $root $file
    if (Test-Path -LiteralPath $path) { Write-Host "OK    FILE  $file" -ForegroundColor Green }
    else { Write-Host "MISS  FILE  $file" -ForegroundColor Red; $fail++ }
}

try {
    $mcp = Get-Content -LiteralPath (Join-Path $root '.mcp.json') -Raw | ConvertFrom-Json
    $ext = $mcp.mcpServers.'jianying-ext'
    if (-not $ext) {
        Write-Host "FAIL  MCP   缺少 jianying-ext 服务器条目" -ForegroundColor Red
        $fail++
    } elseif ($ext.args -notcontains 'mcp_ext.server') {
        Write-Host "FAIL  MCP   jianying-ext 没有指向 mcp_ext.server" -ForegroundColor Red
        $fail++
    } else {
        Write-Host "OK    MCP   jianying-ext -> mcp_ext.server" -ForegroundColor Green
    }
} catch {
    Write-Host "FAIL  MCP   读取 .mcp.json 失败：$($_.Exception.Message)" -ForegroundColor Red
    $fail++
}

foreach ($script in @('install.ps1', 'scripts\launch_mcp.ps1', 'scripts\validate_plugin.ps1')) {
    $path = Join-Path $root $script
    if (Test-Path -LiteralPath $path) { Check-PsSyntax $path } else { Write-Host "MISS  PS    $script" -ForegroundColor Red; $fail++ }
}
foreach ($skill in @('setup', 'analyze-video', 'video-pipeline', 'template-batch', 'rough-cut')) {
    $file = Join-Path $root "skills\$skill\SKILL.md"
    if (Test-Path -LiteralPath $file) { Write-Host "OK    SKILL $skill" -ForegroundColor Green }
    else { Write-Host "MISS  SKILL $skill" -ForegroundColor Red; $fail++ }
}

if ($fail) {
    Write-Host "自检未通过：$fail 项" -ForegroundColor Red
    exit 1
}
Write-Host "自检通过。" -ForegroundColor Green
exit 0
