"""模板批量出片的共享核心。

把"拿一个现成草稿当模板、复制成新草稿、替换其中的素材与文案"这件事做成一组
普通函数：``tools/template_fill.py``（清单驱动的 CLI）与 ``mcp_ext/server.py``
（MCP 工具）都只是它的薄包装。

草稿读写一律走 jianying_tool 的编解码层：新版剪映的 ``draft_content.json``
是密文，只有本机剪映自带的 ``videoeditor.dll`` 能读能写；同时它会校验根文件与
活动时间线是否一致，不一致就拒绝，避免把两个时间线写岔。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from jianying_tool.metadata import write_draft_meta_info
from jianying_tool.native_codec import decode_file, project_content_path, update_project_json
from jianying_tool.paths import draft_root

SEC = 1_000_000
"""一秒的微秒数，剪映内部时间单位。"""

BACKUP_DIR = ".jianying-tool-backups"
"""工具自身写草稿前的备份目录：复制草稿时必须排除，否则体积按份数翻倍。"""


class TemplateError(RuntimeError):
    """模板批量出片过程中的可预期错误。"""


class RowFailed(TemplateError):
    """清单里某一行失败（只影响该行，不中断整批）。"""

    def __init__(self, row: str, reason: str) -> None:
        super().__init__(f"{row}: {reason}")
        self.row = row
        self.reason = reason


def resolve_root(root: str | Path | None = None) -> Path:
    """解析草稿根目录：显式参数 > ``JIANYING_DRAFT_ROOT`` > 工具自动探测。"""
    if root is not None:
        candidate = Path(root).expanduser()
    elif os.environ.get("JIANYING_DRAFT_ROOT"):
        candidate = Path(os.environ["JIANYING_DRAFT_ROOT"]).expanduser()
    else:
        candidate = draft_root()
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise TemplateError(f"草稿根目录不存在：{candidate}")
    return candidate


def _project(root: Path, name: str) -> Path:
    """定位草稿目录，不存在就报错（而不是默默新建一个空目录）。"""
    project = root / name
    if not name or not project.is_dir():
        raise FileNotFoundError(f"草稿不存在：{project}")
    return project


def read_draft(root: Path, name: str) -> tuple[dict[str, Any], str]:
    """读草稿内容，返回 ``(明文内容, 格式名)``；密文由本机编解码层解开。"""
    project = _project(resolve_root(root), name)
    return decode_file(project_content_path(project))


def write_draft(root: Path, name: str, content: dict[str, Any]) -> None:
    """把草稿内容写回去，按原格式重新加密，并校验多时间线一致性。"""
    project = _project(resolve_root(root), name)
    update_project_json(project, lambda current: (current.clear(), current.update(content)))


def duplicate(root: Path, template: str, target: str, *, allow_replace: bool = False) -> Path:
    """把 ``template`` 草稿复制成 ``target``，并给副本一个独立的草稿身份。

    排除 ``.jianying-tool-backups``；草稿目录之外的素材路径原样保留。
    """
    root = resolve_root(root)
    source = _project(root, template)
    destination = root / target
    if destination.exists() and not allow_replace:
        raise FileExistsError(f"草稿已存在：{destination}")
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=allow_replace,
        ignore=shutil.ignore_patterns(BACKUP_DIR),
    )
    write_draft_meta_info(destination, root=root)
    return destination
