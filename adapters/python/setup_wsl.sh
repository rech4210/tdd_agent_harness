#!/usr/bin/env bash
# G6(돌연변이)용 WSL 가상환경. mutmut 은 Windows 를 지원하지 않는다 .
# 사용 (Windows, 저장소 루트에서): wsl -e bash -lc "cd /mnt/c/<경로>/<저장소> && bash adapters/python/setup_wsl.sh"
set -euo pipefail
python3.12 -m venv .venv-wsl
.venv-wsl/bin/python -m pip install -q --upgrade pip
.venv-wsl/bin/python -m pip install -q -e ".[test]"
.venv-wsl/bin/mutmut --version
