"""하네스 자기 검사: 작업자 로그 매칭 정밀화 (WO-H82, HD-81 Q3).

계약은 dev/WO-H82.md §계약. adapters/tools/orca_watch.find_worker_log 가
- AGY: 워커 자신의 MODEL 행 tool_calls 인자에 작업 공간 경로(경로 경계까지)가 있을 때만,
- Codex: 첫 행 session_meta.payload.cwd 가 작업 공간 경로와 같을 때만 후보로 삼고,
- 후보 중 첫 행 시각이 감시 시작과 가장 가까운 것(같으면 경로 문자열이 작은 것)을 고른다.
이름 · 경로를 내용에 언급만 한 다른 대화는 근거가 아니다.

사용: python harness/selftest/check_h82_worker_log_cases.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters" / "tools"))
import orca_watch  # noqa: E402


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def agy(root: Path, conv: str, first: datetime, *, tool_paths: list[str] = (), user_text: str = "Read the brief",
        model_text: str | None = None, source: str = "USER_EXPLICIT", junk: bool = False) -> Path:
    d = root / conv / ".system_generated" / "logs"
    d.mkdir(parents=True)
    rows: list[dict] = [{"step_index": 0, "source": source, "type": "USER_INPUT", "created_at": iso(first),
                         "status": "DONE", "content": user_text}]
    for i, tp in enumerate(tool_paths, start=1):
        rows.append({"step_index": i, "source": "MODEL", "type": "PLANNER_RESPONSE",
                     "created_at": iso(first + timedelta(seconds=i)), "status": "DONE",
                     "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": tp}}]})
    if model_text is not None:
        rows.append({"step_index": 99, "source": "MODEL", "type": "PLANNER_RESPONSE",
                     "created_at": iso(first + timedelta(seconds=50)), "status": "DONE", "content": model_text})
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    if junk:
        text = "{broken\n" + text + "not json either\n"
    p = d / "transcript.jsonl"
    p.write_text(text, encoding="utf-8")
    return p


def codex(root: Path, sid: str, first: datetime, cwd: str, *, first_type: str = "session_meta",
          mention: str | None = None) -> Path:
    d = root / "2026" / "10" / "27"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"timestamp": iso(first), "type": first_type, "payload": {"id": sid, "cwd": cwd}},
            {"timestamp": iso(first + timedelta(seconds=2)), "type": "event_msg",
             "payload": {"type": "agent_message", "message": mention or "done"}}]
    p = d / f"rollout-{sid}.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return p


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail.replace("\n", " | ")[:260]))

    base = Path(tempfile.mkdtemp(prefix="h82-"))
    try:
        start = datetime.now(timezone.utc)

        def ws_of(name: str) -> Path:
            w = base / "workspaces" / "origin" / name
            w.mkdir(parents=True, exist_ok=True)
            return w

        def find(agent: str, root: Path, ws: Path) -> Path | None:
            return orca_watch.find_worker_log(agent, root, ws, start)

        # K01 AGY: 진짜(도구 경로) vs 미끼(USER · MODEL content 에 이름 · 전체 경로 언급, 더 이른 시작)
        ws = ws_of("h-ws")
        root = base / "k01"
        real = agy(root, "real", start + timedelta(seconds=3), tool_paths=[f"{ws.as_posix()}/briefs/x.md"])
        agy(root, "decoy", start - timedelta(seconds=60),
            user_text=f"workspace h-ws at {ws.as_posix()} please audit", model_text=f"I looked at {ws}\\briefs")
        got = find("agy", root, ws)
        case("K01 AGY: 도구 호출 경로가 있는 대화만 후보 — 이름 · 경로를 USER/MODEL 글에 언급한 더 이른 대화는 아님",
             got == real, str(got))

        # K02 형제 접두사: 미끼의 도구 경로는 h-ws2 → 후보 아님(없음)
        root = base / "k02"
        ws_of("h-ws2")
        agy(root, "sibling", start + timedelta(seconds=2), tool_paths=[f"{(base / 'workspaces/origin/h-ws2').as_posix()}/a.py"])
        case("K02 AGY: 형제 작업 공간(h-ws2) 경로는 h-ws 와 맞지 않음 → None", find("agy", root, ws) is None,
             str(find("agy", root, ws)))

        # K03 역슬래시 · 대소문자 다른 경로
        root = base / "k03"
        win = str(ws).replace("/", "\\")
        variant = win.upper() if win[:1].isalpha() else win
        real = agy(root, "win", start + timedelta(seconds=1), tool_paths=[variant + "\\tests\\test_a.py"])
        case("K03 AGY: 역슬래시 · 대소문자만 다른 도구 경로도 같은 작업 공간", find("agy", root, ws) == real,
             str(find("agy", root, ws)))

        # K04 작업 공간 자체를 가리키는 인자(뒤에 '/' 없음)
        root = base / "k04"
        real = agy(root, "self", start + timedelta(seconds=1), tool_paths=[ws.as_posix()])
        case("K04 AGY: 작업 공간 경로 자체(뒤 '/' 없음)를 인자로 가진 도구 호출도 근거", find("agy", root, ws) == real,
             str(find("agy", root, ws)))

        # K05 진짜 후보 둘: 감시 시작과 가까운 쪽
        root = base / "k05"
        near = agy(root, "near", start + timedelta(seconds=5), tool_paths=[f"{ws.as_posix()}/a"])
        agy(root, "far", start - timedelta(seconds=90), tool_paths=[f"{ws.as_posix()}/b"])
        case("K05 AGY: 후보 둘이면 첫 행 시각이 감시 시작과 가까운 것(+5초 vs -90초)", find("agy", root, ws) == near,
             str(find("agy", root, ws)))

        # K06 거리 같으면 경로 문자열이 작은 것
        root = base / "k06"
        a = agy(root, "a-conv", start + timedelta(seconds=10), tool_paths=[f"{ws.as_posix()}/a"])
        agy(root, "b-conv", start - timedelta(seconds=10), tool_paths=[f"{ws.as_posix()}/b"])
        case("K06 AGY: 시각 차이가 같으면 경로 문자열이 작은 것", find("agy", root, ws) == a, str(find("agy", root, ws)))

        # K07 보조 대화(첫 행 SYSTEM · SUBAGENT) 제외
        root = base / "k07"
        agy(root, "sys", start + timedelta(seconds=1), tool_paths=[f"{ws.as_posix()}/a"], source="SYSTEM")
        agy(root, "sub", start + timedelta(seconds=1), tool_paths=[f"{ws.as_posix()}/a"], source="SUBAGENT")
        case("K07 AGY: 첫 행이 사용자 지시가 아닌 대화(SYSTEM · SUBAGENT)는 도구 경로가 있어도 제외",
             find("agy", root, ws) is None, str(find("agy", root, ws)))

        # K08 감시 시작 120초 전보다 이른 대화 제외
        root = base / "k08"
        agy(root, "old", start - timedelta(seconds=300), tool_paths=[f"{ws.as_posix()}/a"])
        case("K08 AGY: 첫 행이 감시 시작 120초 전보다 이르면 제외", find("agy", root, ws) is None, str(find("agy", root, ws)))

        # K09 깨진 줄 섞임
        root = base / "k09"
        real = agy(root, "junk", start + timedelta(seconds=1), tool_paths=[f"{ws.as_posix()}/a"], junk=True)
        broken = root / "only-broken" / ".system_generated" / "logs"
        broken.mkdir(parents=True)
        (broken / "transcript.jsonl").write_text("{x\n[\n", encoding="utf-8")
        try:
            got = find("agy", root, ws)
            ok = True
        except Exception as ex:  # noqa: BLE001
            got, ok = repr(ex), False
        case("K09 AGY: 깨진 JSON 줄은 건너뜀(첫 줄이 깨져도 USER 행을 찾음), 전부 깨진 파일은 무시 · 예외 없음",
             ok and got == real, str(got))

        # K10 이름만 언급(도구 경로 없음)은 근거 아님 — 예전 규칙으로 되돌아가지 않음
        root = base / "k10"
        agy(root, "name-only", start + timedelta(seconds=1), user_text="work in h-ws", model_text="h-ws done",
            tool_paths=["briefs/x.md"])
        case("K10 AGY: 이름 언급 · 상대 경로 도구 호출만 있는 대화는 후보 아님 → None", find("agy", root, ws) is None,
             str(find("agy", root, ws)))

        # K11 Codex: cwd 정확 일치(역슬래시 · 대소문자) vs 미끼
        croot = base / "k11"
        win = str(ws).replace("/", "\\")
        real = codex(croot, "real", start + timedelta(seconds=4), win.upper() if win[:1].isalpha() else win)
        codex(croot, "parent", start + timedelta(seconds=1), str(ws.parent), mention=f"cd {ws}")
        codex(croot, "sibling", start + timedelta(seconds=2), str(base / "workspaces/origin/h-ws2"))
        codex(croot, "notmeta", start + timedelta(seconds=3), str(ws), first_type="turn_context")
        case("K11 Codex: session_meta.cwd 가 작업 공간과 같은 세션만(부모 cwd + 내용 언급 · 형제 · 첫 행이 meta 아님 제외)",
             find("codex", croot, ws) == real, str(find("codex", croot, ws)))

        # K12 Codex 후보 둘 → 가까운 쪽
        croot = base / "k12"
        near = codex(croot, "near", start + timedelta(seconds=2), str(ws))
        codex(croot, "far", start - timedelta(seconds=100), str(ws))
        case("K12 Codex: 같은 cwd 후보 둘이면 감시 시작과 가까운 것", find("codex", croot, ws) == near,
             str(find("codex", croot, ws)))

        # K13 근거 없음 → None
        case("K13 어떤 로그도 근거가 없으면 None", find("agy", base / "empty-root", ws) is None
             and find("codex", base / "empty-root", ws) is None, "")

        # K05b 이름 순서가 반대여도 가까운 쪽(발견 순서로 고르지 않음, 사전 검토 R1)
        root = base / "k05b"
        agy(root, "a-far", start - timedelta(seconds=90), tool_paths=[f"{ws.as_posix()}/b"])
        near = agy(root, "z-near", start + timedelta(seconds=5), tool_paths=[f"{ws.as_posix()}/a"])
        croot = base / "k12b"
        codex(croot, "a-far", start - timedelta(seconds=100), str(ws))
        cnear = codex(croot, "z-near", start + timedelta(seconds=2), str(ws))
        case("K05b 후보 이름 순서가 시각 순서와 반대여도 감시 시작과 가까운 것(AGY · Codex)",
             find("agy", root, ws) == near and find("codex", croot, ws) == cnear,
             f"{find('agy', root, ws)} {find('codex', croot, ws)}")

        # K14 첫 도구 호출은 다른 경로, 뒤 도구 호출이 작업 공간(사전 검토 R2)
        root = base / "k14"
        later = agy(root, "later", start + timedelta(seconds=2),
                    tool_paths=[f"{(base / 'elsewhere').as_posix()}/x.md", f"{ws.as_posix()}/briefs/x.md"])
        case("K14 AGY: 첫 tool_calls 행이 아니라 뒤 행에 작업 공간 경로가 있어도 근거", find("agy", root, ws) == later,
             str(find("agy", root, ws)))

        # K15 검색은 로그 파일을 바꾸지 않는다(사전 검토 R3)
        snap_root = base / "k15"
        agy(snap_root, "none", start + timedelta(seconds=1), user_text=f"see {ws}")
        agy(snap_root, "real", start + timedelta(seconds=2), tool_paths=[f"{ws.as_posix()}/a"])
        codex(snap_root / "cx", "c", start + timedelta(seconds=1), str(ws.parent))
        before = {p: p.read_bytes() for p in snap_root.rglob("*") if p.is_file()}
        find("agy", snap_root, ws)
        find("codex", snap_root / "cx", ws)
        after = {p: p.read_bytes() for p in snap_root.rglob("*") if p.is_file()}
        case("K15 검색 전후 로그 파일 목록 · 바이트 같음", before == after, f"{len(before)} vs {len(after)}")

        # K16 파일 · 행이 많아도 끝난다(사전 검토 R4: 계약에 시간 상한이 없어 넉넉한 상한 30초로 확인)
        import time
        root = base / "k16"
        for i in range(150):
            agy(root, f"bulk{i:03d}", start + timedelta(seconds=1),
                tool_paths=[f"{(base / 'other').as_posix()}/f{j}.md" for j in range(300)])
        bulk_real = agy(root, "zzz-real", start + timedelta(seconds=1), tool_paths=[f"{ws.as_posix()}/a"])
        t0 = time.monotonic()
        got = find("agy", root, ws)
        took = time.monotonic() - t0
        case("K16 대화 151개 · 각 300행에서도 30초 안에 진짜 하나를 찾음", got == bulk_real and took < 30,
             f"{took:.1f}s {got}")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = bool(rows)
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
