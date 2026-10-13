"""하네스 자기 검사: GA (harness/check_assignments.py) 사례 — 분리 제약 · 작업 지시서 · 실제 실행 계열 (HD-17).

임시 저장소에 harness/ 와 최소 설정을 두고 GA 를 실행한다. 판정은 종료 코드와 JSON 의 violations.

사용: python harness/selftest/check_assignments_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GATES = '[harness]\nadapter = "python"\n'
AGENTS = """
[agents.claude]
family = "anthropic"

[agents.codex]
family = "openai"

[agents.agy]
family = "google"
"""
ROLES = """
[roles.test_author]
allow = ["tests/**"]

[roles.implementer]
allow = ["src/**"]

[roles.reviewer]
allow = ["reports/**"]

[separation]
pairs = [["test_author", "implementer"], ["implementer", "reviewer"]]
"""
ASSIGN_OK = """
[slice.S1]
status = "open"
test_author = "codex"
implementer = "agy"
reviewer = "claude"
"""
ASSIGN_SAME = ASSIGN_OK.replace('implementer = "agy"', 'implementer = "codex"')


def wo(family_line: str) -> str:
    return f"""---
x: y
---

# WO-1

```yaml
slice: S1
role: implementer
agent: agy
{family_line}
```
"""


def make_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="ga-case-"))
    shutil.copytree(ROOT / "harness", repo / "harness", ignore=shutil.ignore_patterns("__pycache__", "UPSTREAM"))
    cfg = repo / "config"
    cfg.mkdir()
    for name, text in {"gates.toml": GATES, "agents.toml": AGENTS, "roles.toml": ROLES,
                       "assignments.toml": ASSIGN_OK}.items():
        (cfg / name).write_text(text, encoding="utf-8", newline="\n")
    return repo


def ga(repo: Path) -> tuple[int, list[str]]:
    p = subprocess.run([sys.executable, "harness/check_assignments.py"], cwd=repo, capture_output=True, text=True,
                       encoding="utf-8")
    try:
        return p.returncode, json.loads(p.stdout)["violations"]
    except (json.JSONDecodeError, KeyError):
        return p.returncode, [f"(JSON 아님) {p.stdout[:200]} {p.stderr[:200]}"]


def set_wo(repo: Path, family_line: str | None) -> None:
    d = repo / "work_orders"
    if family_line is None:
        shutil.rmtree(d, ignore_errors=True)
        return
    d.mkdir(exist_ok=True)
    (d / "WO-1.md").write_text(wo(family_line), encoding="utf-8", newline="\n")


DELEGATION = """
[delegation]
record = ".jobs/{job}/meta.json"
family_key = "family"
require_family = REQ
"""


def wo_plain(lines: str) -> str:
    return f"# WO\n\n```yaml\nslice: S1\nrole: implementer\n{lines}\n```\n"


def delegation_cases() -> list[tuple[str, bool, str]]:
    """config/delegation.toml (선택) 이 있을 때: 위임 기록에서 실제 계열을 읽어 대조한다. 없으면 지금처럼."""
    out: list[tuple[str, bool, str]] = []
    repo = make_repo()
    try:
        cfg = repo / "config" / "delegation.toml"
        wod = repo / "work_orders"
        wod.mkdir()
        wo_file = wod / "WO-9.md"

        def record(job: str, text: str | None) -> None:
            d = repo / ".jobs" / job
            if text is None:
                shutil.rmtree(d, ignore_errors=True)
                return
            d.mkdir(parents=True, exist_ok=True)
            (d / "meta.json").write_bytes(text.encode("utf-8-sig"))   # 위임 도구는 BOM 을 붙일 수 있다

        cfg.write_text(DELEGATION.replace("REQ", "true"), encoding="utf-8", newline="\n")
        wo_file.write_text(wo_plain("agent: agy"), encoding="utf-8", newline="\n")
        code, v = ga(repo)
        out.append(("D01 require_family: family · job 둘 다 없음 → [계열] 위반", code == 1 and any(x.startswith("[계열]") for x in v), str(v)))

        wo_file.write_text(wo_plain("agent: agy\njob: wo-9"), encoding="utf-8", newline="\n")
        record("wo-9", '{"agent": "agy", "family": "google"}')
        code, v = ga(repo)
        out.append(("D02 job 기록의 계열 = 등록 계열 → 통과", code == 0 and not v, str(v)))

        record("wo-9", '{"agent": "agy", "family": "anthropic"}')
        code, v = ga(repo)
        out.append(("D03 job 기록의 계열 ≠ 등록 (agy 를 deep 프로필로) → [계열] 위반",
                    code == 1 and any(x.startswith("[계열]") and "anthropic" in x for x in v), str(v)))

        record("wo-9", None)
        code, v = ga(repo)
        out.append(("D04 job 기록이 없음 → [계열] 위반", code == 1 and any(x.startswith("[계열]") for x in v), str(v)))

        record("wo-9", '{"agent": "agy", "family": "anthropic"}')
        wo_file.write_text(wo_plain("agent: agy\nfamily: google\njob: wo-9"), encoding="utf-8", newline="\n")
        code, v = ga(repo)
        out.append(("D05 머리말 family 와 기록이 다름 (손으로 덮어씀) → [계열] 위반", code == 1 and any(x.startswith("[계열]") for x in v), str(v)))

        record("wo-9", "not json")
        wo_file.write_text(wo_plain("agent: agy\njob: wo-9"), encoding="utf-8", newline="\n")
        code, v = ga(repo)
        out.append(("D06 기록이 JSON 이 아님 → [계열] 위반 (추적 없이)", code == 1 and any(x.startswith("[계열]") for x in v), str(v)))

        cfg.write_text(DELEGATION.replace("REQ", "false"), encoding="utf-8", newline="\n")
        wo_file.write_text(wo_plain("agent: agy"), encoding="utf-8", newline="\n")
        code, v = ga(repo)
        out.append(("D07 require_family = false, family · job 없음 → 통과", code == 0 and not v, str(v)))

        cfg.write_text('[delegation]\nrecord = ".jobs/meta.json"\nfamily_key = "family"\n', encoding="utf-8", newline="\n")
        code, v = ga(repo)
        out.append(("D08 record 에 {job} 이 없음 → [설정] 위반", code == 1 and any(x.startswith("[설정]") for x in v), str(v)))

        cfg.write_text('[delegation]\nrecord = "../{job}/meta.json"\nfamily_key = "family"\n', encoding="utf-8", newline="\n")
        code, v = ga(repo)
        out.append(("D09 record 가 저장소 밖 (..) → [설정] 위반", code == 1 and any(x.startswith("[설정]") for x in v), str(v)))

        cfg.write_text('[delegation]\nrecord = "sub/{job}/meta.json"\nfamily_key = "family"\n', encoding="utf-8", newline="\n")
        (repo / "meta.json").write_text('{"family": "google"}', encoding="utf-8")
        wo_file.write_text(wo_plain("agent: agy\njob: .."), encoding="utf-8", newline="\n")
        code, v = ga(repo)
        out.append(("D11 job '..' 로 기록 경로를 옮김 → [계열] 위반", code == 1 and any(x.startswith("[계열]") for x in v), str(v)))
        (repo / "meta.json").unlink()

        cfg.unlink()
        wo_file.write_text(wo_plain("agent: agy\njob: wo-9"), encoding="utf-8", newline="\n")
        record("wo-9", '{"agent": "agy", "family": "anthropic"}')
        code, v = ga(repo)
        out.append(("D10 delegation.toml 없음 → 기록을 읽지 않는다 (분리 = 파일 삭제)", code == 0 and not v, str(v)))
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    return out


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    repo = make_repo()
    try:
        code, v = ga(repo)
        results.append(("F01 배정만, 지시서 없음 → 통과", code == 0 and not v, str(v)))

        set_wo(repo, "family: google")
        code, v = ga(repo)
        results.append(("F02 지시서 family = 등록 계열 → 통과", code == 0 and not v, str(v)))

        set_wo(repo, "family: anthropic")
        code, v = ga(repo)
        hit = [x for x in v if x.startswith("[계열]") and "WO-1.md" in x and "anthropic" in x and "google" in x]
        results.append(("F03 지시서 family ≠ 등록 계열 → [계열] 위반", code == 1 and len(hit) == 1, str(v)))

        set_wo(repo, "")
        code, v = ga(repo)
        results.append(("F04 family 없음 → 통과 (호환)", code == 0 and not v, str(v)))

        set_wo(repo, "family: google      # meta.json 에서 옮김")
        code, v = ga(repo)
        results.append(("F05 family 뒤 주석 → 첫 단어만 비교, 통과", code == 0 and not v, str(v)))

        set_wo(repo, "family: Google")
        code, v = ga(repo)
        hit = [x for x in v if x.startswith("[계열]")]
        results.append(("F06 대소문자가 다른 계열 → [계열] 위반 (그대로 비교)", code == 1 and len(hit) == 1, str(v)))

        d = repo / "work_orders"
        (d / "WO-1.md").write_text("# WO-1\n\n```yaml\nagent: agy\nfamily: anthropic\n```\n", encoding="utf-8", newline="\n")
        code, v = ga(repo)
        hit = [x for x in v if x.startswith("[계열]") and "WO-1.md" in x]
        results.append(("F08 슬라이스 · 역할 없는 지시서도 family 를 대조 → [계열] 위반", code == 1 and len(hit) == 1, str(v)))

        (d / "WO-1.md").write_text("# WO-1\n\n```yaml\nagent: ghost\nfamily: google\n```\n", encoding="utf-8", newline="\n")
        code, v = ga(repo)
        hit = [x for x in v if x.startswith("[계열]") and "ghost" in x]
        results.append(("F09 등록 안 된 agent 에 family → [계열] 위반", code == 1 and len(hit) == 1, str(v)))

        (d / "WO-1.md").write_text("---\nagent: agy\nfamily: anthropic\n---\n\n# WO-1\n", encoding="utf-8", newline="\n")
        code, v = ga(repo)
        results.append(("F10 '---' 머리말만 있는 지시서는 읽지 않는다 (yaml 블록만)", code == 0 and not v, str(v)))

        set_wo(repo, None)
        (repo / "config" / "assignments.toml").write_text(ASSIGN_SAME, encoding="utf-8", newline="\n")
        code, v = ga(repo)
        hit = [x for x in v if x.startswith("[분리]")]
        results.append(("F07 배정 분리 위반 → [분리] (기존 동작)", code == 1 and len(hit) == 1, str(v)))
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    results.extend(delegation_cases())

    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {detail}"))
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
