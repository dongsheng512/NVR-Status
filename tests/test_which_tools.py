"""_which_tools / 捆绑 bin 路径探测。"""

from __future__ import annotations

import os
from pathlib import Path

from nvr_core.util import _is_runnable_binary, _tool_bin_dirs, _which_tools


def test_tool_bin_dirs_includes_project_bin():
    dirs = _tool_bin_dirs()
    assert any(d.endswith("bin") or d.endswith(f"bin{os.sep}") or d.endswith("bin") for d in dirs)
    # 开发树应包含项目 bin/
    project_bin = str(Path(__file__).resolve().parents[1] / "bin")
    assert any(os.path.normpath(d) == os.path.normpath(project_bin) for d in dirs)


def test_which_tools_finds_repo_bin_if_present():
    """仓库 bin/ 有 ffmpeg 时能找到（CI 可能没有，则跳过）。"""
    root = Path(__file__).resolve().parents[1]
    ff = root / "bin" / "ffmpeg"
    fp = root / "bin" / "ffprobe"
    if not (ff.is_file() and fp.is_file()):
        return
    tools = _which_tools()
    assert tools.get("ffmpeg")
    assert tools.get("ffprobe")
    assert Path(tools["ffmpeg"]).name.startswith("ffmpeg")
    assert Path(tools["ffprobe"]).name.startswith("ffprobe")


def test_is_runnable_binary_false_for_missing(tmp_path: Path):
    assert _is_runnable_binary(str(tmp_path / "nope")) is False


def test_is_runnable_binary_true_for_script(tmp_path: Path):
    p = tmp_path / "tool"
    p.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    p.chmod(0o644)  # 无 +x，应尝试 chmod 后仍可用或至少 isfile+R_OK
    assert _is_runnable_binary(str(p)) is True
