"""어댑터 로더: gates.toml [harness] adapter = "<이름>" 의 어댑터를 불러온다.

Core 는 어댑터 이름과 계약(함수 이름)만 안다. 언어 · 도구 세부는 adapters/<이름>/adapter.py 에 있다.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path
from types import ModuleType

from paths import GATES, ROOT


def harness_settings() -> dict:
    return tomllib.loads(GATES.read_text(encoding="utf-8")).get("harness", {})


def load_adapter(name: str | None = None) -> ModuleType:
    name = name or harness_settings().get("adapter", "python")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(f"adapters.{name}.adapter")
