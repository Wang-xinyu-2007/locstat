# locstat

数一数一个项目到底有多少行代码：**代码 / 注释 / 空行** 分开算，按语言出报告。

**零第三方依赖**——只用 Python 标准库，`python locstat.py` 直接跑，不用 `pip install` 任何东西。

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![Tests](https://github.com/Wang-xinyu-2007/locstat/actions/workflows/tests.yml/badge.svg)

---

## 为什么写这个

想看看自己的项目「长胖了没有」，或者想挑一个别人不开源的仓库估估体量，
装 cloc 要配环境、要等包管理器。这个工具只有一个文件，拷过去就能跑。

顺便，它也是一个很合适的 **Python 练习项目**：没有框架、没有第三方库，
pathlib、dataclass、生成器、状态机、argparse、unittest 都用上了，
从头读到尾大概二十分钟。

---

## 功能

- **按语言统计**：文件数、代码行、注释行、空行、总行数、占比
- **认得注释**：行注释（`#`、`//`、`--`、`;` …）和块注释（`/* */`、`<!-- -->`、`"""` …），支持跨行、行内、代码后面跟注释
- **自动跳过**：`.git`、`node_modules`、`venv`、`build`、`dist`、`__pycache__` 等目录压根不会进去
- **跳过二进制**：前 8KB 里有 NUL 或者三成以上控制字符，直接当二进制跳过
- **条形图**：一眼看出哪种语言占大头
- **最大的文件 Top N**：找出那一个顶十个的巨型文件
- **`--json`**：机器可读，方便接 `jq` 或者写进自己的统计脚本
- **不怕脏数据**：编码坏了、文件读不了，都只是记一笔跳过，不会中断整个扫描

---

## 快速开始

```bash
# 1. 拿到代码
git clone https://github.com/Wang-xinyu-2007/locstat.git
cd locstat

# 2. 直接跑（不需要装任何包）
python locstat.py examples/demo
```

`examples/demo/` 里放了四个小文件（Python / C / CSS / Shell），用来演示统计结果。

---

## 用法

```bash
# 统计当前目录
python locstat.py

# 统计指定目录
python locstat.py path/to/project

# 也可以只统计一个文件
python locstat.py locstat.py

# 额外排除一些文件（目录名或通配符，可以写多次）
python locstat.py . --exclude test_*.py --exclude vendor

# 只看 Python 和 C
python locstat.py . --ext py --ext c

# 列出最大的 5 个文件
python locstat.py . --top 5

# 输出 JSON，交给别的脚本处理
python locstat.py . --json

# 看所有参数
python locstat.py --help
```

| 参数 | 说明 |
| --- | --- |
| `path` | 要统计的目录或文件，默认当前目录 |
| `--exclude PATTERN` | 额外跳过的文件名/相对路径通配符，可重复 |
| `--ext EXT` | 只统计这些扩展名，可重复 |
| `--top N` | 列出最大的 N 个文件，`0` 表示不列 |
| `--json` | 输出 JSON |

---

## 输出示例

```
==============================================================
代码行数统计  locstat v1.0.0
==============================================================
目录 examples/demo  ·  文件 4 个  ·  语言 4 种  ·  代码 38 行
总计 66 行 = 代码 38 + 注释 14 + 空行 14  ·  注释率 26.9%

【按语言】
Language           Files     Code  Comment   Blank    Total   Share
-------------------------------------------------------------------
C                      1       18        5       2       25   37.9%
Python                 1        8        6       9       23   34.8%
CSS                    1        8        1       1       10   15.2%
Shell                  1        4        2       2        8   12.1%

【代码行分布】
  C               ████████████████████████████ 18
  Python          ████████████ 8
  CSS             ████████████ 8
  Shell           ██████ 4

【最大的文件】（显示 3/4 个）
      代码        总行  语言            路径
-------------------------------------------------------------------
      18        25  C             util.c
       8        23  Python        app.py
       8        10  CSS           style.css
```

`--json` 的输出长这样（省略了大部分文件）：

```json
{
  "tool": "locstat",
  "version": "1.0.0",
  "root": "/home/you/locstat/examples/demo",
  "label": "examples/demo",
  "summary": {
    "files": 4,
    "languages": 4,
    "code": 38,
    "comment": 14,
    "blank": 14,
    "total": 66,
    "comment_ratio": 0.2692
  },
  "languages": [
    { "language": "C", "files": 1, "code": 18, "comment": 5, "blank": 2, "total": 25 }
  ],
  "files": [
    { "path": "util.c", "language": "C", "code": 18, "comment": 5, "blank": 2, "total": 25 }
  ]
}
```

---

## 它是怎么判断「这一行是注释」的

这是整个程序里唯一需要动脑的地方，做法是一个很小的状态机：

```
对文件里的每一行：
    如果整行都是空白          -> 空行
    否则从左往右扫：
        正处在块注释里？      -> 找结束符，找到就退出块注释，继续扫
        遇到块注释开始符？    -> 进入块注释状态，继续扫
        遇到行注释开始符？    -> 这一行后面全是注释，结束
        其他字符              -> 说明这行有代码，记下来，继续扫
```

于是这几种情况都能算对：

| 写法 | 结果 |
| --- | --- |
| `int x = 1;` | 代码 |
| `// 注释` | 注释 |
| `int x = 1; // 注释` | 代码（这行有代码） |
| `/* 注释 */ int x = 1;` | 代码 |
| `/*` 开头跨好几行 | 注释 |

每种语言的注释符号写在 `_BY_EXT` 表里，加一门新语言基本就是加一行。

---

## 项目结构

```
locstat/
├── locstat.py                  # 主程序：识别语言、分类行、汇总、输出（约 400 行）
├── tests/
│   └── test_locstat.py         # 29 个单元测试
├── examples/
│   └── demo/                   # 四个小文件，用来演示输出
│       ├── app.py
│       ├── util.c
│       ├── style.css
│       └── run.sh
├── README.md
├── LICENSE
└── .gitignore
```

---

## 跑测试

```bash
python -m unittest discover -s tests
```

覆盖：块注释跨行 / 行内 / 代码后注释、空行与 CRLF、语言识别、二进制跳过、
目录排除、`--ext` 过滤、JSON 结构、空目录、不存在的路径。

---

## 已知限制

- **不解析字符串**：`"// 这不是注释"` 这种极端写法会被当成注释开头，对行数影响可以忽略
- **块注释里的空行算空行**，不算注释行（和编辑器显示的行号对得上）
- 语言只按扩展名和文件名识别，没有扩展名的脚本会被跳过
- 只按行统计，不看一行里注释占了多少字符

---

## 许可

[MIT](LICENSE)
