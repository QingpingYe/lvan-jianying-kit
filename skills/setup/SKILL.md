---
name: setup
description: 剪映插件的首次使用引导：准备 Python 运行环境、安装 jianying-tool、探测本机剪映与草稿目录，并唤醒 MCP 工具。当用户第一次提到剪映、列出/生成/修改草稿而 MCP 工具不可用、或运行时环境报错时使用。
---

# 剪映插件 Setup

## 目标

把用户已经提出的剪映请求保留为原任务，在内部补齐运行环境；`ready` 后直接恢复原任务，
不要求用户重新描述。用户不需要知道 `install.ps1`、虚拟环境或依赖包的存在。

## 内部边界

- 命令全部由 AI 执行；只在需要用户决策时（放行网络、批准提权、手动指定剪映路径）开口。
- 不写 `~/.codex/config.toml`，不安装全局技能，不修改插件目录以外的任何文件。
- 运行时安装在用户级目录 `%USERPROFILE%\.jianying-kit`（可用 `JIANYING_KIT_HOME` 或
  `-RuntimeRoot` 覆盖）。插件目录本身只放代码，不落大文件。
- 不升级用户的系统 Python，不动系统注册表；`winget` / 官方安装包只是最后的兜底。

## 插件根目录

从本 Skill 文件定位：`skills/setup/SKILL.md` 向上三层即插件根。Claude Code 等宿主下
也可以直接用 `${CLAUDE_PLUGIN_ROOT}`。下面用 `<root>` 代表插件根绝对路径。

## 流程

1. **看状态**：先读 `%USERPROFILE%\.jianying-kit\runtime.json`。文件存在且
   `python` / `jianying_paths` 指向的路径都在，说明环境已就绪，直接跳到第 4 步，
   不要再跑安装器。
2. **装环境**：运行安装器（幂等，可重复跑）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File "<root>\install.ps1" -PluginMode
   ```

   它会建 `%USERPROFILE%\.jianying-kit\.venv`、`pip install "jianying-tool[tts,asr]"`、
   探测剪映安装位置与草稿目录，并写 `runtime.json` / `jianying-paths.json`。
   需要指定路径时加 `-JianyingBase "D:\JianyingPro"` 或 `-DraftRoot "E:\草稿"`；
   只想重新探测路径时加 `-PathsOnly`。
3. **失败按错误处理**（不要反复重试同一条命令）：

   | 现象 | 处理 |
   | --- | --- |
   | pip 报连不上 PyPI / 下载超时 | 说明需要放行 `pypi.org`、`files.pythonhosted.org`；放行后重跑一次 |
   | pip 报找不到匹配的发行版 | Python 版本越界（需要 3.11–3.14）；删掉 `.venv` 后用 `-PythonExe` 指定受支持的解释器重跑 |
   | 写 `%USERPROFILE%` 被拒绝 | 沙盒限制；向用户申请提权后重跑，或改用 `-RuntimeRoot <可写目录>`；用自定义目录时要同时把 `JIANYING_KIT_HOME` 设为同一个目录，启动器才会找到它 |
   | 探测不到剪映 | 确认装了剪映专业版并至少打开过一次；仍失败则问用户剪映装在哪个盘，再用 `-JianyingBase` 重跑 |

4. **验证**（用 `runtime.json` 里的 `python` 绝对路径，不要猜 `.venv` 位置）：

   ```powershell
   & "<python>" -m jianying_tool.cli probe --json
   ```

   `probe` 必须输出本机剪映版本、`codec_dir` 和草稿目录。只以 `probe` 判断环境是否就绪。
5. **`ready` 之后**：如果本次会话里 MCP 工具（`list_local_drafts` 等）已经可用，直接
   恢复原任务。如果工具仍然不可见，说明宿主在启动时没有拉起 MCP 服务器 —— 告诉用户
   完全退出并重启 Codex 桌面端，然后重试原请求。

**非 Windows 宿主**（macOS / Linux）：剪映专业版没有对应版本，直接告诉用户本插件只支持 Windows，
不要尝试安装环境。

## 不要做的事

- 不要用 `drafts --check` 判断安装是否成功：它检查的是用户本机已有的草稿库，
  `missing_content` / `unregistered` 属于历史残留，不是安装失败，也不要顺手清理。
- 不要在用户没要求时删除 `.venv`、重装依赖或重建草稿索引。
- 不要向用户复述命令原文；只说环境已就绪、需要重启宿主，或需要用户做的唯一一件事。
