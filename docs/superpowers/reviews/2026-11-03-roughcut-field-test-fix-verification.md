# 粗剪首轮实测复盘 · 核实与修复记录

**日期：** 2026-11-03
**来源报告：** `E:\《戚继光平海卫之战》分镜视频\jianying-kit-粗剪首轮实测复盘-2026-11-03.md`
**核实方式：** 源码走读、真实草稿解码、真实素材复现、单元测试（132 项）
**仓库约束：** 不修改第三方包 `jianying_tool` / `pyJianYingDraft`；上游问题在本插件内做兼容或旁路

---

## 1. 逐项结论

| 编号 | 结论 | 证据 / 处理 |
| --- | --- | --- |
| A1 | 属实，已修 | `venv_python()` 返回 `E:\jianying-kit\.venv\...python.exe`（不存在），而 `runtime.json` 指向用户目录 venv。现按 `--python` → `runtime.json` → 插件 `.venv` 解析；`jy_roughcut.build()` 增加 `python_exe` 透传。真实 shotlist `--dry-run` 已能自动命中运行时 Python。 |
| A2 | 属实，已修 | `jianying_tool.cli create` 本身没有 `--source-start`。现 create 后追加 `edit-segment --segment 0 --source-start 0.3`；真实 shotlist 的 dry-run 输出已确认该命令。 |
| A3 | **未复现为缺 start 解析 bug** | 解码 v1 后，`edit-segment --segment 9 --target-start 80.8` 与第 8 段（77.4167–83.5）真实重叠；首段缺失 `start` 语义就是 0，不能按报告建议忽略它（忽略会让首段不再参与重叠判定）。该报错与 A4「剪映占用 + 磁盘时间线过期」同源。处理：写前占用拦截 + 写后核对 + `audit` 对比，不再按原建议改第三方 `_range()`。 |
| A4 | 属实，已修 | 新增 `jy_template.draft_status / assert_draft_writable`：剪映运行且目标草稿非空时拒绝写入；写后用 `verify_timeline()` 核对段数与时间点（±1 帧，起点按累计取整放宽）。提供 `--force` 逃生口。 |
| A5 | 属实，部分已修 | 新增 `segments[].mute`、`segments[].volume`、顶层 `audio_tracks`（同轨不重叠）、`grouping.media_reuse` + `plan.media_reuse` 双白名单；build 直接执行，不再依赖 `audio_repair` 备注。`split_at`、逐段淡入淡出未实现。 |
| A6 | 属实，已修 | 新增 `roughcut.py audit`：计划↔草稿对比、台词→镜头→时间线、静音区间、分离音轨，产出 JSON + Markdown。真实 v3 审计已发现 11 vs 12 段及多处时长不一致。 |
| A7 | 属实，已修 | `frames --out` 现在按 `--scan` → out 目录 → grouping 同级 → 素材根 `roughcut\` 回退查找 scan.json。 |
| A8 | 属实，已修 | `--existing` 目标草稿非空时拒绝（build 与 build_draft 双层），只允许空草稿；确认风险才加 `--force`。 |
| A9 | 属实，未实现 | 素材内 `regions[]`（同一文件多区间）是结构性扩展，不在本次范围；已在 `使用说明.md` 记为已知限制，跨镜复用可用 `media_reuse` 白名单。 |
| B1 | 属实，已修 | PyAV 19 下 `faster_whisper.audio.decode_audio` 复现 `metadata_errors` TypeError。新增 `roughcut.py transcribe`：ffmpeg 解码 → numpy → faster-whisper，完全绕开 PyAV。真实 `10.mp4` 转写成功；带剧本提示词后逐句文本与时间戳与人工结论一致。 |
| B2 | 属实，已缓解 | `install.ps1 -PreseedAsrModel <tiny/base/small>` 可把模型预置到运行时 `models\`；失败不阻断安装，首次转写仍会自动下载。 |
| B3 | 属实，已修（插件侧） | `jy_template` 现在同时读取 `runtime.json.jianying_paths` 与旧的 `jianying-paths.json`，且 import 时自动注入；`build_draft.py` / `subtitle_tools.py` 同步。真实 v3 草稿已用「只 import jy_template」的新进程解密验证。第三方直接调用 `native_codec` 的路径仍不在插件控制内。 |
| B4 | 属实，未建 CI | 仓库没有 CI 配置；Python 3.14 + av 19 的坑已写进 `BOOTSTRAP.md`。 |
| B5 | 属实，已修 | CLI 统一 UTF-8；`build_draft` 子进程输出加 `[cmd] / [stdout] / [stderr]` 前缀；文档注明外部捕获时设置 `PYTHONIOENCODING=utf-8`。 |
| C1 | 属实，已缓解 | build 现在写 `manifest.json`（段、区间、转场、静音、音轨），可作为迁移后的比对基线；完整 doctor 一致性检查未实现。 |
| C2 | 属实，已修 | 写后核对与 audit 使用 ±1 帧容差；目标起点按段号累计放宽。 |
| C3 | 属实，已修 | 剪映运行中新建草稿会打印重启刷新提示；非空草稿写入直接拦截。 |
| D1 | 属实，已修 | 技能流程从六步扩为八步：看片后先做音频核验，build 后必须 audit；规则集同步。 |
| D2 | 属实，未实现 | 剧本节拍覆盖表未做；报告的警告列表仍可用。 |
| E1/E2/E4 | 属实，已修 | 看片步骤强制检查内部切点并允许 `--count 12`；音频核验前移；重建换新草稿名 + 写前拦截。 |

---

## 2. 真实素材验证

### 转写（B1 + D1）

```text
母亲大人膝下敬禀者   0.34–2.68
儿自离家投军         3.00–5.04
现已编入戚家军       5.04–7.00
戚将军治军极严       7.46–9.46
军纪肃然             9.94–10.84
```

不带 `--initial-prompt` 时 small/int8 会把「膝下敬禀者 / 戚家军 / 治军极严」听成同音字；
技能已要求把该镜剧本台词作为提示词传入。词级时间戳保证提示词不会把整段对白并成一句。

### 审计（A6 + C1）

对真实 `戚继光平海卫之战 第1集 粗剪v3` 运行 audit，结果：计划 11 段、草稿 12 段，
第 7–10 段区间与计划不一致——v3 的手工音频手术没有回写进 `roughcut.json`。
这台机器上的 v3 与计划已经漂移；后续自动修改前应先重建或把手工改动回写计划。

---

## 3. 验证命令

```powershell
& $py -m unittest discover -s tests -t .        # 132 tests OK
powershell -ExecutionPolicy Bypass -File scripts\validate_plugin.ps1
git diff --check

# 真实素材
& $py tools\roughcut.py transcribe --media "...\10.mp4" --out "$env:TEMP\t.json" --language zh --initial-prompt "母亲大人膝下敬禀者 儿自离家投军 现已编入戚家军 戚将军治军极严 军纪肃然"
& $py tools\roughcut.py audit --plan "...\roughcut.json" --draft "戚继光平海卫之战 第1集 粗剪v3" --transcript "$env:TEMP\t.json"
```
