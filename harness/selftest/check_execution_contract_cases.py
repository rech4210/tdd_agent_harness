"""WO-H63 execution boundary controls. Synthetic runner, no model or Orca launch.

Runner observations below are test doubles, not runtime permission attestations.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

from check_role_transition_cases import ROOT, PY, command, fixture, git, snap


class FakeRunner:
    id = "fixture"

    def __init__(self, workspace):
        self.workspace = workspace
        self.status = "running"
        self.model = "fixture-codex"
        self.family = "openai"
        self.agent = "codex"
        self.permission = {"sandbox": "workspace-write", "approval": "never"}
        self.stopped = False
        self.launched = False

    def launch(self, contract):
        self.launched = True
        return self.observe(contract)

    def observe(self, contract):
        return {"workspace": str(self.workspace), "session_id": "synthetic-session",
                "agent": self.agent, "model": self.model, "family": self.family,
                "permission": self.permission, "status": self.status,
                "source": "synthetic test double; not live runtime evidence"}

    def stop(self, contract):
        self.stopped = True


class ExecutionCases(unittest.TestCase):
    def setUp(self):
        source = ROOT / "harness/execution.py"
        self.assertTrue(source.is_file(), "new execution module absent (expected Red on baseline)")
        # Only the new execution module takes root explicitly; subprocesses test
        # the other modules so their existing ROOT globals cannot leak here.
        spec = importlib.util.spec_from_file_location("h63_execution", source)
        self.api = importlib.util.module_from_spec(spec)
        if str(ROOT / "harness") not in sys.path:
            sys.path.insert(0, str(ROOT / "harness"))
        spec.loader.exec_module(self.api)
        self.tmp = tempfile.TemporaryDirectory(prefix="h63-run-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "origin"
        self.root.mkdir()
        fixture(self.root)
        assignment = self.root / "config/assignments.toml"
        assignment.write_text(assignment.read_text() + '\n[slice.DOC]\nstatus="open"\ndoc_editor="codex"\ndoc_auditor="agy"\nreason="synthetic doc task"\n')
        p = self.root / "reports/role-approvals.json"
        approval = json.loads(p.read_text())
        approval["approvals"]["E1"] = {"by": "user", "kind": "execution", "wo": "DOC", "slice": "DOC",
            "role": "doc_editor", "agent": "codex", "model": "fixture-codex", "family": "openai",
            "runner": "fixture", "paths": ["reports/result.md", "docs/HANDOFF.md"]}
        p.write_text(json.dumps(approval))
        self.commit()
        transition_ref = git(self.root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"
        activated = command(self.root, PY, "-B", "harness/roles.py", "--preset", "codex-lead", "--activate", "--approval-ref", transition_ref)
        self.assertEqual(activated.returncode, 0, activated.stdout + activated.stderr)
        self.commit()
        self.base = git(self.root, "rev-parse", "HEAD")
        self.workspace = Path(self.tmp.name) / "worker"
        git(self.root, "clone", "--quiet", "--no-hardlinks", str(self.root), str(self.workspace))
        self.runner = FakeRunner(self.workspace)
        self.task = {"wo": "DOC", "slice": "DOC", "role": "doc_editor", "base": self.base,
                     "approval_ref": self.base + ":reports/role-approvals.json#E1"}

    def commit(self):
        git(self.root, "add", ".")
        git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture input")

    def start(self):
        return self.api.start(self.root, self.task, self.runner)

    def completed(self):
        run_id = self.start()
        (self.workspace / "reports/result.md").write_text("accepted candidate\n")
        self.runner.status = "completed"
        return run_id

    def rejected(self, action):
        try:
            result = action()
        except (ValueError, OSError, RuntimeError):
            return
        self.fail("invalid operation returned success: " + repr(result))

    def test_01_control_accept(self):
        run_id = self.completed()
        self.api.check(self.root, run_id, self.runner)
        self.api.accept(self.root, run_id, self.runner)
        self.assertEqual((self.root / "reports/result.md").read_text(), "accepted candidate\n")
        self.assertTrue((self.root / "reports/executions" / run_id).is_dir())

    def test_02_wrong_model(self):
        self.runner.model = "another-model"
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_03_wrong_family(self):
        self.runner.family = "anthropic"
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_04_unknown_permissions(self):
        self.runner.permission = None
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_05_workspace_is_origin(self):
        self.runner.workspace = self.root
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_06_running_cannot_accept(self):
        run_id = self.start()
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertFalse((self.root / "reports/result.md").exists())

    def test_07_role_allow_is_not_task_scope(self):
        run_id = self.completed()
        (self.workspace / "reports/not-approved.md").write_text("out of task scope")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertFalse((self.root / "reports/result.md").exists())

    def test_08_source_change_rejected(self):
        run_id = self.completed()
        (self.workspace / "src/example.txt").write_text("forbidden")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertEqual((self.root / "src/example.txt").read_text(), "original\n")

    def test_09_ignored_forbidden_change(self):
        run_id = self.completed()
        (self.workspace / ".git/info/exclude").write_text("src/hidden.txt\n")
        (self.workspace / "src/hidden.txt").write_text("hidden forbidden")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))

    def test_10_state_line_rejected(self):
        run_id = self.completed()
        p = self.workspace / "docs/HANDOFF.md"
        p.write_text(p.read_text(encoding="utf-8").replace("상태: 기존 작업 종료", "상태: 임의 완료"), encoding="utf-8")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))

    def test_11_worker_policy_tamper(self):
        run_id = self.completed()
        p = self.workspace / "config/roles.toml"
        p.write_text(p.read_text() + '\n[roles.cheater]\nallow=["**"]\n')
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))

    def test_12_origin_drift(self):
        run_id = self.completed()
        p = self.root / "config/roles.toml"
        p.write_text(p.read_text() + "\n# drift\n")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))

    def test_13_candidate_changed_after_check(self):
        run_id = self.completed()
        self.api.check(self.root, run_id, self.runner)
        (self.workspace / "reports/result.md").write_text("changed after verification")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertFalse((self.root / "reports/result.md").exists())

    def test_14_duplicate_accept(self):
        run_id = self.completed()
        self.api.accept(self.root, run_id, self.runner)
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))

    def test_15_active_run_blocks_transition(self):
        self.start()
        before = (self.root / "config/role-state.json").read_bytes()
        ref = self.base + ":reports/role-approvals.json#A1"
        p = command(self.root, PY, "-B", "harness/roles.py", "--preset", "claude-lead", "--activate", "--approval-ref", ref)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual((self.root / "config/role-state.json").read_bytes(), before)

    def test_16_base_mismatch(self):
        self.task["base"] = "0000000000000000000000000000000000000000"
        self.rejected(self.start)

    def test_17_wrong_role_not_lead_permission(self):
        self.task["role"] = "lead"
        self.rejected(self.start)

    def test_18_deleted_protected_file(self):
        run_id = self.completed()
        (self.workspace / "src/example.txt").unlink()
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))

    def test_19_unsafe_permission_is_rejected(self):
        self.runner.permission = {"sandbox": "danger-full-access", "approval": "never"}
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_20_empty_permission_is_not_verified(self):
        self.runner.permission = {}
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_21_agent_mismatch_is_stopped(self):
        self.runner.agent = "agy"
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_22_permission_drift_before_accept(self):
        run_id = self.completed()
        self.runner.permission = {"sandbox": "danger-full-access", "approval": "never"}
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertFalse((self.root / "reports/result.md").exists())

    def test_23_state_required_before_launch(self):
        (self.root / "config/role-state.json").unlink()
        self.rejected(self.start)

    def test_24_unknown_sandbox_stopped(self):
        self.runner.permission = {"sandbox": "unknown-made-up-policy", "approval": "never"}
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_25_approval_policy_required(self):
        self.runner.permission = {"sandbox": "workspace-write"}
        self.rejected(self.start)
        self.assertTrue(self.runner.stopped)

    def test_26_slice_assignment_required(self):
        (self.root / "config/assignments.toml").write_text('[slice.OLD]\nstatus="closed"\n')
        self.rejected(self.start)

    def test_27_normal_checkout_accepts_and_records_only_candidate(self):
        # Mixed/CRLF source bytes are intentional. A clean Git checkout may
        # normalize them; comparing raw blobs to checkout bytes is incorrect.
        self.assertEqual(git(self.workspace, "status", "--porcelain"), "")
        run_id = self.completed()
        result = self.api.check(self.root, run_id, self.runner)
        self.assertEqual(set(result["verified_changes"]), {"reports/result.md"})
        self.api.accept(self.root, run_id, self.runner)

    def completion_reject(self, mutate):
        run_id = self.completed()
        # Establish a successful check first, so a checkout false positive
        # cannot masquerade as protection against the subsequent drift.
        self.api.check(self.root, run_id, self.runner)
        mutate()
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertFalse((self.root / "reports/result.md").exists())

    def test_28_completion_model_drift(self):
        self.completion_reject(lambda: setattr(self.runner, "model", "other-model"))

    def test_29_completion_agent_drift(self):
        self.completion_reject(lambda: setattr(self.runner, "agent", "agy"))

    def test_30_completion_session_drift(self):
        original = self.runner.observe
        def mutate():
            self.runner.observe = lambda c: {**original(c), "session_id": "other-session"}
        self.completion_reject(mutate)

    def test_31_completion_state_deleted(self):
        self.completion_reject(lambda: (self.root / "config/role-state.json").unlink())

    def test_32_completion_assignment_drift(self):
        def mutate():
            p = self.root / "config/assignments.toml"
            p.write_text(p.read_text().replace('doc_editor="codex"', 'doc_editor="agy"'))
        self.completion_reject(mutate)

    def test_33_bold_state_line_rejected(self):
        # Existing G0/check_handoff_state recognizes Markdown emphasis.
        run_id = self.completed()
        self.api.check(self.root, run_id, self.runner)
        p = self.workspace / "docs/HANDOFF.md"
        p.write_text(p.read_text(encoding="utf-8").replace("상태: 기존 작업 종료", "**상태**: 임의 완료"), encoding="utf-8")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))

    def test_34_origin_target_edit_preserved(self):
        run_id = self.completed()
        self.api.check(self.root, run_id, self.runner)
        target = self.root / "reports/result.md"
        target.write_text("concurrent user edit\n")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertEqual(target.read_text(), "concurrent user edit\n")

    def test_35_document_drift_before_launch(self):
        p = self.root / "AGENTS.md"
        p.write_bytes(p.read_bytes().replace(b"harness:roles:begin", b"broken:roles:begin"))
        self.rejected(self.start)
        self.assertFalse(self.runner.launched)

    def test_36_approval_wo_scope(self):
        self.task["wo"] = "OTHER"
        self.rejected(self.start)
        self.assertFalse(self.runner.launched)

    def test_37_dirty_origin_before_launch(self):
        (self.root / "src/not-committed.txt").write_text("user work")
        self.rejected(self.start)
        self.assertFalse(self.runner.launched)

    def test_38_assignment_disagrees_before_launch(self):
        p = self.root / "config/assignments.toml"
        p.write_text(p.read_text().replace('doc_editor="codex"', 'doc_editor="agy"'))
        self.rejected(self.start)
        self.assertFalse(self.runner.launched)

    def test_39_completion_workspace_drift(self):
        other = Path(self.tmp.name) / "other-worker"
        def mutate():
            shutil.copytree(self.workspace, other)
            self.runner.workspace = other
        self.completion_reject(mutate)

    def mode_change(self, after_check):
        run_id = self.completed()
        if after_check:
            self.api.check(self.root, run_id, self.runner)
        p = self.workspace / "src/example.txt"
        original_mode = stat.S_IMODE(p.stat().st_mode)
        self.addCleanup(lambda: p.chmod(original_mode) if p.exists() else None)
        original_bytes = p.read_bytes()
        p.chmod(stat.S_IREAD)
        self.assertNotEqual(stat.S_IMODE(p.stat().st_mode), original_mode)
        self.assertEqual(p.read_bytes(), original_bytes)
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner) if after_check
                      else self.api.check(self.root, run_id, self.runner))
        self.assertFalse((self.root / "reports/result.md").exists())

    def test_40_forbidden_mode_change_rejected_at_check(self):
        self.mode_change(after_check=False)

    def test_41_forbidden_mode_change_rejected_after_check(self):
        self.mode_change(after_check=True)

    def implementer_fixture(self, content="good candidate\n"):
        # Synthetic task, approvals and H3/S5 prerequisites. The three cheap
        # configured fixture gates below really run; no gate_run is fabricated.
        self.root = Path(self.tmp.name) / "impl-origin"
        self.root.mkdir()
        fixture(self.root)
        assignment = self.root / "config/assignments.toml"
        assignment.write_text(assignment.read_text() + '\n[slice.IMPL]\nstatus="open"\nspec_author="codex"\ntest_author="codex"\nimplementer="agy"\nadversary="claude"\nmutation_auditor="claude"\nreason="synthetic gate binding fixture"\n')
        (self.root / "config/slices.toml").write_text('[slice.IMPL]\nwo="IMPL"\ntitle="synthetic execution binding"\nfiles=["src/example.txt"]\n')
        (self.root / "config/gates.toml").write_text('''[harness]
adapter="python"
locked=["tests/**", "specs/**"]
[[gate]]
id="GA"
name="synthetic assignments"
cmd=["python", "-B", "harness/check_assignments.py"]
[[gate]]
id="G0"
name="synthetic protected paths"
cmd=["python", "-B", "harness/check_protected_paths.py", "--base", "{base}", "--role", "{role}"]
[[gate]]
id="G2"
name="synthetic content gate"
cmd=["python", "-B", "-c", "from pathlib import Path; import sys; sys.exit('bad candidate' in Path('src/example.txt').read_text(encoding='utf-8'))"]
''', encoding="utf-8")
        (self.root / "tests").mkdir(exist_ok=True)
        (self.root / "tests/test_fixture.py").write_text("# synthetic locked input\n")
        p = self.root / "reports/role-approvals.json"
        data = json.loads(p.read_text())
        data["approvals"]["E2"] = {"by": "user", "kind": "execution", "wo": "IMPL", "slice": "IMPL",
            "role": "implementer", "agent": "agy", "model": "fixture-agy", "family": "google",
            "runner": "fixture", "paths": ["src/example.txt"]}
        p.write_text(json.dumps(data))
        evs = [
            {"kind": "wo_class", "tier": "T2", "risk": "low", "basis": "prospective", "confirmed_by": "user", "reason": "synthetic precondition"},
            {"kind": "review_run", "reviewer": "claude", "round": 1, "contract_defects": 0},
            {"kind": "human_wait_end", "stage": "H3", "decisions": 1},
        ]
        (self.root / "reports/events.jsonl").write_text("".join(json.dumps({"ts": "2026-10-20T00:00:00Z", "head": "fixture", "branch": "main", "wo": "IMPL", **e}) + "\n" for e in evs))
        lock = command(self.root, PY, "-B", "harness/check_protected_paths.py", "--write-lock")
        self.assertEqual(lock.returncode, 0, lock.stdout + lock.stderr)
        self.commit()
        git(self.root, "tag", "impl-base")
        ref = git(self.root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"
        activated = command(self.root, PY, "-B", "harness/roles.py", "--preset", "codex-lead", "--activate", "--approval-ref", ref)
        self.assertEqual(activated.returncode, 0, activated.stdout + activated.stderr)
        self.commit()
        self.base = git(self.root, "rev-parse", "HEAD")
        self.workspace = Path(self.tmp.name) / "impl-worker"
        git(self.root, "clone", "--quiet", "--no-hardlinks", str(self.root), str(self.workspace))
        self.runner = FakeRunner(self.workspace)
        self.runner.agent, self.runner.model, self.runner.family = "agy", "fixture-agy", "google"
        self.task = {"wo": "IMPL", "slice": "IMPL", "role": "implementer", "base": self.base,
                     "approval_ref": self.base + ":reports/role-approvals.json#E2"}
        run_id = self.start()
        (self.workspace / "src/example.txt").write_text(content, encoding="utf-8")
        self.runner.status = "completed"
        return run_id

    def gates(self, run_id):
        return command(self.root, PY, "-B", "harness/run_gates.py", "--execution", run_id)

    def test_42_implementer_actual_fixture_gate_control(self):
        run_id = self.implementer_fixture()
        p = self.gates(run_id)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.api.check(self.root, run_id, self.runner)
        self.api.accept(self.root, run_id, self.runner)
        self.assertEqual((self.root / "src/example.txt").read_text(), "good candidate\n")

    def test_43_gates_must_execute_worker_candidate(self):
        run_id = self.implementer_fixture("bad candidate\n")
        p = self.gates(run_id)
        self.assertNotEqual(p.returncode, 0, "origin passes, but the actual candidate must fail: " + p.stdout + p.stderr)
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertEqual((self.root / "src/example.txt").read_text(), "original\n")

    def test_44_gate_evidence_missing_execution_binding(self):
        run_id = self.implementer_fixture()
        p = self.gates(run_id)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        # Corrupt actual fixture output, not a claimed real product verdict.
        for path in [self.root / "reports/gate_report.json", self.root / "reports/executions" / run_id / "gate_report.json"]:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                for obj in [data, data.get("evidence", {})]:
                    obj.pop("execution_id", None)
                    obj.pop("base_sha", None)
                path.write_text(json.dumps(data), encoding="utf-8")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertEqual((self.root / "src/example.txt").read_text(), "original\n")

    def test_45_gate_evidence_wrong_execution(self):
        run_id = self.implementer_fixture()
        p = self.gates(run_id)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for path in [self.root / "reports/gate_report.json", self.root / "reports/executions" / run_id / "gate_report.json"]:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                data["execution_id"] = "other-execution"
                data.setdefault("evidence", {})["execution_id"] = "other-execution"
                path.write_text(json.dumps(data), encoding="utf-8")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertEqual((self.root / "src/example.txt").read_text(), "original\n")

    def test_46_candidate_changed_after_gates_before_check(self):
        run_id = self.implementer_fixture()
        p = self.gates(run_id)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (self.workspace / "src/example.txt").write_text("bad candidate\n")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertEqual((self.root / "src/example.txt").read_text(), "original\n")

    def mismatched_gate_field(self, field, wrong):
        run_id = self.implementer_fixture()
        p = self.gates(run_id)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for path in [self.root / "reports/gate_report.json", self.root / "reports/executions" / run_id / "gate_report.json"]:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                data[field] = wrong
                if field in data.get("evidence", {}):
                    data["evidence"][field] = wrong
                path.write_text(json.dumps(data), encoding="utf-8")
        self.rejected(lambda: self.api.accept(self.root, run_id, self.runner))
        self.assertEqual((self.root / "src/example.txt").read_text(), "original\n")

    def test_47_gate_wrong_role_rejected(self):
        self.mismatched_gate_field("role", "doc_editor")

    def test_48_gate_wrong_wo_rejected(self):
        self.mismatched_gate_field("wo", "OTHER")

    def test_49_gate_wrong_slice_rejected(self):
        self.mismatched_gate_field("slice", "OTHER")


if __name__ == "__main__":
    unittest.main(verbosity=2)
