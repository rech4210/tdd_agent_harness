"""하네스 자기 검사: 새 프로젝트 생성(harness/new_project.py)과 역할 프리셋 적용(harness/roles.py) 사례 (HD-17).

상류 = 이 저장소 작업 트리의 사본(커밋함). 그 사본에서 new_project 를 실행해 새 프로젝트를 만들고,
만든 프로젝트 · 최소 저장소에서 roles 를 실행한다. 판정은 종료 코드와 파일 내용.

사용: python harness/selftest/check_project_cases.py
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
BEGIN, END = "<!-- harness:roles:begin -->", "<!-- harness:roles:end -->"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_fixture import copy_default_config, copy_harness_tree

UPSTREAM_ONLY = {
    "README.md": "# tdd-agent-harness (upstream README)\n\n하네스 저장소 README — 새 프로젝트에 가면 안 된다.\n",
    "HARNESS_STATUS.md": "# harness status (upstream only)\n",
    "HARNESS_QA.md": "# harness QA (upstream only)\n",
    "dev/NOTE.md": "harness development history (upstream only)\n",
}


def sh(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=check)


def git_identity(repo: Path) -> None:
    sh(repo, "git", "config", "user.email", "t@example.invalid")
    sh(repo, "git", "config", "user.name", "t")


def make_upstream(base: Path) -> Path:
    up = base / "up"
    copy_harness_tree(up)
    # 하네스 저장소에만 있는 것(개발 이력 · 하네스 README)을 고정 내용으로 심는다.
    # 소비 저장소에서 돌려도 N01c · N01d 가 new_project 의 제외 규칙을 실제로 검사하게 한다.
    for rel, text in UPSTREAM_ONLY.items():
        (up / rel).parent.mkdir(parents=True, exist_ok=True)
        (up / rel).write_text(text, encoding="utf-8")
    sh(up, "git", "init", "-q", "-b", "main")
    git_identity(up)
    sh(up, "git", "add", "-A")
    sh(up, "git", "commit", "-qm", "upstream")
    return up


def snapshot(root: Path) -> dict[str, bytes]:
    """config/ 와 루트의 *.md 내용 (변경 없음 확인용)."""
    out: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and (rel.startswith("config/") or ("/" not in rel and rel.endswith(".md"))):
            out[rel] = p.read_bytes()
    return out


def block(text: str) -> list[str]:
    """표시 구간 안의 내용 목록 (여러 개면 여러 개)."""
    out, rest = [], text
    while BEGIN in rest:
        rest = rest.split(BEGIN, 1)[1]
        if END not in rest:
            out.append("(끝 표시 없음)")
            break
        inner, rest = rest.split(END, 1)
        out.append(inner.strip())
    return out


def new_project(up: Path, target: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return sh(up, PY, "harness/new_project.py", str(target), *extra, check=False)


def roles(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return sh(repo, PY, "harness/roles.py", *args, check=False)


def project_cases(base: Path, up: Path, results: list[tuple[str, bool, str]]) -> None:
    head = sh(up, "git", "rev-parse", "HEAD").stdout.strip()

    # 거부 사례 먼저: 아무것도 만들지 않아야 한다
    (up / "harness" / "doctor.py").write_text((up / "harness" / "doctor.py").read_text(encoding="utf-8") + "# x\n",
                                              encoding="utf-8")
    t = base / "dirty1"
    p = new_project(up, t, "--package", "demo_app")
    results.append(("N03 상류 하네스 경로에 커밋 안 된 변경 → 거부, 대상 안 만듦", p.returncode == 1 and not t.exists(),
                    p.stdout + p.stderr))
    sh(up, "git", "checkout", "--", "harness/doctor.py")

    (up / "templates" / "stray.md").write_text("x\n", encoding="utf-8")
    t = base / "dirty2"
    p = new_project(up, t, "--package", "demo_app")
    results.append(("N03b 상류 templates/ 에 추적 안 된 파일 → 거부", p.returncode == 1 and not t.exists(),
                    p.stdout + p.stderr))
    (up / "templates" / "stray.md").unlink()

    t = base / "busy"
    t.mkdir()
    (t / "keep.txt").write_text("mine\n", encoding="utf-8")
    p = new_project(up, t, "--package", "demo_app")
    results.append(("N04 비어 있지 않은 대상 → 거부, 기존 파일 그대로",
                    p.returncode == 1 and sorted(x.name for x in t.iterdir()) == ["keep.txt"]
                    and (t / "keep.txt").read_text(encoding="utf-8") == "mine\n", p.stdout + p.stderr))

    t = up / "nested" / "proj"
    p = new_project(up, t, "--package", "demo_app")
    results.append(("N11 상류 저장소 안의 대상 → 거부", p.returncode == 1 and not t.exists(), p.stdout + p.stderr))

    t = base / "badpkg"
    p = new_project(up, t, "--package", "Bad-Name")
    results.append(("N12 패키지 이름이 식별자가 아님 → 거부", p.returncode == 1 and not t.exists(), p.stdout + p.stderr))

    t = base / "nlpkg"
    p = new_project(up, t, "--package", "demo_app\n")
    results.append(("N14 패키지 이름 끝 줄바꿈 → 거부 (전체 일치)", p.returncode == 1 and not t.exists(), p.stdout + p.stderr))

    for rid, name in [("N15a", "../roles/codex-lead"), ("N15b", ""), ("N15c", str((up / "templates" / "roles" / "codex-lead").resolve()))]:
        t = base / f"badroles_{rid}"
        p = new_project(up, t, "--package", "demo_app", "--roles", name)
        results.append((f"{rid} --roles 이름 형식 오류 ({name[:20]!r}) → 거부", p.returncode == 1 and not t.exists(), p.stdout + p.stderr))

    # 무시 파일은 복사하지 않는다 (상류는 깨끗한 상태로 본다)
    (up / "harness" / "__pycache__").mkdir(exist_ok=True)
    (up / "harness" / "__pycache__" / "junk.pyc").write_bytes(b"x")
    (up / "templates" / "project" / "junk.pyc").write_bytes(b"x")       # 무시 파일 (.gitignore *.pyc)
    (up / "templates" / "project" / "__pycache__").mkdir(exist_ok=True)
    (up / "templates" / "project" / "__pycache__" / "x.pyc").write_bytes(b"x")

    # 정상 생성 (역할 없이)
    proj = base / "proj"
    p = new_project(up, proj, "--package", "demo_app")
    ok = p.returncode == 0
    results.append(("N01a 생성 → 종료 코드 0", ok, p.stdout + p.stderr))
    if not ok:
        return
    must = ["harness/run_gates.py", "harness/new_project.py", "harness/roles.py", "adapters/python",
            "templates/roles/codex-lead/preset.toml", "config/gates.toml", "docs/slice-lifecycle.md",
            "docs/harness-sync.md", ".gitignore", ".gitattributes", "README.md", "docs/HANDOFF.md"]
    missing = [m for m in must if not (proj / m).exists()]
    results.append(("N01b 하네스 소유 경로 · 설정 · 사용자 문서 · 프로젝트 문서가 있다", not missing, str(missing)))
    # 상류에 심은 무시 파일 자체를 본다. 생성 중 init 실행이 만드는 harness/__pycache__ 는 복사가 아니다 (바이트코드를 쓰는 환경에서 오판)
    present = [m for m in ["dev", "HARNESS_STATUS.md", "HARNESS_QA.md", "harness/__pycache__/junk.pyc", "junk.pyc", "__pycache__",
                           "templates/project/junk.pyc"] if (proj / m).exists()]
    results.append(("N01c 하네스 개발 이력 · 무시 파일은 없다 (N02 · N16: 틀도 커밋된 내용에서)", not present, str(present)))
    readme = (proj / "README.md").read_text(encoding="utf-8")
    up_readme = (up / "README.md").read_text(encoding="utf-8")
    results.append(("N01d README = 프로젝트 틀 (# proj, 하네스 README 아님)",
                    readme.startswith("# proj") and readme != up_readme, readme[:120]))
    same = (proj / "harness" / "run_gates.py").read_bytes() == (up / "harness" / "run_gates.py").read_bytes()
    results.append(("N13 하네스 파일 = 상류 커밋 내용", same, ""))

    is_repo = sh(proj, "git", "rev-parse", "--is-inside-work-tree", check=False).stdout.strip() == "true"
    toplevel = sh(proj, "git", "rev-parse", "--show-toplevel", check=False).stdout.strip()
    no_commit = sh(proj, "git", "rev-parse", "--verify", "HEAD", check=False).returncode != 0
    branch = sh(proj, "git", "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
    remote = sh(proj, "git", "remote", "get-url", "upstream", check=False).stdout.strip()
    results.append(("N05a 새 git 저장소 (자기 루트, 커밋 없음, 브랜치 main)",
                    is_repo and Path(toplevel).resolve() == proj.resolve() and no_commit and branch == "main",
                    f"{is_repo} {toplevel} {no_commit} {branch}"))
    results.append(("N05b upstream = 상류 경로 (상류에 origin 이 없을 때)",
                    bool(remote) and Path(remote).resolve() == up.resolve(), remote))
    record = (proj / "harness" / "UPSTREAM").read_text(encoding="utf-8").strip() \
        if (proj / "harness" / "UPSTREAM").exists() else ""
    results.append(("N06 harness/UPSTREAM = 상류 HEAD", record == head, record))

    cfg_left = [f.name for f in (proj / "config").glob("*.toml") if "<package>" in f.read_text(encoding="utf-8")]
    made = all((proj / m).exists() for m in ["pyproject.toml", ".specify/memory/constitution.md", "reports"])
    results.append(("N07 init 실행됨 (패키지 이름 반영 · 골격 · 헌법)", not cfg_left and made, str(cfg_left)))

    rendered = {n: (proj / n).read_text(encoding="utf-8") for n in ["README.md", "docs/HANDOFF.md"]}
    left = [n for n, s in rendered.items() if "{{" in s]
    filled = "demo_app" in rendered["README.md"] and head[:10] in rendered["README.md"] \
        and "proj" in rendered["docs/HANDOFF.md"]
    results.append(("N10 틀 채움 (남은 {{ }} 없음, 이름 · 패키지 · 상류 커밋)", not left and filled, str(left)))
    results.append(("N08a 역할 없이 만들면 안내 문서 없음",
                    not (proj / "AGENTS.md").exists() and not (proj / "CLAUDE.md").exists(), ""))

    # 받기: 첫 커밋 뒤 upgrade 가 그대로 돈다 (상류와 이력이 이어지지 않아도)
    git_identity(proj)
    sh(proj, "git", "add", "-A")
    sh(proj, "git", "commit", "-qm", "init")
    p = sh(proj, PY, "harness/upgrade.py", "--remote", "upstream", "--ref", "main", "--dry-run", check=False)
    results.append(("N09a 생성 직후 upgrade --dry-run → 바뀔 것 없음", p.returncode == 0 and "바뀔 것 없음" in p.stdout,
                     p.stdout + p.stderr))
    shutil.rmtree(up / "harness" / "__pycache__", ignore_errors=True)
    shutil.rmtree(up / "templates" / "project" / "__pycache__", ignore_errors=True)
    (up / "templates" / "project" / "junk.pyc").unlink(missing_ok=True)
    doctor = up / "harness" / "doctor.py"
    doctor.write_text(doctor.read_text(encoding="utf-8") + "# upstream change\n", encoding="utf-8", newline="\n")
    sh(up, "git", "commit", "-qam", "upstream change")
    p = sh(proj, PY, "harness/upgrade.py", "--remote", "upstream", "--ref", "main", check=False)
    got = (proj / "harness" / "doctor.py").read_text(encoding="utf-8").endswith("# upstream change\n")
    results.append(("N09b 상류 변경 → upgrade 로 받음", p.returncode == 0 and got, p.stdout + p.stderr))

    # 역할과 함께 생성, upstream 지정
    proj2 = base / "proj2"
    p = new_project(up, proj2, "--package", "demo_app", "--roles", "codex-lead", "--upstream",
                    "https://example.invalid/agent-harness.git")
    agents = tomllib.loads((proj2 / "config" / "agents.toml").read_text(encoding="utf-8")).get("agents", {}) \
        if p.returncode == 0 else {}
    ok = p.returncode == 0 and agents.get("codex", {}).get("family") == "openai" \
        and len(block((proj2 / "AGENTS.md").read_text(encoding="utf-8"))) == 1
    results.append(("N08b --roles codex-lead → 에이전트 등록 · 안내 문서", ok, p.stdout + p.stderr))
    remote = sh(proj2, "git", "remote", "get-url", "upstream", check=False).stdout.strip()
    results.append(("N05c --upstream 지정 → 그 주소", remote == "https://example.invalid/agent-harness.git", remote))


def make_roles_repo(base: Path) -> Path:
    repo = base / "rolerepo"
    repo.mkdir(parents=True)
    shutil.copytree(ROOT / "harness", repo / "harness", ignore=shutil.ignore_patterns("__pycache__", "UPSTREAM"))
    copy_default_config(repo / "config")
    shutil.copytree(ROOT / "templates" / "roles", repo / "templates" / "roles")
    fixtures = {
        "bad-sep": 'about = "x"\n[agents.codex]\nfamily = "openai"\n[assign]\ntest_author = "codex"\nimplementer = "codex"\n',
        "conflict": 'about = "x"\n[agents.codex]\nfamily = "google"\n',
        "ghost": 'about = "x"\n[agents.codex]\nfamily = "openai"\n[assign]\nimplementer = "nobody"\n',
        "quote": 'about = "x"\n[agents.qq]\nfamily = "open\\"ai"\n',
        "badrole": 'about = "x"\n[agents.codex]\nfamily = "openai"\n[assign]\n"test author" = "codex"\n',
        "dotted": 'about = "x"\n[agents."co.dex"]\nfamily = "openai"\n',
        "nofam": 'about = "x"\n[agents.zed]\nname = "z"\n',
        "unknownrole": 'about = "x"\n[agents.codex]\nfamily = "openai"\n[assign]\npainter = "codex"\n',
    }
    for name, text in fixtures.items():
        d = repo / "templates" / "roles" / name
        d.mkdir(parents=True)
        (d / "preset.toml").write_text(text, encoding="utf-8", newline="\n")
    return repo


def role_cases(base: Path, results: list[tuple[str, bool, str]]) -> None:
    repo = make_roles_repo(base)
    preset_dir = repo / "templates" / "roles" / "codex-lead"
    agents_before = (repo / "config" / "agents.toml").read_text(encoding="utf-8")
    assign_before = (repo / "config" / "assignments.toml").read_bytes()
    claude_original = "# 내 프로젝트\n\n메모\n\n<!-- other-tool:begin -->\nKIT\n<!-- other-tool:end -->\n"
    (repo / "CLAUDE.md").write_text(claude_original, encoding="utf-8", newline="\n")

    p = roles(repo, "--list")
    results.append(("R01 --list → 프리셋 이름 · 설명 (codex-lead · claude-lead)", p.returncode == 0 and "codex-lead" in p.stdout
                    and "claude-lead" in p.stdout and "적대적 검토" in p.stdout, p.stdout + p.stderr))

    before = snapshot(repo)
    p = roles(repo, "--preset", "nope")
    results.append(("R02 없는 프리셋 → 거부, 변경 없음", p.returncode == 1 and snapshot(repo) == before, p.stdout + p.stderr))

    p = roles(repo, "--preset", "codex-lead", "--slice", "S9", "--dry-run")
    results.append(("R10 --dry-run → 변경 없음", p.returncode == 0 and snapshot(repo) == before, p.stdout + p.stderr))

    p = roles(repo, "--preset", "codex-lead")
    agents_text = (repo / "config" / "agents.toml").read_text(encoding="utf-8")
    agents = tomllib.loads(agents_text).get("agents", {})
    fam = {k: v.get("family") for k, v in agents.items()}
    results.append(("R03a 적용 → 에이전트 등록 (기존 내용 · 주석 보존)",
                    p.returncode == 0 and agents_text.startswith(agents_before)
                    and fam == {"human": "human", "codex": "openai", "agy": "google", "claude": "anthropic"},
                    p.stdout + p.stderr + str(fam)))
    results.append(("R03b --slice 없음 → 배정 파일 그대로", (repo / "config" / "assignments.toml").read_bytes() == assign_before, ""))
    agents_md = (repo / "AGENTS.md").read_text(encoding="utf-8") if (repo / "AGENTS.md").exists() else ""
    want = (preset_dir / "AGENTS.md").read_text(encoding="utf-8").strip()
    results.append(("R03c AGENTS.md 생성, 구간 하나 = 프리셋 문서", block(agents_md) == [want], agents_md[:120]))
    claude_md = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    want_c = (preset_dir / "CLAUDE.md").read_text(encoding="utf-8").strip()
    results.append(("R05a 기존 CLAUDE.md (다른 도구 구간 포함) 보존 + 하네스 구간 하나",
                    claude_md.startswith(claude_original.rstrip("\n")) and block(claude_md) == [want_c], claude_md[:200]))

    after = snapshot(repo)
    p = roles(repo, "--preset", "codex-lead")
    results.append(("R04 다시 적용 → 바이트까지 같음 (멱등)", p.returncode == 0 and snapshot(repo) == after, p.stdout + p.stderr))

    (preset_dir / "CLAUDE.md").write_text(want_c + "\n\n- 새 줄\n", encoding="utf-8", newline="\n")
    p = roles(repo, "--preset", "codex-lead")
    claude_md = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    results.append(("R05b 프리셋 문서가 바뀌면 구간만 교체 (다른 도구 구간 그대로)",
                    p.returncode == 0 and block(claude_md) == [want_c + "\n\n- 새 줄"]
                    and "<!-- other-tool:begin -->\nKIT\n<!-- other-tool:end -->" in claude_md, claude_md[-200:]))

    p = roles(repo, "--preset", "codex-lead", "--slice", "001-state")
    slices = tomllib.loads((repo / "config" / "assignments.toml").read_text(encoding="utf-8")).get("slice", {})
    got = slices.get("001-state", {})
    expect = {"status": "open", "spec_author": "codex", "test_author": "codex", "implementer": "agy",
              "adversary": "claude", "mutation_auditor": "claude"}  # HD-23: 품질 검토 = adversary, 프리셋은 reviewer 를 배정하지 않는다
    ok = p.returncode == 0 and {k: v for k, v in got.items() if k != "reason"} == expect \
        and "codex-lead" in str(got.get("reason", ""))
    results.append(("R06a --slice → 배정 추가 (status open, reason 에 프리셋)", ok, p.stdout + p.stderr + str(got)))
    p = sh(repo, PY, "harness/check_assignments.py", check=False)
    results.append(("R06b 추가한 배정이 GA 통과", p.returncode == 0, p.stdout[-300:]))

    before = snapshot(repo)
    for rid, args in [("R07 이미 있는 슬라이스 → 거부, 변경 없음", ["--preset", "codex-lead", "--slice", "001-state"]),
                      ("R08a 분리 위반 프리셋 (--slice) → 거부, 변경 없음", ["--preset", "bad-sep", "--slice", "S2"]),
                      ("R08b 분리 위반 프리셋 (슬라이스 없이도) → 거부", ["--preset", "bad-sep"]),
                      ("R09 등록 계열과 다른 계열 → 거부, 변경 없음", ["--preset", "conflict"]),
                      ("R11 슬라이스 ID 형식 오류 → 거부", ["--preset", "codex-lead", "--slice", "a b]"]),
                      ("R12 배정에 모르는 에이전트 → 거부", ["--preset", "ghost", "--slice", "S3"]),
                      ("R13 슬라이스 ID 끝 줄바꿈 → 거부 (전체 일치)", ["--preset", "codex-lead", "--slice", "S4\n"]),
                      ("R14a 프리셋 이름에 경로 (..) → 거부", ["--preset", "../roles/codex-lead"]),
                      ("R14b 프리셋 이름이 절대 경로 → 거부", ["--preset", str(preset_dir.resolve())]),
                      ("R15a 계열 값에 따옴표 → 거부 (TOML 깨짐 방지)", ["--preset", "quote"]),
                      ("R15b 역할 이름이 식별자가 아님 → 거부", ["--preset", "badrole", "--slice", "S5"]),
                      ("R15c 에이전트 이름이 식별자가 아님 → 거부", ["--preset", "dotted"]),
                      ("R15d 프리셋 에이전트에 family 없음 → 거부", ["--preset", "nofam"]),
                      ("R18 roles.toml 에 없는 역할을 배정 → 거부", ["--preset", "unknownrole", "--slice", "S8"])]:
        p = roles(repo, *args)
        results.append((rid, p.returncode == 1 and "Traceback" not in p.stderr and snapshot(repo) == before,
                        p.stdout + p.stderr))

    r16 = make_roles_repo(base / "r16")   # 앞 사례의 결과에 기대지 않도록 새 저장소
    roles_toml = r16 / "config" / "roles.toml"
    roles_toml.write_text(roles_toml.read_text(encoding="utf-8").split("[separation]")[0], encoding="utf-8",
                          newline="\n")
    before = snapshot(r16)
    p = roles(r16, "--preset", "codex-lead", "--slice", "S6")
    results.append(("R16 roles.toml 에 분리 규칙이 없음 → 거부 (검사가 약해지지 않게)",
                    p.returncode == 1 and "Traceback" not in p.stderr and snapshot(r16) == before, p.stdout + p.stderr))

    clean = make_roles_repo(base / "r17")
    (clean / "AGENTS.md").mkdir()
    before = snapshot(clean)
    p = roles(clean, "--preset", "codex-lead", "--slice", "S7")
    results.append(("R17 안내 문서를 쓸 수 없음 (같은 이름 폴더) → 종료 코드 1, 설정 무변경 (먼저 모두 준비한 뒤 쓴다)",
                    p.returncode == 1 and "Traceback" not in p.stderr and snapshot(clean) == before,
                    p.stdout + p.stderr))


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    base = Path(tempfile.mkdtemp(prefix="project-case-"))
    try:
        up = make_upstream(base)
        if not (up / "harness" / "new_project.py").exists() or not (up / "harness" / "roles.py").exists():
            results.append(("N00 harness/new_project.py · roles.py 가 있다", False, "없음"))
        project_cases(base, up, results)
        if (ROOT / "harness" / "roles.py").exists():
            role_cases(base, results)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {detail.strip()[:400]}"))
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
