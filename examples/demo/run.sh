#!/usr/bin/env bash
# 示例脚本：打印当前目录
set -euo pipefail

target="${1:-.}"

echo "正在查看 ${target}"
ls -lh "${target}"  # 只列文件
