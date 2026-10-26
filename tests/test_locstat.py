"""locstat 的单元测试：python -m unittest discover -s tests"""

import io
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import locstat  # noqa: E402  必须放在 sys.path 之后才能导入

PY = locstat.Syntax(line=("#",), block=(('"""', '"""'),))
C = locstat.Syntax(line=("//",), block=(("/*", "*/"),))


def new_temp_dir() -> Path:
    """在系统临时目录里开一个独立的空目录。

    没用 tempfile.TemporaryDirectory()：Windows 上它用 0700 建目录，
    在部分受限的环境里自己反而进不去，换个普通目录最省事。
    """
    base = Path(tempfile.gettempdir()) / "locstat-tests"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    return path


class TempDirTestCase(unittest.TestCase):
    """给每个测试准备一个干净的临时目录，结束时删掉。"""

    def setUp(self):
        self.root = new_temp_dir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestClassify(unittest.TestCase):
    """逐行分类是最容易出错的地方，先把它测透。"""

    def test_python_docstring_and_comments(self):
        text = (
            '"""模块文档\n'
            "第二行\n"
            '"""\n'
            "import os\n"
            "\n"
            "# 一条注释\n"
            "x = 1  # 行尾注释\n"
        )
        counts = locstat.classify_lines(text, PY)
        self.assertEqual(counts.code, 2)      # import os / x = 1
        self.assertEqual(counts.comment, 4)   # 三行 docstring + 一条独立注释
        self.assertEqual(counts.blank, 1)

    def test_block_comment_spanning_lines(self):
        counts = locstat.classify_lines("/*\n * 注释\n */\nint x = 1;\n", C)
        self.assertEqual((counts.code, counts.comment, counts.blank), (1, 3, 0))

    def test_block_comment_closed_then_code_on_same_line(self):
        counts = locstat.classify_lines("/* 注释 */ int x = 1;\n", C)
        self.assertEqual(counts.code, 1)

    def test_code_then_block_comment_start(self):
        counts = locstat.classify_lines("int x = 1; /* 注释\n继续注释\n*/\n", C)
        self.assertEqual(counts.code, 1)
        self.assertEqual(counts.comment, 2)

    def test_only_whitespace_is_blank(self):
        counts = locstat.classify_lines("\n   \n\t\n", locstat.Syntax())
        self.assertEqual((counts.code, counts.comment, counts.blank), (0, 0, 3))

    def test_empty_text(self):
        self.assertEqual(locstat.classify_lines("", locstat.Syntax()).total, 0)

    def test_crlf_line_endings(self):
        counts = locstat.classify_lines("a\r\nb\r\n", locstat.Syntax())
        self.assertEqual((counts.code, counts.total), (2, 2))

    def test_blank_inside_block_comment_counts_as_blank(self):
        # 文档化的行为：块注释里的空行记为空行，和编辑器行号对得上
        counts = locstat.classify_lines("/*\n\n*/\n", C)
        self.assertEqual((counts.comment, counts.blank), (2, 1))

    def test_counts_total(self):
        counts = locstat.Counts(code=3, comment=2, blank=1)
        self.assertEqual(counts.total, 6)


class TestDetectLanguage(unittest.TestCase):
    def test_by_extension(self):
        self.assertEqual(locstat.detect_language(Path("a.py"))[0], "Python")
        self.assertEqual(locstat.detect_language(Path("a.C"))[0], "C")
        self.assertEqual(locstat.detect_language(Path("dir/b.js"))[0], "JavaScript")

    def test_by_filename(self):
        self.assertEqual(locstat.detect_language(Path("Makefile"))[0], "Makefile")
        self.assertEqual(locstat.detect_language(Path("CMakeLists.txt"))[0], "CMake")

    def test_unknown(self):
        self.assertIsNone(locstat.detect_language(Path("data.bin")))
        self.assertIsNone(locstat.detect_language(Path("LICENSE")))


class TestScanFile(TempDirTestCase):
    def test_binary_is_skipped(self):
        path = self.root / "x.py"
        path.write_bytes(b"\x00\x01\x02binary")
        self.assertIsNone(locstat.scan_file(path, PY))

    def test_high_control_byte_ratio_is_binary(self):
        path = self.root / "x.dat"
        path.write_bytes(bytes(range(1, 40)) * 20)
        self.assertTrue(locstat.looks_binary(path.read_bytes()))

    def test_gbk_text_still_counted(self):
        path = self.root / "x.py"
        path.write_bytes("# 中文注释\nx = 1\n".encode("gbk"))
        counts = locstat.scan_file(path, PY)
        self.assertEqual((counts.code, counts.comment), (1, 1))


class TestBuildReport(TempDirTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "main.py").write_text("# 注释\nprint(1)\n\nprint(2)\n", encoding="utf-8")
        (self.root / "util.c").write_text("/* c */\nint x;\n", encoding="utf-8")
        (self.root / "README.md").write_text("<!-- 注释 -->\n正文\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("未识别的扩展名\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "hook.py").write_text("# 不该被统计\n", encoding="utf-8")

    def test_totals(self):
        report = locstat.build_report(self.root)
        totals = report.total
        self.assertEqual(totals.code, 4)             # print(1) print(2) int x; / 正文
        self.assertEqual(totals.comment, 3)          # # 注释 / /* c */ / <!-- -->
        self.assertEqual(totals.blank, 1)            # main.py 里的那个空行
        self.assertEqual(report.skipped_unknown, 1)  # notes.txt

    def test_git_directory_is_ignored(self):
        report = locstat.build_report(self.root)
        self.assertNotIn(".git/hook.py", [row.path for row in report.files])

    def test_sorted_by_code_desc(self):
        report = locstat.build_report(self.root)
        codes = [row.counts.code for row in report.languages]
        self.assertEqual(codes, sorted(codes, reverse=True))
        self.assertEqual(report.languages[0].language, "Python")

    def test_exclude_pattern(self):
        report = locstat.build_report(self.root, excludes=["*.c"])
        self.assertNotIn("C", [row.language for row in report.languages])
        self.assertEqual(report.skipped_excluded, 1)

    def test_ext_filter(self):
        report = locstat.build_report(self.root, include_exts=["py"])
        self.assertEqual([row.language for row in report.languages], ["Python"])

    def test_empty_directory(self):
        empty = new_temp_dir()
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        report = locstat.build_report(empty)
        self.assertEqual(report.total.total, 0)
        self.assertIn("没有找到可统计的源码文件", locstat.render(report))

    def test_label_is_kept(self):
        report = locstat.build_report(self.root, label="examples/demo")
        self.assertIn("examples/demo", locstat.render(report))


class TestSingleFile(TempDirTestCase):
    """路径给的是一个文件时，只统计它自己。"""

    def test_single_file(self):
        path = self.root / "only.py"
        path.write_text("# 注释\nx = 1\n", encoding="utf-8")
        (self.root / "other.py").write_text("y = 2\n", encoding="utf-8")

        report = locstat.build_report(path)

        self.assertEqual(len(report.files), 1)
        self.assertEqual(report.files[0].path, "only.py")
        self.assertEqual(report.total.code, 1)


class TestOutput(TempDirTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "a.py").write_text("# c\nx = 1\n", encoding="utf-8")

    def test_json_is_parseable(self):
        payload = json.loads(locstat.to_json(locstat.build_report(self.root)))
        self.assertEqual(payload["tool"], "locstat")
        self.assertEqual(payload["summary"]["files"], 1)
        self.assertEqual(payload["summary"]["code"], 1)
        self.assertEqual(payload["languages"][0]["language"], "Python")
        self.assertEqual(payload["files"][0]["path"], "a.py")

    def test_render_contains_summary(self):
        text = locstat.render(locstat.build_report(self.root), top=5)
        self.assertIn("代码行数统计", text)
        self.assertIn("Python", text)

    def test_bar_scales(self):
        self.assertEqual(locstat.bar(0, 10), "")
        self.assertEqual(len(locstat.bar(10, 10, width=10)), 10)
        self.assertEqual(len(locstat.bar(1, 100, width=10)), 1)  # 再小也留一个字符


class TestCli(TempDirTestCase):
    def test_missing_path_returns_2(self):
        self.assertEqual(locstat.main(["definitely-not-here-12345"]), 2)

    def test_json_flag(self):
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = locstat.main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["summary"]["code"], 1)

    def test_top_zero_hides_file_list(self):
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        report = locstat.build_report(self.root)
        self.assertNotIn("最大的文件", locstat.render(report, top=0))


if __name__ == "__main__":
    unittest.main()
