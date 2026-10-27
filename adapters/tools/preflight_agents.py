"""위임 전 권한 사전 점검 공통 도구 (읽기 전용, HD-40 §3a · HD-09 · WO-H63).

리드가 위임하기 전에, 특히 리드 역할이 바뀐 뒤에 실행한다. 아무 설정도 바꾸지 않는다.
  1 Orca 프로필 DB 의 에이전트별 기본 실행 인자(agentDefaultArgs) — 샌드박스 · 승인 우회 인자 확인
  2 Codex 사용자 설정(config.toml) 의 sandbox_mode · approval_policy
  3 AGY 기본 CLI 프로젝트의 deny 목록 — 편집 전용이면 command(*) 가 막혀 있어야 한다
  3b AGY 다른 프로젝트 — 같은 폴더의 다른 *.json 의 command(*) 차단 확인

사용: python adapters/tools/preflight_agents.py [--orca-db P] [--codex-config P] [--agy-project P]
종료: FAIL(샌드박스 · 권한 우회)이 있으면 1, 아니면 0. 파일이 없거나 못 읽으면 WARN(확인 불가).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import sys
import tomllib
from pathlib import Path

sys.dont_write_bytecode = True

# 인자 안에 이 문자열이 있으면 샌드박스 · 승인 우회로 본다 (소문자 비교).
BYPASS_MARKS = ("dangerously", "bypass", "yolo", "danger-full-access")
ALWAYS_SHOWN = ("codex", "claude", "antigravity")
AGY_REQUIRED_DENY = "command(*)"

Row = tuple[str, str, str]  # (항목, 결과, 내용)


def default_paths() -> dict[str, Path]:
    home = Path.home()
    appdata = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else home / "AppData" / "Roaming"
    codex_home = Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else home / ".codex"
    return {
        "orca": appdata / "orca" / "profiles" / "local-default" / "profile-state.db",
        "codex": codex_home / "config.toml",
        "agy": home / ".gemini" / "config" / "projects" / "default-cli-project.json",
    }


def split_args(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def classify_args(text: str) -> tuple[str, str]:
    tokens = split_args(text)
    bypass = [t for t in tokens if any(m in t.lower() for m in BYPASS_MARKS)]
    if bypass:
        return "FAIL", "우회 인자: " + " ".join(bypass)
    lowered = [t.lower() for t in tokens]
    approval_off = any(t in ("--ask-for-approval=never", "approval_policy=never", 'approval_policy="never"')
                       for t in lowered)
    approval_off |= any(a in ("--ask-for-approval", "-a") and b == "never" for a, b in zip(lowered, lowered[1:]))
    if approval_off:
        return "WARN", "승인 끔 (샌드박스는 유지): " + text
    return "PASS", text or "(추가 인자 없음)"


def check_orca(path: Path) -> list[Row]:
    item = "Orca 인자"
    if not path.is_file():
        return [(item, "WARN", f"확인 불가: 파일 없음 ({path})")]
    try:
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            found = con.execute("SELECT payload FROM profile_state_documents WHERE domain = 'settings'").fetchone()
        finally:
            con.close()
        settings = json.loads(found[0]) if found else {}
    except (sqlite3.Error, json.JSONDecodeError, TypeError) as e:
        return [(item, "WARN", f"확인 불가: 읽기 실패 ({e})")]
    args = settings.get("agentDefaultArgs") if isinstance(settings, dict) else None
    if not isinstance(args, dict):
        return [(item, "WARN", "확인 불가: settings 에 agentDefaultArgs 없음")]
    rows: list[Row] = []
    for agent in sorted(args, key=lambda a: (a not in ALWAYS_SHOWN, a)):
        value = args[agent] if isinstance(args[agent], str) else json.dumps(args[agent], ensure_ascii=False)
        if agent not in ALWAYS_SHOWN and not value.strip():
            continue
        result, detail = classify_args(value.strip())
        if agent == "codex" and value.strip():
            detail += " — config.toml 보다 우선"
        rows.append((f"{item}: {agent}", result, detail))
    return rows


def walk_tables(table: dict, prefix: str = ""):
    yield prefix, table
    for key, value in table.items():
        if isinstance(value, dict):
            yield from walk_tables(value, f"{prefix}.{key}" if prefix else key)


def check_codex(path: Path) -> list[Row]:
    item = "Codex 설정"
    if not path.is_file():
        return [(item, "WARN", f"확인 불가: 파일 없음 ({path})")]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return [(item, "WARN", f"확인 불가: 읽기 실패 ({e})")]
    rows: list[Row] = []
    for prefix, table in walk_tables(data):
        where = f"{prefix}." if prefix else ""
        sandbox, approval = table.get("sandbox_mode"), table.get("approval_policy")
        if sandbox is not None:
            result = "FAIL" if sandbox == "danger-full-access" else "PASS"
            rows.append((f"{item}: {where}sandbox_mode", result, str(sandbox)))
        if approval is not None:
            result = "WARN" if approval == "never" else "PASS"
            rows.append((f"{item}: {where}approval_policy", result, str(approval)))
    for key in ("sandbox_mode", "approval_policy"):
        if key not in data:
            rows.insert(0, (f"{item}: {key}", "WARN", "최상위 값 없음 (Codex 기본값 · 실행 인자에 따름)"))
    return rows


def check_agy(path: Path) -> list[Row]:
    item = "AGY deny"
    if not path.is_file():
        return [(item, "WARN", f"확인 불가: 파일 없음 ({path})")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        grants = data["permissionGrants"]["permissionGrants"]
        deny = grants.get("deny") or []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
        return [(item, "WARN", f"확인 불가: 읽기 실패 ({type(e).__name__}: {e})")]
    if AGY_REQUIRED_DENY in deny:
        return [(item, "PASS", f"{AGY_REQUIRED_DENY} 차단 (편집 전용) — deny: {', '.join(deny)}")]
    return [(item, "FAIL", f"{AGY_REQUIRED_DENY} 가 deny 에 없음 → 명령 실행 가능 (HD-09 위반) — deny: {', '.join(deny) or '없음'}")]


def check_agy_others(path: Path) -> list[Row]:
    item = "AGY 다른 프로젝트"
    folder = path.parent
    if not folder.is_dir():
        return []
    try:
        candidates = sorted(folder.glob("*.json"))
    except OSError:
        return []

    try:
        target_resolved = path.resolve()
    except Exception:
        target_resolved = None

    other_files: list[Path] = []
    for p in candidates:
        if not p.is_file():
            continue
        try:
            if target_resolved is not None and p.resolve() == target_resolved:
                continue
        except Exception:
            pass
        if p.name.lower() == path.name.lower():
            continue
        other_files.append(p)

    if not other_files:
        return []

    bad: list[str] = []
    for p in other_files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            grants = data["permissionGrants"]["permissionGrants"]
            deny = grants.get("deny") or []
            if not isinstance(deny, (list, tuple, set)) or AGY_REQUIRED_DENY not in deny:
                bad.append(p.stem)
        except Exception:
            bad.append(p.stem)

    if bad:
        return [(item, "WARN", f"{AGY_REQUIRED_DENY} 미차단 또는 읽기 실패: {', '.join(bad)}")]
    return [(item, "PASS", f"모두 {AGY_REQUIRED_DENY} 차단")]


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    defaults = default_paths()
    parser = argparse.ArgumentParser(description="위임 전 권한 사전 점검 (읽기 전용)")
    parser.add_argument("--orca-db", type=Path, default=defaults["orca"])
    parser.add_argument("--codex-config", type=Path, default=defaults["codex"])
    parser.add_argument("--agy-project", type=Path, default=defaults["agy"])
    ns = parser.parse_args(argv)

    rows = (
        check_orca(ns.orca_db)
        + check_codex(ns.codex_config)
        + check_agy(ns.agy_project)
        + check_agy_others(ns.agy_project)
    )
    print("| 항목 | 결과 | 내용 |")
    print("|:---|:---|:---|")
    for item, result, detail in rows:
        print(f"| {cell(item)} | {result} | {cell(detail)} |")
    counts = {k: sum(1 for r in rows if r[1] == k) for k in ("PASS", "WARN", "FAIL")}
    summary = " · ".join(f"{k} {v}" for k, v in counts.items())
    if counts["FAIL"]:
        print(f"판정: FAIL ({summary}) — 샌드박스 · 권한 우회가 있다. 위임 전에 사람이 설정을 확인한다.")
        return 1
    print(f"판정: {'WARN' if counts['WARN'] else 'PASS'} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
