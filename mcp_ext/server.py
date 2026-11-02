"""模板批量出片的 MCP 工具层。

三个工具都是 ``tools/jy_template.py`` 的薄包装：真正的读写、替换、编排逻辑在那边，
这里只负责把参数接过来、把结果变成 MCP 能序列化的形状。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import jy_template  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402


def _root(value: str | None) -> Path:
    return jy_template.resolve_root(value)


def list_template_slots(draft: str, root: str | None = None) -> dict[str, Any]:
    """列出某个草稿的可替换槽位：轨道、片段下标、当前素材名与文案，以及能不能替换。"""
    return jy_template.list_slots(_root(root), draft)


def duplicate_local_draft(
    source: str, target: str, root: str | None = None, allow_replace: bool = False
) -> dict[str, Any]:
    """把 source 草稿复制成 target 草稿，并给副本一个独立的草稿身份。

    复制会排除 ``.jianying-tool-backups``（工具自身的写前备份），否则每复制一份都会把
    历史备份一起带上。已有同名草稿时默认拒绝，不覆盖用户已有草稿。
    """
    path = jy_template.duplicate(_root(root), source, target, allow_replace=allow_replace)
    return {"draft": str(path), "name": target}


def import_local_track(
    source_draft: str,
    target_draft: str,
    track: str,
    root: str | None = None,
    offset: float = 0.0,
    new_name: str | None = None,
) -> dict[str, Any]:
    """把一条轨道从 source_draft 整条搬进 target_draft（素材条目随轨道一起过去）。

    ``offset`` 是整条轨道的时间平移量（秒）。目标已有同名轨道、或已有同 id 素材时直接
    报错：底层保留素材 id，同一条轨道不能重复导入。
    """
    return jy_template.import_track(
        _root(root), source_draft, target_draft, track, offset=offset, new_name=new_name
    )


def build_server() -> MCPServer:
    """建好带三个工具的服务器；启动前先把剪映路径注入环境变量。"""
    jy_template.apply_jianying_env()
    server = MCPServer(
        "jianying-ext",
        title="Jianying Templates",
        description="模板批量出片：列槽位、复制草稿、跨草稿搬轨道。",
    )
    for tool in (list_template_slots, duplicate_local_draft, import_local_track):
        server.tool()(tool)
    return server


def main() -> int:
    build_server().run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
