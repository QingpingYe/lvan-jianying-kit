# 给本仓库的 .ps1 补 UTF-8 BOM（Windows PowerShell 5.1 需要 BOM 才正确识别中文）。
# 重复执行安全。用法：powershell -ExecutionPolicy Bypass -File scripts\fix_bom.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
$files = @(
    (Join-Path $root 'install.ps1'),
    (Join-Path $root 'scripts\launch_mcp.ps1'),
    (Join-Path $root 'scripts\validate_plugin.ps1'),
    (Join-Path $root 'scripts\fix_bom.ps1')
)
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file)) { continue }
    $text = [System.IO.File]::ReadAllText($file)   # 无 BOM 时按 UTF-8 读取，中文不会丢
    [System.IO.File]::WriteAllText($file, $text, $utf8Bom)
    Write-Host "BOM 已写入：$file" -ForegroundColor Green
}
