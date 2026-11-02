#Requires -Version 5.1
<#
剪映自动化工具包 · 一键安装

做四件事：
  1) 建独立 Python 环境（.venv）并安装 jianying-tool（含 TTS / 语音识别可选依赖）
  2) 自检：确认能识别本机剪映与草稿目录
  3) 项目级安装：写 .codex/config.toml（MCP 服务器）+ 技能到 .agents/skills 与 .codex/skills
  4) 全局安装（尽力而为）：同样的配置写进 %USERPROFILE%\.codex，让所有项目都能用

插件模式（-PluginMode）下第 3、4 步由 Codex 插件宿主完成，本脚本只准备运行时：
运行时目录（默认 %USERPROFILE%\.jianying-kit）里放 .venv、jianying-paths.json 与 runtime.json。

用法：
  powershell -ExecutionPolicy Bypass -File install.ps1              # 完整安装
  powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun      # 只显示会做什么
  powershell -ExecutionPolicy Bypass -File install.ps1 -SkipPip     # 跳过依赖安装（环境已就绪）
  powershell -ExecutionPolicy Bypass -File install.ps1 -NoUserScope # 只装项目级，不写 ~/.codex
  powershell -ExecutionPolicy Bypass -File install.ps1 -PythonExe "C:\Python313\python.exe"
  powershell -ExecutionPolicy Bypass -File install.ps1 -Portable    # 强制用工具包内可移植 Python
  powershell -ExecutionPolicy Bypass -File install.ps1 -PathsOnly   # 只重新探测剪映路径并更新配置（剪映重装后用）
  powershell -ExecutionPolicy Bypass -File install.ps1 -PluginMode  # Codex 插件模式：环境装到用户目录，不写宿主配置
#>
param(
    [switch]$DryRun,
    [switch]$SkipPip,
    [switch]$NoUserScope,
    [string]$PythonExe,
    [switch]$Portable,
    [switch]$PathsOnly,
    [string]$JianyingBase,
    [string]$DraftRoot,
    [switch]$PluginMode,
    [string]$RuntimeRoot
)

$ErrorActionPreference = 'Stop'
$kit = $PSScriptRoot

# ---------- 运行目录 ----------
# 独立模式：环境与路径文件都放在工具包目录里（老行为）。
# 插件模式（-PluginMode）：插件目录只放代码，环境落到用户级目录，
#   默认 %USERPROFILE%\.jianying-kit（可用 JIANYING_KIT_HOME 或 -RuntimeRoot 覆盖）。
$runtimeDir = $kit
if ($PluginMode) {
    $runtimeDir = if ($RuntimeRoot) {
        $RuntimeRoot
    } elseif ($env:JIANYING_KIT_HOME) {
        $env:JIANYING_KIT_HOME
    } else {
        Join-Path $env:USERPROFILE '.jianying-kit'
    }
} elseif ($RuntimeRoot) {
    throw "-RuntimeRoot 只在 -PluginMode 下有意义"
}

$venvDir = Join-Path $runtimeDir '.venv'
$venvPy = Join-Path $venvDir 'Scripts\python.exe'
$portableDir = Join-Path $runtimeDir '.runtime\python'
$portablePy = Join-Path $portableDir 'tools\python.exe'
$pipLogPath = Join-Path $kit 'install-pip.log'
$pathsJson = Join-Path $runtimeDir 'jianying-paths.json'
$runtimeJson = Join-Path $runtimeDir 'runtime.json'
$userBase = $env:USERPROFILE
$Script:McpEnv = @{}
$Script:DetectedPaths = @{}
$Script:PluginMode = [bool]$PluginMode

# 统一 UTF-8 输出，避免中文草稿名 / 中文路径在控制台乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'

# PowerShell 5.1 默认可能只启用 TLS 1.0，导致 nuget.org / python.org 下载直接断连。
# 显式启用 TLS 1.2（新系统上再加 TLS 1.3，失败忽略）。
try {
    $tls = [Net.SecurityProtocolType]::Tls12
    try { $tls = $tls -bor [Net.SecurityProtocolType]::Tls13 } catch { }
    [Net.ServicePointManager]::SecurityProtocol = $tls
} catch { }

function Write-Step($n, $text) { Write-Host "[$n] $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "    !!  $text" -ForegroundColor Yellow }

# 支持的 Python 版本窗口。
# 依据：依赖里的 ctranslate2（cp39–cp314）与 onnxruntime（cp311–cp314）目前没有 3.15 的预编译包，
# 用 3.15 会在 pip 阶段失败，所以上限锁在 3.14；下限锁在 3.11（onnxruntime 不再提供 cp310 轮子）。
$Script:VersionMinMM = 311
$Script:VersionMaxMM = 314

function Test-VersionSupported {
    param([string]$Version)
    if ($Version -notmatch '^(\d+)\.(\d+)$') { return $false }
    $mm = [int]$Matches[1] * 100 + [int]$Matches[2]
    return ($mm -ge $Script:VersionMinMM -and $mm -le $Script:VersionMaxMM)
}

# Windows 的「应用执行别名」：%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe 是个 0 字节存根，
# 运行它会拉起 Microsoft Store。必须跳过，否则会误判成"找到了 Python"。
function Test-AliasStub {
    param([string]$Exe)
    if (-not $Exe) { return $false }
    $wa = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'
    return $Exe.StartsWith($wa, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-PythonVersion {
    param([string]$Exe, [string[]]$Args = @())
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = (& $Exe @Args -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Out-String).Trim()
    } catch { $out = '' } finally { $ErrorActionPreference = $prev }
    if ($out -and $out -match '^\d+\.\d+$') { return $out }
    return $null
}

# 在多个位置找 Python 3.11–3.14（PATH 没刷新也能找到，比如刚装完）
# 找到但版本不受支持时记进 $Script:UnsupportedPython，供调用方给出明确提示
function Find-Python {
    $cands = @(
        @{ Cmd = 'py';     Args = @('-3.13') },
        @{ Cmd = 'py';     Args = @('-3.12') },
        @{ Cmd = 'py';     Args = @('-3.14') },
        @{ Cmd = 'py';     Args = @('-3.11') },
        @{ Cmd = 'py';     Args = @('-3') },
        @{ Cmd = 'python'; Args = @() }
    )
    foreach ($c in $cands) {
        $exe = Get-Command $c.Cmd -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        if (Test-AliasStub $exe.Source) {
            if (-not $Script:AliasWarned) {
                $Script:AliasWarned = $true
                Write-Warn2 "跳过 Windows 应用执行别名（不是真的 Python）：$($exe.Source)"
            }
            continue
        }
        $ver = Get-PythonVersion -Exe $exe.Source -Args $c.Args
        if (-not $ver) { continue }
        if (Test-VersionSupported $ver) { return @{ Exe = $exe.Source; Args = $c.Args; Version = $ver } }
        if (-not $Script:UnsupportedPython) { $Script:UnsupportedPython = @{ Version = $ver; Exe = $exe.Source } }
    }
    $globs = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'),
        'C:\Python3*\python.exe',
        "$env:ProgramFiles\Python3*\python.exe"
    )
    foreach ($g in $globs) {
        foreach ($f in @(Get-ChildItem -Path $g -ErrorAction SilentlyContinue | Sort-Object FullName -Descending)) {
            if (Test-AliasStub $f.FullName) { continue }
            $ver = Get-PythonVersion -Exe $f.FullName -Args @()
            if (-not $ver) { continue }
            if (Test-VersionSupported $ver) { return @{ Exe = $f.FullName; Args = @(); Version = $ver } }
            if (-not $Script:UnsupportedPython) { $Script:UnsupportedPython = @{ Version = $ver; Exe = $f.FullName } }
        }
    }
    # 注册表（官方安装包会登记，PATH 没刷新也能找到）
    foreach ($hive in @('HKCU:\Software\Python\PythonCore', 'HKLM:\SOFTWARE\Python\PythonCore')) {
        foreach ($key in @(Get-ChildItem -Path $hive -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending)) {
            $installPath = (Get-ItemProperty -Path (Join-Path $key.PSPath 'InstallPath') -ErrorAction SilentlyContinue).'(default)'
            if (-not $installPath) { continue }
            $exe = Join-Path $installPath 'python.exe'
            if (-not (Test-Path -LiteralPath $exe)) { continue }
            $ver = Get-PythonVersion -Exe $exe -Args @()
            if (-not $ver) { continue }
            if (Test-VersionSupported $ver) { return @{ Exe = $exe; Args = @(); Version = $ver } }
            if (-not $Script:UnsupportedPython) { $Script:UnsupportedPython = @{ Version = $ver; Exe = $exe } }
        }
    }
    return $null
}

# 把 NuGet 的 python 包（本质是 zip）解压成可移植运行时
function Expand-PortableRuntime {
    param([string]$Package)
    try {
        if (Test-Path -LiteralPath $portableDir) { Remove-Item -LiteralPath $portableDir -Recurse -Force }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Package, $portableDir)
    } catch {
        Write-Warn2 "解压失败：$($_.Exception.Message)"
        return $false
    }
    return (Test-Path -LiteralPath $portablePy)
}

# 可移植运行时：从 NuGet 取官方 Python 包，解压到工具包内。
# 不写注册表、不写 %LOCALAPPDATA%、不需要管理员，受限环境（沙盒/企业策略）下也能用。
# 支持离线安装：把 python*.nupkg 放进 <工具包>\.runtime\ 即可跳过下载。
function Install-PortablePython {
    $runtimeRoot = Join-Path $kit '.runtime'
    $localPkg = Get-ChildItem -LiteralPath $runtimeRoot -Filter 'python*.nupkg' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    if ($localPkg) {
        Write-Warn2 "发现本地 Python 包：$($localPkg.Name) —— 离线解压，不联网"
        if (Expand-PortableRuntime -Package $localPkg.FullName) { return $true }
    }
    $tmp = Join-Path $env:TEMP 'python-portable-kit.nupkg'
    foreach ($v in @('3.13.16', '3.13.15', '3.12.10')) {
        $url = "https://api.nuget.org/v3-flatcontainer/python/$v/python.$v.nupkg"
        Write-Warn2 "下载可移植 Python $v（约 14MB）…"
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 600 -ErrorAction Stop
        } catch {
            $ErrorActionPreference = $prev
            Write-Warn2 "下载失败：$($_.Exception.Message)"
            continue
        }
        $ErrorActionPreference = $prev
        if (-not (Test-Path -LiteralPath $tmp) -or (Get-Item -LiteralPath $tmp).Length -lt 5MB) { continue }
        if (Expand-PortableRuntime -Package $tmp) { return $true }
    }
    return $false
}

# 兜底：winget 安装（按用户），再退到 python.org 官方安装包静默安装。
# 注意：官方安装包即使指定 TargetDir，也会写 %LOCALAPPDATA%\Package Cache 与注册表，
# 受限环境下会返回 0x5（拒绝访问）——所以它只是最后的兜底。
function Install-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        foreach ($id in @('Python.Python.3.13', 'Python.Python.3.12')) {
            Write-Warn2 "尝试用 winget 安装 $id（按用户，无需管理员）…"
            $prev = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                & $winget.Source install -e --id $id --scope user --silent --accept-package-agreements --accept-source-agreements 2>$null | Out-Null
            } catch { }
            $ErrorActionPreference = $prev
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
            if (Find-Python) { return $true }   # 以「真的能找到 Python」为准，不看退出码
            Write-Warn2 "winget 之后仍未找到 Python，换下一个来源"
        }
    } else {
        Write-Warn2 "本机没有 winget，改用 python.org 官方安装包"
    }

    $tmp = Join-Path $env:TEMP 'python-setup-kit.exe'
    foreach ($v in @('3.14.8', '3.14.7', '3.13.15', '3.13.14', '3.13.12', '3.12.10', '3.12.8')) {
        $url = "https://www.python.org/ftp/python/$v/python-$v-amd64.exe"
        Write-Warn2 "尝试下载 Python $v …"
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 300 -ErrorAction Stop
        } catch {
            $ErrorActionPreference = $prev
            continue
        }
        $ErrorActionPreference = $prev
        if (-not (Test-Path -LiteralPath $tmp) -or (Get-Item -LiteralPath $tmp).Length -lt 5MB) { continue }
        Write-Warn2 "下载完成，开始静默安装…"
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $exeExit = $null
        try {
            & $tmp /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_pip=1 2>$null | Out-Null
            $exeExit = $LASTEXITCODE
        } catch { }
        $ErrorActionPreference = $prev
        if ($exeExit -eq 5 -or $exeExit -eq 0x5) {
            Write-Warn2 "官方安装包返回 0x5（拒绝访问）：它即使指定了目标目录，仍要写 %LOCALAPPDATA%\Package Cache 与注册表。"
            Write-Warn2 "受限环境请优先用可移植运行时，或手动安装 Python 后加 -PythonExe 指定。"
        }
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
        if (Find-Python) { return $true }
        Write-Warn2 "该版本装完仍未找到 Python，继续尝试下一个版本"
    }
    return $false
}

# ---------- 剪映路径探测（支持非默认安装位置）----------
# 默认布局：%LOCALAPPDATA%\JianyingPro\Apps\<版本>\videoeditor.dll
# 自定义安装（比如装到 D:\JianyingPro）常见两种：
#   D:\JianyingPro\Apps\<版本>\videoeditor.dll
#   D:\JianyingPro\<版本>\videoeditor.dll      ← 实测存在的布局
function Get-NewestCodecDir {
    param($Dlls)
    $best = $null; $bestVer = $null
    foreach ($d in @($Dlls)) {
        $name = Split-Path $d.DirectoryName -Leaf
        $v = $null
        try { $v = [version]$name } catch { }
        if ($v) {
            if (-not $bestVer -or $v -gt $bestVer) { $best = $d; $bestVer = $v }
        } elseif (-not $best) { $best = $d }
    }
    if ($best) { return $best.DirectoryName }
    return $null
}

function Find-JianyingInstall {
    param([string]$Explicit)
    $cands = @()
    if ($Explicit) { $cands += $Explicit }
    if ($env:JIANYING_INSTALL_ROOT) { $cands += $env:JIANYING_INSTALL_ROOT }
    if ($env:LOCALAPPDATA) {
        $cands += (Join-Path $env:LOCALAPPDATA 'JianyingPro')
        $cands += (Join-Path $env:LOCALAPPDATA 'JianyingPro\Apps')
    }
    foreach ($drv in @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        if (-not $drv.Root -or $drv.Root.Length -lt 2) { continue }
        foreach ($sub in @('JianyingPro', 'Program Files\JianyingPro', 'Program Files (x86)\JianyingPro')) {
            $cands += (Join-Path $drv.Root $sub)
        }
    }
    foreach ($c in $cands) {
        if (-not $c -or -not (Test-Path -LiteralPath $c)) { continue }
        $apps = Join-Path $c 'Apps'
        if (Test-Path -LiteralPath $apps) {
            $dlls = @(Get-ChildItem -Path (Join-Path $apps '*\videoeditor.dll') -ErrorAction SilentlyContinue)
            if ($dlls.Count) {
                $codec = Get-NewestCodecDir $dlls
                if ($codec) { return [pscustomobject]@{ InstallRoot = $apps; CodecDir = $codec } }
            }
        }
        $dlls = @(Get-ChildItem -Path (Join-Path $c '*\videoeditor.dll') -ErrorAction SilentlyContinue)
        if ($dlls.Count) {
            $codec = Get-NewestCodecDir $dlls
            if ($codec) { return [pscustomobject]@{ InstallRoot = $c; CodecDir = $codec } }
        }
        if (Test-Path -LiteralPath (Join-Path $c 'videoeditor.dll')) {
            return [pscustomobject]@{ InstallRoot = $c; CodecDir = $c }
        }
    }
    return $null
}

# 草稿目录：优先读剪映自己的配置（用户在「全局设置」里改过草稿位置也能读到）
function Find-JianyingDraftRoot {
    param([string]$Explicit)
    if ($Explicit) { return $Explicit }
    if ($env:JIANYING_DRAFT_ROOT) { return $env:JIANYING_DRAFT_ROOT }
    $default = Join-Path $env:LOCALAPPDATA 'JianyingPro\User Data\Projects\com.lveditor.draft'
    $cfg = Join-Path $env:LOCALAPPDATA 'JianyingPro\User Data\Config\globalSetting'
    if (Test-Path -LiteralPath $cfg) {
        $m = Select-String -LiteralPath $cfg -Pattern '^currentCustomDraftPath=(.+)$' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($m) {
            $p = ($m.Matches[0].Groups[1].Value.Trim() -replace '\\\\', '\')
            if ($p -and (Test-Path -LiteralPath $p)) { return $p }
        }
    }
    return $default
}

Write-Host "== 剪映自动化工具包 · 安装器 ==" -ForegroundColor Cyan
Write-Host "工具包目录 : $kit"
if ($PluginMode) { Write-Host "运行目录   : $runtimeDir（插件模式）" -ForegroundColor DarkGray }
Write-Host "虚拟环境   : $venvDir"
if ($DryRun) { Write-Host "模式       : DryRun（不改动任何文件）" -ForegroundColor Yellow }
Write-Host ""

# 安装位置体检：只提醒，不阻止（推荐放 D 盘等非系统盘的短路径，但任何路径都能用）
if ($kit -match 'OneDrive|Dropbox|Nutstore|坚果云') {
    Write-Warn2 "工具包位于云同步目录：同步客户端会锁文件，可能导致环境写入失败。建议移到本地磁盘，例如 D:\jianying-kit"
}
if ($kit -match '[&%!#]') {
    Write-Warn2 "路径含 & % ! # 等特殊字符，个别命令组合可能出错。建议只用中英文、数字、下划线、短横线"
}
if ($kit.Length -ge 120) {
    Write-Warn2 "路径较长（$($kit.Length) 字符）：Windows 有 260 字符上限，建议放到更浅的目录，例如 D:\jianying-kit"
}
if ($kit -match '\\Downloads\\|\\Desktop\\|\\下载\\|\\桌面\\') {
    Write-Warn2 "工具包在下载 / 桌面目录里，容易被清理。建议移到稳定位置，例如 D:\jianying-kit"
}

if (-not $PathsOnly) {
# ---------- 1) Python 环境 ----------
Write-Step '1/4' '准备 Python 环境'

# ① 已有可用的 .venv 直接复用 —— 不再要求本机必须能找到系统 Python
$venvReady = $false
if (Test-Path -LiteralPath $venvPy) {
    $venvVer = Get-PythonVersion -Exe $venvPy -Args @()
    if (-not $venvVer) {
        Write-Warn2 "已有 .venv 但解释器跑不起来，将重建：$venvDir"
    } elseif (-not (Test-VersionSupported $venvVer)) {
        Write-Warn2 "已有 .venv 用的是 Python $venvVer，超出支持范围（3.11–3.14）。"
        throw "请先删除这个目录再重跑本脚本：$venvDir（删掉后脚本会自动重建）"
    } else {
        $venvReady = $true
        Write-Ok "已有可用虚拟环境：Python $venvVer —— 跳过 Python 查找与安装"
    }
}

if (-not $venvReady) {
    # ② 选解释器：-PythonExe > 系统 Python > 工具包内可移植运行时 > 自动准备
    $baseExe = $null
    $baseArgs = @()
    $baseVer = $null

    if ($PythonExe) {
        $v = Get-PythonVersion -Exe $PythonExe -Args @()
        if (-not $v) { throw "-PythonExe 指定的解释器跑不起来：$PythonExe" }
        if (-not (Test-VersionSupported $v)) { throw "-PythonExe 指定的 Python $v 超出支持范围（3.11–3.14）" }
        $baseExe = $PythonExe; $baseVer = $v
        Write-Ok "使用 -PythonExe 指定的 Python $v"
    }

    if (-not $baseExe -and -not $Portable) {
        $sys = Find-Python
        if ($sys) {
            $baseExe = $sys.Exe; $baseArgs = $sys.Args; $baseVer = $sys.Version
            Write-Ok "找到系统 Python $($sys.Version)（$($sys.Exe)）"
        }
    }

    if (-not $baseExe -and (Test-Path -LiteralPath $portablePy)) {
        $v = Get-PythonVersion -Exe $portablePy -Args @()
        if ($v -and (Test-VersionSupported $v)) {
            $baseExe = $portablePy; $baseVer = $v
            Write-Ok "使用工具包内置的可移植 Python $v"
        }
    }

    if (-not $baseExe) {
        if ($Script:UnsupportedPython) {
            Write-Warn2 "本机只有 Python $($Script:UnsupportedPython.Version)（$($Script:UnsupportedPython.Exe)），依赖暂不支持该版本 ——"
            Write-Warn2 "ctranslate2 / onnxruntime 目前只提供 3.11–3.14 的预编译包。会另装一个受支持的版本，不动你现有的 Python。"
        }
        if ($DryRun) {
            Write-Warn2 "将自动准备可移植 Python（NuGet 包，解压到 $portableDir）"
            $baseExe = '<自动安装>'
        } else {
            Write-Warn2 "本机没有可用的 Python，开始自动准备…"
            Write-Warn2 "① 可移植运行时（NuGet 包，解压到工具包内；不写注册表、不写用户目录、不需要管理员）"
            $ok = Install-PortablePython
            if ($ok) {
                $baseExe = $portablePy
                $baseVer = Get-PythonVersion -Exe $portablePy -Args @()
                Write-Ok "可移植 Python 就绪：Python $baseVer"
            } else {
                Write-Warn2 "② 可移植运行时不可用，退回 winget / python.org 安装包（这一步会写 %LOCALAPPDATA% 与注册表）"
                Install-Python | Out-Null
                $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
                $sys = Find-Python
                if ($sys) { $baseExe = $sys.Exe; $baseArgs = $sys.Args; $baseVer = $sys.Version }
            }
        }
        if (-not $baseExe -and -not $DryRun) {
            Write-Warn2 "自动准备失败。可任选一种手动方式后重跑本脚本："
            Write-Warn2 "  a) 指定现成的解释器：install.ps1 -PythonExe `"C:\Python313\python.exe`""
            Write-Warn2 "  b) 自行安装 Python 3.13：https://www.python.org/downloads/windows/"
            throw "缺少可用的 Python（需要 3.11–3.14）"
        }
    }

    if ($DryRun) {
        Write-Warn2 "将创建虚拟环境：$venvDir"
    } else {
        Write-Warn2 "用 Python $baseVer 创建虚拟环境…"
        & $baseExe @baseArgs -m venv $venvDir
        if (-not (Test-Path -LiteralPath $venvPy)) { throw "创建虚拟环境失败：$venvDir" }
        Write-Ok "已创建虚拟环境：$venvDir"
    }
}

# ---------- 2) 安装依赖 ----------
Write-Step '2/4' '安装 jianying-tool（含 TTS / 语音识别）'
if ($SkipPip) {
    Write-Warn2 "按 -SkipPip 跳过依赖安装"
} elseif ($DryRun) {
    Write-Warn2 "将执行：pip install --upgrade pip"
    Write-Warn2 '将执行：pip install "jianying-tool[tts,asr]"'
} else {
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $venvPy -m pip install --upgrade pip 2>&1 | Out-Null
    $pipLog = (& $venvPy -m pip install "jianying-tool[tts,asr]" 2>&1 | Out-String)
    $pipExit = $LASTEXITCODE
    $ErrorActionPreference = $prevPref
    try {
        [System.IO.File]::WriteAllText($pipLogPath, $pipLog, (New-Object System.Text.UTF8Encoding($false)))
    } catch { }
    if ($pipExit -ne 0) {
        Write-Host "    ---- pip 输出（末尾 20 行；完整日志：$pipLogPath）----" -ForegroundColor DarkGray
        ($pipLog -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 20) |
            ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        if ($pipLog -match 'ResolutionImpossible|no matching distributions|conflicting dependencies|Could not find a version that satisfies|No matching distribution') {
            $vNow = Get-PythonVersion -Exe $venvPy -Args @()
            throw "依赖解析失败：Python $vNow 下没有可用的依赖发行版（常见原因是 Python 版本过新）。删掉 $venvDir 后重跑本脚本即可用受支持的版本重建。完整日志：$pipLogPath"
        }
        if ($pipLog -match 'CERTIFICATE_VERIFY_FAILED|SSLError|SSL: ') {
            throw "pip 证书校验失败（常见于公司代理或安全软件拦截 HTTPS）。请配置企业证书或换网络后重跑。完整日志：$pipLogPath"
        }
        if ($pipLog -match 'WinError 10013|Connection|timed out|Temporary failure in name resolution|Could not fetch URL') {
            throw "pip 连不上 PyPI（网络问题）。请确认能访问 pypi.org 后重跑本脚本。完整日志：$pipLogPath"
        }
        if ($pipLog -match 'Access is denied|Permission denied|WinError 5') {
            throw "pip 写入被拒绝（权限问题）。请检查 $venvDir 的写权限，或把工具包换个位置。完整日志：$pipLogPath"
        }
        throw "依赖安装失败（pip 退出码 $pipExit）。完整日志：$pipLogPath"
    }
    Write-Ok "依赖安装完成（日志：$pipLogPath）"
}

}  # end of: if (-not $PathsOnly)

# ---------- 3) 自检 ----------
if ($PathsOnly) {
    Write-Step '路径' '只重新探测剪映路径并更新配置'
} else {
    Write-Step '3/4' '自检：识别本机剪映'
}

$defaultInstallRoot = Join-Path $env:LOCALAPPDATA 'JianyingPro\Apps'
$defaultDraftRoot = Join-Path $env:LOCALAPPDATA 'JianyingPro\User Data\Projects\com.lveditor.draft'

# 自动探测剪映安装位置（默认路径 / 各盘常见位置 / 剪映自己配置的草稿目录）
$jy = Find-JianyingInstall -Explicit $JianyingBase
$jyDraft = Find-JianyingDraftRoot -Explicit $DraftRoot

if ($jy) {
    $env:JIANYING_INSTALL_ROOT = $jy.InstallRoot
    $env:JY_INSTALL_DIR = $jy.CodecDir
    Write-Ok "剪映安装根：$($jy.InstallRoot)"
    Write-Ok "编解码目录：$($jy.CodecDir)"
    if ($jy.InstallRoot -ne $defaultInstallRoot) {
        Write-Warn2 "剪映不在默认安装位置 —— 已自动识别，并会记录到配置里（工具以后都用这个路径）"
    }
} else {
    Write-Warn2 "没找到 videoeditor.dll（默认位置和各盘常见位置都找过了）"
}

if ($jyDraft) {
    Write-Ok "草稿目录  ：$jyDraft"
    if ($jyDraft -ne $defaultDraftRoot) {
        Write-Warn2 "草稿目录不是默认位置（可能你在剪映「全局设置」里改过）—— 已自动识别并记录"
    }
}

$paths = [ordered]@{}
if ($jy) { $paths['JIANYING_INSTALL_ROOT'] = $jy.InstallRoot; $paths['JY_INSTALL_DIR'] = $jy.CodecDir }
if ($jyDraft) { $paths['JIANYING_DRAFT_ROOT'] = $jyDraft }
$Script:DetectedPaths = $paths

# 非默认路径必须写进 MCP 配置，否则重启后 MCP 服务找不到剪映
if ($jy -and ($jy.InstallRoot -ne $defaultInstallRoot)) {
    $Script:McpEnv['JIANYING_INSTALL_ROOT'] = $jy.InstallRoot
    $Script:McpEnv['JY_INSTALL_DIR'] = $jy.CodecDir
}
if ($jyDraft -and ($jyDraft -ne $defaultDraftRoot)) {
    $Script:McpEnv['JIANYING_DRAFT_ROOT'] = $jyDraft
}

if (-not $DryRun) {
    if ($paths.Count) {
        try {
            [System.IO.File]::WriteAllText($pathsJson, ($paths | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
            Write-Ok "路径已记录：jianying-paths.json（build_draft.py / subtitle_tools.py 会自动读取）"
        } catch {
            Write-Warn2 "写 jianying-paths.json 失败：$($_.Exception.Message)"
        }
    }
}

if ($DryRun -or (-not (Test-Path -LiteralPath $venvPy))) {
    Write-Warn2 "跳过 probe 自检（依赖还没装好）"
} else {
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $probe = (& $venvPy -m jianying_tool.cli probe --json 2>$null | Out-String)
    $ErrorActionPreference = $prevPref
    $info = $null
    if ($probe.Trim()) {
        try { $info = $probe | ConvertFrom-Json } catch { $info = $null }
    }
    if ($info -and $info.installed_version) {
        Write-Ok "剪映版本：$($info.installed_version)"
        Write-Ok "编解码目录：$($info.codec_dir)"
        Write-Ok "草稿目录：$($info.draft_root)"
    } else {
        if (-not $jy) {
            Write-Warn2 "没识别到剪映。如果确实装了，用参数指定它的位置后重跑："
            Write-Warn2 "  install.ps1 -JianyingBase \"D:\JianyingPro\"（填含 videoeditor.dll 的那层目录或它的上级）"
        } else {
            Write-Warn2 "已按探测到的路径设置环境变量，但 probe 仍未通过；请把上面的输出发给 AI 排查。"
        }
    }
}

# ---------- 4) 写配置 + 装技能 ----------
function Install-Scope {
    param([string]$Root, [string]$Label, [switch]$Required, [switch]$EnvOnly)
    try {
        if ($Script:PluginMode) {
            Write-Ok "插件模式：宿主负责 MCP 服务器与技能注册，跳过 $Label 配置写入"
            return $true
        }
        $codexDir = Join-Path $Root '.codex'
        $configPath = Join-Path $codexDir 'config.toml'
        if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $codexDir | Out-Null }

        $content = if (Test-Path -LiteralPath $configPath) { [System.IO.File]::ReadAllText($configPath) } else { '' }

        # 剪映不在默认位置时，把路径作为环境变量写进 MCP 配置（否则 MCP 服务找不到剪映）
        $envToml = ''
        if ($Script:McpEnv.Count -gt 0) {
            $envToml = "[mcp_servers.jianying.env]`n"
            foreach ($key in $Script:McpEnv.Keys) {
                $envToml += "$key = '$($Script:McpEnv[$key])'`n"
            }
        }

        if ($content -match '(?m)^\[mcp_servers\.jianying\]') {
            # env 子表已存在 → 用新探测到的路径覆盖（剪映重装/换位置后必须能更新，不能只是跳过）
            $envPattern = '(?ms)^\[mcp_servers\.jianying\.env\]\r?\n.*?(?=^\[|\z)'
            if (-not $envToml) {
                Write-Ok "$Label 已有 MCP 配置（剪映在默认位置，无需路径变量）"
            } elseif ($content -match $envPattern) {
                if ($DryRun) {
                    Write-Warn2 "$Label 将更新剪映路径环境变量 -> $configPath"
                } else {
                    try {
                        $updated = [regex]::Replace($content, $envPattern, ($envToml.TrimEnd() + "`r`n"))
                        [System.IO.File]::WriteAllText($configPath, $updated, (New-Object System.Text.UTF8Encoding($false)))
                        Write-Ok "$Label 已更新剪映路径环境变量"
                    } catch {
                        Write-Warn2 "$Label 更新环境变量失败：$($_.Exception.Message)"
                    }
                }
            } else {
                if ($DryRun) {
                    Write-Warn2 "$Label 将补写剪映路径环境变量 -> $configPath"
                } else {
                    try {
                        [System.IO.File]::AppendAllText($configPath, "`n$envToml", (New-Object System.Text.UTF8Encoding($false)))
                        Write-Ok "$Label 已补写剪映路径环境变量（剪映不在默认位置）"
                    } catch {
                        Write-Warn2 "$Label 补写环境变量失败：$($_.Exception.Message)"
                    }
                }
            }
            if ($EnvOnly) { return $true }
        } else {
            if ($EnvOnly) {
                Write-Warn2 "$Label 配置里没有 [mcp_servers.jianying] 段，跳过（请先跑一次完整安装）"
                return $true
            }
            $block = @"

[mcp_servers.jianying]
command = '$venvPy'
args = ["-m", "jianying_tool.mcp_server"]
cwd = '$kit'
startup_timeout_sec = 120
$envToml
"@
            if ($DryRun) {
                Write-Warn2 "$Label 将写入 MCP 配置 -> $configPath"
            } else {
                if (Test-Path -LiteralPath $configPath) {
                    $backup = "$configPath.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
                    Copy-Item -LiteralPath $configPath -Destination $backup -Force
                }
                [System.IO.File]::AppendAllText($configPath, $block, (New-Object System.Text.UTF8Encoding($false)))
                Write-Ok "$Label 已写入 MCP 配置"
            }
        }

        # 技能：同时放 .agents/skills（跨工具标准）与 .codex/skills（Codex 专属）
        # 每个技能只汇报一行，写清两个目标目录，避免看起来像重复安装
        $skillRoots = @(
            @{ Path = (Join-Path $Root '.agents\skills'); Label = '.agents/skills' },
            @{ Path = (Join-Path $Root '.codex\skills');  Label = '.codex/skills' }
        )
        foreach ($skill in Get-ChildItem -LiteralPath (Join-Path $kit 'skills') -Directory) {
            $written = @()
            $skipped = @()
            foreach ($r in $skillRoots) {
                $dest = Join-Path $r.Path $skill.Name
                if (Test-Path -LiteralPath $dest) { $skipped += $r.Label; continue }
                if ($DryRun) { $written += $r.Label; continue }
                New-Item -ItemType Directory -Force -Path $dest | Out-Null
                $src = Join-Path $skill.FullName 'SKILL.md'
                $text = [System.IO.File]::ReadAllText($src).Replace('__KIT_ROOT__', $kit)
                [System.IO.File]::WriteAllText((Join-Path $dest 'SKILL.md'), $text, (New-Object System.Text.UTF8Encoding($false)))
                $written += $r.Label
            }
            if ($DryRun) {
                if ($written) { Write-Warn2 "$Label 将安装技能 $($skill.Name) → $($written -join ' + ')" }
            } else {
                if ($written) { Write-Ok "$Label 已安装技能 $($skill.Name) → $($written -join ' + ')" }
                if ($skipped) { Write-Ok "$Label 技能已存在，跳过 $($skill.Name)（$($skipped -join ' + ')）" }
            }
        }
        return $true
    } catch {
        if ($Required) { throw }
        Write-Warn2 "$Label 写入失败（通常是沙盒/权限限制）：$($_.Exception.Message)"
        return $false
    }
}

if ($PathsOnly) {
    Write-Step '路径 2/2' '更新 MCP 配置里的剪映路径'
    $userOk = $true
    if ($NoUserScope) {
        Write-Warn2 "按 -NoUserScope 跳过全局配置更新"
    } else {
        $userOk = Install-Scope -Root $userBase -Label '全局级' -EnvOnly
    }
} else {
    Write-Step '4/4' '安装 MCP 配置与技能'
    Install-Scope -Root $kit -Label '项目级' -Required | Out-Null

    $userOk = $true
    if ($NoUserScope) {
        Write-Warn2 "按 -NoUserScope 跳过全局安装"
    } else {
        $userOk = Install-Scope -Root $userBase -Label '全局级'
    }
}

# ---------- 收尾 ----------
# ---------- 运行时清单 + 收尾 ----------
if (-not $DryRun) {
    $runtimeInfo = [ordered]@{
        python          = $venvPy
        runtime_root    = $runtimeDir
        plugin_root     = $kit
        kit_root        = $kit
        plugin_mode     = [bool]$PluginMode
        detected_at     = (Get-Date).ToString('s')
        jianying_paths  = $Script:DetectedPaths
    }
    try {
        New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
        [System.IO.File]::WriteAllText($runtimeJson, ($runtimeInfo | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
        if ($PluginMode) { Write-Ok "运行时清单已写入：$runtimeJson" }
    } catch {
        Write-Warn2 "写 runtime.json 失败：$($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "== 完成 ==" -ForegroundColor Cyan
if ($PluginMode) {
    Write-Host "插件模式：运行时就绪，MCP 服务器与技能由宿主加载。" -ForegroundColor Green
} elseif (-not $userOk) {
    Write-Host "全局安装被拒绝（AI 在沙盒里跑时正常）。请你自己在普通 PowerShell 里跑一次这条命令：" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$kit\install.ps1`" -SkipPip" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "下一步：" -ForegroundColor Cyan
if ($PluginMode) {
    Write-Host "  1. 完全退出并重启 Codex 桌面端（MCP 在启动时加载）"
    Write-Host "  2. 直接说：`"列出我的剪映草稿`" 验证 MCP 是否可用"
    Write-Host "  3. 剪映重装或换位置后，重跑本命令的 -PathsOnly 更新路径"
} else {
    Write-Host "  1. 完全退出并重启 Codex 桌面端（MCP 与技能在启动时加载）"
    Write-Host "  2. 在 Codex 里打开本工具包目录（或你自己的工作目录）"
    Write-Host "  3. 直接说：`"列出我的剪映草稿`" 验证 MCP 是否可用"
}
Write-Host ""
Write-Host "注意：改草稿前先在剪映里退出该草稿，否则剪映内存里的版本会覆盖脚本的修改。" -ForegroundColor Yellow
