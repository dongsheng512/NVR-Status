#!/usr/bin/env python3
"""AST 未定义名扫描：找「用了但整个文件都没绑定过」的名字。

用途：抓漏 import / 拼错变量名这一类**测试未必覆盖**的错误。
2026-09-10 的代码审查就是靠同一思路抓到 `nvr_core/recording.py` 里
`except ScanCancelled:` 没 import —— 用户点取消时异常处理器自己先抛
NameError，把真正的取消吞掉。

用法：
    python scripts/undef_scan.py [路径 ...]      # 默认扫仓库根目录

退出码：0=无发现；1=有发现。可直接挂到 CI。

过近似（文件内任何位置绑定过即算已定义、lambda/推导式局部变量都收集），
因此**宁可少报**：真正漏 import 的名字会全中，误报来自动态赋值（globals() 之类）。
"""

from __future__ import annotations

import ast
import builtins
import pathlib
import sys

SKIP_DIRS = {".venv", "build", "dist", "__pycache__", ".git", "node_modules"}
BUILTINS = set(dir(builtins)) | {
    "__file__",
    "__name__",
    "__doc__",
    "__package__",
    "__spec__",
    "__loader__",
    "__builtins__",
    "self",
    "cls",
    "__class__",
}


def _collect_args(a: ast.arguments, out: set[str]) -> None:
    for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
        out.add(arg.arg)
    if a.vararg:
        out.add(a.vararg.arg)
    if a.kwarg:
        out.add(a.kwarg.arg)


def _collect_target(node: ast.AST, out: set[str]) -> None:
    """收集赋值目标里的名字（含元组解包 / 下标 / 属性 / 星号）。"""
    if isinstance(node, ast.Name):
        out.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _collect_target(elt, out)
    elif isinstance(node, ast.Starred):
        _collect_target(node.value, out)
    # Subscript / Attribute 不需要（目标对象自身会被单独 Load 出来）


def bound_names(tree: ast.AST) -> set[str]:
    """文件内所有被绑定过的名字（近似：不区分作用域）。"""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _collect_args(node.args, out)
        elif isinstance(node, ast.Lambda):
            _collect_args(node.args, out)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in node.generators:
                _collect_target(gen.target, out)
        elif isinstance(node, ast.Import):
            for al in node.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for al in node.names:
                out.add(al.asname or al.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.withitem):
            if isinstance(node.optional_vars, ast.Name):
                out.add(node.optional_vars.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            out.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            out.add(node.rest)
    return out


def scan_file(path: pathlib.Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return [(e.lineno or 0, f"[SYNTAX] {e.msg}")]
    defined = bound_names(tree) | BUILTINS
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id not in defined
        ):
            hits.append((node.lineno, node.id))
    return hits


def main(argv: list[str]) -> int:
    roots = [pathlib.Path(a) for a in argv[1:]] or [pathlib.Path(".")]
    targets: list[pathlib.Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            targets.append(root)
        else:
            targets.extend(sorted(root.rglob("*.py")))
    total = 0
    for path in targets:
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        for lineno, name in scan_file(path):
            print(f"[UNDEF] {path}:{lineno} {name}")
            total += 1
    print(f"--- 扫描 {len(targets)} 个文件, 发现 {total} 处 ---")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
