# 剪映自动化（Codex 插件）

<!-- readme-check -->

让 Codex 桌面端用自然语言指挥剪映专业版做自动化剪辑：
**剧本 + AI 生成的视频片段 → 带字幕、BGM 的剪映草稿 → 你在剪映里精修导出。**

> 📖 **完整文档见 [使用说明.md](使用说明.md)** —— 安装、使用、常见问题、已知限制都在里面。

## 怎么用（三步）

1. **安装插件**：在 Codex 桌面端打开 Plugins 侧栏，添加本仓库的 marketplace（仓库根目录的 `.agents/plugins/marketplace.json`），安装 `Jianying Kit`；也可让 Codex 打开本目录后手动注册（见「不用插件」一节）。
2. **首次使用会自动引导**：装好后直接说一句和剪映有关的话（例如「列出我的剪映草稿」），
   Codex 会运行插件自带的 `setup` 技能：建 Python 运行环境、装依赖、探测剪映与草稿目录。
3. **重启一次**：setup 完成后完全退出并重启 Codex 桌面端，MCP 工具与技能才会挂上。

装完**完全重启 Codex**，然后说「列出我的剪映草稿」验证。

## 前置条件

| 依赖 | 说明 |
| --- | --- |
| 剪映专业版 | 已安装并至少打开过一次（Windows） |
| Python 3.11–3.14 | **没装也行**：安装器会自动装（先 winget，再 python.org 安装包，都是按用户装、不需要管理员）。装了 3.15 也不会用，原因见[使用说明](使用说明.md)的 FAQ |
| 网络 | 首次安装需要下载 Python 依赖（约 100MB） |

## 装完之后能说什么

- 「把 `E:\素材\第1集` 里的 11 个片段按剧本顺序排成草稿」
- 「第 2 镜缩短半秒」「给第 4 段加 0.5 秒叠化」
- 「字幕改成居中、字号加大」
- 「给这一集配个旁白，用女声」
- 「用《第1集》当模板，按这份清单套出第 2 到第 5 集」（剪同款批量出片）
- 「分析 `E:\素材\第3集`，同镜多条 take 挑最好的一条，生成粗剪草稿」（智能粗剪）
- 「这一集有配音错位，先转写核对，再建粗剪草稿并做音画对位审计」（智能粗剪 + 音频核验）

## 手动安装（不依赖 AI）

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

参数：`-DryRun`（只看会做什么）、`-SkipPip`（跳过装依赖）、`-NoUserScope`（只装项目级）。

## 不用插件（独立安装）

插件宿主不方便用时，也可以把仓库当作原来的工具包直接用：让 Codex 打开本目录读
[BOOTSTRAP.md](BOOTSTRAP.md)，或自己跑下面这条命令。独立模式沿用老行为 ——
环境建在仓库的 `.venv`，并往 `.codex/config.toml` 写 MCP 配置、往
`.agents/skills` 与 `.codex/skills` 拷技能。

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

插件模式（只准备运行时，不写宿主配置）用：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -PluginMode
```

改过插件文件之后，可以先跑一次结构自检（只读，不写任何文件）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate_plugin.ps1
```

自检会读 `examples/` 下的例子 JSON。Windows PowerShell 5.1 没有 BOM 就会把中文 UTF-8
当 ANSI 读，含中文的 JSON 会被读成非法 JSON；改过这类文件后跑一次
`scripts\fix_bom.ps1` 补 BOM 即可。

## 常见问题

**Q：setup 说写用户目录被拒绝了？**
那是我（AI）在沙盒里跑的结果，正常。你自己在普通 PowerShell 里跑一次
`install.ps1 -SkipPip` 即可完成全局安装。

**Q：重启后还是不能列出草稿？**
确认 Codex 是**完全退出**再打开的；确认 `%USERPROFILE%\.jianying-kit\runtime.json` 存在；
再不行让 AI 跑一次插件自带的 `setup` 技能排错。

**Q：能装到别人的电脑上吗？**
可以。整个文件夹拷过去（或打包 zip），按上面三步走即可；每台机器各自建 `.venv`，互不影响。

**Q：剪映版本要求？**
实测通过的是 Windows 剪映专业版 **11.6.0**。5.9 及更早版本同样可用（字幕/导出等能力反而更全）。
