#!/usr/bin/env python3
"""locstat —— 统计一个项目里有多少「代码行 / 注释行 / 空行」。

用法：
    python locstat.py                # 统计当前目录
    python locstat.py path/to/proj   # 统计指定目录
    python locstat.py . --json       # 机器可读输出

设计目标：只用 Python 标准库，单文件，读完这一个文件就能明白它是怎么工作的。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# 1. 语言识别：扩展名 / 文件名 -> 语言名 + 注释语法
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Syntax:
    """一门语言的注释长什么样。

    line  : 行注释前缀，例如 Python 的 "#"、C 的 "//"
    block : 块注释的 (开始, 结束) 对，例如 ("/*", "*/")、('"""', '"""')
    """

    line: tuple[str, ...] = ()
    block: tuple[tuple[str, str], ...] = ()


# 注释语法相同的语言归为一组，避免重复抄写
_HASH = Syntax(line=("#",))
_SLASH = Syntax(line=("//",), block=(("/*", "*/"),))
_DASH = Syntax(line=("--",), block=(("/*", "*/"),))
_XML = Syntax(block=(("<!--", "-->"),))
_CSS = Syntax(block=(("/*", "*/"),))
_SEMI = Syntax(line=(";",))
_PY = Syntax(line=("#",), block=(('"""', '"""'), ("'''", "'''")))
_DOC = Syntax(line=("///", "//!", "//"), block=(("/*", "*/"),))

# 扩展名 -> (语言名, 注释语法)
_BY_EXT: dict[str, tuple[str, Syntax]] = {
    ".py": ("Python", _PY),
    ".pyi": ("Python", _PY),
    ".c": ("C", _SLASH),
    ".h": ("C/C++ Header", _DOC),
    ".cpp": ("C++", _SLASH),
    ".cc": ("C++", _SLASH),
    ".hpp": ("C++", _SLASH),
    ".cs": ("C#", _SLASH),
    ".java": ("Java", _SLASH),
    ".kt": ("Kotlin", _SLASH),
    ".swift": ("Swift", _SLASH),
    ".go": ("Go", _SLASH),
    ".rs": ("Rust", _SLASH),
    ".js": ("JavaScript", _SLASH),
    ".mjs": ("JavaScript", _SLASH),
    ".ts": ("TypeScript", _SLASH),
    ".tsx": ("TypeScript", _SLASH),
    ".vue": ("Vue", _XML),
    ".html": ("HTML", _XML),
    ".htm": ("HTML", _XML),
    ".xml": ("XML", _XML),
    ".css": ("CSS", _CSS),
    ".scss": ("SCSS", _SLASH),
    ".sh": ("Shell", _HASH),
    ".bash": ("Shell", _HASH),
    ".ps1": ("PowerShell", _HASH),
    ".bat": ("Batch", Syntax(line=("REM ", "rem ", "::"))),
    ".sql": ("SQL", _DASH),
    ".lua": ("Lua", _DASH),
    ".rb": ("Ruby", _HASH),
    ".php": ("PHP", Syntax(line=("//", "#"), block=(("/*", "*/"),))),
    ".r": ("R", _HASH),
    ".yml": ("YAML", _HASH),
    ".yaml": ("YAML", _HASH),
    ".toml": ("TOML", _HASH),
    ".ini": ("INI", _SEMI),
    ".cfg": ("INI", _SEMI),
    ".md": ("Markdown", _XML),
    ".rst": ("reStructuredText", Syntax(line=("..",))),
    ".tex": ("TeX", Syntax(line=("%",))),
    ".vim": ("VimScript", Syntax(line=('"',))),
    ".asm": ("Assembly", Syntax(line=(";", "#"))),
    ".s": ("Assembly", Syntax(line=(";", "#"))),
    ".m": ("Objective-C", _SLASH),
}

# 没有扩展名、但有固定名字的文件
_BY_NAME: dict[str, tuple[str, Syntax]] = {
    "Makefile": ("Makefile", _HASH),
    "makefile": ("Makefile", _HASH),
    "GNUmakefile": ("Makefile", _HASH),
    "Dockerfile": ("Dockerfile", _HASH),
    "CMakeLists.txt": ("CMake", _HASH),
    "Rakefile": ("Ruby", _HASH),
    ".editorconfig": ("INI", _SEMI),
}


def detect_language(path: Path) -> tuple[str, Syntax] | None:
    """根据文件名猜语言。猜不出来返回 None（调用方决定要不要跳过）。"""
    name = path.name
    if name in _BY_NAME:
        return _BY_NAME[name]
    # 特殊后缀，例如 foo.go.mod —— 先匹配长的复合后缀
    lower = name.lower()
    for ext in sorted(_BY_EXT, key=len, reverse=True):
        if lower.endswith(ext):
            return _BY_EXT[ext]
    return None


# ---------------------------------------------------------------------------
# 2. 逐行分类：这一行是空行、注释，还是代码？
# ---------------------------------------------------------------------------

@dataclass
class Counts:
    """一个文件（或一组文件）的四类行数。"""

    code: int = 0
    comment: int = 0
    blank: int = 0

    @property
    def total(self) -> int:
        return self.code + self.comment + self.blank

    def __iadd__(self, other: "Counts") -> "Counts":
        self.code += other.code
        self.comment += other.comment
        self.blank += other.blank
        return self


def classify_lines(text: str, syntax: Syntax) -> Counts:
    """把一段源码按行分成 代码 / 注释 / 空行。

    这是整个程序的核心，思路是一个很小的状态机：

      * 普通状态：遇到的第一个非空白字符如果是注释开头，整行算注释；
        否则这行里有代码（哪怕后面跟着注释）。
      * 块注释状态：一直吃到块注释结束符；如果结束符后面还有代码，
        这一行仍然算代码行。

    说明：这里不解析字符串，所以 `"// 不是注释"` 这种极端情况会被算错，
    对行数统计影响可以忽略——把它当作一个已知限制而不是 bug。
    """
    counts = Counts()
    block_end: str | None = None  # 非 None 表示"正处在块注释里"

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            counts.blank += 1
            continue

        index = 0
        has_code = False
        length = len(line)

        while index < length:
            if block_end is not None:
                # 在块注释里：找结束符
                close = line.find(block_end, index)
                if close == -1:
                    index = length
                    break
                index = close + len(block_end)
                block_end = None
                continue

            char = line[index]
            if char.isspace():
                index += 1
                continue

            # 块注释开始？
            opened = False
            for start, end in syntax.block:
                if line.startswith(start, index):
                    block_end = end
                    index += len(start)
                    opened = True
                    break
            if opened:
                continue

            # 行注释：后面全是注释，可以直接结束这一行
            for prefix in syntax.line:
                if line.startswith(prefix, index):
                    index = length
                    break
            else:
                # 既不是注释开头，就是普通代码字符
                has_code = True
                index += 1
                continue
            break

        if has_code:
            counts.code += 1
        else:
            counts.comment += 1

    return counts


def looks_binary(chunk: bytes) -> bool:
    """粗判二进制：前 8KB 里出现 NUL 或者大量不可打印字节，就当二进制跳过。"""
    if b"\x00" in chunk:
        return True
    if not chunk:
        return False
    control = sum(1 for b in chunk if b < 9 or (13 < b < 32))
    return control / len(chunk) > 0.30


def scan_file(path: Path, syntax: Syntax) -> Counts | None:
    """读一个文件并分类。返回 None 表示它是二进制、跳过。"""
    with path.open("rb") as fh:
        head = fh.read(8192)
        if looks_binary(head):
            return None
        rest = fh.read()
    # 解码失败也不影响行数统计，用替换字符兜底
    text = (head + rest).decode("utf-8", errors="replace")
    return classify_lines(text, syntax)


# ---------------------------------------------------------------------------
# 3. 遍历目录：哪些文件要统计？
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDES = (
    ".git", ".hg", ".svn",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", "bower_components",
    ".venv", "venv", "env", ".env",
    "build", "dist", "target", "out", "bin", "obj",
    ".idea", ".vscode", ".DS_Store",
)


def walk_files(root: Path, excludes: Sequence[str]) -> Iterator[Path]:
    """递归列出 root 下所有普通文件，跳过隐藏/依赖/构建目录。

    用 os.walk 而不是 Path.rglob，因为可以在原地裁剪 dirnames，
    整个被排除的目录一次都不会进去——这是这类工具主要的性能来源。
    """
    skip_dirs = set(DEFAULT_EXCLUDES) | set(excludes)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.is_file():
                yield path


def matches_exclude(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    """--exclude 的匹配：既支持文件名通配，也支持相对路径通配。"""
    if not patterns:
        return False
    rel = path.relative_to(root).as_posix()
    return any(
        fnmatch.fnmatch(path.name, pat) or fnmatch.fnmatch(rel, pat)
        for pat in patterns
    )


# ---------------------------------------------------------------------------
# 4. 汇总成报告
# ---------------------------------------------------------------------------

@dataclass
class FileRow:
    path: str
    language: str
    counts: Counts


@dataclass
class LangRow:
    language: str
    files: int
    counts: Counts = field(default_factory=Counts)


@dataclass
class Report:
    root: Path
    languages: list[LangRow]
    files: list[FileRow]
    label: str = ""
    skipped_binary: int = 0
    skipped_unknown: int = 0
    skipped_excluded: int = 0

    @property
    def total(self) -> Counts:
        counts = Counts()
        for row in self.languages:
            counts += row.counts
        return counts


def build_report(
    root: Path,
    excludes: Sequence[str] = (),
    include_exts: Sequence[str] = (),
    label: str | None = None,
) -> Report:
    """扫描 root，返回完整报告。

    root 可以是目录，也可以是单个文件（那就只统计这一个文件）。
    """
    languages: dict[str, LangRow] = {}
    files: list[FileRow] = []
    skipped_binary = skipped_unknown = skipped_excluded = 0

    allowed = {e.lower() if e.startswith(".") else "." + e.lower() for e in include_exts}

    if root.is_file():
        base = root.parent
        candidates: Iterable[Path] = [root]
    else:
        base = root
        candidates = walk_files(root, excludes)

    for path in candidates:
        if not root.is_file() and matches_exclude(path, base, excludes):
            skipped_excluded += 1
            continue

        detected = detect_language(path)
        if detected is None:
            skipped_unknown += 1
            continue

        language, syntax = detected
        if allowed and path.suffix.lower() not in allowed:
            skipped_unknown += 1
            continue

        try:
            counts = scan_file(path, syntax)
        except OSError as exc:  # 权限、被占用等等，记一笔但不要中断
            print(f"locstat: 跳过 {path}: {exc}", file=sys.stderr)
            skipped_unknown += 1
            continue

        if counts is None:
            skipped_binary += 1
            continue

        files.append(FileRow(path.relative_to(base).as_posix(), language, counts))
        row = languages.setdefault(language, LangRow(language, 0))
        row.files += 1
        row.counts += counts

    # 按代码行数从多到少排，代码行相同时按总行数
    ordered = sorted(languages.values(), key=lambda r: (r.counts.code, r.counts.total), reverse=True)
    files.sort(key=lambda f: (f.counts.code, f.counts.total), reverse=True)
    return Report(root, ordered, files, label if label is not None else root.as_posix(),
                  skipped_binary, skipped_unknown, skipped_excluded)


# ---------------------------------------------------------------------------
# 5. 输出
# ---------------------------------------------------------------------------

def human(n: int) -> str:
    """1234 -> 1,234；让大数字更好读。"""
    return f"{n:,}"


def bar(value: int, maximum: int, width: int = 28) -> str:
    """用 █ 画一个按比例缩放的条形图。"""
    if maximum <= 0 or value <= 0:
        return ""
    filled = max(1, round(value / maximum * width))
    return "█" * filled


def ratio(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "0.0%"


def render(report: Report, top: int = 10) -> str:
    """把报告渲染成终端里的文本。"""
    totals = report.total
    lines: list[str] = []
    add = lines.append

    title = f"代码行数统计  locstat v{__version__}"
    add("=" * 62)
    add(title)
    add("=" * 62)
    add(
        f"目录 {report.label}  ·  文件 {human(len(report.files))} 个  ·  "
        f"语言 {len(report.languages)} 种  ·  代码 {human(totals.code)} 行"
    )
    add(
        f"总计 {human(totals.total)} 行 = 代码 {human(totals.code)}"
        f" + 注释 {human(totals.comment)} + 空行 {human(totals.blank)}"
        f"  ·  注释率 {ratio(totals.comment, totals.code + totals.comment)}"
    )
    add("")

    if not report.languages:
        add("没有找到可统计的源码文件。")
        return "\n".join(lines)

    add("【按语言】")
    add(f"{'Language':<18}{'Files':>6}{'Code':>9}{'Comment':>9}{'Blank':>8}{'Total':>9}{'Share':>8}")
    add("-" * 67)
    max_code = max(row.counts.code for row in report.languages) or 1
    for row in report.languages:
        add(
            f"{row.language:<18}{row.files:>6}{human(row.counts.code):>9}"
            f"{human(row.counts.comment):>9}{human(row.counts.blank):>8}"
            f"{human(row.counts.total):>9}{ratio(row.counts.total, totals.total):>8}"
        )
    add("")

    add("【代码行分布】")
    for row in report.languages:
        add(f"  {row.language:<16}{bar(row.counts.code, max_code)} {human(row.counts.code)}")
    add("")

    if top > 0 and report.files:
        shown = report.files[:top]
        add(f"【最大的文件】（显示 {len(shown)}/{len(report.files)} 个）")
        add(f"{'代码':>8}  {'总行':>8}  {'语言':<14}路径")
        add("-" * 67)
        for item in shown:
            add(
                f"{human(item.counts.code):>8}  {human(item.counts.total):>8}  "
                f"{item.language:<14}{item.path}"
            )
        add("")

    skipped = []
    if report.skipped_binary:
        skipped.append(f"二进制 {report.skipped_binary}")
    if report.skipped_unknown:
        skipped.append(f"未知类型 {report.skipped_unknown}")
    if report.skipped_excluded:
        skipped.append(f"被排除 {report.skipped_excluded}")
    if skipped:
        add("跳过：" + " · ".join(skipped))

    return "\n".join(lines)


def to_json(report: Report) -> str:
    """给脚本消费的 JSON 版本（字段名固定，方便写 jq 查询）。"""
    totals = report.total
    payload = {
        "tool": "locstat",
        "version": __version__,
        "root": report.root.as_posix(),
        "label": report.label,
        "summary": {
            "files": len(report.files),
            "languages": len(report.languages),
            "code": totals.code,
            "comment": totals.comment,
            "blank": totals.blank,
            "total": totals.total,
            "comment_ratio": round(totals.comment / (totals.code + totals.comment), 4)
            if (totals.code + totals.comment)
            else 0.0,
            "skipped_binary": report.skipped_binary,
            "skipped_unknown": report.skipped_unknown,
            "skipped_excluded": report.skipped_excluded,
        },
        "languages": [
            {
                "language": row.language,
                "files": row.files,
                "code": row.counts.code,
                "comment": row.counts.comment,
                "blank": row.counts.blank,
                "total": row.counts.total,
            }
            for row in report.languages
        ],
        "files": [
            {
                "path": item.path,
                "language": item.language,
                "code": item.counts.code,
                "comment": item.counts.comment,
                "blank": item.counts.blank,
                "total": item.counts.total,
            }
            for item in report.files
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 6. 命令行
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="locstat",
        description="统计一个目录里的代码行 / 注释行 / 空行。",
        epilog="示例：python locstat.py . --exclude '*.min.js' --top 5",
    )
    parser.add_argument("path", nargs="?", default=".", help="要统计的目录（默认当前目录）")
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN",
        help="额外跳过的目录名或通配符，可重复，例如 --exclude test_*.py",
    )
    parser.add_argument(
        "--ext", action="append", default=[], metavar="EXT",
        help="只统计这些扩展名，可重复，例如 --ext py --ext c",
    )
    parser.add_argument("--top", type=int, default=10, metavar="N", help="列出最大的 N 个文件（0 表示不列）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    parser.add_argument("--version", action="version", version=f"locstat {__version__}")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.path)
    if not root.exists():
        print(f"locstat: 路径不存在：{root}", file=sys.stderr)
        return 2

    if args.top < 0:
        print("locstat: --top 不能是负数", file=sys.stderr)
        return 2

    report = build_report(root.resolve(), args.exclude, args.ext, label=args.path)
    print(to_json(report) if args.json else render(report, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
