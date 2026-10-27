"""P3 wrapper boundaries; existing Core and step fixtures remain authoritative."""
from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from unittest import mock

import check_role_flexibility_cases as flexibility
import check_step_cases as steps

ROOT=Path(__file__).resolve().parents[2]

class FinishWorkCases(unittest.TestCase):
    def setUp(self):
        source=ROOT/'harness/finish_work.py'
        self.assertTrue(source.is_file(),'P3 finish entry absent')
        spec=importlib.util.spec_from_file_location('p3_finish',source)
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)

    def doc_fixture(self, auditor=False):
        owner=flexibility.FlexibilityCases('test_01_manual_control_and_honest_evidence');owner.setUp();self.addCleanup(owner.doCleanups)
        if auditor:
            p=owner.root/'reports/role-approvals.json';data=json.loads(p.read_text())
            data['approvals']['E1'].update(role='doc_auditor',agent='agy',model='fixture-agy',family='google')
            p.write_text(json.dumps(data));owner.task['role']='doc_auditor'
        (owner.root/'reports/audit.txt').write_text('Human audit artifact; its contents are not machine verdicts.')
        scripts=owner.root/'scripts';scripts.mkdir(exist_ok=True)
        (scripts/'check_docs_fresh.py').write_text("print('fixture document checker');raise SystemExit(0)\n")
        owner.prepare_manual();run=owner.manual_start()
        return owner,run

    def finish(self,o,run,kind='doc-edit',**kwargs):
        return self.module.finish_work(o.root,run,kind,'Lead checked saved candidate',**kwargs)

    def test_document_normal_missing_evidence_and_core_boundaries(self):
        o,run=self.doc_fixture()
        r=self.finish(o,run,audit_report='reports/audit.txt',lead_note='Lead compared artifacts',audited_inputs=['src/example.txt'])
        self.assertFalse(r['blockers']);self.assertEqual(r['checks']['core']['status'],'checked')
        self.assertNotIn('step',r['checks']);self.assertNotEqual(r['status'],'completed')
        self.assertFalse(o.contract(run).get('accepted'));self.assertFalse((o.root/'reports/result.md').exists())
        ev=o.root/'reports/events.jsonl'
        with ev.open('a') as f:f.write(json.dumps(dict(kind='model_usage',wo='DOC',role=o.task['role'],execution_id=run,agent='codex',run_id='fixture-actual-id',tokens={'input':1,'output':1}))+'\n')
        events_before=ev.read_bytes()
        good=self.finish(o,run,audit_report='reports/audit.txt',lead_note='Compared',audited_inputs=['src/example.txt'])
        self.assertEqual(good['status'],'checks_passed');self.assertEqual(ev.read_bytes(),events_before)
        self.assertTrue((o.root/'reports/kpi.md').is_file());self.assertTrue((o.root/'reports/tdd-timing.md').is_file())
        missing=self.finish(o,run)
        self.assertEqual(missing['status'],'manual_confirmation');self.assertTrue(missing['advice'])
        self.assertIn('audit',json.dumps(missing).lower());self.assertIn('usage',json.dumps(missing).lower())
        wrong=self.finish(o,run,kind='code');self.assertTrue(wrong['blockers']);self.assertNotIn('step',wrong['checks'])
        for bad in ('../outside','missing-contract'):
            self.assertTrue(self.finish(o,bad)['blockers'])
        # One real public CLI call exercises supplied root, repeated input options, advisory exit and JSON.
        cli=[sys.executable,'-B',str(ROOT/'harness/finish_work.py'),'--root',str(o.root),'--run',run,'--kind','doc-edit','--completion-note','Saved candidate','--audit-input','src/example.txt','--audit-input','docs/HANDOFF.md']
        q=subprocess.run(cli,capture_output=True,text=True,encoding='utf-8');self.assertEqual(q.returncode,0,q.stderr)
        self.assertEqual(json.loads(q.stdout)['status'],'manual_confirmation')
        checker=o.root/'scripts/check_docs_fresh.py';checker.write_text('raise SystemExit(1)\n')
        failed=self.finish(o,run);self.assertTrue(failed['blockers']);self.assertEqual(failed['checks']['docs']['returncode'],1)
        q=subprocess.run(cli,capture_output=True,text=True,encoding='utf-8');self.assertNotEqual(q.returncode,0);self.assertEqual(json.loads(q.stdout)['status'],'blocked')
        checker.unlink();self.assertEqual(self.finish(o,run)['status'],'manual_confirmation')
        (o.workspace/'src/example.txt').write_text('forbidden change')
        rejected=self.finish(o,run)
        self.assertTrue(rejected['blockers']);self.assertEqual(rejected['status'],'blocked')

    def test_audit_zero_changes_explicit_input_drift_and_unknown_kind(self):
        o,run=self.doc_fixture(auditor=True)
        r=self.finish(o,run,kind='doc-audit',audited_inputs=['src/example.txt'])
        self.assertFalse(r['blockers']);self.assertNotIn('step',r['checks'])
        unknown=self.finish(o,run,kind='unknown');self.assertEqual(unknown['status'],'manual_confirmation')
        (o.workspace/'reports/result.md').write_text('auditor must not write')
        self.assertTrue(self.finish(o,run,kind='doc-audit')['blockers'])
        # An allowed edit passes Core, but invalidates explicitly listed audit-baseline input.
        e,er=self.doc_fixture();(e.workspace/'docs/HANDOFF.md').write_text((e.workspace/'docs/HANDOFF.md').read_text()+'\nnew details\n')
        drift=self.finish(e,er,audit_report='reports/audit.txt',lead_note='Compared',audited_inputs=['docs/HANDOFF.md'])
        self.assertFalse(drift['blockers']);self.assertEqual(drift['status'],'manual_confirmation')
        self.assertEqual(drift['checks']['audit_inputs']['changed'],['docs/HANDOFF.md']);self.assertTrue(drift['advice'])
        # Same edit is not automatically called related to a different explicit input.
        stable=self.finish(e,er,audited_inputs=['src/example.txt'])
        self.assertFalse(stable['checks']['audit_inputs']['changed'])
        (e.root/'src/example.txt').write_text('origin drift')
        origin=self.finish(e,er,audited_inputs=['src/example.txt']);self.assertEqual(origin['checks']['audit_inputs']['changed'],['src/example.txt'])
        absent=self.finish(e,er,audited_inputs=['absent.txt']);self.assertEqual(absent['checks']['audit_inputs']['changed'],['absent.txt'])
        escape=self.finish(e,er,audited_inputs=['../outside']);self.assertTrue(escape['blockers'])

    def test_code_dispatch_preserves_real_step_failure_and_normal_control(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='p3-step-') as td:
            p=steps.make_project(Path(td),'flow');(p/'config/slices.toml').write_text(steps.SLICES)
            sha=steps.commit(p,'config/slices.toml',steps.SLICES,'slices')
            d=p/'reports/executions/manual_fixture';d.mkdir(parents=True)
            (d/'contract.json').write_text(json.dumps(dict(run_id='manual_fixture',mode='manual',wo='WO-51',slice='WO-51',role='implementer',workspace=str(p),worker_start_snapshot={})))
            # Core is independently covered by real document cases; here isolate dispatch to real step.
            with mock.patch.object(self.module.execution,'check_manual',return_value={'status':'checked','runtime_verification':'unverified'}):
                steps.put(p,[steps.cls()])
                r=self.module.finish_work(p,'manual_fixture','code','Checked saved code')
                self.assertTrue(r['blockers']);self.assertIn('C1',json.dumps(r))
                steps.put(p,[steps.cls(),steps.H3,steps.USAGE,steps.verdict(steps.T4,sha),steps.review(steps.T5)])
                events_before=(p/'reports/events.jsonl').read_bytes()
                good=self.module.finish_work(p,'manual_fixture','code','Checked saved code')
                self.assertFalse(good['blockers']);self.assertEqual(good['checks']['step']['returncode'],0)
                self.assertEqual(good['status'],'manual_confirmation')
                self.assertEqual((p/'reports/events.jsonl').read_bytes(),events_before)
                self.assertTrue(self.module.finish_work(p,'manual_fixture','doc-edit','Checked saved code')['blockers'])
                # Existing step events are synthetic fixture evidence, never forwarded to root events.
                self.assertFalse(json.loads((d/'contract.json').read_text()).get('accepted'))
                # A required close checker that exits 0 without its JSON evidence is not a pass.
                (p/'harness/step.py').write_text("print('invalid close result')\n")
                self.assertEqual(self.module.finish_work(p,'manual_fixture','code','Checked saved code')['status'],'blocked')


    def test_manual_accept_cli_checks_p3_before_applying_candidate(self):
        o,run=self.doc_fixture()
        (o.workspace/'reports/result.md').write_text('H72 candidate\n')
        o.manual_check(run)
        baseline=o.contract(run)['verified_worker_snapshot']
        frozen={k:o.contract(run)[k] for k in ('verified_worker_snapshot','verified_changes','completion_note','verified_at','ga_result','g0_result')}
        checker=o.root/'scripts/check_docs_fresh.py'
        checker.write_text('raise SystemExit(1)\n')
        cli=[sys.executable,'-B',str(o.root/'harness/execution.py'),'manual-accept','--run',run,'--review-note','Explicit lead adoption with runtime unknown']
        failed=subprocess.run(cli,cwd=o.root,capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(failed.returncode,0,failed.stdout+failed.stderr)
        self.assertFalse((o.root/'reports/result.md').exists())
        self.assertFalse(o.contract(run).get('accepted'))
        self.assertEqual(o.contract(run)['verified_worker_snapshot'],baseline)
        self.assertEqual({k:o.contract(run)[k] for k in frozen},frozen)
        checker.write_text('raise SystemExit(0)\n')
        normal=subprocess.run(cli,cwd=o.root,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(normal.returncode,0,normal.stdout+normal.stderr)
        import ast
        result=ast.literal_eval(normal.stdout[normal.stdout.index('{'):].strip())
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(result['closure']['status'],'manual_confirmation')
        self.assertFalse(result['closure']['blockers'])
        self.assertTrue(result['closure']['advice'])
        self.assertEqual(result['closure']['checks']['docs']['returncode'],0)
        self.assertEqual(o.contract(run)['verified_worker_snapshot'],baseline)
        self.assertEqual({k:o.contract(run)[k] for k in frozen},frozen)
        self.assertEqual((o.root/'reports/result.md').read_text(),'H72 candidate\n')

    def test_manual_code_accept_cannot_skip_existing_close_blocker(self):
        o=flexibility.FlexibilityCases('test_29_manual_implementer_requires_bound_gates');o.setUp();self.addCleanup(o.doCleanups)
        run=o.manual_impl()
        # Existing real bound gate fixture establishes that rejection is P3, not missing Core gates.
        q=flexibility.execution_cases.ExecutionCases.gates(o,run)
        self.assertEqual(q.returncode,0,q.stdout+q.stderr)
        o.manual_check(run)
        # Remove only the close-review event in this synthetic fixture; do not change gate evidence.
        ev=o.root/'reports/events.jsonl'
        rows=[json.loads(x) for x in ev.read_text().splitlines()]
        ev.write_text(''.join(json.dumps(x)+'\n' for x in rows if x.get('kind')!='review_run'))
        with self.assertRaisesRegex((ValueError,RuntimeError),'C5'):
            o.manual_accept(run)
        self.assertEqual((o.root/'src/example.txt').read_text(),'original\n')
        self.assertFalse(o.contract(run).get('accepted'))
        # Restore the existing review plus explicitly synthetic usage: same candidate must proceed.
        rows.append(dict(kind='model_usage',wo='IMPL',role='implementer',agent='agy',source='synthetic fixture only'))
        ev.write_text(''.join(json.dumps(x)+'\n' for x in rows))
        accepted=o.manual_accept(run)
        self.assertEqual(accepted['status'],'accepted')
        self.assertEqual(accepted['closure']['checks']['step']['returncode'],0)
        self.assertEqual((o.root/'src/example.txt').read_text(),'good candidate\n')

if __name__=='__main__':unittest.main()
