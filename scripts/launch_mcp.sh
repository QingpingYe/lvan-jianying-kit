#!/bin/sh
# 剪映 MCP 启动器（POSIX 侧薄包装）。
# 剪映专业版只有 Windows 版本，这里存在只是为了在非 Windows 宿主上给出明确报错，
# 而不是让 MCP 静默失败。
set -eu

plugin_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

printf '%s\n' "剪映专业版只有 Windows 版本，本插件只能在 Windows 上运行（当前插件目录：$plugin_root）。" >&2
exit 127
