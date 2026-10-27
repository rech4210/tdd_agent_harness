"""하네스 자기 검사: 역할 대화형 마법사 (harness/roles.py --ask) 사례 (HD-18).

마법사는 표준 입력을 한 줄씩 읽는다. 순서 (WO-H13 §3.1):
  1. 프리셋 (이름 또는 번호, 빈 줄 = 프리셋 없음)
  2. 추가할 에이전트: 이름 → 계열, 반복 (이름에 빈 줄 = 끝)
  3. 역할마다 담당 에이전트 (빈 줄 = 기본값, "-" = 배정 안 함). 역할 순서 = roles.toml [roles] 순서, 그다음 [separation] 에만 나오는 역할 (처음 나온 순서)
  4. 슬라이스 ID (빈 줄 = 배정 안 함)
  5. 적용 확인 (y = 적용, 그 밖 = 적용 안 함)
판정은 종료 코드와 파일 내용. 프롬프트 문구에는 기대지 않는다.

사용: python harness/selftest/check_roles_wizard_cases.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
BEGIN = "<!-- harness:roles:begin -->"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_fixture import copy_default_config


def make_repo(base: Path, name: str) -> Path:
    repo = base / name
    repo.mkdir(parents=True)
    shutil.copytree(ROOT / "harness", repo / "harness", ignore=shutil.ignore_patterns("__pycache__", "UPSTREAM"))
    copy_default_config(repo / "config")
    shutil.copytree(ROOT / "templates" / "roles", repo / "templates" / "roles")
    return repo


def role_order(repo: Path) -> list[str]:
    data = tomllib.loads((repo / "config" / "roles.toml").read_text(encoding="utf-8"))
    order = list(data.get("roles", {}))
    for pair in data.get("separation", {}).get("pairs", []):
        for r in pair:
            if r not in order:
                order.append(r)
    return order


def snapshot(repo: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(repo.rglob("*")):
        rel = p.relative_to(repo).as_posix()
        if p.is_file() and (rel.startswith("config/") or ("/" not in rel and rel.endswith(".md"))):
            out[rel] = p.read_bytes()
    return out


def ask(repo: Path, lines: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, "harness/roles.py", "--ask"], cwd=repo, input="".join(x + "\n" for x in lines),
                          capture_output=True, text=True, encoding="utf-8")


def answers(repo: Path, preset: str, roles: dict[str, str], slice_id: str = "", confirm: str = "y",
            new_agents: list[tuple[str, str]] | None = None) -> list[str]:
    lines = [preset]
    for name, fam in new_agents or []:
        lines += [name, fam]
    lines.append("")
    lines += [roles.get(r, "") for r in role_order(repo)]
    lines += [slice_id, confirm]
    return lines


def agents_of(repo: Path) -> dict[str, str]:
    data = tomllib.loads((repo / "config" / "agents.toml").read_text(encoding="utf-8")).get("agents", {})
    return {k: v.get("family") for k, v in data.items()}


def slice_of(repo: Path, sid: str) -> dict:
    return tomllib.loads((repo / "config" / "assignments.toml").read_text(encoding="utf-8")).get("slice", {}).get(sid, {})


def ga(repo: Path) -> int:
    return subprocess.run([PY, "harness/check_assignments.py"], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8").returncode


def clean(p: subprocess.CompletedProcess[str]) -> bool:
    return "Traceback" not in p.stderr


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    base = Path(tempfile.mkdtemp(prefix="wizard-case-"))
    try:
        # W01 기본값 그대로 = --preset 과 같은 결과
        a = make_repo(base, "w01a")
        b = make_repo(base, "w01b")
        p = ask(a, answers(a, "codex-lead", {}))
        q = subprocess.run([PY, "harness/roles.py", "--preset", "codex-lead"], cwd=b, capture_output=True, text=True,
                           encoding="utf-8")
        results.append(("W01 프리셋 기본값 그대로 → --preset 과 같은 파일 (에이전트 · 안내 문서)",
                         p.returncode == 0 and q.returncode == 0 and snapshot(a) == snapshot(b) and (a / "AGENTS.md").exists(),
                         p.stdout[-300:] + p.stderr[-300:]))

        # W02 로컬 모델 에이전트 추가 + 구현자로 배정 + 슬라이스
        r = make_repo(base, "w02")
        p = ask(r, answers(r, "claude-lead", {"implementer": "qwen-local"}, "S1", new_agents=[("qwen-local", "alibaba")]))
        s = slice_of(r, "S1")
        ok = p.returncode == 0 and agents_of(r).get("qwen-local") == "alibaba" and s.get("implementer") == "qwen-local" \
            and s.get("test_author") == "claude" and s.get("status") == "open" and ga(r) == 0
        results.append(("W02 새 에이전트(로컬 모델) 등록 · 배정 → GA 통과", ok, p.stdout[-300:] + p.stderr[-300:]))
        results.append(("W02b 배정이 프리셋과 다르면 안내 문서는 쓰지 않는다",
                         not (r / "CLAUDE.md").exists() and not (r / "AGENTS.md").exists(), ""))

        # W03 분리 위반 → 거부, 무변경
        r = make_repo(base, "w03")
        before = snapshot(r)
        p = ask(r, answers(r, "codex-lead", {"implementer": "codex"}, "S1"))
        results.append(("W03 분리 위반 답 → 종료 코드 1, 무변경", p.returncode == 1 and clean(p) and snapshot(r) == before,
                         p.stdout[-300:] + p.stderr[-300:]))

        # W04 확인에서 n → 무변경, 종료 코드 0
        r = make_repo(base, "w04")
        before = snapshot(r)
        p = ask(r, answers(r, "codex-lead", {}, "S1", confirm="n"))
        results.append(("W04 적용 확인 거절 → 종료 코드 0, 무변경", p.returncode == 0 and snapshot(r) == before,
                         p.stdout[-300:] + p.stderr[-300:]))

        # W05 입력이 중간에 끝남 → 종료 코드 1, 무변경, 추적 없음
        r = make_repo(base, "w05")
        before = snapshot(r)
        p = ask(r, ["codex-lead", ""])
        results.append(("W05 입력이 중간에 끝남 → 종료 코드 1, 무변경", p.returncode == 1 and clean(p) and snapshot(r) == before,
                         p.stdout[-300:] + p.stderr[-300:]))

        # W06 "-" = 배정 안 함
        r = make_repo(base, "w06")
        p = ask(r, answers(r, "codex-lead", {"reviewer": "-", "mutation_auditor": "-"}, "S1"))
        s = slice_of(r, "S1")
        results.append(("W06 '-' 로 역할 배정 안 함", p.returncode == 0 and "reviewer" not in s and "mutation_auditor" not in s
                         and s.get("implementer") == "agy", str(s) + p.stderr[-300:]))

        # W07 세분화한 역할 (프로젝트가 roles.toml 에 추가) 도 묻는다
        r = make_repo(base, "w07")
        rt = r / "config" / "roles.toml"
        text = rt.read_text(encoding="utf-8")
        rt.write_text(text.replace("[separation]", '[roles.doc_writer]\nallow = ["docs/**", "reports/**"]\n'
                                   'deny = ["src/**", "tests/**", "config/**"]\n\n[separation]', 1),
                      encoding="utf-8", newline="\n")
        p = ask(r, answers(r, "codex-lead", {"doc_writer": "agy"}, "S1"))
        results.append(("W07 프로젝트가 추가한 역할(doc_writer)도 배정", p.returncode == 0 and slice_of(r, "S1").get("doc_writer") == "agy"
                         and ga(r) == 0, p.stdout[-300:] + p.stderr[-300:]))

        # W08 프리셋 없이 모두 직접
        r = make_repo(base, "w08")
        p = ask(r, answers(r, "", {"spec_author": "a1", "test_author": "a1", "implementer": "a2", "reviewer": "a1"}, "S1",
                           new_agents=[("a1", "anthropic"), ("a2", "meta")]))
        s = slice_of(r, "S1")
        results.append(("W08 프리셋 없이 새 에이전트 · 배정", p.returncode == 0 and s.get("implementer") == "a2"
                         and s.get("reviewer") == "a1" and "adversary" not in s and ga(r) == 0, p.stdout[-300:] + p.stderr[-300:]))

        # W09 등록 안 된 에이전트를 배정 → 거부
        r = make_repo(base, "w09")
        before = snapshot(r)
        p = ask(r, answers(r, "codex-lead", {"implementer": "ghost"}, "S1"))
        results.append(("W09 모르는 에이전트 배정 → 종료 코드 1, 무변경", p.returncode == 1 and clean(p) and snapshot(r) == before,
                         p.stdout[-300:] + p.stderr[-300:]))

        # W10 새 에이전트 계열 형식 오류 → 거부
        r = make_repo(base, "w10")
        before = snapshot(r)
        p = ask(r, answers(r, "codex-lead", {}, "", new_agents=[("x1", "bad family")]))
        results.append(("W10 계열 형식 오류 → 종료 코드 1, 무변경", p.returncode == 1 and clean(p) and snapshot(r) == before,
                         p.stdout[-300:] + p.stderr[-300:]))

        # W11 프리셋 번호로 고르기 (정렬 순서 1 = claude-lead)
        r = make_repo(base, "w11")
        p = ask(r, answers(r, "1", {}, "S1"))
        results.append(("W11 프리셋을 번호로 (1 = 이름순 첫 프리셋)", p.returncode == 0 and slice_of(r, "S1").get("test_author") == "claude",
                         p.stdout[-300:] + p.stderr[-300:]))

        # W12 이미 등록된 에이전트를 다른 계열로 추가 → 거부
        r = make_repo(base, "w12")
        before = snapshot(r)
        p = ask(r, answers(r, "", {}, "", new_agents=[("human", "openai")]))
        results.append(("W12 등록된 에이전트를 다른 계열로 → 종료 코드 1, 무변경", p.returncode == 1 and clean(p) and snapshot(r) == before,
                         p.stdout[-300:] + p.stderr[-300:]))
        # W13 --ask --dry-run → y 를 받아도 무변경
        r = make_repo(base, "w13")
        before = snapshot(r)
        p = subprocess.run([PY, "harness/roles.py", "--ask", "--dry-run"], cwd=r,
                           input="".join(x + "\n" for x in answers(r, "codex-lead", {}, "S1")),
                           capture_output=True, text=True, encoding="utf-8")
        results.append(("W13 --ask --dry-run → y 여도 무변경 (종료 코드 0)", p.returncode == 0 and snapshot(r) == before,
                         p.stdout[-300:] + p.stderr[-300:]))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    failed = [x for x in results if not x[1]]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {detail.strip()[:400]}"))
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
