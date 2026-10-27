"""WO-H63 closed behavioral cases; copied repositories only, never mutate ROOT.

Run: python harness/selftest/check_role_transition_cases.py
The approval fixture is synthetic maintainer evidence, not a real user approval.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


def command(root, *args):
    return subprocess.run(list(args), cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")


def git(root, *args):
    p = command(root, "git", *args)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()


def fixture(root):
    for name in ("harness", "adapters", "templates", "config"):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__", "UPSTREAM"))
    (root / "config/agents.toml").write_text('[agents.codex]\nfamily="openai"\n[agents.claude]\nfamily="anthropic"\n[agents.agy]\nfamily="google"\n', encoding="utf-8")
    (root / "config/assignments.toml").write_text('[slice.OLD]\nstatus="closed"\nspec_author="claude"\ntest_author="claude"\nimplementer="agy"\nadversary="codex"\nmutation_auditor="codex"\nreason="historical authors"\n', encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs/HANDOFF.md").write_text('# Project handoff\n\n상태: 기존 작업 종료\n\nHistorical paragraph.\n', encoding="utf-8")
    (root / "AGENTS.md").write_bytes(b"Project prefix\r\n")
    (root / "CLAUDE.md").write_bytes(b"Other prefix\r\n")
    (root / "src").mkdir(exist_ok=True)
    (root / "src/example.txt").write_text("original\n", encoding="utf-8")
    (root / "reports").mkdir(exist_ok=True)
    approval = {"schema": 1, "approvals": {"A1": {
        "by": "user", "kind": "role-transition", "presets": ["codex-lead", "claude-lead"],
        "handoff": "docs/HANDOFF.md", "models": {
            "codex": {"model": "fixture-codex", "family": "openai", "effort": "medium"},
            "claude": {"model": "fixture-claude", "family": "anthropic", "effort": "medium"},
            "agy": {"model": "fixture-agy", "family": "google", "effort": "high"}}}}}
    (root / "reports/role-approvals.json").write_text(json.dumps(approval), encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "synthetic baseline")


def snap(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts and "__pycache__" not in p.parts}


class TransitionCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="h63-state-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        fixture(self.root)
        self.ref = git(self.root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"

    def roles(self, *args):
        return command(self.root, PY, "-B", "harness/roles.py", *args)

    def activate(self, preset="codex-lead", *extra):
        return self.roles("--preset", preset, "--activate", "--approval-ref", self.ref, *extra)

    def require_active(self):
        p = self.activate()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads((self.root / "config/role-state.json").read_text(encoding="utf-8"))

    def commit(self):
        git(self.root, "add", ".")
        git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture transition")

    def test_01_activation_control(self):
        before = (self.root / "config/assignments.toml").read_bytes()
        state = self.require_active()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["preset"], "codex-lead")
        self.assertEqual(state["lead"], "codex")
        self.assertEqual(state["default_assignments"]["test_author"], "codex")
        self.assertTrue(state["config_version"])
        self.assertEqual((self.root / "config/assignments.toml").read_bytes(), before)
        self.assertEqual(self.roles("--check").returncode, 0)

    def test_02_dry_run_no_writes(self):
        before = snap(self.root)
        p = self.activate("codex-lead", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(snap(self.root), before)

    def test_03_preserve_prefix_history_state_line(self):
        handoff = (self.root / "docs/HANDOFF.md").read_bytes()
        self.require_active()
        self.assertTrue((self.root / "AGENTS.md").read_bytes().startswith(b"Project prefix\r\n"))
        self.assertTrue((self.root / "CLAUDE.md").read_bytes().startswith(b"Other prefix\r\n"))
        text = (self.root / "docs/HANDOFF.md").read_bytes()
        self.assertIn(handoff, text)
        self.assertIn(b"harness:role-guide:begin", text)

    def test_04_idempotent(self):
        self.require_active()
        before = snap(self.root)
        p = self.activate()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(snap(self.root), before)

    def test_05_two_way_switch_preserves_old_assignments(self):
        before = (self.root / "config/assignments.toml").read_bytes()
        first = self.require_active()
        self.commit()
        p = self.activate("claude-lead")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        state = json.loads((self.root / "config/role-state.json").read_text())
        self.assertEqual(state["lead"], "claude")
        self.assertGreater(state["generation"], first["generation"])
        self.assertEqual((self.root / "config/assignments.toml").read_bytes(), before)

    def test_06_missing_approval(self):
        before = snap(self.root)
        p = self.roles("--preset", "codex-lead", "--activate")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(snap(self.root), before)

    def test_07_uncommitted_approval_not_accepted(self):
        before = snap(self.root)
        p = self.roles("--preset", "codex-lead", "--activate", "--approval-ref", "reports/role-approvals.json#A1")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(snap(self.root), before)

    def test_08_approval_scope(self):
        doc = json.loads((self.root / "reports/role-approvals.json").read_text())
        doc["approvals"]["A1"]["presets"] = ["claude-lead"]
        (self.root / "reports/role-approvals.json").write_text(json.dumps(doc))
        self.commit()
        self.ref = git(self.root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"
        before = snap(self.root)
        self.assertNotEqual(self.activate().returncode, 0)
        self.assertEqual(snap(self.root), before)

    def test_09_bad_model_family(self):
        doc = json.loads((self.root / "reports/role-approvals.json").read_text())
        doc["approvals"]["A1"]["models"]["agy"]["family"] = "openai"
        (self.root / "reports/role-approvals.json").write_text(json.dumps(doc))
        self.commit()
        self.ref = git(self.root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"
        self.assertNotEqual(self.activate().returncode, 0)

    def test_10_duplicate_markers(self):
        self.require_active()
        p = self.root / "AGENTS.md"
        p.write_bytes(p.read_bytes() + b"\n<!-- harness:roles:begin -->\n<!-- harness:roles:end -->\n")
        self.assertNotEqual(self.roles("--check").returncode, 0)

    def test_11_generated_document_drift(self):
        self.require_active()
        p = self.root / "CLAUDE.md"
        p.write_bytes(p.read_bytes().replace(b"<!-- harness:roles:begin -->", b"<!-- harness:roles:begin -->\nInjected instruction"))
        self.assertNotEqual(self.roles("--check").returncode, 0)

    def test_12_config_drift(self):
        self.require_active()
        p = self.root / "config/roles.toml"
        p.write_bytes(p.read_bytes() + b"\n# changed policy\n")
        self.assertNotEqual(self.roles("--check").returncode, 0)

    def test_13_template_drift(self):
        self.require_active()
        p = self.root / "templates/roles/codex-lead/AGENTS.md"
        p.write_bytes(p.read_bytes() + b"\nChanged template\n")
        self.assertNotEqual(self.roles("--check").returncode, 0)

    def test_14_pending_is_not_ready(self):
        self.require_active()
        p = self.root / "config/role-state.json"
        state = json.loads(p.read_text())
        state["phase"] = "pending"
        p.write_text(json.dumps(state))
        self.assertNotEqual(self.roles("--check").returncode, 0)

    def test_15_legacy_bypass_is_blocked_with_state(self):
        self.require_active()
        before = snap(self.root)
        p = self.roles("--preset", "claude-lead")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(snap(self.root), before)

    def test_16_failed_target_preserves(self):
        (self.root / "CLAUDE.md").unlink()
        (self.root / "CLAUDE.md").mkdir()
        before = snap(self.root)
        self.assertNotEqual(self.activate().returncode, 0)
        self.assertEqual(snap(self.root), before)

    def test_17_handoff_path_escape(self):
        doc = json.loads((self.root / "reports/role-approvals.json").read_text())
        doc["approvals"]["A1"]["handoff"] = "../outside.md"
        (self.root / "reports/role-approvals.json").write_text(json.dumps(doc))
        self.commit()
        self.ref = git(self.root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"
        self.assertNotEqual(self.activate().returncode, 0)
        self.assertFalse((self.root.parent / "outside.md").exists())

    def test_18_handoff_old_lead_requires_lead_update(self):
        (self.root / "docs/HANDOFF.md").write_text("상태: 구현 전 · lead=Claude\n", encoding="utf-8")
        self.commit()
        before = snap(self.root)
        self.assertNotEqual(self.activate().returncode, 0)
        self.assertEqual(snap(self.root), before)

    def test_19_nonexistent_state_check_fails(self):
        self.assertNotEqual(self.roles("--check").returncode, 0)

    def test_20_new_slice_uses_active_not_historical_author(self):
        self.require_active()
        p = command(self.root, PY, "-B", "harness/new_slice.py", "--slice", "WO-64", "--title", "Synthetic", "--files", "src/example.txt", "--tests", "tests/unit/test_x.py", "--risk", "low", "--policy", "D-28")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('test_author = "codex"', p.stdout)
        self.assertNotIn('test_author = "claude"', p.stdout)

    def test_21_policy_family_conflict_rejected_before_write(self):
        p = self.root / "templates/roles/codex-lead/preset.toml"
        p.write_text(p.read_text(encoding="utf-8").replace('implementer = "agy"', 'implementer = "codex"'), encoding="utf-8")
        self.commit()
        before = snap(self.root)
        self.assertNotEqual(self.activate().returncode, 0)
        self.assertEqual(snap(self.root), before)

    def test_22_state_claim_does_not_override_preset(self):
        self.require_active()
        p = self.root / "config/role-state.json"
        state = json.loads(p.read_text())
        state["lead"] = "claude"
        state["default_assignments"]["test_author"] = "claude"
        p.write_text(json.dumps(state))
        self.assertNotEqual(self.roles("--check").returncode, 0)

    def test_23_failure_mid_write_never_leaves_ready(self):
        # Inject one filesystem failure after planning. Fail the CLAUDE replace
        # once; rollback is allowed to use replace afterwards.
        before_agents = (self.root / "AGENTS.md").read_bytes()
        script = '''import os, pathlib, sys
sys.path.insert(0, "harness")
real = os.replace
fired = [False]
def fail_once(src, dst):
    if pathlib.Path(dst).name == "CLAUDE.md" and not fired[0]:
        fired[0] = True
        raise OSError("synthetic mid-write failure")
    return real(src, dst)
os.replace = fail_once
import roles
sys.argv = ["roles.py", "--preset", "codex-lead", "--activate", "--approval-ref", sys.argv[1]]
raise SystemExit(roles.main())
'''
        p = command(self.root, PY, "-B", "-c", script, self.ref)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotEqual(self.roles("--check").returncode, 0)
        state_path = self.root / "config/role-state.json"
        if state_path.exists():
            self.assertNotEqual(json.loads(state_path.read_text())["phase"], "ready")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), before_agents)

    def interrupted_transition(self, failed=False, all_documents=False):
        self.require_active()
        self.commit()
        before = snap(self.root)
        # Use the real journal writer. Crash after pending + AGENTS, before
        # CLAUDE. Exception gives failed state; SystemExit simulates a crash.
        script = '''import os, pathlib, sys, json
sys.path.insert(0, "harness")
real = os.replace
fired = [False]
failed = sys.argv[2] == "failed"
all_documents = sys.argv[3] == "all"
def interrupt(src, dst):
    if all_documents:
        trigger = pathlib.Path(dst).name == "role-state.json" and json.loads(pathlib.Path(src).read_text(encoding="utf-8")).get("phase") == "ready"
    else:
        trigger = pathlib.Path(dst).name == "CLAUDE.md"
    if trigger and not fired[0]:
        fired[0] = True
        if failed:
            raise OSError("synthetic transition failure")
        raise SystemExit(77)
    return real(src, dst)
os.replace = interrupt
import roles
ref = sys.argv[1]
sys.argv = ["roles.py", "--preset", "claude-lead", "--activate", "--approval-ref", ref]
raise SystemExit(roles.main())
'''
        p = command(self.root, PY, "-B", "-c", script, self.ref, "failed" if failed else "pending", "all" if all_documents else "partial")
        self.assertNotEqual(p.returncode, 0)
        state = json.loads((self.root / "config/role-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "failed" if failed else "pending")
        self.assertNotEqual(self.roles("--check").returncode, 0)
        return before

    def test_24_owned_pending_recovery_control(self):
        before = self.interrupted_transition()
        p = self.activate("codex-lead", "--recover")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(snap(self.root), before)
        self.assertEqual(self.roles("--check").returncode, 0)

    def edited_state_recovery_rejected(self, failed=False):
        self.interrupted_transition(failed)
        p = self.root / "config/role-state.json"
        state = json.loads(p.read_text(encoding="utf-8"))
        state["user_concurrent_edit"] = "must preserve"
        p.write_text(json.dumps(state), encoding="utf-8")
        before = snap(self.root)
        result = self.activate("codex-lead", "--recover")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snap(self.root), before)

    def test_25_edited_pending_recovery_preserves(self):
        self.edited_state_recovery_rejected()

    def test_26_edited_failed_recovery_preserves(self):
        self.edited_state_recovery_rejected(failed=True)

    def test_27_recovery_rechecks_before_each_restore(self):
        self.interrupted_transition()
        # Inject a user edit when the first document is restored. A single
        # preflight snapshot for the whole recovery is insufficient.
        script = '''import os, pathlib, sys, json
sys.path.insert(0, "harness")
real = os.replace
fired = [False]
def concurrent_edit(src, dst):
    if pathlib.Path(dst).name == "AGENTS.md" and not fired[0]:
        fired[0] = True
        p = pathlib.Path("config/role-state.json")
        st = json.loads(p.read_text(encoding="utf-8"))
        st["user_concurrent_edit"] = "during recovery"
        p.write_text(json.dumps(st), encoding="utf-8")
    return real(src, dst)
os.replace = concurrent_edit
import roles
ref = sys.argv[1]
sys.argv = ["roles.py", "--preset", "codex-lead", "--activate", "--approval-ref", ref, "--recover"]
raise SystemExit(roles.main())
'''
        result = command(self.root, PY, "-B", "-c", script, self.ref)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads((self.root / "config/role-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state.get("user_concurrent_edit"), "during recovery")
        self.assertNotEqual(self.roles("--check").returncode, 0)

    def test_28_corrupt_recovery_backup_preserves(self):
        self.interrupted_transition()
        journal = self.root / git(self.root, "rev-parse", "--git-path", "role-transition-journal.json")
        data = json.loads(journal.read_text(encoding="utf-8"))
        data["files"]["AGENTS.md"]["orig_bytes_hex"] = b"corrupt backup".hex()
        journal.write_text(json.dumps(data), encoding="utf-8")
        before = snap(self.root)
        result = self.activate("codex-lead", "--recover")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snap(self.root), before)

    def test_29_owned_failed_recovery_control(self):
        before = self.interrupted_transition(failed=True)
        result = self.activate("codex-lead", "--recover")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snap(self.root), before)
        self.assertEqual(self.roles("--check").returncode, 0)

    def test_30_recovery_preserves_concurrent_document_edit(self):
        self.interrupted_transition()
        script = '''import pathlib, sys
sys.path.insert(0, "harness")
real = pathlib.Path.read_bytes
fired = [False]
def concurrent_edit(p):
    result = real(p)
    if p.name == "role-state.json" and not fired[0]:
        fired[0] = True
        doc = pathlib.Path("AGENTS.md")
        doc.write_bytes(real(doc) + b"\\nuser concurrent document edit\\n")
    return result
pathlib.Path.read_bytes = concurrent_edit
import roles
ref = sys.argv[1]
sys.argv = ["roles.py", "--preset", "codex-lead", "--activate", "--approval-ref", ref, "--recover"]
raise SystemExit(roles.main())
'''
        result = command(self.root, PY, "-B", "-c", script, self.ref)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"user concurrent document edit", (self.root / "AGENTS.md").read_bytes())

    def custom_handoff(self):
        custom = self.root / "handoff/PROJECT.md"
        custom.parent.mkdir()
        (self.root / "docs/HANDOFF.md").rename(custom)
        p = self.root / "reports/role-approvals.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["approvals"]["A1"]["handoff"] = "handoff/PROJECT.md"
        p.write_text(json.dumps(data), encoding="utf-8")
        self.commit()
        self.ref = git(self.root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"
        return custom

    def test_31_approved_custom_handoff_recovery_control(self):
        # Recovery must use the same approved scope as the real transition.
        custom = self.custom_handoff()
        before = self.interrupted_transition(all_documents=True)
        self.assertNotEqual(custom.read_bytes(), before["handoff/PROJECT.md"])
        result = self.activate("codex-lead", "--recover")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snap(self.root), before)
        self.assertEqual(self.roles("--check").returncode, 0)

    def test_32_custom_handoff_concurrent_edit_preserved(self):
        custom = self.custom_handoff()
        before = self.interrupted_transition(all_documents=True)
        self.assertNotEqual(custom.read_bytes(), before["handoff/PROJECT.md"])
        script = '''import pathlib, sys
sys.path.insert(0, "harness")
real = pathlib.Path.read_bytes
fired = [False]
def concurrent_edit(p):
    result = real(p)
    if p.name == "role-state.json" and not fired[0]:
        fired[0] = True
        doc = pathlib.Path("handoff/PROJECT.md")
        doc.write_bytes(real(doc) + b"\\nuser custom handoff edit\\n")
    return result
pathlib.Path.read_bytes = concurrent_edit
import roles
ref = sys.argv[1]
sys.argv = ["roles.py", "--preset", "codex-lead", "--activate", "--approval-ref", ref, "--recover"]
raise SystemExit(roles.main())
'''
        result = command(self.root, PY, "-B", "-c", script, self.ref)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"user custom handoff edit", custom.read_bytes())

    def test_33_custom_handoff_corrupt_backup_preserved(self):
        custom = self.custom_handoff()
        original = self.interrupted_transition(all_documents=True)
        self.assertNotEqual(custom.read_bytes(), original["handoff/PROJECT.md"])
        journal = self.root / git(self.root, "rev-parse", "--git-path", "role-transition-journal.json")
        data = json.loads(journal.read_text(encoding="utf-8"))
        data["files"]["handoff/PROJECT.md"]["orig_bytes_hex"] = b"corrupt custom backup".hex()
        journal.write_text(json.dumps(data), encoding="utf-8")
        before = snap(self.root)
        result = self.activate("codex-lead", "--recover")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snap(self.root), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
