"""WO-H64: manual verification and explicit coordinator handover, isolated Git fixtures.

Synthetic declarations are not live Orca observations. The prior strict 82 cases remain unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
import stat
import sys
import unittest

import check_execution_contract_cases as execution_cases
from check_role_transition_cases import PY, command, git, snap


class FlexibilityCases(unittest.TestCase):
    setUp = execution_cases.ExecutionCases.setUp
    commit = execution_cases.ExecutionCases.commit
    rejected = execution_cases.ExecutionCases.rejected

    def prepare_manual(self):
        p = self.root / 'reports/role-approvals.json'
        data = json.loads(p.read_text())
        data['approvals']['M1'] = dict(data['approvals']['E1'], runner='manual')
        p.write_text(json.dumps(data), encoding='utf-8')
        self.commit()
        self.base = git(self.root, 'rev-parse', 'HEAD')
        self.task.update(base=self.base, approval_ref=self.base + ':reports/role-approvals.json#M1')
        self.workspace = Path(self.tmp.name) / 'manual-worker'
        git(self.root, 'clone', '--quiet', '--no-hardlinks', str(self.root), str(self.workspace))

    def manual_start(self, note='Lead checked declared assignment and approved scope; live Orca unavailable'):
        return self.api.start_manual(self.root, self.task, self.workspace, note)

    def manual_ready(self):
        self.prepare_manual()
        run = self.manual_start()
        (self.workspace / 'reports/result.md').write_text('manual candidate\n', encoding='utf-8')
        return run

    def manual_check(self, run):
        return self.api.check_manual(self.root, run, 'Lead reviewed work completion and candidate files')

    def manual_accept(self, run):
        return self.api.accept_manual(self.root, run, 'Lead reviewed diff, static checks and unverified runtime limitation')

    def contract(self, run):
        return json.loads((self.root / 'reports/executions' / run / 'contract.json').read_text(encoding='utf-8'))

    def handover(self, preset='claude-lead'):
        return command(self.root, PY, '-B', 'harness/roles.py', '--activate', '--handover', '--preset', preset,
                       '--approval-ref', self.base + ':reports/role-approvals.json#A1')

    def require_handover(self, preset='claude-lead'):
        p = self.handover(preset)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        check = command(self.root, PY, '-B', 'harness/roles.py', '--check')
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_01_manual_control_and_honest_evidence(self):
        run = self.manual_ready()
        c = self.contract(run)
        self.assertEqual(c['mode'], 'manual')
        self.assertEqual(c['runtime_verification'], 'unverified')
        self.assertIsNone(c.get('observed_model'))
        self.assertIsNone(c.get('permission'))
        checked = self.manual_check(run)
        self.assertEqual(checked['static_validation'], 'passed')
        self.assertEqual(checked['runtime_verification'], 'unverified')
        accepted = self.manual_accept(run)
        self.assertEqual(accepted['runtime_verification'], 'unverified')
        self.assertEqual((self.root / 'reports/result.md').read_text(), 'manual candidate\n')

    def test_02_manual_start_requires_note(self):
        self.prepare_manual()
        self.rejected(lambda: self.manual_start('  '))
        self.assertFalse((self.root / 'reports/result.md').exists())

    def test_03_manual_complete_requires_note(self):
        run = self.manual_ready()
        self.rejected(lambda: self.api.check_manual(self.root, run, '  '))
        self.rejected(lambda: self.manual_accept(run))
        self.assertFalse((self.root / 'reports/result.md').exists())

    def test_04_manual_accept_requires_review(self):
        run = self.manual_ready()
        self.manual_check(run)
        self.rejected(lambda: self.api.accept_manual(self.root, run, '  '))
        self.assertFalse((self.root / 'reports/result.md').exists())

    def test_05_manual_requires_manual_approval(self):
        self.rejected(lambda: self.manual_start())

    def test_06_same_workspace_rejected(self):
        self.prepare_manual()
        self.workspace = self.root
        self.rejected(self.manual_start)

    def test_07_dirty_initial_workspace_rejected(self):
        self.prepare_manual()
        (self.workspace / 'reports/result.md').write_text('already edited')
        self.rejected(self.manual_start)

    def test_08_forbidden_change_stays_blocked(self):
        run = self.manual_ready()
        (self.workspace / 'src/example.txt').write_text('forbidden')
        self.rejected(lambda: self.manual_check(run))
        self.rejected(lambda: self.manual_accept(run))
        self.assertEqual((self.root / 'src/example.txt').read_text(), 'original\n')

    def test_09_post_check_change_blocked(self):
        run = self.manual_ready()
        self.manual_check(run)
        (self.workspace / 'reports/result.md').write_text('changed after review')
        self.rejected(lambda: self.manual_accept(run))
        self.assertFalse((self.root / 'reports/result.md').exists())

    def test_10_manual_cannot_be_promoted_to_strict(self):
        run = self.manual_ready()
        self.manual_check(run)
        fake = execution_cases.FakeRunner(self.workspace)
        fake.id = 'manual'
        fake.status = 'completed'
        self.rejected(lambda: self.api.check(self.root, run, fake))
        self.rejected(lambda: self.api.accept(self.root, run, fake))

    def test_11_strict_cannot_be_accepted_as_manual(self):
        run = self.api.start(self.root, self.task, self.runner)
        (self.workspace / 'reports/result.md').write_text('strict candidate')
        self.rejected(lambda: self.manual_check(run))
        self.rejected(lambda: self.manual_accept(run))

    def test_12_manual_handover_preserves_and_accepts_old_task(self):
        run = self.manual_ready()
        old = self.contract(run)
        assignments = (self.root / 'config/assignments.toml').read_bytes()
        self.require_handover()
        self.assertEqual((self.root / 'config/assignments.toml').read_bytes(), assignments)
        for key in ('role', 'agent', 'model', 'family', 'base', 'approval_ref', 'task_paths'):
            self.assertEqual(self.contract(run)[key], old[key])
        self.manual_check(run)
        self.manual_accept(run)
        self.assertEqual((self.root / 'reports/result.md').read_text(), 'manual candidate\n')

    def test_13_explicit_handover_allows_strict_old_task(self):
        run = self.api.start(self.root, self.task, self.runner)
        self.require_handover()
        (self.workspace / 'reports/result.md').write_text('strict old task\n')
        self.runner.status = 'completed'
        self.api.check(self.root, run, self.runner)
        self.api.accept(self.root, run, self.runner)
        self.assertEqual((self.root / 'reports/result.md').read_text(), 'strict old task\n')

    def test_14_handover_before_and_after_check(self):
        run = self.manual_ready()
        self.manual_check(run)
        self.require_handover()
        self.require_handover('codex-lead')
        self.manual_accept(run)

    def test_15_role_only_head_advance_allowed(self):
        run = self.manual_ready()
        self.require_handover()
        git(self.root, 'add', 'AGENTS.md', 'CLAUDE.md', 'docs/HANDOFF.md', 'config/role-state.json')
        git(self.root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'approved role handover only')
        self.manual_check(run)
        self.manual_accept(run)

    def test_16_unrelated_head_advance_blocked(self):
        run = self.manual_ready()
        self.require_handover()
        (self.root / 'src/example.txt').write_text('unrelated product change')
        git(self.root, 'add', 'AGENTS.md', 'CLAUDE.md', 'docs/HANDOFF.md', 'config/role-state.json', 'src/example.txt')
        git(self.root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'mixed handover and product change')
        self.rejected(lambda: self.manual_check(run))

    def test_17_arbitrary_state_drift_not_handover(self):
        run = self.manual_ready()
        p = self.root / 'config/role-state.json'
        data = json.loads(p.read_text())
        data['generation'] += 1
        p.write_text(json.dumps(data), encoding='utf-8')
        self.rejected(lambda: self.manual_check(run))

    def test_18_handover_does_not_allow_policy_drift(self):
        run = self.manual_ready()
        self.require_handover()
        p = self.root / 'config/roles.toml'
        p.write_text(p.read_text() + '\n# unrelated policy drift\n')
        self.rejected(lambda: self.manual_check(run))

    def test_19_handover_does_not_allow_worker_reassignment(self):
        run = self.manual_ready()
        self.require_handover()
        p = self.root / 'config/assignments.toml'
        p.write_text(p.read_text().replace('doc_editor="codex"', 'doc_editor="claude"'))
        self.rejected(lambda: self.manual_check(run))

    def test_20_manual_does_not_bypass_broken_documents(self):
        self.prepare_manual()
        p = self.root / 'AGENTS.md'
        p.write_text(p.read_text(encoding='utf-8').replace('작업 가이드', 'tampered guide'), encoding='utf-8')
        self.rejected(self.manual_start)

    def test_21_old_worker_cannot_overwrite_new_role_guide(self):
        run = self.manual_ready()
        self.require_handover()
        current = (self.root / 'docs/HANDOFF.md').read_bytes()
        p = self.workspace / 'docs/HANDOFF.md'
        p.write_text(p.read_text(encoding='utf-8') + '\nworker edited old handoff\n', encoding='utf-8')
        self.rejected(lambda: self.manual_check(run))
        self.assertEqual((self.root / 'docs/HANDOFF.md').read_bytes(), current)

    def test_22_new_slice_uses_new_preset(self):
        self.manual_ready()
        self.require_handover()
        p = command(self.root, PY, '-B', 'harness/new_slice.py', '--slice', 'WO-64', '--title', 'new defaults', '--files', 'src/example.txt', '--tests', 'tests/unit/test_x.py', '--risk', 'low')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('test_author = "claude"', p.stdout)

    def test_23_duplicate_manual_accept_rejected(self):
        run = self.manual_ready()
        self.manual_check(run)
        self.manual_accept(run)
        self.rejected(lambda: self.manual_accept(run))

    def test_24_handoff_state_line_stays_protected(self):
        run = self.manual_ready()
        p = self.workspace / 'docs/HANDOFF.md'
        p.write_text(p.read_text(encoding='utf-8').replace('상태: 기존 작업 종료', '상태: worker declared done'), encoding='utf-8')
        self.rejected(lambda: self.manual_check(run))

    def test_25_arbitrary_declared_lead_model_is_recorded(self):
        p = self.root / 'reports/role-approvals.json'
        data = json.loads(p.read_text())
        data['approvals']['A1']['models']['claude']['model'] = 'user-selected-future-model'
        p.write_text(json.dumps(data), encoding='utf-8')
        self.commit()
        run = self.manual_ready()
        self.require_handover()
        state = json.loads((self.root / 'config/role-state.json').read_text())
        self.assertEqual(state['lead'], 'claude')
        self.assertEqual(state['model_bindings']['claude']['model'], 'user-selected-future-model')
        self.assertEqual(self.contract(run)['model'], 'fixture-codex')
        self.manual_check(run)
        self.manual_accept(run)

    def test_26_manual_known_family_mismatch_rejected(self):
        self.prepare_manual()
        p = self.root / 'reports/role-approvals.json'
        data = json.loads(p.read_text())
        data['approvals']['M1']['family'] = 'anthropic'
        p.write_text(json.dumps(data), encoding='utf-8')
        self.commit()
        self.base = git(self.root, 'rev-parse', 'HEAD')
        self.task.update(base=self.base, approval_ref=self.base + ':reports/role-approvals.json#M1')
        self.workspace = Path(self.tmp.name) / 'bad-family-worker'
        git(self.root, 'clone', '--quiet', '--no-hardlinks', str(self.root), str(self.workspace))
        self.rejected(self.manual_start)

    def test_27_manual_mode_change_stays_blocked(self):
        run = self.manual_ready()
        p = self.workspace / 'src/example.txt'
        p.chmod(stat.S_IMODE(p.stat().st_mode) & ~stat.S_IWRITE)
        self.addCleanup(lambda: p.chmod(stat.S_IREAD | stat.S_IWRITE) if p.exists() else None)
        self.rejected(lambda: self.manual_check(run))

    def manual_impl(self, content='good candidate\n'):
        def start_manual_impl():
            p = self.root / 'reports/role-approvals.json'
            data = json.loads(p.read_text())
            data['approvals']['E2']['runner'] = 'manual'
            p.write_text(json.dumps(data), encoding='utf-8')
            self.commit()
            self.base = git(self.root, 'rev-parse', 'HEAD')
            self.task.update(base=self.base, approval_ref=self.base + ':reports/role-approvals.json#E2')
            self.workspace = Path(self.tmp.name) / 'manual-impl-worker'
            git(self.root, 'clone', '--quiet', '--no-hardlinks', str(self.root), str(self.workspace))
            return self.manual_start()
        self.start = start_manual_impl
        return execution_cases.ExecutionCases.implementer_fixture(self, content)

    def test_28_manual_implementer_real_gate_control_after_handover(self):
        run = self.manual_impl()
        self.require_handover()
        p = execution_cases.ExecutionCases.gates(self, run)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.manual_check(run)
        # 수동 경로는 runner 가 사용량을 남기지 않는다 — 실제 운용의 수집기(manual-run --collect-on-exit)처럼
        # 구현 실행 사용량을 기록해야 P3 종료 검사 C5 를 통과한다 (WO-H79, H72 이후 계약)
        rec = command(self.root, PY, '-B', '-c',
                      "import sys; sys.path.insert(0, 'harness'); import usage; "
                      "usage.record(wo='IMPL', role='implementer', agent='agy', run_id='manual-fixture-usage', "
                      "tokens=None, model='fixture-agy', condition={'exec_path': 'manual', 'permission': None})")
        self.assertEqual(rec.returncode, 0, rec.stdout + rec.stderr)
        self.manual_accept(run)
        self.assertEqual((self.root / 'src/example.txt').read_text(), 'good candidate\n')

    def test_29_manual_implementer_requires_bound_gates(self):
        run = self.manual_impl()
        self.manual_check(run)
        self.rejected(lambda: self.manual_accept(run))
        self.assertEqual((self.root / 'src/example.txt').read_text(), 'original\n')

    def test_30_manual_bad_candidate_fails_real_gate(self):
        run = self.manual_impl('bad candidate\n')
        p = execution_cases.ExecutionCases.gates(self, run)
        self.assertNotEqual(p.returncode, 0)
        self.rejected(lambda: self.manual_accept(run))
        self.assertEqual((self.root / 'src/example.txt').read_text(), 'original\n')

    def test_31_manual_task_paths_stay_narrow(self):
        run = self.manual_ready()
        (self.workspace / 'reports/extra.md').write_text('outside approval')
        self.rejected(lambda: self.manual_check(run))
        self.rejected(lambda: self.manual_accept(run))
        self.assertFalse((self.root / 'reports/extra.md').exists())
        self.assertFalse((self.root / 'reports/result.md').exists())

    def test_32_manual_ignored_forbidden_change(self):
        run = self.manual_ready()
        (self.workspace / '.git/info/exclude').write_text('src/hidden.txt\n')
        (self.workspace / 'src/hidden.txt').write_text('hidden forbidden')
        self.rejected(lambda: self.manual_check(run))
        self.rejected(lambda: self.manual_accept(run))
        self.assertFalse((self.root / 'src/hidden.txt').exists())

    def test_33_manual_preserves_concurrent_origin_edit(self):
        run = self.manual_ready()
        self.manual_check(run)
        target = self.root / 'reports/result.md'
        target.write_text('user concurrent edit\n')
        self.rejected(lambda: self.manual_accept(run))
        self.assertEqual(target.read_text(), 'user concurrent edit\n')

    def test_34_manual_rejects_pending_state(self):
        run = self.manual_ready()
        p = self.root / 'config/role-state.json'
        state = json.loads(p.read_text())
        state['phase'] = 'pending'
        p.write_text(json.dumps(state), encoding='utf-8')
        self.rejected(lambda: self.manual_check(run))
        self.rejected(lambda: self.manual_accept(run))
        self.assertFalse((self.root / 'reports/result.md').exists())

    def test_35_interrupted_handover_needs_recovery(self):
        run = self.manual_ready()
        ref = self.base + ':reports/role-approvals.json#A1'
        script = '''import sys,json,os
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'harness'))
import role_state
original=role_state.os.replace
def interrupted(src,dst):
    if Path(dst).name=='role-state.json':
        data=json.loads(Path(src).read_text(encoding='utf-8'))
        if data.get('phase')=='ready':
            raise SystemExit(93)
    return original(src,dst)
role_state.os.replace=interrupted
raise SystemExit(role_state.activate(Path.cwd(),'claude-lead',sys.argv[1],handover=True))
'''
        p = command(self.root, PY, '-B', '-c', script, ref)
        self.assertEqual(p.returncode, 93, p.stdout + p.stderr)
        state = json.loads((self.root / 'config/role-state.json').read_text())
        self.assertEqual(state['phase'], 'pending')
        self.rejected(lambda: self.manual_check(run))
        self.rejected(lambda: self.manual_accept(run))
        self.assertNotEqual(self.handover().returncode, 0)
        recovered = command(self.root, PY, '-B', 'harness/roles.py', '--activate', '--handover', '--recover', '--preset', 'claude-lead', '--approval-ref', ref)
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.manual_check(run)
        self.manual_accept(run)

    def test_36_corrupt_handover_record_rejected(self):
        run = self.manual_ready()
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*.json') if p.is_file()}
        self.require_handover()
        changed_records = [p for p in self.root.rglob('*.json') if p.is_file()
                           and p.relative_to(self.root).as_posix() != 'config/role-state.json'
                           and p.read_bytes() != before.get(p.relative_to(self.root).as_posix())]
        self.assertTrue(changed_records, 'handover must persist evidence beyond merely incrementing state')
        for p in changed_records:
            p.write_text('{}', encoding='utf-8')
        self.rejected(lambda: self.manual_check(run))
        self.rejected(lambda: self.manual_accept(run))

    def test_37_hidden_nested_workspace_is_not_isolated(self):
        self.prepare_manual()
        # A clean Git status is insufficient: an ignored nested clone is still
        # inside the origin. Reject before reserving a manual execution.
        (self.root / '.git/info/exclude').write_text('nested-worker/\n')
        # Neutral two-level name and a different ignore mechanism prevent
        # special-casing the sample name, info/exclude, or direct children.
        (self.root / '.gitignore').write_text('/cache/wk-a/\n')
        self.commit()
        self.base = git(self.root, 'rev-parse', 'HEAD')
        self.task.update(base=self.base, approval_ref=self.base + ':reports/role-approvals.json#M1')
        for relative in ['nested-worker', 'cache/wk-a']:
            with self.subTest(workspace=relative):
                nested = self.root / relative
                nested.parent.mkdir(parents=True, exist_ok=True)
                git(self.root, 'clone', '--quiet', '--no-hardlinks', str(self.root), str(nested))
                self.assertEqual(git(self.root, 'status', '--porcelain'), '')
                self.workspace = nested
                before = snap(self.root)
                self.rejected(self.manual_start)
                self.assertEqual(snap(self.root), before, 'rejected registration must not reserve orphan runs or change files')

    def test_38_derived_kpi_reports_do_not_block_handover(self):
        # 채택 사후 검사(finish_work)가 kpi.py 로 다시 쓰는 파생 보고서 두 개는 인계 검증의 허용 밖 변경이 아니다 (WO-H79)
        run = self.manual_ready()
        self.require_handover()
        (self.root / 'reports/kpi.md').write_text('# derived kpi\n', encoding='utf-8')
        (self.root / 'reports/tdd-timing.md').write_text('# derived timing\n', encoding='utf-8')
        self.manual_check(run)
        self.manual_accept(run)
        self.assertEqual((self.root / 'reports/result.md').read_text(), 'manual candidate\n')

    def test_39_other_report_files_still_block_handover(self):
        # 허용은 정확히 두 경로뿐: 비슷한 이름 · 하위 폴더 · 다른 reports 파일은 그대로 거부 (WO-H79)
        run = self.manual_ready()
        self.require_handover()
        for rel in ('reports/kpi.md.bak', 'reports/sub/kpi.md', 'reports/KPI.md', 'reports/other.md',
                    'reports/tdd-timing.md.tmp'):
            with self.subTest(path=rel):
                p = self.root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('not a derived report\n', encoding='utf-8')
                self.rejected(lambda: self.manual_check(run))
                p.unlink()


if __name__ == '__main__':
    unittest.main(verbosity=2)
