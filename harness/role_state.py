"""역할 상태 및 전환 트랜잭션 관리 공통 모듈 (WO-H63).

역할 상태 파일(config/role-state.json)의 생명주기(schema, generation, phase),
고정 git commit blob 기반 승인(approval-ref) 검증, 문서 관리 구간 치환,
공유 락 및 원자적 전환(지속 journal + CAS + replace + 후검증 + 안전 롤백)을 제공한다.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

# 바이트코드 생성 차단
sys.dont_write_bytecode = True

# harness 디렉터리 및 저장소 루트를 sys.path 에 등록
HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ROLES_BEGIN = b"<!-- harness:roles:begin -->"
ROLES_END = b"<!-- harness:roles:end -->"
GUIDE_BEGIN = b"<!-- harness:role-guide:begin -->"
GUIDE_END = b"<!-- harness:role-guide:end -->"
STATE_FILE_REL = Path("config/role-state.json")


def _get_git_lock_dir(root: Path) -> Path:
    """저장소별 안전한 락 디렉터리를 구한다 (.git 디렉터리 우선, config에는 절대 쓰지 않음)."""
    git_p = root / ".git"
    if git_p.is_dir():
        return git_p
    elif git_p.is_file():
        try:
            line = git_p.read_text(encoding="utf-8").strip()
            if line.startswith("gitdir:"):
                target = Path(line.split(":", 1)[1].strip())
                if not target.is_absolute():
                    target = (root / target).resolve()
                if target.is_dir():
                    return target
        except Exception:
            pass
    # git 저장소가 아닐 경우 시스템 임시 디렉터리에 root 해시 기반으로 둠 (config 오염 방지)
    h = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    tmp_lock_dir = Path(tempfile.gettempdir()) / f"h63-locks-{h}"
    tmp_lock_dir.mkdir(parents=True, exist_ok=True)
    return tmp_lock_dir


@contextlib.contextmanager
def role_lock(root: Path, timeout: float = 10.0):
    """전환과 executor가 공유하는 저장소 단위 락 (.git 내부 위치 고정, config 찌꺼기 방지)."""
    lock_dir = _get_git_lock_dir(root)
    lock_file = lock_dir / "role-state.lock"

    f = open(lock_file, "a+", encoding="utf-8")
    start_time = time.time()
    acquired = False
    try:
        while not acquired:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, IOError):
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"역할 상태 락 획득 시간 초과 ({lock_file})")
                time.sleep(0.05)
        yield f
    finally:
        if acquired:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
        f.close()


def replace_or_append_managed_section(
    target_bytes: bytes,
    begin_marker: bytes,
    end_marker: bytes,
    inner_content_bytes: bytes,
) -> bytes:
    """대상 바이트에서 관리 구간을 찾아 교체하거나, 없으면 끝에 추가한다.
    중복 마커 또는 손상된 마커가 있으면 ValueError를 발생시킨다.
    """
    count_begin = target_bytes.count(begin_marker)
    count_end = target_bytes.count(end_marker)

    if count_begin == 0 and count_end == 0:
        work = target_bytes
        if work and not work.endswith(b"\n"):
            work += b"\n"
        if work and not work.endswith(b"\n\n"):
            work += b"\n"
        section = begin_marker + b"\n" + inner_content_bytes.strip() + b"\n" + end_marker + b"\n"
        return work + section

    if count_begin != 1 or count_end != 1:
        raise ValueError(
            f"중복되거나 누락된 관리 마커 감지: begin={count_begin}, end={count_end}"
        )

    idx_begin = target_bytes.find(begin_marker)
    idx_end = target_bytes.find(end_marker)
    if idx_end < idx_begin:
        raise ValueError("관리 마커의 순서가 올바르지 않습니다 (end가 begin보다 앞섬)")

    prefix = target_bytes[:idx_begin]
    suffix = target_bytes[idx_end + len(end_marker):]
    section = begin_marker + b"\n" + inner_content_bytes.strip() + b"\n" + end_marker
    return prefix + section + suffix


def extract_managed_section(
    target_bytes: bytes,
    begin_marker: bytes,
    end_marker: bytes,
) -> bytes:
    """문서에서 관리 구간 내용만 추출한다."""
    count_begin = target_bytes.count(begin_marker)
    count_end = target_bytes.count(end_marker)
    if count_begin != 1 or count_end != 1:
        raise ValueError(f"관리 마커 개수 비정상: begin={count_begin}, end={count_end}")
    idx_begin = target_bytes.find(begin_marker)
    idx_end = target_bytes.find(end_marker)
    if idx_end < idx_begin:
        raise ValueError("관리 마커 순서 역전")
    inner = target_bytes[idx_begin + len(begin_marker):idx_end]
    return inner.strip()


def generate_handoff_role_guide_bytes(preset_name: str, lead: str) -> bytes:
    """HANDOFF.md 에 들어갈 일반 역할 안내 구간 바이트 생성."""
    lines = [
        f"## 역할 안내 (lead: {lead}, preset: {preset_name})",
        "",
        f"- 현재 조율 lead: {lead}",
        "- 과거 슬라이스 배정은 해당 슬라이스 기준을 우선합니다.",
        "- 새 작업 초안은 활성 기본 배정을 사용합니다.",
    ]
    return "\n".join(lines).encode("utf-8")


def compute_config_version(
    root: Path,
    preset_name: str,
    agents_override_bytes: bytes | None = None,
) -> dict[str, str]:
    """roles/agents/preset/선택 템플릿/하네스 버전의 해시 묶음 생성.
    반영 예정인 agents bytes가 제공되면 해당 바이트 해시를 적용한다.
    """
    hashes: dict[str, str] = {}

    roles_path = root / "config" / "roles.toml"
    if roles_path.is_file():
        hashes["roles"] = hashlib.sha256(roles_path.read_bytes()).hexdigest()

    agents_path = root / "config" / "agents.toml"
    if agents_override_bytes is not None:
        hashes["agents"] = hashlib.sha256(agents_override_bytes).hexdigest()
    elif agents_path.is_file():
        hashes["agents"] = hashlib.sha256(agents_path.read_bytes()).hexdigest()

    preset_dir = root / "templates" / "roles" / preset_name
    preset_file = preset_dir / "preset.toml"
    if preset_file.is_file():
        hashes["preset"] = hashlib.sha256(preset_file.read_bytes()).hexdigest()

    if preset_dir.is_dir():
        for mf in sorted(preset_dir.glob("*.md")):
            if mf.is_file():
                hashes[f"template:{mf.name}"] = hashlib.sha256(mf.read_bytes()).hexdigest()

    upstream_file = root / "harness" / "UPSTREAM"
    if upstream_file.is_file():
        hashes["upstream"] = hashlib.sha256(upstream_file.read_bytes()).hexdigest()

    return hashes


def read_approval(root: Path, approval_ref: str, preset_name: str) -> dict:
    """고정 git commit blob JSON에서 승인 참조를 읽고 범위를 검증한다."""
    if not approval_ref:
        raise ValueError("승인 참조(--approval-ref)가 비어 있습니다.")

    m = re.fullmatch(r"([0-9a-fA-F]{7,40}):([^#]+)#([A-Za-z0-9_-]+)", approval_ref)
    if not m:
        raise ValueError(
            f"승인 참조 형식이 올바르지 않습니다: '{approval_ref}'. "
            "<commit>:<path>#<id> 형식이어야 합니다 (커밋 없는 작업 트리 참조 불허)."
        )

    commit_sha, rel_path, approval_id = m.group(1), m.group(2), m.group(3)

    proc = subprocess.run(
        ["git", "show", f"{commit_sha}:{rel_path}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise ValueError(
            f"고정 커밋 blob을 읽을 수 없습니다 ({commit_sha}:{rel_path}): {proc.stderr.strip()}"
        )

    blob_text = proc.stdout
    blob_hash = hashlib.sha256(blob_text.encode("utf-8")).hexdigest()

    try:
        doc = json.loads(blob_text)
    except Exception as e:
        raise ValueError(f"승인 blob JSON 파싱 실패: {e}")

    if doc.get("schema") != 1:
        raise ValueError(f"승인 문서 schema={doc.get('schema')} 지원되지 않습니다 (기대값: 1)")

    approvals = doc.get("approvals", {})
    if approval_id not in approvals:
        raise ValueError(f"승인 ID '{approval_id}' 가 승인 문서에 없습니다.")

    app = approvals[approval_id]
    if app.get("by") != "user":
        raise ValueError(f"승인자(by)가 'user'가 아닙니다: '{app.get('by')}'")

    if app.get("kind") != "role-transition":
        raise ValueError(f"승인 종류(kind)가 'role-transition'이 아닙니다: '{app.get('kind')}'")

    presets = app.get("presets", [])
    if preset_name not in presets:
        raise ValueError(
            f"프리셋 '{preset_name}' 이 승인 목록 {presets} 에 없습니다."
        )

    handoff_rel = app.get("handoff", "docs/HANDOFF.md")
    handoff_p = Path(handoff_rel)
    if handoff_p.is_absolute() or ".." in handoff_p.parts:
        raise ValueError(f"HANDOFF 경로 탈출 감지: '{handoff_rel}'")

    target_handoff = (root / handoff_rel).resolve()
    try:
        target_handoff.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"HANDOFF 경로가 저장소 루트를 벗어납니다: '{handoff_rel}'")

    # models 배치 검증
    agents_file = root / "config" / "agents.toml"
    existing_agents = {}
    if agents_file.is_file():
        existing_agents = tomllib.loads(agents_file.read_text(encoding="utf-8")).get("agents", {})

    models = app.get("models", {})
    for aname, minfo in models.items():
        if not isinstance(minfo, dict) or "family" not in minfo:
            raise ValueError(f"승인 모델 배치 '{aname}' 에 family 정보가 없습니다.")
        app_family = minfo["family"]
        if aname in existing_agents:
            reg_family = existing_agents[aname].get("family")
            if reg_family and reg_family != app_family:
                raise ValueError(
                    f"에이전트 '{aname}' 의 승인 계열 '{app_family}' 이 "
                    f"config/agents.toml 등록 계열 '{reg_family}' 과 다릅니다."
                )

    return {
        "commit": commit_sha,
        "path": rel_path,
        "id": approval_id,
        "blob_hash": blob_hash,
        "approval": app,
        "handoff_rel": handoff_rel,
    }


def check_handoff_state_line(handoff_path: Path, target_lead: str) -> None:
    """HANDOFF 상태 줄에서 옛 lead 충돌을 검사한다."""
    if not handoff_path.is_file():
        return
    content = handoff_path.read_text(encoding="utf-8", errors="replace")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("상태:"):
            m = re.search(r"lead\s*[=:]\s*([A-Za-z0-9_-]+)", stripped, re.IGNORECASE)
            if m:
                found_lead = m.group(1)
                if found_lead.lower() != target_lead.lower():
                    raise ValueError(
                        f"HANDOFF 상태줄의 lead '{found_lead}' 가 전환 대상 lead '{target_lead}' 와 일치하지 않습니다. 별도 lead 갱신이 필요합니다."
                    )


def validate_preset_separation(root: Path, preset_name: str, preset_data: dict) -> None:
    """프리셋의 기본 배정이 roles.toml의 분리 제약을 지키는지 검증한다."""
    roles_file = root / "config" / "roles.toml"
    if not roles_file.is_file():
        raise ValueError(f"roles.toml 파일이 없습니다: {roles_file}")
    roles_data = tomllib.loads(roles_file.read_text(encoding="utf-8"))
    pairs = roles_data.get("separation", {}).get("pairs", [])

    agents_file = root / "config" / "agents.toml"
    existing_agents = {}
    if agents_file.is_file():
        existing_agents = tomllib.loads(agents_file.read_text(encoding="utf-8")).get("agents", {})

    combined_families: dict[str, str] = {}
    for k, v in existing_agents.items():
        if isinstance(v, dict) and "family" in v:
            combined_families[k] = v["family"]

    preset_agents = preset_data.get("agents", {})
    for k, v in preset_agents.items():
        if isinstance(v, dict) and "family" in v:
            combined_families[k] = v["family"]

    assigned_roles = preset_data.get("assign", {})
    for r1, r2 in pairs:
        if r1 in assigned_roles and r2 in assigned_roles:
            a1, a2 = assigned_roles[r1], assigned_roles[r2]
            f1, f2 = combined_families.get(a1), combined_families.get(a2)
            if f1 and f2 and f1 == f2:
                raise ValueError(
                    f"분리 제약 위반: {r1}({a1}) 와 {r2}({a2}) 가 같은 계열 '{f1}' 입니다"
                )


def check_no_active_executions(root: Path) -> None:
    """reports/executions 에 미종료 또는 회수 대기 상태의 실행이 있는지 검사 (fail closed)."""
    exec_dir = root / "reports" / "executions"
    if not exec_dir.is_dir():
        return

    try:
        entries = list(exec_dir.iterdir())
    except OSError as e:
        raise ValueError(f"실행 디렉터리 접근 실패: {e}")

    for entry in entries:
        if not entry.is_dir():
            continue

        status_file = entry / "status.json"
        contract_file = entry / "contract.json"
        target_file = status_file if status_file.is_file() else (contract_file if contract_file.is_file() else None)

        if target_file is None:
            json_files = list(entry.glob("*.json"))
            if not json_files:
                continue
            raise ValueError(f"실행 기록 형식 불명 ({entry.name}): fail closed 로 전환 거부")

        try:
            data = json.loads(target_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"실행 기록 JSON 손상 감지 ({entry.name}): fail closed 로 전환 거부 ({e})")

        status = data.get("status")
        accepted = data.get("accepted", False)

        if status in ("pending", "running") or (status == "completed" and not accepted) or (status not in ("accepted", "failed", "rejected")):
            raise ValueError(
                f"진행 중이거나 회수 대기 상태의 실행이 존재합니다: run={entry.name}, status={status}, accepted={accepted}. "
                "역할 전환 전에 실행을 완료/채택하거나 정리해야 합니다."
            )


def generate_documents(
    root: Path,
    preset_name: str,
    lead: str,
    handoff_rel: str,
) -> tuple[dict[Path, bytes], dict[str, str]]:
    """AGENTS.md, CLAUDE.md, docs/HANDOFF.md의 새 전체 바이트 및 관리 구간 해시를 생성한다."""
    preset_dir = root / "templates" / "roles" / preset_name
    if not preset_dir.is_dir():
        raise ValueError(f"프리셋 디렉터리가 없습니다: {preset_dir}")

    target_bytes_map: dict[Path, bytes] = {}
    doc_section_hashes: dict[str, str] = {}

    for mf in sorted(preset_dir.glob("*.md")):
        target_file = root / mf.name
        if target_file.is_symlink() or mf.is_symlink():
            raise ValueError(f"심볼릭 링크는 관리 문서로 허용되지 않습니다: {target_file}")
        if target_file.exists() and not target_file.is_file():
            raise ValueError(f"대상 파일이 일반 파일이 아닙니다: {target_file}")

        curr_bytes = target_file.read_bytes() if target_file.is_file() else b""
        template_bytes = mf.read_bytes().strip()

        new_bytes = replace_or_append_managed_section(
            target_bytes=curr_bytes,
            begin_marker=ROLES_BEGIN,
            end_marker=ROLES_END,
            inner_content_bytes=template_bytes,
        )
        target_bytes_map[target_file] = new_bytes
        doc_section_hashes[mf.name] = hashlib.sha256(template_bytes).hexdigest()

    handoff_file = root / handoff_rel
    if handoff_file.is_symlink():
        raise ValueError(f"HANDOFF 파일이 심볼릭 링크입니다: {handoff_file}")
    if handoff_file.exists() and not handoff_file.is_file():
        raise ValueError(f"HANDOFF 대상 파일이 일반 파일이 아닙니다: {handoff_file}")

    curr_handoff_b = handoff_file.read_bytes() if handoff_file.is_file() else b""
    guide_inner_b = generate_handoff_role_guide_bytes(preset_name, lead)

    new_handoff_b = replace_or_append_managed_section(
        target_bytes=curr_handoff_b,
        begin_marker=GUIDE_BEGIN,
        end_marker=GUIDE_END,
        inner_content_bytes=guide_inner_b,
    )
    target_bytes_map[handoff_file] = new_handoff_b
    doc_section_hashes[handoff_rel] = hashlib.sha256(guide_inner_b).hexdigest()

    return target_bytes_map, doc_section_hashes


def internal_check(root: Path, state: dict) -> list[str]:
    """state와 파일 시스템 및 승인 blob 간의 계약 및 일치성 검사."""
    errs: list[str] = []

    if state.get("schema") != 1:
        errs.append(f"role-state schema 가 1이 아닙니다: {state.get('schema')}")

    gen = state.get("generation")
    if not isinstance(gen, int) or gen < 1:
        errs.append(f"role-state generation 이 유효한 양의 정수가 아닙니다: {gen}")

    if state.get("phase") != "ready":
        errs.append(f"role-state phase 가 'ready' 가 아닙니다: '{state.get('phase')}'")
        return errs

    preset_name = state.get("preset", "")
    if not preset_name:
        errs.append("role-state 에 preset 이 지정되지 않았습니다.")
        return errs

    preset_dir = root / "templates" / "roles" / preset_name
    preset_file = preset_dir / "preset.toml"
    if not preset_file.is_file():
        errs.append(f"프리셋 파일이 없습니다: {preset_file}")
        return errs

    try:
        preset_data = tomllib.loads(preset_file.read_text(encoding="utf-8"))
    except Exception as e:
        errs.append(f"프리셋 파싱 실패: {e}")
        return errs

    if state.get("lead") != preset_data.get("lead"):
        errs.append(
            f"state lead '{state.get('lead')}' 가 preset 원본 '{preset_data.get('lead')}' 과 다릅니다"
        )
    if state.get("default_assignments") != preset_data.get("assign"):
        errs.append("state default_assignments 가 preset 원본과 다릅니다")

    # 승인 참조(approval_ref) 및 model_bindings 재대조
    approval_ref = state.get("approval_ref")
    expected_handoff_rel = "docs/HANDOFF.md"
    if not approval_ref:
        errs.append("role-state 에 approval_ref 가 없습니다")
    else:
        try:
            app_info = read_approval(root, approval_ref, preset_name)
            expected_handoff_rel = app_info["handoff_rel"]

            if state.get("approval_blob_hash") != app_info["blob_hash"]:
                errs.append("state approval_blob_hash 가 승인 commit blob 해시와 다릅니다")

            app = app_info["approval"]
            app_models = app.get("models", {})
            if state.get("model_bindings") != app_models:
                errs.append("state model_bindings 가 승인 blob models 과 일치하지 않습니다 (임의 변경 금지)")

            # 등록된 agent 계열과의 일치성 확인
            agents_file = root / "config" / "agents.toml"
            if agents_file.is_file():
                try:
                    reg_agents = tomllib.loads(agents_file.read_text(encoding="utf-8")).get("agents", {})
                    for aname, minfo in app_models.items():
                        if isinstance(minfo, dict) and "family" in minfo and aname in reg_agents:
                            if reg_agents[aname].get("family") != minfo["family"]:
                                errs.append(f"에이전트 '{aname}' 승인 계열 '{minfo['family']}' 과 등록 계열 불일치")
                except Exception as e:
                    errs.append(f"agents.toml 읽기 실패: {e}")
        except Exception as e:
            errs.append(f"approval_ref 검증 실패: {e}")

    curr_cv = compute_config_version(root, preset_name)
    if curr_cv != state.get("config_version"):
        errs.append("설정 파일 또는 템플릿 변경 감지 (config_version 불일치)")

    doc_hashes = state.get("documents", {})

    # state.documents 키가 승인 handoff 위치와 일치하는지 비교 (임의 변경 방지)
    if expected_handoff_rel not in doc_hashes:
        errs.append(f"state documents 에 승인된 handoff 경로 '{expected_handoff_rel}' 가 없습니다")

    for k in doc_hashes.keys():
        if ("HANDOFF" in k or k.endswith(".md")) and k not in [mf.name for mf in preset_dir.glob("*.md")]:
            if k != expected_handoff_rel:
                errs.append(
                    f"state documents 의 handoff 경로 '{k}' 가 승인된 경로 '{expected_handoff_rel}' 와 다릅니다 (임의 변경 금지)"
                )

    for mf in sorted(preset_dir.glob("*.md")):
        target_file = root / mf.name
        if not target_file.is_file():
            errs.append(f"관리 대상 파일이 없습니다: {target_file}")
            continue
        try:
            curr_b = target_file.read_bytes()
            inner = extract_managed_section(curr_b, ROLES_BEGIN, ROLES_END)
            expected_inner = mf.read_bytes().strip()
            if inner != expected_inner:
                errs.append(f"문서 '{mf.name}' 의 관리 구간 내용이 템플릿과 일치하지 않습니다 (drift)")
        except Exception as e:
            errs.append(f"문서 '{mf.name}' 관리 구간 검사 실패: {e}")

    handoff_file = root / expected_handoff_rel
    if not handoff_file.is_file():
        errs.append(f"HANDOFF 파일이 없습니다: {handoff_file}")
    else:
        try:
            curr_b = handoff_file.read_bytes()
            inner = extract_managed_section(curr_b, GUIDE_BEGIN, GUIDE_END)
            expected_guide = generate_handoff_role_guide_bytes(preset_name, state.get("lead", "")).strip()
            if inner != expected_guide:
                errs.append("HANDOFF 문서의 관리 구간 내용이 일치하지 않습니다 (drift)")
            # 상태줄 lead 충돌 검사
            check_handoff_state_line(handoff_file, state.get("lead", ""))
        except Exception as e:
            errs.append(f"HANDOFF 문서 관리 구간 검사 실패: {e}")

    return errs


def check(root: Path) -> int:
    """현재 저장소의 역할 상태 파일 및 문서 정합성을 검증한다."""
    state_path = root / STATE_FILE_REL
    if not state_path.is_file():
        print(f"오류: 역할 상태 파일이 없습니다: {state_path}", file=sys.stderr)
        return 1

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"오류: 역할 상태 파일 파싱 실패: {e}", file=sys.stderr)
        return 1

    errs = internal_check(root, state)
    if errs:
        for err in errs:
            print(f"오류: {err}", file=sys.stderr)
        return 1

    print(
        f"역할 상태 '{state.get('preset')}' (세대 {state.get('generation')}, lead: {state.get('lead')}) 정상 확인."
    )
    return 0


def _get_allowed_recovery_paths(
    root: Path,
    preset_name: str | None = None,
    approval_ref: str | None = None,
    jdata: dict | None = None,
) -> set[str]:
    """전환의 고정 승인과 프리셋이 실제 관리하는 대상(상태/등록agents/프리셋문서/승인HANDOFF) 범위 계산."""
    allowed: set[str] = {
        STATE_FILE_REL.as_posix(),
        "config/agents.toml",
        "reports/role-handovers.json",
    }

    state_path = root / STATE_FILE_REL
    state_data: dict | None = None
    if state_path.is_file():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state_data = None

    ref = approval_ref
    if not ref and jdata:
        ref = jdata.get("approval_ref")
    if not ref and state_data:
        ref = state_data.get("approval_ref")

    if not ref:
        raise RuntimeError("승인 참조가 없어 안전 복구가 불가능합니다 (fail closed).")

    candidate_presets: list[str] = []
    for candidate in (
        preset_name,
        jdata.get("preset") if jdata else None,
        state_data.get("preset") if state_data else None,
    ):
        if candidate and candidate not in candidate_presets:
            candidate_presets.append(candidate)

    app_info = None
    last_err = None
    for p in candidate_presets:
        try:
            app_info = read_approval(root, ref, p)
            break
        except Exception as e:
            last_err = e

    if app_info is None:
        if candidate_presets:
            raise RuntimeError(f"승인 검증 실패로 복구 불가: {last_err}")
        else:
            raise RuntimeError("프리셋 정보가 없어 승인을 검증할 수 없습니다 (fail closed).")

    handoff_rel = app_info.get("handoff_rel")
    if handoff_rel:
        allowed.add(Path(handoff_rel).as_posix())

    relevant_presets = set(candidate_presets)
    relevant_presets.update(app_info.get("approval", {}).get("presets", []))
    for p in relevant_presets:
        preset_dir = root / "templates" / "roles" / p
        if preset_dir.is_dir():
            for mf in preset_dir.glob("*.md"):
                allowed.add(mf.name)

    return allowed


def _is_allowed_transition_path(rel_path: str, allowed_paths: set[str] | None = None) -> bool:
    """루트 안의 허용된 전환 대상 경로만 허용."""
    p = Path(rel_path)
    if p.is_absolute() or ".." in p.parts:
        return False
    posix_path = p.as_posix()
    if allowed_paths is not None:
        return posix_path in allowed_paths
    if posix_path == STATE_FILE_REL.as_posix() or posix_path == "config/agents.toml" or posix_path == "reports/role-handovers.json":
        return True
    # 루트 직하 markdown 파일 (AGENTS.md, CLAUDE.md 등)
    if len(p.parts) == 1 and p.suffix.lower() == ".md":
        return True
    # docs/ 직하 markdown 파일 (docs/HANDOFF.md 등)
    if len(p.parts) >= 2 and p.parts[0] == "docs" and p.suffix.lower() == ".md":
        return True
    return False


def _recover_from_journal(
    root: Path,
    preset_name: str | None = None,
    approval_ref: str | None = None,
) -> None:
    """journal을 검증해 안전 복구를 수행한다 (fail closed).
    is_pending_match를 제거하고, journal에 기록된 정확한 자기작성 해시(target, pending, failed)와
    일치하는 경우에만 복원하며, 외부 수정이 감지되면 거부하고 보존한다.
    """
    lock_dir = _get_git_lock_dir(root)
    journal_file = lock_dir / "role-transition-journal.json"
    state_path = root / STATE_FILE_REL

    if not journal_file.is_file():
        if state_path.is_file():
            try:
                st = json.loads(state_path.read_text(encoding="utf-8"))
                if st.get("phase") in ("pending", "failed"):
                    raise RuntimeError("복구할 저널 파일이 존재하지 않아 안전 복구가 불가능합니다 (fail closed).")
            except Exception as e:
                raise RuntimeError(f"손상된 상태 및 저널 부재로 복구 불가: {e}")
        return

    try:
        jdata = json.loads(journal_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"저널 파일 파싱 실패 (fail closed): {e}")

    files_info = jdata.get("files", {})
    if not isinstance(files_info, dict) or not files_info:
        raise RuntimeError("저널 파일 항목이 올바르지 않습니다 (fail closed)")

    allowed_paths = _get_allowed_recovery_paths(
        root=root,
        preset_name=preset_name,
        approval_ref=approval_ref,
        jdata=jdata,
    )

    restore_plan: list[tuple[Path, bytes | None, str | None, set[str]]] = []
    root_resolved = root.resolve()

    # 1단계: 전체 사전 검증
    for rel_path, item in files_info.items():
        if not _is_allowed_transition_path(rel_path, allowed_paths=allowed_paths):
            raise RuntimeError(f"저널 내 허용되지 않은 전환 경로 감지: {rel_path}")

        p = Path(rel_path)
        target_file = (root / p).resolve()
        try:
            if not target_file.is_relative_to(root_resolved):
                raise RuntimeError(f"저널 내 루트 밖 경로 감지: {rel_path}")
        except Exception:
            raise RuntimeError(f"저널 내 잘못된 경로: {rel_path}")

        orig_hash = item.get("orig_hash")
        orig_hex = item.get("orig_bytes_hex")
        if orig_hex is not None:
            try:
                orig_bytes = bytes.fromhex(orig_hex)
            except ValueError:
                raise RuntimeError(f"저널 내 백업 16진수 디코딩 실패: {rel_path}")
            if hashlib.sha256(orig_bytes).hexdigest() != orig_hash:
                raise RuntimeError(f"백업 바이트 해시 불일치 (corrupt backup): {rel_path}")
        else:
            orig_bytes = None
            if orig_hash is not None:
                raise RuntimeError(f"백업 바이트 누락 (orig_hash 존재하나 orig_bytes 없음): {rel_path}")

        target_hash = item.get("target_hash")
        pending_hash = item.get("pending_hash")
        failed_hash = item.get("failed_hash")

        valid_self_hashes = {h for h in (target_hash, pending_hash, failed_hash) if h is not None}

        curr_bytes = target_file.read_bytes() if target_file.is_file() else None
        curr_hash = hashlib.sha256(curr_bytes).hexdigest() if curr_bytes is not None else None

        if curr_hash == orig_hash:
            continue
        elif curr_hash in valid_self_hashes:
            restore_plan.append((target_file, orig_bytes, orig_hash, valid_self_hashes))
        else:
            raise RuntimeError(
                f"외부 수정 감지 ({rel_path}): 안전 복구 불가능하여 외부 수정을 보존합니다 (fail closed)"
            )

    # 2단계: 각각 복원하기 직전 current를 재대조(CAS)하여 외부 수정 보존
    for target_file, orig_bytes, orig_hash, valid_self_hashes in restore_plan:
        curr_b = target_file.read_bytes() if target_file.is_file() else None
        curr_h = hashlib.sha256(curr_b).hexdigest() if curr_b is not None else None

        if curr_h != orig_hash and curr_h not in valid_self_hashes:
            raise RuntimeError(
                f"복원 직전 동시 외부 수정 감지 ({target_file.name}): 안전 복구를 중단하고 외부 수정을 보존합니다 (fail closed)"
            )

        if orig_bytes is not None:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_f = target_file.with_name(f".{target_file.name}.rec.{os.getpid()}")
            tmp_f.write_bytes(orig_bytes)
            os.replace(tmp_f, target_file)
        else:
            if target_file.exists():
                target_file.unlink()

    try:
        journal_file.unlink(missing_ok=True)
    except Exception:
        pass


def activate(
    root: Path,
    preset_name: str,
    approval_ref: str,
    dry_run: bool = False,
    recover: bool = False,
    handover: bool = False,
) -> int:
    """역할 프리셋을 원자적 트랜잭션으로 활성화한다."""
    lock_cm = contextlib.nullcontext() if dry_run else role_lock(root)
    with lock_cm:
        # 1. 진행/회수 대기 실행 차단 (handover 미지정 시에만 active 작업 차단)
        if not handover:
            try:
                check_no_active_executions(root)
            except Exception as e:
                print(f"오류: {e}", file=sys.stderr)
                return 1

        state_path = root / STATE_FILE_REL
        old_state = None
        prev_generation = 0

        # 손상된 상태 JSON 은 신규 전환으로 간주하지 않고 즉시 거부 (fail closed)
        if state_path.is_file():
            try:
                old_state = json.loads(state_path.read_text(encoding="utf-8"))
                prev_generation = int(old_state.get("generation", 0))
            except Exception as e:
                print(f"오류: 손상된 역할 상태 파일 감지 ({STATE_FILE_REL}): fail closed 로 전환 거부 ({e})", file=sys.stderr)
                return 1

        # 2. 복구 플래그 처리 및 pending/failed 재진입 거부
        if recover:
            try:
                _recover_from_journal(root, preset_name=preset_name, approval_ref=approval_ref)
                # 복구 후 상태 재확인
                if state_path.is_file():
                    try:
                        old_state = json.loads(state_path.read_text(encoding="utf-8"))
                        prev_generation = int(old_state.get("generation", 0))
                    except Exception:
                        old_state = None
                        prev_generation = 0
                else:
                    old_state = None
                    prev_generation = 0
            except Exception as re:
                print(f"오류: 안전 복구 실패 (fail closed): {re}", file=sys.stderr)
                return 1
        else:
            if old_state is not None:
                old_phase = old_state.get("phase")
                if old_phase in ("pending", "failed"):
                    print(
                        f"오류: 이전 역할 전환 상태가 '{old_phase}' 입니다. "
                        "재진입이 거부되었습니다 (--recover 옵션으로 안전 복구 후 재시도하십시오).",
                        file=sys.stderr,
                    )
                    return 1

        preset_dir = root / "templates" / "roles" / preset_name
        preset_file = preset_dir / "preset.toml"
        if not preset_file.is_file():
            print(f"오류: 프리셋 '{preset_name}' 이 templates/roles 에 없습니다", file=sys.stderr)
            return 1

        try:
            preset_data = tomllib.loads(preset_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"오류: 프리셋 파싱 실패: {e}", file=sys.stderr)
            return 1

        lead = preset_data.get("lead", "")
        if not lead:
            print(f"오류: 프리셋 '{preset_name}' 에 lead 정보가 없습니다", file=sys.stderr)
            return 1

        try:
            app_info = read_approval(root, approval_ref, preset_name)
        except Exception as e:
            print(f"오류: 승인 검증 실패: {e}", file=sys.stderr)
            return 1

        try:
            validate_preset_separation(root, preset_name, preset_data)
        except Exception as e:
            print(f"오류: {e}", file=sys.stderr)
            return 1

        handoff_path = root / app_info["handoff_rel"]
        try:
            check_handoff_state_line(handoff_path, lead)
        except Exception as e:
            print(f"오류: {e}", file=sys.stderr)
            return 1

        try:
            target_files, doc_hashes = generate_documents(
                root, preset_name, lead, app_info["handoff_rel"]
            )
        except Exception as e:
            print(f"오류: 문서 생성 계획 실패: {e}", file=sys.stderr)
            return 1

        # agents.toml 에 프리셋의 필수 에이전트 등록 검토 (human만 있는 초기 저장소 지원)
        agents_path = root / "config" / "agents.toml"
        new_agents_b = None
        if agents_path.is_file():
            try:
                cur_agents_doc = tomllib.loads(agents_path.read_text(encoding="utf-8"))
                reg_agents = cur_agents_doc.get("agents", {})
                missing_agents = {}
                for aname, ainfo in preset_data.get("agents", {}).items():
                    if aname not in reg_agents:
                        missing_agents[aname] = ainfo
                if missing_agents:
                    append_txt = ""
                    for aname, ainfo in missing_agents.items():
                        append_txt += f"\n[agents.{aname}]\nfamily = \"{ainfo.get('family', '')}\"\n"
                    new_agents_b = agents_path.read_bytes() + append_txt.encode("utf-8")
                    target_files[agents_path] = new_agents_b
            except Exception as e:
                print(f"오류: config/agents.toml 검사 실패: {e}", file=sys.stderr)
                return 1

        new_generation = prev_generation + 1
        # 실제 반영 예정인 agents bytes 해시를 config_version 에 계산 반영
        config_version = compute_config_version(root, preset_name, agents_override_bytes=new_agents_b)

        new_state = {
            "schema": 1,
            "generation": new_generation,
            "previous_generation": prev_generation if prev_generation > 0 else None,
            "phase": "ready",
            "preset": preset_name,
            "lead": lead,
            "default_assignments": preset_data.get("assign", {}),
            "model_bindings": app_info["approval"].get("models", {}),
            "config_version": config_version,
            "approval_ref": approval_ref,
            "approval_blob_hash": app_info["blob_hash"],
            "documents": doc_hashes,
        }

        if handover:
            handovers_file = root / "reports" / "role-handovers.json"
            existing_handovers: list[dict] = []
            if handovers_file.is_file():
                try:
                    hdoc = json.loads(handovers_file.read_text(encoding="utf-8"))
                    if not isinstance(hdoc, dict) or hdoc.get("schema") != 1 or not isinstance(hdoc.get("handovers"), list):
                        print("오류: 손상된 인계 기록 파일(reports/role-handovers.json): schema!=1 이거나 handovers 가 리스트가 아닙니다 (fail closed).", file=sys.stderr)
                        return 1
                    for item in hdoc["handovers"]:
                        if not isinstance(item, dict) or item.get("schema") != 1:
                            print("오류: 손상된 인계 기록 항목 감지 (schema!=1): fail closed 로 전환 거부", file=sys.stderr)
                            return 1
                    existing_handovers = list(hdoc["handovers"])
                except Exception as e:
                    print(f"오류: 손상된 인계 기록 파일(reports/role-handovers.json) 파싱 실패: fail closed 로 전환 거부 ({e})", file=sys.stderr)
                    return 1

            allowed_transition_files = sorted(list({
                STATE_FILE_REL.as_posix(),
                "config/agents.toml",
                "reports/role-handovers.json",
                app_info["handoff_rel"],
                *[mf.name for mf in preset_dir.glob("*.md")],
            }))

            # 전환 대상 파일들의 before/after sha256 해시 계산 (records 자체는 순환 방지를 위해 제외)
            file_hashes: dict[str, dict[str, str | None]] = {}

            state_before_b = state_path.read_bytes() if state_path.is_file() else None
            state_before_hash = hashlib.sha256(state_before_b).hexdigest() if state_before_b is not None else None
            state_target_bytes = json.dumps(new_state, indent=2, ensure_ascii=False).encode("utf-8")
            state_target_hash = hashlib.sha256(state_target_bytes).hexdigest()
            file_hashes[STATE_FILE_REL.as_posix()] = {
                "before": state_before_hash,
                "after": state_target_hash,
            }

            if agents_path in target_files:
                ag_before_b = agents_path.read_bytes() if agents_path.is_file() else None
                file_hashes["config/agents.toml"] = {
                    "before": hashlib.sha256(ag_before_b).hexdigest() if ag_before_b is not None else None,
                    "after": hashlib.sha256(target_files[agents_path]).hexdigest(),
                }
            elif agents_path.is_file():
                ag_curr_hash = hashlib.sha256(agents_path.read_bytes()).hexdigest()
                file_hashes["config/agents.toml"] = {
                    "before": ag_curr_hash,
                    "after": ag_curr_hash,
                }

            for p, content_b in target_files.items():
                rel_p = p.relative_to(root).as_posix()
                if rel_p in ("reports/role-handovers.json", STATE_FILE_REL.as_posix(), "config/agents.toml"):
                    continue
                orig_b = p.read_bytes() if p.is_file() else None
                file_hashes[rel_p] = {
                    "before": hashlib.sha256(orig_b).hexdigest() if orig_b is not None else None,
                    "after": hashlib.sha256(content_b).hexdigest(),
                }

            new_handover_record = {
                "schema": 1,
                "generation": new_generation,
                "previous_generation": prev_generation if prev_generation > 0 else None,
                "preset": preset_name,
                "previous_preset": old_state.get("preset") if old_state else None,
                "lead": lead,
                "previous_lead": old_state.get("lead") if old_state else None,
                "approval_ref": approval_ref,
                "approval_blob_hash": app_info["blob_hash"],
                "config_version": config_version,
                "previous_config_version": old_state.get("config_version") if old_state else None,
                "documents": doc_hashes,
                "previous_documents": old_state.get("documents") if old_state else None,
                "model_bindings": app_info["approval"].get("models", {}),
                "previous_model_bindings": old_state.get("model_bindings") if old_state else None,
                "default_assignments": preset_data.get("assign", {}),
                "previous_default_assignments": old_state.get("default_assignments") if old_state else None,
                "allowed_transition_files": allowed_transition_files,
                "file_hashes": file_hashes,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

            handovers_doc = {
                "schema": 1,
                "handovers": [*existing_handovers, new_handover_record],
            }
            handovers_b = json.dumps(handovers_doc, indent=2, ensure_ascii=False).encode("utf-8")
            target_files[handovers_file] = handovers_b

        # 멱등성 검사 (동일 전환은 무변경 성공이나 internal_check 성공 전제)
        if (
            old_state is not None
            and old_state.get("phase") == "ready"
            and old_state.get("preset") == preset_name
            and old_state.get("approval_ref") == approval_ref
        ):
            prior_errs = internal_check(root, old_state)
            if not prior_errs:
                all_match = True
                for path, expected_bytes in target_files.items():
                    if not path.is_file() or path.read_bytes() != expected_bytes:
                        all_match = False
                        break
                if all_match:
                    print(
                        f"역할 상태 '{preset_name}' (세대 {prev_generation}) 가 이미 적용되어 있습니다 (무변경 성공)."
                    )
                    return 0

        # dry-run
        if dry_run:
            print(f"[dry-run] 역할 전환 적용 예정 (preset: {preset_name}, lead: {lead}):")
            print(f"  - 승인 참조: {approval_ref}")
            print(f"  - 세대: {new_generation}")
            for p in target_files.keys():
                rel = p.relative_to(root).as_posix()
                print(f"  - 반영 파일: {rel}")
            print(f"  - 상태 파일: {STATE_FILE_REL.as_posix()}")
            return 0

        # 쓰기 전 원본 바이트 및 CAS 해시 기록
        backups: dict[Path, bytes | None] = {}
        orig_hashes: dict[Path, str | None] = {}
        for path in target_files.keys():
            if path.is_file():
                b = path.read_bytes()
                backups[path] = b
                orig_hashes[path] = hashlib.sha256(b).hexdigest()
            else:
                backups[path] = None
                orig_hashes[path] = None

        if state_path.is_file():
            b = state_path.read_bytes()
            backups[state_path] = b
            orig_hashes[state_path] = hashlib.sha256(b).hexdigest()
        else:
            backups[state_path] = None
            orig_hashes[state_path] = None

        # 지속 저널 파일 기록 (.git 내부 위치, 실패 시 무시하지 않고 즉시 중단)
        state_pending = dict(new_state)
        state_pending["phase"] = "pending"
        pending_bytes = json.dumps(state_pending, indent=2, ensure_ascii=False).encode("utf-8")
        pending_hash = hashlib.sha256(pending_bytes).hexdigest()

        lock_dir = _get_git_lock_dir(root)
        journal_file = lock_dir / "role-transition-journal.json"
        tx_id = f"tx_{int(time.time()*1000)}_{os.getpid()}"
        journal_data = {
            "transaction_id": tx_id,
            "preset": preset_name,
            "approval_ref": approval_ref,
            "generation": new_generation,
            "phase": "pending",
            "files": {},
        }
        for p, content_b in target_files.items():
            rel_p = p.relative_to(root).as_posix()
            orig_b = backups[p]
            journal_data["files"][rel_p] = {
                "orig_hash": orig_hashes[p],
                "orig_bytes_hex": orig_b.hex() if orig_b is not None else None,
                "target_hash": hashlib.sha256(content_b).hexdigest(),
                "target_bytes_hex": content_b.hex(),
            }

        orig_state_b = backups[state_path]
        target_state_b = json.dumps(new_state, indent=2, ensure_ascii=False).encode("utf-8")
        journal_data["files"][STATE_FILE_REL.as_posix()] = {
            "orig_hash": orig_hashes[state_path],
            "orig_bytes_hex": orig_state_b.hex() if orig_state_b is not None else None,
            "target_hash": hashlib.sha256(target_state_b).hexdigest(),
            "target_bytes_hex": target_state_b.hex(),
            "pending_hash": pending_hash,
            "pending_bytes_hex": pending_bytes.hex(),
        }

        try:
            journal_file.write_text(json.dumps(journal_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as je:
            print(f"오류: 트랜잭션 저널 작성 실패: {je}", file=sys.stderr)
            return 1

        applied_files: list[Path] = []
        tmp_files: list[Path] = []

        try:
            # 1단계: state pending 작성 (CAS 검증)
            curr_state_b = state_path.read_bytes() if state_path.is_file() else None
            curr_state_h = hashlib.sha256(curr_state_b).hexdigest() if curr_state_b is not None else None
            if curr_state_h != orig_hashes.get(state_path):
                raise RuntimeError("상태 파일 동시 수정 감지 (CAS 불일치)")

            state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_state = state_path.with_name(f".{state_path.name}.tmp.{os.getpid()}")
            tmp_files.append(tmp_state)
            tmp_state.write_bytes(pending_bytes)
            os.replace(tmp_state, state_path)

            # 2단계: 문서 및 설정 파일 CAS 검사 및 반영
            for target_path, content_bytes in target_files.items():
                curr_disk_b = target_path.read_bytes() if target_path.is_file() else None
                curr_disk_hash = hashlib.sha256(curr_disk_b).hexdigest() if curr_disk_b is not None else None
                if curr_disk_hash != orig_hashes.get(target_path):
                    raise RuntimeError(f"동시 수정 감지 (CAS 불일치): {target_path.name}")

                target_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_doc = target_path.with_name(f".{target_path.name}.tmp.{os.getpid()}")
                tmp_files.append(tmp_doc)
                tmp_doc.write_bytes(content_bytes)
                os.replace(tmp_doc, target_path)
                applied_files.append(target_path)

            # 3단계: 전환 후검증 (internal_check)
            post_errs = internal_check(root, new_state)
            if post_errs:
                raise RuntimeError(f"전환 후검증 실패: {'; '.join(post_errs)}")

            # 4단계: ready 확정
            tmp_ready = state_path.with_name(f".{state_path.name}.tmp.{os.getpid()}")
            tmp_files.append(tmp_ready)
            ready_bytes = json.dumps(new_state, indent=2, ensure_ascii=False).encode("utf-8")
            tmp_ready.write_bytes(ready_bytes)
            os.replace(tmp_ready, state_path)

            # 성공 시에만 저널 정리
            try:
                journal_file.unlink(missing_ok=True)
            except Exception:
                pass

        except Exception as err:
            # 안전 롤백: current bytes 가 자기 작성 내용과 일치할 때만 복구 (외부 수정 보존)
            for target_path in reversed(applied_files):
                orig_bytes = backups.get(target_path)
                curr_b = target_path.read_bytes() if target_path.is_file() else None
                expected_written_b = target_files.get(target_path)
                if curr_b == expected_written_b:
                    if orig_bytes is not None:
                        try:
                            tmp_rb = target_path.with_name(f".{target_path.name}.rb.{os.getpid()}")
                            tmp_rb.write_bytes(orig_bytes)
                            os.replace(tmp_rb, target_path)
                        except Exception:
                            pass
                    else:
                        try:
                            target_path.unlink(missing_ok=True)
                        except Exception:
                            pass

            try:
                curr_state_b = state_path.read_bytes() if state_path.is_file() else None
                # 자기 pending일 때만 failed state 기록 적용하여 외부 수정을 덮지 않음
                if curr_state_b == pending_bytes:
                    state_failed = dict(new_state)
                    state_failed["phase"] = "failed"
                    state_failed["error"] = str(err)
                    failed_bytes = json.dumps(state_failed, indent=2, ensure_ascii=False).encode("utf-8")
                    failed_hash = hashlib.sha256(failed_bytes).hexdigest()

                    # 저널에도 failed_hash/failed_bytes_hex 기록
                    if journal_file.is_file():
                        try:
                            jdata = json.loads(journal_file.read_text(encoding="utf-8"))
                            jdata["phase"] = "failed"
                            st_item = jdata.setdefault("files", {}).setdefault(STATE_FILE_REL.as_posix(), {})
                            st_item["failed_hash"] = failed_hash
                            st_item["failed_bytes_hex"] = failed_bytes.hex()
                            journal_file.write_text(json.dumps(jdata, indent=2, ensure_ascii=False), encoding="utf-8")
                        except Exception:
                            pass

                    tmp_failed = state_path.with_name(f".{state_path.name}.failed.{os.getpid()}")
                    tmp_files.append(tmp_failed)
                    tmp_failed.write_bytes(failed_bytes)
                    os.replace(tmp_failed, state_path)
            except Exception:
                pass

            print(f"오류: 역할 전환 적용 중 실패하여 롤백했습니다: {err}", file=sys.stderr)
            return 1
        finally:
            for tf in tmp_files:
                try:
                    if tf.exists():
                        tf.unlink()
                except Exception:
                    pass

        print(f"역할 상태 '{preset_name}' (세대 {new_generation}, lead: {lead}) 활성화 완료.")
        return 0


def get_active_state(root: Path) -> dict | None:
    """활성(ready) 역할 상태를 반환하거나 없으면 None을 반환한다."""
    state_path = root / STATE_FILE_REL
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if data.get("phase") == "ready":
            return data
    except Exception:
        return None
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description="역할 상태 관리자 (WO-H63)")
    sub = ap.add_subparsers(dest="command")

    p_check = sub.add_parser("check", help="역할 상태 검증")
    p_check.add_argument("--root", type=Path, default=Path.cwd(), help="저장소 루트")

    p_act = sub.add_parser("activate", help="역할 프리셋 활성화")
    p_act.add_argument("preset", help="프리셋 이름")
    p_act.add_argument("--approval-ref", required=True, help="승인 참조")
    p_act.add_argument("--dry-run", action="store_true", help="드라이 런")
    p_act.add_argument("--recover", action="store_true", help="안전 복구")
    p_act.add_argument("--handover", action="store_true", help="진행 중 lead 교체 (WO-H64)")
    p_act.add_argument("--root", type=Path, default=Path.cwd(), help="저장소 루트")

    p_rec = sub.add_parser("recover", help="안전 복구 수행")
    p_rec.add_argument("--root", type=Path, default=Path.cwd(), help="저장소 루트")

    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help()
        return 1

    root = args.root.resolve()
    if args.command == "check":
        return check(root)
    elif args.command == "activate":
        return activate(root, args.preset, args.approval_ref, dry_run=args.dry_run, recover=args.recover, handover=args.handover)
    elif args.command == "recover":
        try:
            _recover_from_journal(root)
            print("안전 복구 완료.")
            return 0
        except Exception as e:
            print(f"오류: 복구 실패: {e}", file=sys.stderr)
            return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
