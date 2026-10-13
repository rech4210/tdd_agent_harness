"""경로 규약 (소유권 규칙, 가이드 §11 V7).

- 하네스 소유: harness/ (Core 코드), adapters/, templates/  — 프로젝트가 고치지 않는다. 업그레이드 때 통째로 바뀐다
- 프로젝트 소유: config/ (게이트 · 역할 · 슬라이스 · 배정 · 에이전트 설정, 잠금 파일), src/, tests/, reports/ …

config/ 가 없으면 예전 배치(harness/ 안의 *.toml)를 읽는다. 기존 프로젝트와의 호환용이다.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" if (ROOT / "config" / "gates.toml").is_file() else ROOT / "harness"
REPORTS = ROOT / "reports"

GATES = CONFIG / "gates.toml"
SLICES = CONFIG / "slices.toml"
ROLES = CONFIG / "roles.toml"
AGENTS = CONFIG / "agents.toml"
ASSIGNMENTS = CONFIG / "assignments.toml"
LOCK = CONFIG / "test_lock.json"
ARCHITECTURE = CONFIG / "architecture.toml"
