# 模板批量出片（剪同款流水线）设计

日期：2026-11-02 · 目标版本：jianying-kit 0.6.0 → 0.7.0 · 状态：已确认（批量导出取选项 A）

## 1. 为什么做

插件现在两头都有、中间缺一块：

- 有"从零建草稿"：`tools/build_draft.py` 吃分镜表 JSON，产出一条时间线。
- 有"细粒度改草稿"：71 个 MCP 工具能加片段、改样式、加转场、导出。
- 没有"拿一个已经精修好的草稿当模板，批量复制成 N 份并逐份换素材换文案"。

参考项目 pyJianYingDraft 的招牌能力正是这一块（模板模式）：`duplicate_as_template()` 复制草稿、
`replace_material_by_name()` / `replace_material_by_seg()` 换素材、`replace_text()` 换文案、
`import_track()` 跨草稿搬轨道。插件目前只把 pyJianYingDraft 当建草稿用的库，这些 API 一个都没用。

典型场景：一集短剧的"模板草稿"做好之后（字幕样式、片头文字卡、水印、调色、转场都在），
用它批量套出第 2 到第 20 集。

## 2. 目标

1. 用户给一份清单，得到 N 个可直接在剪映里打开、继续精修的草稿。
2. 模板的样式、特效、轨道结构原样保留，只换素材与文案。
3. 新素材时长与目标片段不匹配时按明确策略处理，并在报告里说明，不产出坏草稿。
4. 每个新草稿有独立身份，剪映首页正常识别（不报"草稿内容已损坏"）。
5. 批量导出**不在 v1 范围内**（用户 2026-11-02 拍板取选项 A），理由见 §11.1。

## 3. 非目标

- 不做在线模板市场或模板下载。
- 不实现剪映界面独占的能力（识别字幕、文本朗读、智能抠像）。
- 不改第三方包 `jianying-tool` 的代码。
- 不做跨机器、跨账号的模板分享格式。

## 4. 反目标

技术上"跑通"但视为失败的情况：

- 产出的草稿在剪映里打不开、提示损坏，或卡片时长显示 00:00。
- 复制 N 份时把模板里的旧素材也整份拷贝 N 次，磁盘占用无意义地膨胀。
- 某一行动到一半失败，留下半成品草稿且报告里不说。
- 时间轴错位：换了素材但片段长度与素材不匹配，剪映里出现黑帧或卡帧。

## 5. 约束

| 约束 | 影响 |
| --- | --- |
| 本机草稿是密文（剪映 11.6） | 必须走 `jianying_tool.native_codec` 解密/回写，pyJianYingDraft 自己读不了 |
| `jianying-tool` 精确 pin `pyJianYingDraft==0.3.0` | 不升级库，只用 0.3.0 已有的 API |
| venv 里没有 pytest | 测试用标准库 `unittest`，不新增依赖、不改 `install.ps1` |
| 宿主加载的是插件缓存目录 | 开发在 `E:\jianying-kit`，改完要同步缓存并重启 Codex |
| 模板模式下导入轨道是"只读"的 | 只能在导入轨道上换素材、换文本、把整条轨道搬走；不能往里加片段、转场、特效（库的限制） |
| 草稿目录里带历史备份 | `.jianying-tool-backups` 实测占某个草稿 169MB 里的 162.7MB；复制时必须排除，否则 N 份会把磁盘塞满 |
| 导出后剪映停在编辑页 | 现有导出流程只在首页-编辑页之间进入，导出结束不回首页；元素库（2026-09-16 抓于 11.4.2）里没有"返回首页"控件，批量导出受限，见 §11.1 |

## 6. 方案选择（已确认：丙）

用户已从三条路线里选了**丙**：

- 甲（只做脚本 + 技能）：改动最小、脚本立即可跑，但跨会话复用弱，能力不在工具层。
- 乙（只做第二个 MCP server）：交互强、任何会话可调，但核心算法没验证就先加壳，返工风险高。
- **丙（选中）**：核心逻辑抽成共享模块，脚本与 MCP server 都只是薄包装。批量清单走脚本省 token，
  细粒度操作走 MCP 工具。实施顺序是先甲后乙，共享同一份核心。

## 7. 结构

```
jianying-kit/
├─ tools/
│   ├─ jy_template.py            ← 新增：共享核心（复制、列槽位、替换、写回）
│   ├─ template_fill.py          ← 新增：CLI 薄包装（清单 JSON 进，N 草稿出）
│   ├─ build_draft.py            （不动）
│   └─ subtitle_tools.py         （不动）
├─ mcp_ext/
│   └─ server.py                 ← 新增：MCP 薄包装（4 个工具）
├─ skills/
│   ├─ template-batch/SKILL.md   ← 新增：教 AI 何时用、怎么用
│   └─ （现有三个技能不动）
├─ tests/
│   └─ test_jy_template.py       ← 新增：unittest，不依赖剪映
├─ examples/
│   └─ templatefill.example.json ← 新增：清单示例
├─ .mcp.json                     ← 改：加第二个 server
├─ scripts/launch_mcp.ps1        ← 改：支持 -Module 与 PYTHONPATH
├─ scripts/validate_plugin.ps1   ← 改：技能清单、新 server 与 JSON 检查
├─ .codex-plugin/plugin.json     ← 改：版本 0.7.0、能力描述
└─ README.md / 使用说明.md / 工具清单.md ← 改：新能力入档
```

`mcp_ext/` 与 `tools/` 分开是有意的：`tools/` 是给人和 AI 直接跑的脚本，`mcp_ext/` 是给宿主加载的
Python 包。共享核心放在用户点名的 `tools/jy_template.py`，MCP server 通过把 `<root>/tools` 插入
`sys.path` 复用它。

## 8. 共享核心 `tools/jy_template.py`

### 8.1 对外 API

| 函数 | 作用 |
| --- | --- |
| `list_slots(root, draft)` | 列出模板草稿的可替换槽位（素材槽 + 文本槽） |
| `fill(root, plan, *, dry_run=False, rows=None)` | 按清单执行整批，返回报告 |
| `duplicate(root, template, target, *, allow_replace=False)` | 复制草稿 + 重置身份 |
| `read_draft(root, name)` | 解密读取草稿内容，返回 `(dict, 格式名)` |
| `write_draft(root, name, content)` | 重新加密写回草稿内容 |

### 8.2 密文草稿的读写管线

这是全流程最容易出错的地方，步骤固定：

1. `native_codec.decode_file(draft_content.json)` 得到明文 dict 与格式名。
2. 在草稿目录里写一个影子明文文件 `.draft_content.jytool-<uuid>.json`
   （与 `jianying_tool.edit._native_script_edit` 的既有做法一致）。
3. `ScriptFile._load_template(shadow)` 或 `DraftFolder.duplicate_as_template()` 打开。
4. 执行替换。
5. `script.save()` 把明文写回影子文件；读回该明文。
6. `native_codec.update_project_json(project, ...)` 重新加密写回真正的草稿内容文件。
7. `finally` 里删除影子文件。

### 8.3 槽位寻址

| 寻址方式 | 底层 API | 适用 |
| --- | --- | --- |
| 按素材名 | `replace_material_by_name(name, material)` | 一张占位图/一段占位音频被多个片段引用，一次全换 |
| 按轨道 + 片段序号 | `replace_material_by_seg(track, index, material, source_timerange)` | 逐镜换素材，且要重新指定取用范围 |
| 文本按轨道 + 片段序号 | `replace_text(track, index, text)` | 保留全部样式，按新文案长度重算样式区间 |

导入轨道用 `script.get_imported_track(track_type, name=..., index=...)` 获取；它要求"唯一匹配"，
不唯一时抛 `AmbiguousTrack`，所以槽位枚举与替换都按轨道名优先。

### 8.4 时长不匹配的策略

默认值与库的默认值一致：

- 新素材比目标片段短：`ShrinkMode.cut_tail`，把片段尾部裁到素材长度，报告中标注该槽位。
- 新素材比目标片段长：`ExtendMode.cut_material_tail`，保持片段原长，只取素材前一段。

清单里可按槽位覆盖，实现时把策略名映射到 `ShrinkMode` / `ExtendMode` 的枚举，非法值直接报错而不是静默取默认。

`replace_material_by_name` 不走上面这套：库的语义是**不改动片段时长与取用范围**（官方注释说明它
"尤其适合图片素材"）。沿用该语义，并加一条预检：新素材短于"引用它的片段里最短的那个"时，
该行直接失败，报错文案提示改用 `by_slot` 重新指定取用范围。宁可不做，也不产出时间轴对不上的草稿。

### 8.5 复制草稿后的身份重置

直接 `shutil.copytree` 出来的副本会原样继承模板的 `draft_id`、`draft_name`、`draft_fold_path`，
剪映的草稿索引按 `draft_id` 对账，对不上就报"草稿内容已损坏"。复制后必须调用
`jianying_tool.metadata.write_draft_meta_info(draft_dir, root=root)` 重新生成身份，
它同时会写 `tm_duration` 与素材体积，让首页卡片显示正确时长。

### 8.6 复制时带什么、不带什么

复制走"自己控制的 copytree + 过滤"，不直接用 `duplicate_as_template` 的裸 `shutil.copytree`：

| 内容 | 处理 | 理由 |
| --- | --- | --- |
| `draft_content.json` / `draft_meta_info.json` / 封面 / 缩略图 | 复制 | 草稿本体 |
| `Timelines/` | 复制 | 多时间线布局 |
| 草稿内的 `media/` | 复制 | 模板自己放在草稿里的素材（实测 5.3MB / 14.3MB） |
| `.jianying-tool-backups/` | **排除** | 本工具自己写的历史备份，实测单份 162.7MB；带过去会让 N 份草稿的占用爆炸 |
| 草稿目录之外的素材（`E:\素材\...` 这类绝对路径） | **不改动、不拷入** | 与剪映自身行为一致；拷进来会把几 GB 素材放大 N 份 |

因此清单里没有 `media` 开关，行为只有一种，不留歧义。替换后不再被引用的模板占位素材**保留不删**：
删素材是破坏性动作，且素材条目留在草稿里本身无害；报告里会把"没有任何片段引用的素材"列出来提示。

## 9. 清单格式

```json
{
  "template": "Codex 剪映工具演示",
  "defaults": { "shrink": "cut_tail", "extend": "cut_material_tail" },
  "rows": [
    {
      "name": "第1集",
      "materials": {
        "by_name": { "占位图.png": "E:\\素材\\ep01\\封面.png" },
        "by_slot": [
          { "track": "main_video", "segment": 0,
            "file": "E:\\素材\\ep01\\01.mp4",
            "source_start": 2.0, "duration": 6.0 }
        ]
      },
      "texts": { "title": ["第1集"], "subtitle": ["第一句", "第二句"] }
    }
  ]
}
```

- `template`：模板草稿名（草稿目录下的文件夹名）。
- `rows[].name`：新草稿名；不写时用 `<template>-<行号>`。
- `materials.by_name`：素材名 → 新文件路径。
- `materials.by_slot[].track/segment`：轨道名与片段下标；`source_start` / `duration` 为可选取用范围（秒）。
- `texts`：轨道名 → 按片段顺序排列的新文案数组。
- 相对路径按清单文件所在目录解析（与 `build_draft.py` 的约定一致）。
- 清单里**没有导出配置**：按 §11.1 的选项 A，v1 只产草稿不导出。

一行不要求填满所有槽位：没写的槽位保持模板原样。为避免"看起来成功、实际还带着模板占位素材"，
报告里会把"仍然保留模板原样的槽位"逐条列出来；清单顶层可选 `"require_all_slots": true`，
打开后这些警告升级为该行失败。

## 10. 脚本 `tools/template_fill.py`

- 参数：`--plan`（必需）、`--root`、`--rows 1-3`（只跑部分行）、`--dry-run`、`--python`。
- 流程：预检（模板存在、输出草稿不重名、素材文件存在、轨道与槽位能解析）
  → 逐行复制 → 替换 → 写回 → 重置身份 → 汇总报告 → 可选导出。
- 失败处理：单行失败不中断整批，报告里逐行给出 `ok` / `failed` 与原因；有失败行时退出码非零。
- 写草稿与导出是两个阶段，报告里分开记：草稿生成成功但导出失败的行，草稿**保留不删**
  （删除是破坏性动作），该行记为 `draft: ok / export: failed`，整批退出码非零。
- 约定与 `build_draft.py` 保持一致：自动读 `jianying-paths.json` 注入环境变量、默认用运行时 venv 的 python。

## 11. MCP server `mcp_ext/server.py`

第一版暴露 3 个工具，全部是共享核心的薄包装（命名与现有 71 个工具不冲突）：

| 工具 | 作用 |
| --- | --- |
| `list_template_slots(draft)` | 列出某草稿的可替换槽位，供 AI 设计清单 |
| `duplicate_local_draft(source, target)` | 复制草稿成新草稿，排除 `.jianying-tool-backups`，重置身份 |
| `import_local_track(source_draft, target_draft, track, offset=0, new_name=None)` | 跨草稿搬整条轨道 |

启动方式：`.mcp.json` 的 `mcpServers` 增加第二项，走同一个启动器并多传一个模块名；
`launch_mcp.ps1` 增加 `-Module` 参数，未给时保持现有行为（仍启动 `jianying_tool.mcp_server`），
同时把 `<plugin root>` 写进 `PYTHONPATH`，否则 `python -m mcp_ext.server` 找不到包。

`import_local_track` 的语义（按底层库的约束定死）：

- 被搬轨道引用的素材条目**随轨道深拷贝进目标草稿**；媒体文件仍按原有绝对路径引用，不搬文件。
- 底层保留素材 id，且明确不支持"同一条轨道重复导入同一草稿"。因此重复导入（目标已存在同名轨道，
  或某素材 id 已存在）直接**报错**，不做静默覆盖或改名。

### 11.1 批量导出：v1 不做（已拍板取选项 A）

查证结果：`jianying_tool.control.export_draft` 只处理"首页 → 打开草稿 → 导出"，导出成功并关闭
成功弹窗后**停在编辑页**；再次导出另一个草稿时，它会核对导出对话框标题属于哪个草稿，
不匹配就拒绝执行。而元素库里没有任何"返回首页 / 关闭草稿"控件，所以现成的循环导出在第 2 条就会失败。

三个选项：

| 选项 | 内容 | 代价 |
| --- | --- | --- |
| A（推荐） | v1 不做批量导出，只做"批量生成草稿"；导出继续用现有单条工具 | 用户要逐条导出，但零新增 UI 自动化风险 |
| B | v1 就做：先补一个"返回首页"元素模板 | 需要一次真机抓图与验证（剪映停在编辑页，截图裁控件、验证点击后页面锚点确实变回首页） |
| C | v1 做"半自动批量"：只在剪映停在首页时启动，导完一条检测到还在编辑页就停下并提示用户 | 一次只能自动导一条，省不了多少事 |

**决定（2026-11-02）：取选项 A。** v1 只批量生成草稿，导出沿用现有单条
`export_jianying_draft` 工具（它已经能驱动剪映自己的导出对话框并校验结果）。
选项 B 留作后续独立任务，届时需要新增一节说明元素模板的抓取与验证步骤。

## 12. 技能 `skills/template-batch/SKILL.md`

内容要点：什么时候用（用户说"按模板批量出片""剪同款""套模板""这条模板出 10 集"）、
先列槽位再写清单、清单字段、怎么跑 `template_fill.py`、
怎么读报告（截断的槽位、失败行）、批量导出前的注意事项（剪映停在目录页、导出期间别动鼠标）。

## 13. 测试

`tests/test_jy_template.py`，标准库 `unittest`，不依赖剪映：用 pyJianYingDraft 在工作目录里造一个
明文模板草稿，再跑共享核心。运行方式：`& $py -m unittest discover -s tests -t .`（仓库根执行）。

覆盖点：

1. 复制后新草稿身份独立：`draft_id` 与模板不同，`draft_name` / `draft_fold_path` 指向新目录。
2. 按素材名替换后，所有引用该素材的片段都指向新素材。
3. 按槽位替换且新素材更短时，片段被裁到素材长度，报告里标出该槽位。
4. 换文案后文本内容正确，样式区间随新长度重算。
5. 素材缺失时该行失败、不产出草稿，整批继续并最终非零退出。
6. 复制时排除 `.jianying-tool-backups`，且草稿内的 `media/` 被复制。
7. `--dry-run` 走完整预检（模板、素材路径、槽位解析、时长兼容），但不复制、不写影子文件、
   不产生任何新文件；测试按"运行前后目录树与文件 mtime 完全一致"断言。
8. 根 `draft_content.json` 与活动时间线内容不一致时，沿用 `update_project_json` 的拒绝行为并报错。

`--dry-run` 的深度按字面理解："不写任何文件"就是不写任何文件，预检只做只读解析；
密文写回管线依赖的影子文件只在真正执行时才出现。

## 14. 交付后如何验收

1. 用真实模板草稿（先拿现有的《Codex 剪映工具演示》）跑一次 `list_template_slots`，人工核对槽位。
2. 造一份 3 行的清单跑 `template_fill.py`，在剪映首页确认 3 个新草稿都能打开、时长正确。
3. 打开其中一个草稿，确认样式、特效、轨道结构与模板一致，素材已替换。
4. 跑一次批量导出（1 到 2 个草稿）确认导出闭环。
5. 跑 `scripts/validate_plugin.ps1` 与 `unittest`。

## 15. 留在实现阶段的决定

- 清单字段的最终命名与校验报错文案。
- 报告的具体字段与打印格式。
- 槽位枚举是否过滤掉明显不可替换的轨道（如特效轨、滤镜轨）。

## 16. 已知限制（要写进用户文档）

- 导入轨道不能直接增删片段、转场、特效（pyJianYingDraft 模板模式的限制）。
- 模板模式要求目标轨道名唯一，同名多条轨道会导致 `AmbiguousTrack`。
- 剪映开着目标草稿时不要跑，内存里的版本会覆盖脚本写入。
- 导出依赖剪映界面自动化，导出期间不要操作鼠标键盘；v1 不含批量导出（见 §11.1）。
- 草稿目录内的 `media/` 会随复制放大 N 份；把大素材放在草稿目录外的模板不会放大。

## 17. 构建者复核记录（2026-11-02）

设计完成后交给一个独立复核者（只读文档、不看对话）提"上手前还必须问清的问题"，回来 7 条，
分类处理如下：

| 复核问题 | 归属 | 结论位置 |
| --- | --- | --- |
| `media="copy"` 的自包含边界 / 旧素材是否删除 | 我定 | §8.6：排除 `.jianying-tool-backups`，不拷外部素材，不删素材 |
| 按名称替换时长不匹配怎么办 | 我定 | §8.4：沿用库语义 + 预检拒绝，提示改用 `by_slot` |
| 草稿成功但导出失败算不算成功 | 我定 | §10：草稿保留，两阶段分别记账，退出码非零 |
| 一行是否必须填满所有槽位 | 我定 | §9：不强制，报告列出保留原样的槽位，可选 `require_all_slots` |
| `--dry-run` 验证到多深 | 我定 | §13：只读全量预检，字面意义上不写任何文件 |
| 是否必须支持多时间线草稿 | 既有设施覆盖 | §13 第 8 条：复用 `update_project_json`，不一致时它自己会拒绝 |
| `import_local_track` 的素材归属与重复导入 | 库约束定死 | §11：素材条目随轨道拷贝，重复导入直接报错 |

此外在写文档期间自查发现一条复核没覆盖的问题：批量导出受"导出后停在编辑页且没有返回首页控件"限制，
已单列为 §11.1，需要用户拍板。
