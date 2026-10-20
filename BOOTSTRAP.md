# 剪映自动化工具包 · 安装引导（给 AI 读）

这是一个「剧本 → 剪映草稿」自动化工具包。人类用户把本文件所在的文件夹交给 Codex，
然后发送下面这段**固定提示词**，由 AI 完成安装。

---

## 给用户复制的一段提示词

```
请阅读当前目录下的 BOOTSTRAP.md，并严格按里面的「AI 执行步骤」完成安装与自检。

要求：
1. 每一步都要真的执行命令并检查输出，不要凭猜测跳过或假装成功。
2. 遇到需要联网、下载包、写系统目录时，先说明原因再执行。
3. 不要修改工具包以外的任何文件；不要删除任何东西。
4. 如果某一步因为沙盒/权限被拒绝，最多重试一次，然后跳过并在最后列出「需要你手动执行」的命令原文。
5. 全部结束后，用一张表格汇报：步骤 / 结果 / 证据（关键输出）/ 需要你做的事。
```

---

## AI 执行步骤

### 步骤 0 · 环境确认

```powershell
$kit = "<本目录的绝对路径>"
$py  = "$kit\.venv\Scripts\python.exe"
Test-Path $kit
```

**Python 完全不用管** —— 安装器按下面的顺序自己解决，前一步成功就不会走后面：

1. **已有可用的 `.venv`** → 直接复用（即使本机根本找不到系统 Python）
2. **系统里的 Python 3.11–3.14** → 用它建 `.venv`
3. **工具包内的可移植运行时**（`.runtime\python`，NuGet 包解压）→ 直接用
4. **自动准备**：先下载可移植运行时（不写注册表/用户目录），失败才退到 `winget` → python.org 安装包

所以 **"安装器说找不到 Python"这种情况不应该再出现**；如果出现，说明是真的没有可用解释器，
按报错里给的两条命令处理即可（`-PythonExe` 指定现成的，或手动装 3.13）。

**两条已知陷阱（安装器已自动处理，知道即可）**：

- **版本窗口是 3.11 – 3.14**。依赖里的 `ctranslate2` / `onnxruntime` 目前没有 Python 3.15 的预编译包，
  所以本机若只有 3.15，安装器会保留它不动、另装一个 3.13 来用。不要试图把 3.15 塞进项目环境。
- **跳过 Windows「应用执行别名」**：`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` 是 0 字节存根，
  运行它会拉起 Microsoft Store。安装器会自动识别并跳过，转而扫描常见安装目录与注册表。

### 步骤 1 · 运行一键安装器

```powershell
powershell -ExecutionPolicy Bypass -File "$kit\install.ps1"
```

安装器会：建 `.venv` → `pip install "jianying-tool[tts,asr]"` → 自检剪映 → 写 MCP 配置 → 装技能。

**受限环境（沙盒 / 只读用户目录 / 企业策略）推荐加参数**：

```powershell
# 只装项目级（不写工具包外的任何地方）
powershell -ExecutionPolicy Bypass -File "$kit\install.ps1" -NoUserScope

# 指定现成的解释器（比如用户自己装的 3.13）
powershell -ExecutionPolicy Bypass -File "$kit\install.ps1" -PythonExe "C:\Python313\python.exe"

# 强制用工具包内的可移植 Python（不碰系统 Python）
powershell -ExecutionPolicy Bypass -File "$kit\install.ps1" -Portable
```

| 参数 | 作用 |
| --- | --- |
| `-DryRun` | 只显示会做什么，不改动任何文件 |
| `-SkipPip` | 跳过依赖安装（环境已就绪时用） |
| `-NoUserScope` | 只装项目级，不写 `%USERPROFILE%\.codex` |
| `-PythonExe <路径>` | 指定用哪个解释器建环境 |
| `-Portable` | 强制使用工具包内的可移植 Python |
| `-JianyingBase <路径>` | 指定剪映安装位置（自动探测失败时用） |
| `-DraftRoot <路径>` | 指定草稿目录（用户改过草稿位置时用） |
| `-PathsOnly` | 只重新探测剪映路径并更新配置（剪映重装 / 换位置后用，不建环境不装依赖） |

**完全离线 / 下载被拦**：把 `python.3.13.16.nupkg`（来自
`https://api.nuget.org/v3-flatcontainer/python/3.13.16/python.3.13.16.nupkg`）放进工具包的 `.runtime\` 目录，
安装器会直接离线解压，不再联网下载。

**三种"看起来像失败但其实是预期"的情况**：

- **写 `%USERPROFILE%\.codex` 被拒绝**：AI 在沙盒里跑时必然如此。属于正常现象，
  安装器会打印一条需要用户手动执行的命令。原样抄给用户即可，不要反复重试。
- **pip 报错**：安装器现在会把真实错误打到屏幕并写进 `<工具包>\install-pip.log`。
  看到 "解析失败/需要更高版本" 说明 Python 版本不对；看到 "连不上 PyPI" 才是网络问题；两者别混。
- **`drafts --check` 报 error**：这是**用户本机已有的剪映草稿数据问题**，跟安装无关，见步骤 2。

**剪映可能不在默认位置**：安装器会自动探测（默认路径 → 各盘常见位置 → 剪映自己配置里的草稿目录），
支持 `<根>\Apps\<版本>\videoeditor.dll` 和 `<根>\<版本>\videoeditor.dll` 两种布局。
探测到非默认路径后会写进 `jianying-paths.json` 和 MCP 配置的环境变量；如果自动探测没命中，
用 `-JianyingBase` 指定即可。

### 步骤 2 · 验证

```powershell
& $py -m jianying_tool.cli probe --json
& $py -m jianying_tool.cli drafts --check
```

`probe` 应输出本机剪映版本、`codec_dir`（含 `videoeditor.dll` 的目录）和草稿目录；

**`drafts --check` 要分开看** —— 它检查的是"用户本机的草稿库"，不是"安装是否成功"：

- 报 `missing_content`（索引里有、磁盘上没有时间线文件）、`unregistered`（有目录没索引）
  都属于**用户已有的草稿数据状态**，**不代表安装失败**，不要因此告诉用户"装失败了"。
- 只有 `probe` 失败才说明环境有问题。
- 发现这类问题只需告知用户"你的草稿库里有 N 个残留条目，通常是以前删草稿没删干净"，问他要不要处理；
  任何写入用户草稿目录的修复动作都要**单独征求确认**。

若 `probe` 报找不到剪映：说明本机没装剪映专业版，或装在非默认位置。
提示用户安装剪映（https://www.capcut.cn/），或按报错内容设置 `JIANYING_INSTALL_ROOT`。

### 步骤 3 · 交付给用户

汇报表格之后，明确告诉用户两件事：

1. **要完全重启 Codex 桌面端**，MCP 服务器和技能才会加载。
2. 重启后说一句「列出我的剪映草稿」来验证；能列出草稿就说明 MCP 通了。

---

## 工具包里有什么

| 路径 | 作用 |
| --- | --- |
| `使用说明.md` | 给人看的完整说明书（安装/使用/FAQ/限制） |
| `install.ps1` | 一键安装（建环境、装依赖、写配置、装技能） |
| `tools/build_draft.py` | 分镜表 JSON → 剪映草稿 |
| `tools/subtitle_tools.py` | 字幕批量导出 / 回写（配合剪映「识别字幕」用） |
| `tools/new_empty_content.py` | 修复「建草稿中途失败」留下的空草稿文件夹 |
| `skills/analyze-video/` | 技能：素材打标（抽帧 → 联系表 → 索引） |
| `skills/video-pipeline/` | 技能：剧本 + 素材 → 剪映草稿 |
| `examples/shotlist.example.json` | 分镜表示例 |

安装后 AI 会拿到 71 个 MCP 工具（建草稿、改片段、加字幕、转场、关键帧、导出等），
以及两个技能（素材打标、排片）。

---

## 边界与注意事项

- **剪映索引由剪映独占**：脚本只写草稿文件夹，不写 `root_meta_info.json`。
  剪映开着时新建的草稿会被自动登记；剪映关闭时建的，需要在剪映里刷一次草稿首页，
  或先在剪映里建一个空草稿再用 `--existing` 填充。
- **改草稿前先在剪映里退出该草稿**，否则剪映内存里的版本会覆盖脚本写入。
- **自动导出**依赖剪映版本与显示缩放（截图模板），版本对不上会明确报错而不是乱点，此时手动导出即可。
- 工具会调用本机剪映自带的 `videoeditor.dll` 读写加密草稿，不包含剪映的任何二进制文件。
- 素材文件名建议用英文/数字，规避历史编码问题。
