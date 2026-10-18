"""하네스 자기 검사: 위임 전 권한 사전 점검 (HD-40 §3a, HD-09).

계약 (scripts/preflight_agents.py, Core 밖 · 비공개 도구, 읽기 전용)
  사용: python scripts/preflight_agents.py [--orca-db P] [--codex-config P] [--agy-project P]
  기본 경로: Orca = %APPDATA%/orca/profiles/local-default/profile-state.db, Codex = $CODEX_HOME 또는 ~/.codex/config.toml,
            AGY = ~/.gemini/config/projects/default-cli-project.json (~ 는 Path.home()).
  점검 1 Orca: profile_state_documents 의 domain = "settings" payload(JSON) 의 agentDefaultArgs (에이전트 → 인자 문자열).
    샌드박스 · 승인 우회 인자 → FAIL: --dangerously-bypass-approvals-and-sandbox, --dangerously-skip-permissions, --yolo,
    --sandbox danger-full-access (= 포함), --permission-mode bypassPermissions, 그 밖에 dangerously · bypass · yolo 가 든 인자.
    승인만 끄는 인자(--ask-for-approval never, -a never) → WARN. 그 밖의 인자 · 빈 값 → PASS.
  점검 2 Codex: 어느 표(프로필 포함)든 sandbox_mode = "danger-full-access" → FAIL, approval_policy = "never" → WARN,
    최상위 sandbox_mode · approval_policy 가 없으면 WARN.
  점검 3 AGY: permissionGrants.permissionGrants.deny 에 "command(*)" 가 없으면 FAIL (편집 전용, HD-09).
  파일이 없거나 읽지 못하면 그 항목은 WARN (확인 불가). 설정 파일을 바꾸지 않는다.
  출력: 결과 표(| 항목 | 결과 | 내용 |) + “판정:” 줄. 종료: FAIL 이 있으면 1, 아니면 0.

사용: python harness/selftest/check_preflight_cases.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = ROOT / "scripts" / "preflight_agents.py"

CLEAN_ARGS = {"codex": "", "claude": "", "antigravity": "--mode accept-edits", "aider": ""}
CLEAN_TOML = 'model = "m"\napproval_policy = "on-request"\nsandbox_mode = "workspace-write"\n\n[projects."x"]\ntrust_level = "trusted"\n'
CLEAN_DENY = ["command(*)", "mcp(*)", "execute_url(*)", "read_url(*)"]


def make_db(path: Path, agent_args: dict | None, *, table: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if table:
        con.execute("CREATE TABLE profile_state_documents (domain TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL, "
                    "domain_version INTEGER NOT NULL, revision INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
                    "content_hash TEXT NOT NULL)")
        con.execute("INSERT INTO profile_state_documents VALUES ('ui', '{}', 1, 1, 0, 'h')")
        payload = {"theme": "dark"} if agent_args is None else {"theme": "dark", "agentDefaultArgs": agent_args}
        con.execute("INSERT INTO profile_state_documents VALUES ('settings', ?, 1, 1, 0, 'h')", (json.dumps(payload),))
    else:
        con.execute("CREATE TABLE other (x TEXT)")
    con.commit()
    con.close()
    return path


def make_toml(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def make_agy(path: Path, deny: list[str] | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    grants = {} if deny is None else {"deny": deny}
    path.write_text(json.dumps({"id": "default-cli-project", "permissionGrants": {"permissionGrants": grants}}),
                    encoding="utf-8")
    return path


def fixture(base: Path, name: str, *, args=CLEAN_ARGS, toml=CLEAN_TOML, deny=CLEAN_DENY) -> list[str]:
    d = base / name
    db = make_db(d / "profile-state.db", args)
    cfg = make_toml(d / "config.toml", toml)
    agy = make_agy(d / "agy.json", deny)
    return ["--orca-db", str(db), "--codex-config", str(cfg), "--agy-project", str(agy)]


def run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, str(TOOL), *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def rows_with(out: str, result: str) -> list[str]:
    return [l for l in out.splitlines() if l.startswith("|") and f"| {result} |" in l]


def digest(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


def main() -> int:
    if not TOOL.exists() and not (ROOT / "dev").exists():
        print("알림: 비공개 도구가 없는 공개본이라 건너뜁니다.")
        return 0
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail))

    has = TOOL.exists()
    base = Path(tempfile.mkdtemp(prefix="preflight-"))
    try:
        r = run(fixture(base, "p01")) if has else None
        case("P01 깨끗한 설정 → 종료 0, FAIL 없음, 표 · 판정 줄",
             has and r.returncode == 0 and not rows_with(r.stdout, "FAIL") and rows_with(r.stdout, "PASS")
             and "| 항목 | 결과 | 내용 |" in r.stdout and "판정:" in r.stdout,
             (r.stdout + r.stderr)[-200:] if r else "도구 없음")

        r = run(fixture(base, "p02", args={**CLEAN_ARGS, "codex": "--dangerously-bypass-approvals-and-sandbox"})) if has else None
        case("P02 codex 샌드박스 · 승인 전체 우회 → FAIL(codex), 종료 1",
             has and r.returncode == 1 and any("codex" in l for l in rows_with(r.stdout, "FAIL")),
             (r.stdout + r.stderr)[-200:] if r else "")

        r = run(fixture(base, "p03", args={**CLEAN_ARGS, "claude": "--dangerously-skip-permissions"})) if has else None
        case("P03 claude 권한 확인 생략 → FAIL(claude), 종료 1",
             has and r.returncode == 1 and any("claude" in l for l in rows_with(r.stdout, "FAIL")),
             (r.stdout + r.stderr)[-200:] if r else "")

        variants = {"gemini": "--yolo", "codex": "--sandbox danger-full-access",
                    "claude": "--permission-mode bypassPermissions", "aider": "--sandbox=danger-full-access",
                    "cline": "--auto-bypass"}
        bad = []
        for i, (agent, arg) in enumerate(variants.items()):
            r = run(fixture(base, f"p04-{i}", args={**CLEAN_ARGS, agent: arg})) if has else None
            if not (r and r.returncode == 1 and any(agent in l for l in rows_with(r.stdout, "FAIL"))):
                bad.append(f"{agent}={arg}")
        case("P04 그 밖의 우회 인자(yolo · danger-full-access · bypass) → FAIL", has and not bad, ",".join(bad))

        r = run(fixture(base, "p05", args={**CLEAN_ARGS, "codex": "--sandbox workspace-write --ask-for-approval never"})) if has else None
        case("P05 샌드박스 유지 + 승인 끔 → WARN(codex), 종료 0",
             has and r.returncode == 0 and any("codex" in l for l in rows_with(r.stdout, "WARN"))
             and not rows_with(r.stdout, "FAIL"), (r.stdout + r.stderr)[-200:] if r else "")

        r1 = run(fixture(base, "p06a", toml=CLEAN_TOML.replace("workspace-write", "danger-full-access"))) if has else None
        r2 = run(fixture(base, "p06b", toml=CLEAN_TOML.replace("on-request", "never"))) if has else None
        case("P06 Codex 설정: danger-full-access → FAIL 종료 1, approval never → WARN 종료 0",
             has and r1.returncode == 1 and any("sandbox_mode" in l for l in rows_with(r1.stdout, "FAIL"))
             and r2.returncode == 0 and any("approval_policy" in l for l in rows_with(r2.stdout, "WARN")),
             f"{r1.returncode if r1 else '-'},{r2.returncode if r2 else '-'}")

        prof = CLEAN_TOML + '\n[profiles.fast]\nsandbox_mode = "danger-full-access"\n'
        r = run(fixture(base, "p07", toml=prof)) if has else None
        case("P07 프로필 표의 danger-full-access → FAIL, 종료 1",
             has and r.returncode == 1 and any("profiles.fast" in l for l in rows_with(r.stdout, "FAIL")),
             (r.stdout + r.stderr)[-200:] if r else "")

        r1 = run(fixture(base, "p08a", deny=["mcp(*)", "read_url(*)"])) if has else None
        r2 = run(fixture(base, "p08b", deny=None)) if has else None
        case("P08 AGY deny 에 command(*) 없음 · deny 없음 → FAIL, 종료 1",
             has and r1.returncode == 1 and r2.returncode == 1
             and any("command(*)" in l for l in rows_with(r1.stdout, "FAIL")),
             f"{r1.returncode if r1 else '-'},{r2.returncode if r2 else '-'}")

        nope = base / "nope"
        r = run(["--orca-db", str(nope / "a.db"), "--codex-config", str(nope / "c.toml"),
                 "--agy-project", str(nope / "g.json")]) if has else None
        case("P09 파일 없음 → 세 항목 WARN(확인 불가), 종료 0, 파일을 만들지 않는다",
             has and r.returncode == 0 and len(rows_with(r.stdout, "WARN")) >= 3 and not nope.exists()
             and "Traceback" not in r.stderr, (r.stdout + r.stderr)[-200:] if r else "")

        d = base / "p10"
        db = make_db(d / "profile-state.db", None, table=False)
        cfg = make_toml(d / "config.toml", "this is = = not toml [")
        agy = d / "agy.json"
        agy.write_text("{not json", encoding="utf-8")
        r = run(["--orca-db", str(db), "--codex-config", str(cfg), "--agy-project", str(agy)]) if has else None
        case("P10 못 읽는 형식(표 없음 · TOML · JSON 오류) → WARN, 종료 0, Traceback 없음",
             has and r.returncode == 0 and len(rows_with(r.stdout, "WARN")) >= 3 and "Traceback" not in r.stderr,
             (r.stdout + r.stderr)[-200:] if r else "")

        d = base / "p11"
        args = fixture(base, "p11", args={**CLEAN_ARGS, "codex": "--dangerously-bypass-approvals-and-sandbox"})
        before = digest(d)
        r = run(args) if has else None
        case("P11 읽기 전용: 설정 파일 내용 · 폴더 목록 그대로 (DB 저널도 안 생김)", has and digest(d) == before,
             f"{sorted(before)} → {sorted(digest(d))}")

        home = base / "home"
        make_db(home / "AppData" / "Roaming" / "orca" / "profiles" / "local-default" / "profile-state.db",
                {**CLEAN_ARGS, "claude": "--dangerously-skip-permissions"})
        make_toml(home / ".codex" / "config.toml", CLEAN_TOML)
        make_agy(home / ".gemini" / "config" / "projects" / "default-cli-project.json", CLEAN_DENY)
        env = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
        env.update(USERPROFILE=str(home), HOME=str(home), APPDATA=str(home / "AppData" / "Roaming"))
        r = run([], env=env) if has else None
        case("P12 기본 경로는 홈 · APPDATA 기준 (인자 없이 위 파일을 찾는다)",
             has and r.returncode == 1 and any("claude" in l for l in rows_with(r.stdout, "FAIL"))
             and len(rows_with(r.stdout, "PASS")) >= 2, (r.stdout + r.stderr)[-200:] if r else "")

        case("P13 도구는 Core 밖 (harness/ 에 사전 점검 모듈이 없다)",
             has and not list((ROOT / "harness").glob("preflight*.py")))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
