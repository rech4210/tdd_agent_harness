"""역할 인계 검증 및 변경 경계 통제 모듈 (WO-H64).

진행 중 lead 교체(handover)가 발생한 저장소에서, 이전 실행(run)이
현재 origin 상태와 여전히 안전하게 정합하는지 검증한다.
- False: 인계 관련 없음 (호출자가 기존 strict 원본 검사를 계속 진행)
- True: 해당 run에 승인된 인계와 현재 origin의 정확한 일치를 모두 검증 완료
- 손상 / 불완전 / 무관 변경 발견 시 ValueError 발생
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

# 바이트코드 생성 차단
sys.dont_write_bytecode = True

HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import role_state

HANDOVER_RECORDS_REL = Path("reports/role-handovers.json")
STATE_FILE_REL = Path("config/role-state.json")
ROLES_FILE_REL = Path("config/roles.toml")
ASSIGNMENTS_FILE_REL = Path("config/assignments.toml")
AGENTS_FILE_REL = Path("config/agents.toml")


def _git(root: Path, *args: str) -> str:
    """Git 명령어 실행 헬퍼 (rev-parse 등 단일 문자열 출력용)."""
    p = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if p.returncode != 0:
        raise RuntimeError(f"Git 명령 실패 ({' '.join(args)}): {p.stderr.strip()}")
    return p.stdout.strip()


def _get_git_status_files(root: Path) -> list[str]:
    """git status --porcelain -z --untracked-files=all 를 안전하게 파싱하여 변경된 파일 상대 경로(POSIX) 목록을 반환한다."""
    p = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if p.returncode != 0:
        raise RuntimeError(f"Git status 실패: {p.stderr.decode('utf-8', 'replace').strip()}")

    raw = p.stdout
    if not raw:
        return []

    tokens = raw.split(b"\0")
    files: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token:
            i += 1
            continue
        if len(token) >= 3:
            xy = token[:2].decode("latin-1", "replace")
            path_bytes = token[3:]
            p_str = path_bytes.decode("utf-8", "replace").replace("\\", "/")
            if p_str.startswith('"') and p_str.endswith('"'):
                p_str = p_str[1:-1]
            files.append(p_str)
            # rename 또는 copy 인 경우 다음 토큰이 target path
            if "R" in xy or "C" in xy:
                i += 1
                if i < len(tokens) and tokens[i]:
                    target_str = tokens[i].decode("utf-8", "replace").replace("\\", "/")
                    if target_str.startswith('"') and target_str.endswith('"'):
                        target_str = target_str[1:-1]
                    files.append(target_str)
        i += 1
    return files


def _get_git_diff_names(root: Path, rev_range: str) -> list[str]:
    """git diff --name-only -z rev_range 안전 파싱."""
    p = subprocess.run(
        ["git", "diff", "--name-only", "-z", rev_range],
        cwd=root,
        capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if p.returncode != 0:
        raise RuntimeError(f"Git diff 실패 ({rev_range}): {p.stderr.decode('utf-8', 'replace').strip()}")

    raw = p.stdout
    if not raw:
        return []

    tokens = raw.split(b"\0")
    files: list[str] = []
    for token in tokens:
        if token:
            p_str = token.decode("utf-8", "replace").replace("\\", "/")
            if p_str.startswith('"') and p_str.endswith('"'):
                p_str = p_str[1:-1]
            files.append(p_str)
    return files


def _hash_tree(dir_path: Path) -> dict[str, str]:
    """디렉터리 내 모든 일반 파일의 상대 경로별 SHA-256 해시를 계산한다."""
    hashes: dict[str, str] = {}
    if not dir_path.is_dir():
        return hashes
    for p in sorted(dir_path.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_file() and not p.is_symlink():
            try:
                rel = p.relative_to(dir_path).as_posix()
                hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            except Exception:
                pass
    return hashes


def _is_sha256_hex(val: Any) -> bool:
    """정확한 SHA-256 64자리 16진수 문자열인지 검증한다."""
    return isinstance(val, str) and len(val) == 64 and all(c in "0123456789abcdefABCDEF" for c in val)


def _get_legit_preset_paths(root: Path, preset_name: str, approval_ref: str | None = None) -> set[str]:
    """고정 승인과 실제 프리셋이 관리하는 합법 경로 목록 계산 (임의 ledger 경로 차단용)."""
    allowed: set[str] = {
        STATE_FILE_REL.as_posix(),
        AGENTS_FILE_REL.as_posix(),
        HANDOVER_RECORDS_REL.as_posix(),
    }
    handoff_rel = "docs/HANDOFF.md"
    if approval_ref:
        try:
            app_info = role_state.read_approval(root, approval_ref, preset_name)
            handoff_rel = app_info.get("handoff_rel", "docs/HANDOFF.md")
        except Exception:
            pass
    allowed.add(Path(handoff_rel).as_posix())

    preset_dir = root / "templates" / "roles" / preset_name
    if preset_dir.is_dir():
        for mf in preset_dir.glob("*.md"):
            allowed.add(mf.name)
    return allowed


def get_handover_allowed_paths(root: Path, contract: dict | None = None) -> set[str]:
    """인계 기록 및 승인/프리셋에 근거해 합법적으로 변경이 허용된 파일 상대 경로 집합 반환."""
    root = Path(root).resolve()
    allowed: set[str] = {
        STATE_FILE_REL.as_posix(),
        AGENTS_FILE_REL.as_posix(),
        HANDOVER_RECORDS_REL.as_posix(),
    }

    records_file = root / HANDOVER_RECORDS_REL
    if records_file.is_file():
        try:
            rdata = json.loads(records_file.read_text(encoding="utf-8"))
            if isinstance(rdata, dict) and isinstance(rdata.get("handovers"), list):
                for rec in rdata["handovers"]:
                    if isinstance(rec, dict):
                        preset_name = rec.get("preset")
                        approval_ref = rec.get("approval_ref")
                        if preset_name:
                            allowed.update(_get_legit_preset_paths(root, preset_name, approval_ref))
        except Exception:
            pass

    state_file = root / STATE_FILE_REL
    if state_file.is_file():
        try:
            sdata = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(sdata, dict):
                cur_preset = sdata.get("preset")
                cur_ref = sdata.get("approval_ref")
                if cur_preset:
                    allowed.update(_get_legit_preset_paths(root, cur_preset, cur_ref))
        except Exception:
            pass

    return allowed


def _validate_uncommitted_events_log(root: Path, contract: dict) -> None:
    """작업 트리의 reports/events.jsonl 이 유효한 append-only 로그인지 검증한다.

    시작 시점 스냅샷(origin_start_snapshot)의 초기 바이트가 온전히 보존(prefix)되었는지
    SHA-256 및 크기로 대조하고, 새로 추가된 접미사(suffix)가 UTF-8 JSON 객체 레코드만으로
    구성되어 있는지 검증한다.
    """
    origin_start_snap = contract.get("origin_start_snapshot")
    if origin_start_snap is not None and not isinstance(origin_start_snap, dict):
        raise ValueError("계약(contract)의 origin_start_snapshot 형식이 올바르지 않습니다.")
    if origin_start_snap is None:
        origin_start_snap = {}

    events_rel = "reports/events.jsonl"
    if events_rel in origin_start_snap:
        base_snap = origin_start_snap[events_rel]
        if not isinstance(base_snap, dict):
            raise ValueError(f"origin_start_snapshot 의 '{events_rel}' 스냅샷 정보가 유효한 딕셔너리가 아닙니다.")
        base_sha = base_snap.get("sha256")
        if not _is_sha256_hex(base_sha):
            raise ValueError(f"origin_start_snapshot 의 '{events_rel}' sha256 해시가 올바르지 않습니다.")
        base_size = base_snap.get("size")
        if not isinstance(base_size, int) or base_size < 0:
            raise ValueError(f"origin_start_snapshot 의 '{events_rel}' size 가 올바르지 않습니다.")
        base_type = base_snap.get("type")
        if base_type != "file":
            raise ValueError(f"origin_start_snapshot 의 '{events_rel}' type 이 'file'이 아닙니다: {base_type!r}")
        base_mode = base_snap.get("mode")
        if base_mode is not None and (not isinstance(base_mode, int) or base_mode < 0):
            raise ValueError(f"origin_start_snapshot 의 '{events_rel}' mode 가 올바르지 않습니다: {base_mode!r}")
    else:
        # 시작 스냅샷에 없는 경우 빈 접두사로 취급
        base_sha = None
        base_size = 0
        base_type = "file"
        base_mode = None

    events_path = root / "reports" / "events.jsonl"
    if not events_path.exists():
        raise ValueError("reports/events.jsonl 파일이 존재하지 않습니다 (삭제 또는 부재).")
    if events_path.is_symlink():
        raise ValueError("reports/events.jsonl 이 심볼릭 링크입니다 (안전하지 않은 파일 타입).")
    if not events_path.is_file():
        raise ValueError("reports/events.jsonl 이 일반 파일이 아닙니다.")

    try:
        events_path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError("reports/events.jsonl 경로 탈출 감지")

    st = events_path.stat()
    if base_mode is not None:
        curr_mode = stat.S_IMODE(st.st_mode)
        if curr_mode != base_mode:
            raise ValueError(
                f"reports/events.jsonl 파일 모드 변경 감지: {oct(curr_mode)} != {oct(base_mode)}"
            )

    curr_bytes = events_path.read_bytes()
    curr_size = len(curr_bytes)

    if curr_size < base_size:
        raise ValueError(
            f"reports/events.jsonl 파일 크기가 시작 스냅샷보다 작습니다: {curr_size} < {base_size}"
        )

    if base_size > 0:
        prefix_bytes = curr_bytes[:base_size]
        prefix_sha = hashlib.sha256(prefix_bytes).hexdigest()
        if prefix_sha != base_sha:
            raise ValueError(
                f"reports/events.jsonl 기존 시작 내용 변조 감지 (prefix sha256 불일치: {prefix_sha} != {base_sha})"
            )

    new_bytes = curr_bytes[base_size:]
    if new_bytes:
        try:
            new_text = new_bytes.decode("utf-8")
        except UnicodeDecodeError as ue:
            raise ValueError(f"reports/events.jsonl 새로 추가된 내용 UTF-8 디코딩 실패: {ue}")

        for line in new_text.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            try:
                record = json.loads(line_str)
            except Exception as je:
                raise ValueError(f"reports/events.jsonl 새로 추가된 레코드 JSON 파싱 실패: {je}")
            if not isinstance(record, dict):
                raise ValueError(
                    f"reports/events.jsonl 새로 추가된 레코드가 JSON 객체가 아닙니다: {type(record).__name__}"
                )


def validate_handover(root: Path, contract: dict) -> bool:
    """해당 실행(contract)에 대한 역할 인계의 정합성과 origin 일치를 검증한다.

    Returns:
        False: 해당 실행과 관련된 인계가 발생하지 않음 (호출자가 기존 strict 검사를 계속함).
        True: 승인된 인계와 현재 origin 의 정확한 일치 및 불변식이 모두 검증됨.

    Raises:
        ValueError: 인계 기록 손상, 임의 상태 변경, 정책/배정 드리프트, 허용되지 않은 파일 변경 등 위반 감지 시.
    """
    root = Path(root).resolve()
    if not isinstance(contract, dict):
        raise ValueError("실행 계약(contract)이 딕셔너리가 아닙니다.")

    state_file = root / STATE_FILE_REL
    records_file = root / HANDOVER_RECORDS_REL

    contract_gen = contract.get("generation")
    if contract_gen is None or not isinstance(contract_gen, int) or contract_gen < 1:
        raise ValueError(f"계약의 generation({contract_gen})이 유효하지 않습니다.")

    # 1. 인계 발생 여부 판별
    if not records_file.is_file():
        if not state_file.is_file():
            # 상태 파일도 없음 -> 인계 없음 (호출자가 strict 검사로 처리)
            return False

        try:
            cur_state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"손상된 역할 상태 파일: {e}")

        cur_gen = cur_state.get("generation")
        if cur_gen != contract_gen:
            # 상태 파일의 세대는 올랐으나 인계 기록이 전혀 없음 (임의 상태 drift)
            raise ValueError("임의의 역할 상태 세대 변경(drift) 감지: 승인된 인계 기록 부재")
        return False

    # 2. 인계 기록 파일 파싱 및 스키마 검증 (손상 감지)
    try:
        records_data = json.loads(records_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"손상된 인계 기록 파일 감지 (JSON 파싱 실패): {e}")

    if not isinstance(records_data, dict) or records_data.get("schema") != 1:
        raise ValueError("인계 기록 파일 스키마 오류 또는 형식 손상 (schema != 1)")

    handovers = records_data.get("handovers")
    if not isinstance(handovers, list):
        raise ValueError("인계 기록 항목 손상 (handovers 가 리스트가 아님)")

    for item in handovers:
        if not isinstance(item, dict) or item.get("schema") != 1:
            raise ValueError("인계 기록 내 항목 스키마 불일치 또는 손상")

    if not state_file.is_file():
        raise ValueError("역할 상태 파일(config/role-state.json)이 없으나 인계 기록이 존재합니다.")

    try:
        cur_state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"손상된 역할 상태 파일: {e}")

    cur_gen = cur_state.get("generation")
    if cur_gen is None or not isinstance(cur_gen, int):
        raise ValueError(f"역할 상태 세대(generation)가 올바르지 않습니다: {cur_gen}")

    # 현재 세대가 계약 세대와 동일하고, contract_gen 이후의 인계 기록이 없다면 인계 없음
    subsequent_handovers = [
        h for h in handovers
        if isinstance(h, dict) and isinstance(h.get("generation"), int) and h.get("generation") > contract_gen
    ]
    if cur_gen == contract_gen:
        if not subsequent_handovers:
            return False
        else:
            raise ValueError("인계 기록에는 새 세대가 존재하나 현재 상태 세대가 전진하지 않았습니다.")

    if cur_gen < contract_gen:
        raise ValueError(f"현재 상태 세대({cur_gen})가 계약 세대({contract_gen})보다 과거입니다.")

    # 3. 현재 역할 상태 정합성 검증 (phase, internal_check)
    cur_phase = cur_state.get("phase")
    if cur_phase != "ready":
        raise ValueError(f"역할 상태 phase 가 'ready' 가 아닙니다: '{cur_phase}' (미완료/실패/중단된 인계)")

    state_errs = role_state.internal_check(root, cur_state)
    if state_errs:
        raise ValueError(f"현재 역할 상태/문서 정합성 검사 실패: {'; '.join(state_errs)}")

    # 4. 인계 체인 연결성 및 승인/스냅샷 검증
    # contract_gen -> ... -> cur_gen 까지 끊김 없이 이어지는지 확인
    chain_by_gen: dict[int, dict] = {}
    for h in handovers:
        hgen = h.get("generation")
        if isinstance(hgen, int):
            if hgen in chain_by_gen:
                raise ValueError(f"중복된 인계 세대(generation={hgen}) 기록 감지")
            chain_by_gen[hgen] = h

    origin_start_snap = contract.get("origin_start_snapshot")
    if not isinstance(origin_start_snap, dict):
        origin_start_snap = {}
    chain_expected_hashes: dict[str, str | None] = {}

    for g in range(contract_gen + 1, cur_gen + 1):
        if g not in chain_by_gen:
            raise ValueError(f"인계 체인 단절 감지: 세대 {g} 의 인계 기록이 없습니다.")
        rec = chain_by_gen[g]
        expected_prev = g - 1
        if rec.get("previous_generation") != expected_prev:
            raise ValueError(
                f"인계 기록 세대 연결 오류: 세대 {g} 의 이전 세대({rec.get('previous_generation')}) != {expected_prev}"
            )

        # 각 단계 승인 재검증
        rec_ref = rec.get("approval_ref")
        rec_preset = rec.get("preset")
        if not rec_ref or not rec_preset:
            raise ValueError(f"인계 기록(세대 {g})에 approval_ref 또는 preset 이 누락되었습니다.")

        try:
            app_info = role_state.read_approval(root, rec_ref, rec_preset)
        except Exception as ae:
            raise ValueError(f"인계 기록(세대 {g}) 승인 검증 실패: {ae}")

        if app_info.get("blob_hash") != rec.get("approval_blob_hash"):
            raise ValueError(f"인계 기록(세대 {g}) 승인 blob 해시 불일치")

        app = app_info["approval"]
        # 모델명은 등록 계열과 일치하는 임의 문자열 허용
        app_models = app.get("models", {})
        if rec.get("model_bindings") != app_models:
            raise ValueError(f"인계 기록(세대 {g}) model_bindings 가 승인 blob models 과 다릅니다.")

        # 프리셋 원본 lead 대조
        pdir = root / "templates" / "roles" / rec_preset
        pfile = pdir / "preset.toml"
        if not pfile.is_file():
            raise ValueError(f"인계 기록(세대 {g}) 프리셋 파일 부재: {pfile}")
        try:
            pdata = tomllib.loads(pfile.read_text(encoding="utf-8"))
            if rec.get("lead") != pdata.get("lead"):
                raise ValueError(
                    f"인계 기록(세대 {g}) lead '{rec.get('lead')}' != 프리셋 원본 '{pdata.get('lead')}'"
                )
        except Exception as pe:
            raise ValueError(f"인계 기록(세대 {g}) 프리셋 파싱 실패: {pe}")

        # 합법 관리 경로 집합 (임의 ledger 경로 차단)
        legit_paths = _get_legit_preset_paths(root, rec_preset, rec_ref)

        # file_hashes 체인 연결 검증 (origin_start_snapshot -> rec1 -> ... -> recN)
        rec_fhashes = rec.get("file_hashes")
        if not isinstance(rec_fhashes, dict):
            raise ValueError(f"인계 기록(세대 {g})에 file_hashes 딕셔너리가 누락되었거나 올바르지 않습니다.")

        expected_paths = legit_paths - {HANDOVER_RECORDS_REL.as_posix()}
        if set(rec_fhashes.keys()) != expected_paths:
            missing = expected_paths - set(rec_fhashes.keys())
            if missing:
                raise ValueError(f"인계 기록(세대 {g})에 필수 전환 대상 파일 해시 누락: {sorted(missing)}")
            extra = set(rec_fhashes.keys()) - expected_paths
            raise ValueError(f"인계 기록(세대 {g})에 승인되지 않은 임의 경로 포함: {sorted(extra)}")

        for f_path in sorted(rec_fhashes.keys()):
            hinfo = rec_fhashes[f_path]
            if not isinstance(hinfo, dict) or set(hinfo.keys()) != {"before", "after"}:
                raise ValueError(f"인계 기록(세대 {g})의 '{f_path}' 해시 항목 형식이 올바르지 않습니다.")

            b_hash = hinfo["before"]
            a_hash = hinfo["after"]

            for val_name, val in (("before", b_hash), ("after", a_hash)):
                if val is not None and not _is_sha256_hex(val):
                    raise ValueError(
                        f"인계 기록(세대 {g})의 '{f_path}' {val_name} 해시 형식이 올바르지 않습니다: {val!r}"
                    )

            if f_path not in chain_expected_hashes:
                snap_item = origin_start_snap.get(f_path)
                expected_b = snap_item.get("sha256") if isinstance(snap_item, dict) else None
                chain_expected_hashes[f_path] = expected_b

            if b_hash != chain_expected_hashes[f_path]:
                raise ValueError(
                    f"인계 체인 연결 해시 불일치: 세대 {g} 의 '{f_path}' before 해시({b_hash}) != "
                    f"이전 상태 해시({chain_expected_hashes[f_path]})"
                )
            chain_expected_hashes[f_path] = a_hash

    # 최종 세대 레코드와 현재 역할 상태 일치 확인
    last_rec = chain_by_gen[cur_gen]
    if cur_state.get("preset") != last_rec.get("preset"):
        raise ValueError(f"현재 상태 preset({cur_state.get('preset')}) != 최종 인계 preset({last_rec.get('preset')})")
    if cur_state.get("lead") != last_rec.get("lead"):
        raise ValueError(f"현재 상태 lead({cur_state.get('lead')}) != 최종 인계 lead({last_rec.get('lead')})")
    if cur_state.get("approval_blob_hash") != last_rec.get("approval_blob_hash"):
        raise ValueError("현재 상태 approval_blob_hash != 최종 인계 approval_blob_hash")
    if cur_state.get("documents") != last_rec.get("documents"):
        raise ValueError("현재 상태 documents != 최종 인계 documents")

    # 체인에 기록된 최종 파일 해시와 현재 실제 디스크 내용 일치 검증
    for f_path, expected_after_h in chain_expected_hashes.items():
        disk_p = root / f_path
        curr_disk_b = disk_p.read_bytes() if disk_p.is_file() else None
        curr_disk_h = hashlib.sha256(curr_disk_b).hexdigest() if curr_disk_b is not None else None
        if curr_disk_h != expected_after_h:
            raise ValueError(
                f"인계 대상 파일 '{f_path}' 의 현재 디스크 내용이 최종 인계 결과와 일치하지 않습니다 (임의 변조 감지)"
            )

    # 5. 기존 run 불변식 검증
    # (a) roles.toml 불변
    roles_file = root / ROLES_FILE_REL
    if not roles_file.is_file():
        raise ValueError("config/roles.toml 파일이 없습니다.")
    cur_roles_hash = hashlib.sha256(roles_file.read_bytes()).hexdigest()
    if cur_roles_hash != contract.get("origin_roles_hash"):
        raise ValueError("config/roles.toml 정책 드리프트 감지 (인계 중 정책 변경 불허)")

    # (b) assignments.toml 불변 (기존 슬라이스 배정 보존)
    assignments_file = root / ASSIGNMENTS_FILE_REL
    if not assignments_file.is_file():
        raise ValueError("config/assignments.toml 파일이 없습니다.")
    cur_asg_hash = hashlib.sha256(assignments_file.read_bytes()).hexdigest()
    if cur_asg_hash != contract.get("assignments_hash"):
        raise ValueError("config/assignments.toml 배정 드리프트 감지 (기존 슬라이스 배정 보존 위반)")

    # (c) contract 필수 task 키 불변성
    for k in ("role", "agent", "model", "family", "base", "approval_ref", "task_paths"):
        if k not in contract:
            raise ValueError(f"계약(contract)에서 필수 작업 키 '{k}' 가 누락되었습니다.")

    # (d) agents.toml 검증 (계약 당시 에이전트 및 계열 보존)
    agents_file = root / AGENTS_FILE_REL
    if not agents_file.is_file():
        raise ValueError("config/agents.toml 파일이 없습니다.")
    cur_ag_doc = tomllib.loads(agents_file.read_text(encoding="utf-8")).get("agents", {})
    contract_agent = contract.get("agent")
    if contract_agent not in cur_ag_doc:
        raise ValueError(f"계약의 에이전트 '{contract_agent}' 가 config/agents.toml 에서 삭제되었습니다.")
    if cur_ag_doc[contract_agent].get("family") != contract.get("family"):
        raise ValueError(
            f"계약의 에이전트 '{contract_agent}' 계열이 변경되었습니다: "
            f"'{cur_ag_doc[contract_agent].get('family')}' != '{contract.get('family')}'"
        )

    # (e) 신뢰 디렉터리 불변성 검증 (harness, adapters, templates)
    saved_trusted = contract.get("trusted_sources_hash", {})
    for d in ("harness", "adapters", "templates"):
        if d in saved_trusted:
            cur_tree = _hash_tree(root / d)
            if cur_tree != saved_trusted[d]:
                raise ValueError(f"신뢰 디렉터리 '{d}' 변조 감지 (인계 중 코드/템플릿 변경 불허)")

    # (f) config 디렉터리 무관 설정 파일 불변성 검증 (gates, slices, locks 등)
    if "config" in saved_trusted:
        config_baseline = saved_trusted["config"]
        cur_config_tree = _hash_tree(root / "config")
        all_config_keys = set(config_baseline.keys()) | set(cur_config_tree.keys())
        for ck in sorted(all_config_keys):
            if ck == "role-state.json":
                continue
            if ck == "agents.toml":
                continue
            if ck not in config_baseline:
                raise ValueError(f"config 디렉터리에 미승인 파일 추가 감지: config/{ck}")
            if ck not in cur_config_tree:
                raise ValueError(f"config 디렉터리에서 필수 파일 삭제 감지: config/{ck}")
            if cur_config_tree[ck] != config_baseline[ck]:
                raise ValueError(f"config 디렉터리 내 무관 설정 변경 감지 (drift): config/{ck}")

    # 6. 허용된 인계 파일 목록 계산
    allowed_files = get_handover_allowed_paths(root, contract)

    # 7. HEAD 검증 및 커밋 diff 검증 (role-only HEAD 전진 허용, 무관 변경 거부)
    start_head = contract.get("origin_head")
    if not start_head:
        raise ValueError("계약(contract)에 origin_head 정보가 없습니다.")

    cur_head = _git(root, "rev-parse", "HEAD")
    if cur_head != start_head:
        # start_head 가 cur_head 의 직계 조상인지 확인 (직계 전진)
        p_anc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", start_head, cur_head],
            cwd=root,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if p_anc.returncode != 0:
            raise ValueError(f"HEAD 가 시작 커밋의 직계 후손이 아닙니다: {start_head} -> {cur_head}")

        # start_head..cur_head 사이의 변경 파일 목록 검사 (-z 안전 파싱)
        committed_changed = _get_git_diff_names(root, f"{start_head}..{cur_head}")
        for f in committed_changed:
            if f not in allowed_files:
                raise ValueError(f"인계 커밋에 허용되지 않은 파일 변경 포함 (무관 제품/문서 변경 거부): '{f}'")

    # 8. 작업 트리(uncommitted) 상태 검증
    # -z 안전 파싱 및 예약 제어 산출물(reports/executions/<run>/, reports/gate_report.json, reports/runs/*.json), 채택 사후 검사가 다시 쓰는 파생 보고서 reports/kpi.md · reports/tdd-timing.md (WO-H79) 허용
    status_files = _get_git_status_files(root)
    for raw_path in status_files:
        norm_path = raw_path.replace("\\", "/")
        parts = norm_path.split("/")
        is_control_artifact = (
            (len(parts) >= 4 and parts[0] == "reports" and parts[1] == "executions" and parts[2] != "")
            or norm_path == "reports/gate_report.json"
            or (len(parts) == 3 and parts[0] == "reports" and parts[1] == "runs" and parts[2].endswith(".json") and parts[2] != ".json")
            or norm_path in ("reports/kpi.md", "reports/tdd-timing.md")
        )
        if is_control_artifact:
            continue
        if norm_path == "reports/events.jsonl":
            _validate_uncommitted_events_log(root, contract)
            continue
        if norm_path not in allowed_files:
            raise ValueError(f"작업 트리에 허용되지 않은 파일 변경 감지: '{raw_path}'")

    return True
