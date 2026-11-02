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
foreach ($script in @('install.ps1', 'scripts\launch_mcp.ps1', 'scripts\validate_plugin.ps1')) {
    $path = Join-Path $root $script
    if (Test-Path -LiteralPath $path) { Check-PsSyntax $path } else { Write-Host "MISS  PS    $script" -ForegroundColor Red; $fail++ }
}
foreach ($skill in @('setup', 'analyze-video', 'video-pipeline')) {
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
