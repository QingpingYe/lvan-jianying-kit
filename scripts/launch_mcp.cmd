@echo off
rem MCP launcher wrapper for jianying-kit. ASCII only: cmd.exe reads batch
rem files with the system code page, so keep this file plain 7-bit ASCII.
rem Called by .mcp.json as: cmd /c call launch_mcp.cmd <path-to-launch_mcp.ps1>
setlocal
if "%~1"=="" goto missing

where pwsh.exe >nul 2>&1
if not errorlevel 1 (
  pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~1"
  exit /b %errorlevel%
)

where powershell.exe >nul 2>&1
if not errorlevel 1 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~1"
  exit /b %errorlevel%
)

echo launch_mcp.cmd: PowerShell not found (need pwsh or powershell.exe). 1>&2
exit /b 127

:missing
echo launch_mcp.cmd: missing argument 1 (path to launch_mcp.ps1). 1>&2
exit /b 2
