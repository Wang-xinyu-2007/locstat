"""示例：一个用来演示 locstat 的小程序。

这整个文件的顶部三引号是块注释（docstring），
locstat 会把它们记成注释行而不是代码行。
"""

import math

# 常量：半径
RADIUS = 2.5


def area(radius: float) -> float:
    """圆面积。"""
    return math.pi * radius * radius  # 行尾注释也算代码行


def main() -> None:
    print(f"面积 = {area(RADIUS):.2f}")


if __name__ == "__main__":
    main()
