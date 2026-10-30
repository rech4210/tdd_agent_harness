"""하네스 자기 검사: 걸러낸 첫 커밋 작업 공간으로 수동 실행 계약 시작 (WO-H89, ENV-01/02).

계약은 dev/WO-H89.md. 격리 위임(docs/isolated-delegation.md §2.1)은 계획 파일만 raw blob 으로 꺼내
새 저장소 첫 커밋을 만든다 — 그 HEAD 는 원본 base 와 같을 수 없어 manual-start 가 거부했다(ENV-01).
작업 명세 `workspace_profile` 이 있으면 원본 base 에서 계획을 다시 만들고, 워커 저장소가 그 계획과
정확히 같은 단일 독립 커밋일 때만 시작한다. 없으면 기존(HEAD == base) 그대로.
거부 사례는 계약의 거부 원인 표지(`[격리:…]` · `[계획]` · `[미제공 덮어쓰기]`)까지 단언한다 — 다른 검사로 헛통과 방지.
사용: python harness/selftest/check_h89_filtered_start_cases.py
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import check_execution_contract_cases as execution_cases
from check_role_transition_cases import PY, ROOT, command, git

PROFILE = (
    'schema=1\n\n[profiles.W1]\n'
    'include=["AGENTS.md","reports/role-approvals.json","src/**"]\n'
    'exclude=["src/secret.txt"]\n'
    'required=["AGENTS.md"]\n\n'
    '[profiles.W2]\n'
    'include=["AGENTS.md","config/workspace.toml","src/example.txt"]\n'
    'exclude=[]\n'
    'required=["AGENTS.md"]\n\n'
    '[profiles.WREQ]\n'
    'include=["AGENTS.md"]\n'
    'exclude=[]\n'
    'required=["AGENTS.md","docs/missing.md"]\n\n'
    '[profiles.WEMPTY]\n'
    'include=["nothing/**"]\n'
    'exclude=[]\n'
    'required=[]\n'
)
PLAN_PATHS = ["AGENTS.md", "reports/role-approvals.json", "src/example.txt", "src/run.sh"]
W2_PATHS = ["AGENTS.md", "config/workspace.toml", "src/example.txt"]

T_GITDIR = "[격리:git-dir]"
T_ALT = "[격리:alternates]"
T_META = "[격리:메타]"
T_PARENT = "[격리:parent]"
T_TREE = "[격리:tree]"
T_OBJ = "[격리:objects]"
T_PLAN = "[계획]"
T_UNPROVIDED = "[미제공 덮어쓰기]"


def git_bytes(root, *args, stdin: bytes | None = None) -> bytes:
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, input=stdin)
    if p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8", "replace"))
    return p.stdout


def commit_in(ws, msg="base: filtered"):
    git(ws, "-c", "user.name=Lead", "-c", "user.email=lead@example.invalid", "commit", "-qm", msg)


class FilteredStartCases(unittest.TestCase):
    setUp = execution_cases.ExecutionCases.setUp
    commit = execution_cases.ExecutionCases.commit

    # ---- 준비 ----

    def prepare(self, profile_text=PROFILE, config="config/workspace.toml", profile="W1"):
        p = self.root / "reports/role-approvals.json"
        data = json.loads(p.read_text())
        data["approvals"]["M1"] = dict(data["approvals"]["E1"], runner="manual")
        data["approvals"]["M1"]["paths"] = list(data["approvals"]["M1"]["paths"]) + ["reports/notes.md", "reports/NOTES.md"]
        p.write_text(json.dumps(data), encoding="utf-8")
        (self.root / "src/secret.txt").write_text("hidden from worker\n", encoding="utf-8")
        (self.root / "src/run.sh").write_text("#!/bin/sh\necho run\n", encoding="utf-8")
        (self.root / "reports/notes.md").write_text("origin notes\n", encoding="utf-8")
        if profile_text is not None:
            cfg = self.root / config
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(profile_text, encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "update-index", "--chmod=+x", "src/run.sh")
        self.commit()
        self.base = git(self.root, "rev-parse", "HEAD")
        self.assertIn("100755 blob", git(self.root, "ls-tree", self.base, "src/run.sh"))
        self.task.update(base=self.base, approval_ref=self.base + ":reports/role-approvals.json#M1",
                         workspace_profile=profile)
        if config != "config/workspace.toml":
            self.task["workspace_config"] = config
        self.workspace = Path(self.tmp.name) / "filtered-worker"

    def base_mode(self, rel):
        return git(self.root, "ls-tree", self.base, "--", rel).split()[0]

    def build(self, paths=PLAN_PATHS, *, alter=None, extra=None, modes=None, init=True):
        """원본 base 의 blob 으로 독립 첫 커밋을 만든다(격리 위임 §2.1 과 같은 방식). 모드도 원본 그대로."""
        ws = self.workspace
        if init:
            ws.mkdir(parents=True)
            git(ws, "init", "-q")
            git(ws, "config", "core.autocrlf", "false")  # 연결된 작업 트리(R11)에서는 원본 설정을 건드리지 않게 init 때만
        for rel in paths:
            data = git_bytes(self.root, "cat-file", "blob", f"{self.base}:{rel}")
            if alter and rel in alter:
                data = alter[rel]
            target = ws / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for rel, data in (extra or {}).items():
            target = ws / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        git(ws, "-c", "core.autocrlf=false", "add", "-A")
        wanted = {rel: self.base_mode(rel) for rel in paths}
        wanted.update(modes or {})
        for rel, mode in wanted.items():
            git(ws, "update-index", "--chmod=+x" if mode == "100755" else "--chmod=-x", rel)
        commit_in(ws)
        return ws

    def start(self, note="Lead built filtered first commit from plan; live Orca unavailable"):
        return self.api.start_manual(self.root, self.task, self.workspace, note)

    def runs(self):
        d = self.root / "reports/executions"
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def contract(self, run):
        return json.loads((self.root / "reports/executions" / run / "contract.json").read_text(encoding="utf-8"))

    def rejected_with(self, token, action):
        try:
            result = action()
        except (ValueError, OSError, RuntimeError) as e:
            self.assertIn(token, str(e), f"거부 원인이 다름: {e}")
            return
        self.fail("invalid operation returned success: " + repr(result))

    def rejected_start(self, token):
        before = self.runs()
        self.rejected_with(token, self.start)
        self.assertEqual(self.runs(), before, "거부된 시작이 실행 기록을 남김")

    def check(self, run):
        return self.api.check_manual(self.root, run, "Lead reviewed filtered worker result")

    # ---- 통과 경로 ----

    def test_F01_filtered_start_check_accept(self):
        """ENV-01/02: 걸러낸 첫 커밋으로 시작 · 점검 · 채택까지, 결과가 원본에 반영."""
        self.prepare()
        self.build()
        run = self.start()
        (self.workspace / "reports/result.md").write_text("filtered candidate\n", encoding="utf-8")
        self.assertEqual(self.check(run)["static_validation"], "passed")
        self.api.accept_manual(self.root, run, "Lead reviewed diff and unverified runtime limitation")
        self.assertEqual((self.root / "reports/result.md").read_text(), "filtered candidate\n")

    def test_F02_contract_records_filtered_provenance(self):
        self.prepare()
        ws = self.build()
        c = self.contract(self.start())
        self.assertEqual(c["base"], self.base)
        self.assertEqual(c["base_mode"], "filtered")
        self.assertEqual(c["workspace_profile"], "W1")
        self.assertEqual(c["workspace_config"], "config/workspace.toml")
        self.assertEqual(c["policy_blob"], git(self.root, "rev-parse", f"{self.base}:config/workspace.toml"))
        self.assertEqual(c["worker_head"], git(ws, "rev-parse", "HEAD"))
        self.assertEqual(c["worker_tree"], git(ws, "rev-parse", "HEAD^{tree}"))
        self.assertEqual(c["plan_paths"], PLAN_PATHS)

    def test_F03_custom_config_path(self):
        """소비 프로젝트처럼 계획 설정이 work_orders/ 아래에 있어도 된다."""
        self.prepare(config="work_orders/worker-workspace.toml")
        self.build()
        c = self.contract(self.start())
        self.assertEqual(c["workspace_config"], "work_orders/worker-workspace.toml")

    def test_F04_exact_mode_unchanged(self):
        """프로필이 없으면 기존 계약 그대로: base 와 같은 HEAD 의 복제본은 시작, 기록은 exact."""
        self.prepare()
        del self.task["workspace_profile"]
        git(self.root, "clone", "--quiet", "--no-hardlinks", str(self.root), str(self.workspace))
        c = self.contract(self.start())
        self.assertEqual(c["base_mode"], "exact")

    def test_F05_crlf_bytes_preserved(self):
        """CRLF 를 담은 원문 blob 이 변환 없이 그대로면 통과(변환 없는 blob 일치)."""
        self.prepare()
        (self.root / "src/crlf.txt").write_bytes(b"line one\r\nline two\r\n")
        git(self.root, "-c", "core.autocrlf=false", "add", "src/crlf.txt")
        git(self.root, "-c", "core.autocrlf=false", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "crlf blob")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.task.update(base=self.base, approval_ref=self.base + ":reports/role-approvals.json#M1")
        self.build(paths=PLAN_PATHS + ["src/crlf.txt"])
        self.start()
        self.assertEqual((self.workspace / "src/crlf.txt").read_bytes(), b"line one\r\nline two\r\n")
        self.assertEqual(git_bytes(self.root, "cat-file", "blob", f"{self.base}:src/crlf.txt"), b"line one\r\nline two\r\n")

    def test_F06_executable_mode_from_plan_allowed(self):
        """계획에 100755 파일(src/run.sh)이 있으면 같은 모드로 시작 성공(실행 비트 일괄 거부 아님)."""
        self.prepare()
        ws = self.build()
        self.assertIn("100755", git(ws, "ls-tree", "HEAD", "src/run.sh"))
        self.assertTrue(self.start())

    def test_F07_config_provided_unchanged_allowed(self):
        """설정 파일 자체를 제공하는 프로필(W2): 원본 그대로면 시작 성공."""
        self.prepare(profile="W2")
        self.build(paths=W2_PATHS)
        self.assertEqual(self.contract(self.start())["plan_paths"], W2_PATHS)

    def test_F08_plan_shared_with_workspace_cli(self):
        """같은 규칙 · 같은 코드: workspace.py plan 출력의 files 가 시작 기록과 같고,
        execution.py 는 workspace.build_plan 을, plan 서브명령도 build_plan 을 쓴다."""
        self.prepare()
        p = command(self.root, PY, "-B", "harness/workspace.py", "plan", "--base", self.base, "--profile", "W1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        files = json.loads(p.stdout)["files"]
        self.assertEqual([f["path"] for f in files], PLAN_PATHS)
        self.build()
        self.assertEqual(self.contract(self.start())["plan_paths"], [f["path"] for f in files])
        ws_src = (ROOT / "harness/workspace.py").read_text(encoding="utf-8")
        ex_src = (ROOT / "harness/execution.py").read_text(encoding="utf-8")
        ws_tree = ast.parse(ws_src)
        defs = {n.name: n for n in ast.walk(ws_tree) if isinstance(n, ast.FunctionDef)}
        self.assertIn("build_plan", defs)
        calls_in_plan = {c.func.id for c in ast.walk(defs["plan_subcommand"])
                         if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        self.assertIn("build_plan", calls_in_plan, "plan 서브명령이 build_plan 을 쓰지 않음")
        self.assertIn("build_plan", ex_src, "execution.py 가 build_plan 을 쓰지 않음")

    # ---- 시작 거부: 계획 ----

    def test_R01_filtered_worker_without_profile_rejected(self):
        self.prepare()
        del self.task["workspace_profile"]
        self.build()
        self.rejected_start("워커 HEAD 불일치")

    def test_R02_full_clone_with_profile_rejected(self):
        """원본 이력 · 숨긴 파일을 가진 복제본은 프로필로 시작할 수 없다(여러 조건 동시 위반 — 통합 사례)."""
        self.prepare()
        git(self.root, "clone", "--quiet", "--no-hardlinks", str(self.root), str(self.workspace))
        self.rejected_start("[격리:")

    def test_R03_origin_head_moved_rejected(self):
        """프로필 경로도 기존 공통 검사(원본 HEAD == base)를 지난다."""
        self.prepare()
        self.build()
        (self.root / "reports/later.md").write_text("later\n", encoding="utf-8")
        self.commit()
        self.rejected_start("기준 커밋 불일치")

    def test_R04_unknown_profile_rejected(self):
        self.prepare()
        self.task["workspace_profile"] = "W9"
        self.build()
        self.rejected_start(T_PLAN)

    def test_R05_config_missing_at_base_rejected(self):
        self.prepare(profile_text=None)
        self.build()
        self.rejected_start(T_PLAN)

    def test_R06_config_path_invalid_rejected(self):
        self.prepare()
        self.build()
        for bad in ("../outside.toml", "/abs/workspace.toml", "C:/x/workspace.toml", "config\\workspace.toml",
                    ".GIT/config", "config//workspace.toml", "config/*.toml", ""):
            with self.subTest(bad=bad):
                self.task["workspace_config"] = bad
                self.rejected_start(T_PLAN)

    def test_R07_profile_value_invalid_rejected(self):
        self.prepare()
        self.build()
        for bad in ("", "  ", 7, ["W1"]):
            with self.subTest(bad=bad):
                self.task["workspace_profile"] = bad
                self.rejected_start(T_PLAN)

    def test_R08_required_missing_rejected(self):
        self.prepare(profile="WREQ")
        self.build(paths=["AGENTS.md"])
        self.rejected_start(T_PLAN)

    def test_R09_empty_selection_rejected(self):
        self.prepare(profile="WEMPTY")
        self.build(paths=["AGENTS.md"])
        self.rejected_start(T_PLAN)

    def test_R10_dirty_origin_rejected(self):
        """원본 작업 트리 clean 조건은 그대로 — 설정을 고친 미커밋 원본은 거부."""
        self.prepare()
        self.build()
        (self.root / "config/workspace.toml").write_text(PROFILE.replace("src/**", "src/none/**"), encoding="utf-8")
        try:
            self.rejected_start("dirty origin")
        finally:
            git(self.root, "checkout", "--quiet", "--", "config/workspace.toml")

    # ---- 시작 거부: 저장소 구조 ----

    def test_R11_linked_worktree_rejected(self):
        """원본에 연결된 작업 트리(공유 .git)는 트리가 같아도 거부."""
        self.prepare()
        git(self.root, "worktree", "add", "--quiet", "--detach", str(self.workspace))
        ws = self.workspace
        git(ws, "checkout", "--quiet", "--orphan", "filtered")
        git(ws, "rm", "-rq", "--cached", ".")
        for p in sorted(ws.iterdir()):
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        self.build(init=False)
        self.assertEqual(git(ws, "rev-list", "--count", "HEAD"), "1")
        self.rejected_start(T_GITDIR)

    def test_R12_alternates_rejected(self):
        """원본 objects 를 빌려 쓰는(alternates) 저장소는 거부."""
        self.prepare()
        ws = self.build()
        alt = ws / ".git/objects/info/alternates"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text(str((self.root / ".git/objects").resolve()) + "\n", encoding="utf-8")
        self.rejected_start(T_ALT)

    def test_R13_alternates_to_empty_store_rejected(self):
        """alternates 가 추가 객체 없는 빈 저장소를 가리켜도 존재 자체로 거부(객체 검사와 독립)."""
        self.prepare()
        ws = self.build()
        empty = Path(self.tmp.name) / "empty-objects"
        (empty / "info").mkdir(parents=True)
        (empty / "pack").mkdir()
        alt = ws / ".git/objects/info/alternates"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text(str(empty.resolve()) + "\n", encoding="utf-8")
        self.rejected_start(T_ALT)

    def test_R14_grafts_shallow_replace_modules_rejected(self):
        """부모를 숨기거나 바꾸는 메타(grafts · shallow · refs/replace) · 하위 모듈 저장소(.git/modules)는 거부."""
        self.prepare()
        for kind in ("grafts", "shallow", "replace", "modules"):
            with self.subTest(kind=kind):
                self.workspace = Path(self.tmp.name) / f"filtered-worker-{kind}"
                ws = self.build()
                head = git(ws, "rev-parse", "HEAD")
                if kind == "grafts":
                    (ws / ".git/info").mkdir(exist_ok=True)
                    (ws / ".git/info/grafts").write_text(head + "\n", encoding="utf-8")
                elif kind == "shallow":
                    (ws / ".git/shallow").write_text(head + "\n", encoding="utf-8")
                elif kind == "replace":
                    (ws / ".git/refs/replace").mkdir(parents=True, exist_ok=True)
                    (ws / ".git/refs/replace" / head).write_text(head + "\n", encoding="utf-8")
                else:
                    (ws / ".git/modules/sub").mkdir(parents=True)
                self.rejected_start(T_META)

    def test_R15_second_commit_rejected(self):
        """원시 commit 에 parent 헤더가 있으면 거부(이력 전달 가능)."""
        self.prepare()
        ws = self.build(paths=["AGENTS.md"])
        self.build(init=False)
        self.assertIn(b"\nparent ", git_bytes(ws, "cat-file", "commit", "HEAD"))
        self.rejected_start(T_PARENT)

    # ---- 시작 거부: 트리 ----

    def test_R16_extra_file_rejected(self):
        self.prepare()
        self.build(extra={"docs/HANDOFF.md": git_bytes(self.root, "cat-file", "blob", f"{self.base}:docs/HANDOFF.md")})
        self.rejected_start(T_TREE)

    def test_R17_excluded_file_rejected(self):
        """exclude 가 적용된다: 계획에서 뺀 src/secret.txt 를 넣은 트리는 거부."""
        self.prepare()
        self.build(paths=PLAN_PATHS + ["src/secret.txt"])
        self.rejected_start(T_TREE)

    def test_R18_missing_planned_file_rejected(self):
        self.prepare()
        self.build(paths=["AGENTS.md", "reports/role-approvals.json", "src/example.txt"])
        self.rejected_start(T_TREE)

    def test_R19_changed_content_rejected(self):
        self.prepare()
        self.build(alter={"src/example.txt": b"original\r\n"})
        self.rejected_start(T_TREE)

    def test_R20_mode_mismatch_rejected(self):
        self.prepare()
        for rel, mode in (("src/example.txt", "100755"), ("src/run.sh", "100644")):
            with self.subTest(rel=rel):
                self.workspace = Path(self.tmp.name) / f"filtered-worker-{mode}"
                self.build(modes={rel: mode})
                self.rejected_start(T_TREE)

    def test_R21_worker_config_does_not_define_plan(self):
        """계획은 원본 base 설정으로만 — 워커가 바꾼 설정과 그에 맞춘 트리는 거부."""
        self.prepare(profile="W2")
        changed_cfg = PROFILE.replace('"config/workspace.toml","src/example.txt"]',
                                      '"config/workspace.toml","src/example.txt","src/secret.txt"]')
        self.assertNotEqual(changed_cfg, PROFILE)
        self.build(paths=W2_PATHS + ["src/secret.txt"], alter={"config/workspace.toml": changed_cfg.encode("utf-8")})
        self.rejected_start(T_TREE)

    def test_R22_dirty_filtered_worker_rejected(self):
        self.prepare()
        ws = self.build()
        (ws / "reports/result.md").write_text("pre-edited\n", encoding="utf-8")
        self.rejected_start("깨끗하지 않습니다")

    # ---- 시작 거부: 저장소 객체 ----

    def test_R23_hidden_loose_object_rejected(self):
        """HEAD 트리는 계획과 같아도 저장소에 숨긴 blob(끊긴 객체)이 있으면 거부."""
        self.prepare()
        ws = self.build()
        hidden = Path(self.tmp.name) / "hidden.bin"
        hidden.write_bytes(git_bytes(self.root, "cat-file", "blob", f"{self.base}:docs/HANDOFF.md"))
        git(ws, "hash-object", "-w", str(hidden))
        self.rejected_start(T_OBJ)

    def test_R24_extra_ref_rejected(self):
        """다른 ref 로 원본 내용을 담은 커밋을 남긴 저장소도 거부."""
        self.prepare()
        ws = self.build()
        git(ws, "checkout", "--quiet", "-b", "side")
        (ws / "docs").mkdir()
        (ws / "docs/HANDOFF.md").write_bytes(git_bytes(self.root, "cat-file", "blob", f"{self.base}:docs/HANDOFF.md"))
        git(ws, "add", "-A")
        commit_in(ws, "side")
        git(ws, "checkout", "--quiet", "-")
        git(ws, "clean", "-fdq")
        self.rejected_start(T_OBJ)

    def test_R25_hidden_object_in_pack_only_rejected(self):
        """ref 도 loose 사본도 없이 별도 pack 에만 있는 숨긴 blob 도 거부."""
        self.prepare()
        ws = self.build()
        secret = git_bytes(self.root, "cat-file", "blob", f"{self.base}:src/secret.txt")
        oid = git_bytes(ws, "hash-object", "-w", "--stdin", stdin=secret).decode().strip()
        git_bytes(ws, "pack-objects", "-q", ".git/objects/pack/pack", stdin=(oid + "\n").encode())
        loose = ws / ".git/objects" / oid[:2] / oid[2:]
        loose.chmod(0o644)
        loose.unlink()
        self.assertFalse(loose.exists())
        self.assertEqual(git(ws, "cat-file", "-t", oid), "blob")
        self.rejected_start(T_OBJ)

    def test_R26_annotated_tag_object_rejected(self):
        """허용 집합(HEAD commit · 그 tree · 계획 blob) 밖의 tag 객체는 거부."""
        self.prepare()
        ws = self.build()
        git(ws, "-c", "user.name=Lead", "-c", "user.email=lead@example.invalid", "tag", "-a", "v0", "-m", "tag")
        self.rejected_start(T_OBJ)

    def test_R27_plain_extra_ref_to_allowed_object_allowed(self):
        """대조: 같은 허용 객체를 가리키는 추가 ref(가벼운 태그 · 브랜치)는 3f 위반 아님."""
        self.prepare()
        ws = self.build()
        git(ws, "tag", "light")
        git(ws, "branch", "copy")
        self.assertTrue(self.start())

    # ---- 점검: 제공하지 않은 원본 파일 ----

    def test_C01_overwrite_unprovided_origin_file_rejected(self):
        """워커가 받지 않은 원본 파일(reports/notes.md, 작업 승인 · 역할 범위 안)을 새로 만들면 점검에서 거부."""
        self.prepare()
        self.build()
        run = self.start()
        (self.workspace / "reports/notes.md").write_text("rewritten blind\n", encoding="utf-8")
        self.rejected_with(T_UNPROVIDED, lambda: self.check(run))
        self.assertRaises((ValueError, OSError, RuntimeError),
                          lambda: self.api.accept_manual(self.root, run, "Lead reviewed"))
        self.assertEqual((self.root / "reports/notes.md").read_text(), "origin notes\n")

    def test_C02_control_same_edit_in_exact_mode_passes_check(self):
        """대조: 같은 편집을 파일을 받은 exact 모드에서 하면 점검 통과(C01 이 헛거부가 아님)."""
        self.prepare()
        del self.task["workspace_profile"]
        git(self.root, "clone", "--quiet", "--no-hardlinks", str(self.root), str(self.workspace))
        run = self.start()
        (self.workspace / "reports/notes.md").write_text("rewritten blind\n", encoding="utf-8")
        self.assertEqual(self.check(run)["static_validation"], "passed")

    def test_C03_new_file_absent_at_base_allowed(self):
        """원본 base 에 없는 새 파일은 그대로 허용."""
        self.prepare()
        self.build()
        run = self.start()
        (self.workspace / "reports/result.md").write_text("new\n", encoding="utf-8")
        self.assertEqual(self.check(run)["static_validation"], "passed")

    def test_C04_case_alias_of_unprovided_file_rejected(self):
        """대소문자만 다른 경로(reports/NOTES.md)도 원본 경로와 같은 파일로 보고 거부(Windows 파일 시스템).
        대소문자를 구분하지 않는 원본에서는 기존 원본 대상 보호가 먼저 거부할 수 있다 — 둘 중 하나면 된다."""
        self.prepare()
        self.build()
        run = self.start()
        (self.workspace / "reports/NOTES.md").write_text("alias\n", encoding="utf-8")
        try:
            self.check(run)
        except (ValueError, OSError, RuntimeError) as e:
            self.assertTrue(T_UNPROVIDED in str(e) or "원본에 이미 존재" in str(e), f"거부 원인이 다름: {e}")
        else:
            self.fail("대소문자 별칭 덮어쓰기가 점검을 통과함")
        self.assertEqual((self.root / "reports/notes.md").read_text(), "origin notes\n")

    def test_C05_provided_file_modify_allowed(self):
        """제공받은 파일의 수정(삭제 후 재생성 포함)은 계약 5 대상이 아니다 — 역할 · 범위 검사만."""
        self.prepare()
        ws = self.build()
        run = self.start()
        target = ws / "reports/role-approvals.json"
        data = target.read_bytes()
        target.unlink()
        target.write_bytes(data)
        self.assertEqual(self.check(run)["static_validation"], "passed")

    def test_C06_ignored_unprovided_file_modify_rejected(self):
        """시작 전에 무시 파일로 숨겨 둔 미제공 원본 경로를 나중에 고쳐도(= modify) 거부(마일스톤 검토 R01)."""
        self.prepare()
        ws = self.build()
        (ws / "reports/notes.md").write_text("pre-placed\n", encoding="utf-8")
        (ws / ".git/info").mkdir(exist_ok=True)
        (ws / ".git/info/exclude").write_text("reports/notes.md\n", encoding="utf-8")
        self.assertEqual(git(ws, "status", "--porcelain"), "")
        run = self.start()
        (ws / "reports/notes.md").write_text("rewritten blind\n", encoding="utf-8")
        self.rejected_with(T_UNPROVIDED, lambda: self.check(run))
        self.assertEqual((self.root / "reports/notes.md").read_text(), "origin notes\n")

    # ---- CLI ----

    def test_L01_cli_passes_profile(self):
        self.prepare(config="work_orders/worker-workspace.toml")
        self.build()
        p = command(self.root, PY, "-B", "harness/execution.py", "manual-start", "--wo", "DOC", "--slice", "DOC",
                    "--role", "doc_editor", "--base", self.base, "--approval-ref", self.task["approval_ref"],
                    "--workspace", str(self.workspace), "--note", "Lead built filtered first commit",
                    "--workspace-profile", "W1", "--workspace-config", "work_orders/worker-workspace.toml")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        runs = self.runs()
        self.assertEqual(len(runs), 1, runs)
        self.assertEqual(self.contract(runs[0])["base_mode"], "filtered")

    def test_L02_cli_without_profile_rejects_filtered(self):
        self.prepare()
        self.build()
        p = command(self.root, PY, "-B", "harness/execution.py", "manual-start", "--wo", "DOC", "--slice", "DOC",
                    "--role", "doc_editor", "--base", self.base, "--approval-ref", self.task["approval_ref"],
                    "--workspace", str(self.workspace), "--note", "Lead built filtered first commit")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("워커 HEAD 불일치", p.stdout + p.stderr)
        self.assertEqual(self.runs(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
