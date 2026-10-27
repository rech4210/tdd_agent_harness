"""P2: observe only the new registration-to-launch boundary; real existing Core fixture."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import check_role_flexibility_cases as flexibility

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from adapters.tools import preflight_agents

FAKE='''import json,os,sys
from pathlib import Path
a=sys.argv[1:]; state=Path(os.environ['P2_STATE']); root=Path(os.environ['P2_ROOT'])
if a[:2]==['worktree','show']:
 assert a[a.index('--worktree')+1]=='id:fixture::worker' and '--json' in a
 print(json.dumps({'ok':True,'result':{'worktree':{'id':'fixture::worker','path':os.environ['P2_WORKSPACE']}}}));sys.exit(0)
if a[:2]==['agent','launch']:
 contracts=list((root/'reports/executions').glob('*/contract.json'))
 assert len(contracts)==1,'launch occurred without exactly one registration'
 c=json.loads(contracts[0].read_text(encoding='utf-8'))
 assert c['mode']=='manual' and c['workspace']==os.environ['P2_EXPECTED_WORKSPACE']
 with (state/'launches.jsonl').open('a') as f:f.write(json.dumps({'args':a,'execution_id':c['run_id']})+'\\n')
 mode=(state/'mode').read_text() if (state/'mode').exists() else ''
 if mode=='fail':print(json.dumps({'ok':False,'error':{'code':'refused'}}));sys.exit(1)
 if mode=='uncertain':print(json.dumps({'ok':True,'result':{}}));sys.exit(0)
 if mode=='prompt_unconfirmed':print(json.dumps({'ok':True,'result':{'outcome':{'kind':'terminal','handle':'term_fixture'},'prompt':{'outcome':'unconfirmed'}}}));sys.exit(0)
 print(json.dumps({'ok':True,'result':{'outcome':{'kind':'terminal','handle':'term_fixture'},'prompt':{'outcome':'handed-to-terminal'}}}));sys.exit(0)
raise SystemExit(2)
'''

class ManualLaunchCases(unittest.TestCase):
    def setUp(self):
        sp=importlib.util.spec_from_file_location('p2_adapter',ROOT/'adapters/tools/orca_execution.py')
        self.module=importlib.util.module_from_spec(sp);sp.loader.exec_module(self.module)
        self.assertTrue(callable(getattr(self.module,'manual_launch',None)),'P2 manual launch entry absent')
        self.owner=flexibility.FlexibilityCases('test_01_manual_control_and_honest_evidence');self.owner.setUp();self.addCleanup(self.owner.doCleanups)
        self.root=self.owner.root
        self.state=Path(self.owner.tmp.name)/'cli-state';self.state.mkdir()
        self.fake=self.root/'fake_orca.py';self.fake.write_text(FAKE,encoding='utf-8')
        self.prompt=self.root/'prompt.md';self.prompt.write_text('Perform only the approved document task.',encoding='utf-8')
        self.empty_prompt=self.root/'empty.md';self.empty_prompt.write_text(' \n\t',encoding='utf-8')
        ignore=self.root/'.gitignore';ignore.write_text((ignore.read_text() if ignore.exists() else '')+'\nreports/events.jsonl\n',encoding='utf-8')
        self.owner.prepare_manual();self.worker=self.owner.workspace
        self.env=mock.patch.dict(os.environ,{'ORCA_CLI_COMMAND':str(self.fake),'P2_STATE':str(self.state),'P2_ROOT':str(self.root),'P2_WORKSPACE':str(self.worker.resolve()),'P2_EXPECTED_WORKSPACE':str(self.worker.resolve())})
        self.env.start();self.addCleanup(self.env.stop)
        self.preflight=mock.patch.object(preflight_agents,'main',return_value=0);self.preflight.start();self.addCleanup(self.preflight.stop)

    def launch(self,**overrides):
        args=dict(root=self.root,task=self.owner.task,workspace=self.worker,worktree='id:fixture::worker',prompt_file=self.prompt,note='Declared manual registration; observed permissions remain unknown')
        args.update(overrides);return self.module.manual_launch(**args)

    def launches(self):
        p=self.state/'launches.jsonl';return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_registered_before_one_launch_and_declared_identity_preserved(self):
        result=self.launch();self.assertEqual(result['status'],'launched');self.assertEqual(result['terminal'],'term_fixture')
        launches=self.launches();self.assertEqual(len(launches),1);self.assertEqual(launches[0]['execution_id'],result['execution_id'])
        a=launches[0]['args'];self.assertEqual(a[a.index('--agent')+1],'codex');self.assertEqual(a[a.index('--model')+1],'fixture-codex')
        self.assertEqual(a[a.index('--worktree')+1],'id:fixture::worker');self.assertEqual(a[a.index('--prompt')+1],self.prompt.read_text());self.assertIn('--json',a)
        c=json.loads((self.root/'reports/executions'/result['execution_id']/'contract.json').read_text())
        self.assertEqual(c['runtime_verification'],'unverified');self.assertIsNone(c['observed_model']);self.assertIsNone(c['permission'])
        self.assertTrue((self.root/'reports/executions'/result['execution_id']/'orca-launch.json').is_file())
        with self.assertRaises(RuntimeError):self.module.OrcaRunner(self.root,self.worker).launch({})
        self.assertEqual(len(self.launches()),1)

    def test_rejections_do_not_start_agent(self):
        for case in ('missing_prompt','empty_prompt','binding','workspace','permission'):
            with self.subTest(case=case):
                kw={}
                if case=='missing_prompt':kw['prompt_file']=self.root/'missing.md'
                if case=='empty_prompt':kw['prompt_file']=self.empty_prompt
                if case=='binding':kw['task']={**self.owner.task,'role':'undefined-role'}
                if case=='workspace':os.environ['P2_WORKSPACE']=str(self.root)
                with mock.patch.object(preflight_agents,'main',return_value=1 if case=='permission' else 0):
                    with self.assertRaises((ValueError,RuntimeError,OSError)):self.launch(**kw)
                os.environ['P2_WORKSPACE']=str(self.worker.resolve())
                self.assertEqual(self.launches(),[])
                self.assertEqual(list((self.root/'reports/executions').glob('*/contract.json')),[])

    def test_failure_or_uncertain_reply_is_retained_without_retry(self):
        # Two independent registrations in isolated fixture state; contract directories are evidence, not rewritten.
        for mode,status in [('fail','launch_failed'),('uncertain','launch_unverified'),('prompt_unconfirmed','launch_unverified')]:
            with self.subTest(mode=mode):
                (self.state/'mode').write_text(mode)
                # Fake CLI sees only the current registration; previous receipt is preserved outside the execution namespace.
                old=self.root/'reports/executions';archive=self.state/('saved-'+mode)
                if old.exists():old.rename(archive)
                result=self.launch();self.assertEqual(result['status'],status);self.assertIsNone(result.get('terminal'))
                saved=json.loads((old/result['execution_id']/'orca-launch.json').read_text())
                self.assertEqual(saved['execution_id'],result['execution_id']);self.assertEqual(saved['status'],status)
        # R5 uses the public CLI in a child with only the already-tested preflight boundary stubbed.
        bootstrap="from adapters.tools import preflight_agents; preflight_agents.main=lambda argv=None:0; import runpy; runpy.run_path('adapters/tools/orca_execution.py',run_name='__main__')"
        for mode in ('normal','fail'):
            with self.subTest(cli=mode):
                (self.root/'reports/executions').rename(self.state/('saved-cli-'+mode))
                (self.state/'mode').write_text(mode)
                t=self.owner.task
                p=subprocess.run([sys.executable,'-B','-c',bootstrap,'manual-launch','--root',str(self.root),'--wo',t['wo'],'--slice',t['slice'],'--role',t['role'],'--base',t['base'],'--approval-ref',t['approval_ref'],'--workspace',str(self.worker),'--worktree','id:fixture::worker','--prompt-file',str(self.prompt),'--note','Declared manual execution; runtime remains unverified'],cwd=self.root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
                if mode=='normal':self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                else:self.assertNotEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertEqual(len(list((self.root/'reports/executions').glob('*/contract.json'))),1)
        self.assertEqual(len(self.launches()),5)


    def p1_setup(self, mode='idle'):
        from datetime import datetime, timezone
        from check_orca_watch_cases import codex_rollout
        self.brain=self.state/('sessions-'+mode);self.brain.mkdir(exist_ok=True)
        self.fake=self.state/'p1_fake_orca.py';os.environ['ORCA_CLI_COMMAND']=str(self.fake)
        (self.state/'mode').write_text(mode)
        terminal_fake="""
if a[:2]==['terminal','read']:
 with (state/'monitor.jsonl').open('a') as f:f.write(json.dumps(a)+'\\n')
 if (state/'mode').read_text()=='gone':raise SystemExit(1)
 print('working');raise SystemExit(0)
if a[:2]==['terminal','wait']:
 mode=(state/'mode').read_text()
 print(json.dumps({'ok':True,'result':{'wait':{'satisfied':mode in ('idle','idle_missing','idle_api')}}}));raise SystemExit(0)
if a[:2]==['terminal','send']:
 with (state/'interrupts').open('a') as f:f.write('interrupt\\n')
 raise SystemExit(1 if (state/'mode').read_text()=='sendfail' else 0)
"""
        fake=FAKE.replace("mode=(state/'mode').read_text() if (state/'mode').exists() else ''","mode=(state/'mode').read_text() if (state/'mode').exists() else ''\n if mode=='watchfail':(root/'adapters/tools/orca_watch.py').write_text('raise SystemExit(7)\\n')")
        self.fake.write_text(fake.replace('raise SystemExit(2)',terminal_fake+'\nraise SystemExit(2)'),encoding='utf-8')
        if mode not in ('idle_missing','fail','uncertain'):
            codex_rollout(self.brain,'h73-'+mode,datetime.now(timezone.utc),str(self.worker))

    def p1_cli(self, **overrides):
        t=self.owner.task
        options={'cap-seconds':3,'interval':1,'round':2,'transcript-root':str(self.brain)}
        options.update(overrides)
        bootstrap="from adapters.tools import preflight_agents; preflight_agents.main=lambda argv=None:0; import runpy; runpy.run_path('adapters/tools/orca_execution.py',run_name='__main__')"
        cmd=[sys.executable,'-B','-c',bootstrap,'manual-run','--root',str(self.root),'--wo',t['wo'],'--slice',t['slice'],'--role',t['role'],'--base',t['base'],'--approval-ref',t['approval_ref'],'--workspace',str(self.worker),'--worktree','id:fixture::worker','--prompt-file',str(self.prompt),'--note','Approved one bounded run; runtime remains unverified']
        for k,v in options.items():cmd.extend(['--'+k,str(v)])
        return subprocess.run(cmd,cwd=self.root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25)

    def p1_events(self, run, kind):
        f=self.root/'reports/events.jsonl'
        return [j for j in (json.loads(x) for x in f.read_text(encoding='utf-8').splitlines() if x.strip()) if j.get('kind')==kind and j.get('execution_id')==run] if f.exists() else []

    def test_p1_required_options_reject_before_any_launch(self):
        self.assertTrue(callable(getattr(self.module,'manual_run',None)),'P1 manual-run connection absent')
        self.p1_setup()
        for options in ({'cap-seconds':0},{'interval':0},{'round':0}):
            with self.subTest(options=options):
                p=self.p1_cli(**options);self.assertNotEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertEqual(self.launches(),[])
                self.assertEqual(list((self.root/'reports/executions').glob('*/contract.json')),[])
        from check_orca_watch_cases import files
        for invalid in (float('inf'), True):
            before=files(self.root)
            with self.assertRaises((ValueError,TypeError,RuntimeError,OSError)):
                self.module.manual_run(root=self.root,task=self.owner.task,workspace=self.worker,worktree='id:fixture::worker',prompt_file=self.prompt,note='Required option rejection',cap_seconds=invalid,interval=1,round=1)
            self.assertEqual(files(self.root),before);self.assertEqual(self.launches(),[])
        for name in ('orca_watch.py','collect_usage.py'):
            tool=self.root/'adapters/tools'/name;saved=tool.read_bytes();tool.unlink()
            try:
                before=files(self.root)
                p=self.p1_cli();self.assertNotEqual(p.returncode,0,p.stdout+p.stderr);self.assertEqual(self.launches(),[])
                self.assertEqual(files(self.root),before)
            finally:tool.write_bytes(saved)

    def test_p1_single_cli_observes_and_collects_without_acceptance(self):
        from check_orca_watch_cases import files
        self.assertTrue(callable(getattr(self.module,'manual_run',None)),'P1 manual-run connection absent')
        self.p1_setup()
        worker_before=files(self.worker);index_before=(self.root/'.git/index').read_bytes()
        p=self.p1_cli();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        result=json.loads(p.stdout);run=result['execution_id'];self.assertEqual(len(self.launches()),1)
        self.assertEqual(result['status'],'observed');self.assertEqual(result['outcome'],'idle_only')
        self.assertEqual(result['usage_status'],'recorded');self.assertTrue(result['requires_manual_confirmation'])
        self.assertEqual(result['time_scope'],'runner_wall');self.assertGreaterEqual(result['wall_seconds'],0)
        self.assertTrue(result['started']);self.assertTrue(result['ended'])
        d=self.root/'reports/executions'/run
        self.assertEqual(json.loads((d/'orca-run.json').read_text(encoding='utf-8')),result)
        c=(d/'contract.json').read_bytes();contract=json.loads(c)
        self.assertFalse(contract['accepted']);self.assertEqual(contract['runtime_verification'],'unverified')
        self.assertIsNone(contract['permission']);self.assertIsNone(contract['observed_model'])
        obs=self.p1_events(run,'execution_observation');self.assertEqual(len(obs),1)
        self.assertEqual(obs[0]['terminal'],'term_fixture');self.assertEqual(obs[0]['workspace'],str(self.worker.resolve()))
        usage=self.p1_events(run,'model_usage');self.assertEqual(len(usage),1)
        self.assertEqual(usage[0]['run_id'],'h73-idle');self.assertEqual(usage[0]['round'],2)
        self.assertEqual(usage[0]['time_scope'],'transcript_span')
        self.assertEqual(self.p1_events(run,'gate_run'),[])
        source=next(self.brain.rglob('rollout-*.jsonl'))
        q=subprocess.run([sys.executable,'-B',str(self.root/'adapters/tools/collect_usage.py'),'--codex-session',str(source),'--wo',self.owner.task['wo'],'--role',self.owner.task['role'],'--execution-id',run],cwd=self.root,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(q.returncode,0,q.stdout+q.stderr);self.assertEqual(len(self.p1_events(run,'model_usage')),1)
        self.assertEqual((d/'contract.json').read_bytes(),c);self.assertEqual(files(self.worker),worker_before)
        self.assertEqual((self.root/'.git/index').read_bytes(),index_before)
        self.assertFalse((self.state/'interrupts').exists(),'Normal idle must not be interrupted')
        self.assertEqual(result['watcher_exit'],0);self.assertEqual(result['terminal'],'term_fixture')
        (self.root/'reports/executions').rename(self.state/'prior-api')
        external=ROOT/'reports/events.jsonl';external_before=external.read_bytes() if external.exists() else None
        self.p1_setup('idle_api')
        api=self.module.manual_run(root=self.root,task=self.owner.task,workspace=self.worker,worktree='id:fixture::worker',prompt_file=self.prompt,note='Explicit root API control',cap_seconds=3,interval=1,round=3,transcript_root=self.brain)
        self.assertEqual(api['status'],'observed');self.assertEqual(api['usage_status'],'recorded')
        self.assertEqual(len(self.p1_events(api['execution_id'],'model_usage')),1)
        self.assertEqual(external.read_bytes() if external.exists() else None,external_before)
        self.assertEqual(files(self.worker),worker_before)

    def test_p1_launch_failure_cap_and_missing_evidence_stay_explicit(self):
        self.assertTrue(callable(getattr(self.module,'manual_run',None)),'P1 manual-run connection absent')
        for mode,status,outcome,exit_code in [('fail','launch_failed',None,1),('uncertain','launch_unverified',None,1),('prompt_unconfirmed','launch_unverified',None,1),('watchfail','monitoring_unverified',None,1),('cap','capped','interrupt_requested',3),('sendfail','capped','stop_unverified',3),('gone','monitoring_unverified','terminal_unavailable',1),('idle_missing','observed','idle_only',0)]:
            with self.subTest(mode=mode):
                prior=self.root/'reports/executions'
                if prior.exists():prior.rename(self.state/('prior-'+mode))
                self.p1_setup(mode)
                # A stale run's plausible idle observation must not mask a failed/capped current run.
                ev=self.root/'reports/events.jsonl';ev.parent.mkdir(exist_ok=True)
                with ev.open('a',encoding='utf-8') as f:f.write(json.dumps({'kind':'execution_observation','execution_id':'stale-run','terminal':'term_fixture','workspace':str(self.worker.resolve()),'outcome':'idle_only'})+'\n')
                interrupts=self.state/'interrupts';before_count=interrupts.read_text().count('interrupt') if interrupts.exists() else 0
                watcher=self.root/'adapters/tools/orca_watch.py';watcher_before=watcher.read_bytes()
                opts={'cap-seconds':4,'interval':2,'round':3} if mode=='sendfail' else {}
                try:p=self.p1_cli(**opts)
                finally:watcher.write_bytes(watcher_before)
                self.assertEqual(p.returncode,exit_code,p.stdout+p.stderr)
                result=json.loads(p.stdout);run=result['execution_id'];self.assertEqual(result['status'],status)
                self.assertEqual(result.get('outcome'),outcome);self.assertTrue(result['requires_manual_confirmation'])
                self.assertFalse(json.loads((prior/run/'contract.json').read_text(encoding='utf-8'))['accepted'])
                self.assertNotEqual(status,'completed')
                self.assertEqual(json.loads((prior/run/'orca-run.json').read_text(encoding='utf-8')),result)
                c=json.loads((prior/run/'contract.json').read_text(encoding='utf-8'))
                self.assertIsNone(c['observed_model']);self.assertIsNone(c['permission']);self.assertEqual(c['runtime_verification'],'unverified')
                after_count=interrupts.read_text().count('interrupt') if interrupts.exists() else 0
                self.assertEqual(after_count-before_count,1 if mode in ('cap','sendfail','watchfail') else 0)
                self.assertTrue((prior/run/'orca-launch.json').is_file())
                if mode in ('fail','uncertain','prompt_unconfirmed'):
                    self.assertIsNone(result.get('terminal'));self.assertFalse(self.p1_events(run,'execution_observation'))
                    self.assertFalse((self.state/'monitor.jsonl').exists())
                elif mode=='watchfail':
                    self.assertEqual(self.p1_events(run,'execution_observation'),[]);self.assertEqual(self.p1_events(run,'model_usage'),[])
                elif mode in ('cap','sendfail'):
                    self.assertEqual(len(self.p1_events(run,'execution_observation')),1)
                    self.assertEqual(len(self.p1_events(run,'model_usage')),1)
                    u=self.p1_events(run,'model_usage')[0]
                    self.assertEqual(u['cap'],{'kind':'time','value':opts.get('cap-seconds',3),'unit':'s'})
                    self.assertEqual(u['stop'],{'reason':'cap','by':'watcher'});self.assertEqual(u['round'],opts.get('round',2))
                    self.assertEqual(result['watcher_exit'],3);self.assertEqual(result['terminal'],'term_fixture')
                    self.assertGreaterEqual(result['wall_seconds'],2);self.assertGreaterEqual(result['ended'],result['started'])
                elif mode=='gone':
                    self.assertEqual(len(self.p1_events(run,'model_usage')),1);self.assertEqual(result['usage_status'],'recorded')
                elif mode=='idle_missing':
                    self.assertEqual(result['usage_status'],'missing');self.assertTrue(result['advice'])
                    self.assertEqual(self.p1_events(run,'model_usage'),[])
        self.assertEqual(len(self.launches()),8,'Each requested run must launch once, no retries')

    def test_claude_registered_run_uses_supervision_without_foreign_usage(self):
        # This approved agent is currently rejected by manual-run; generic existing
        # cap/idle controls never traverse Claude's new no-collector branch.
        from check_orca_watch_cases import files
        from check_role_transition_cases import git
        assignment=self.root/'config/assignments.toml'
        assignment.write_text(assignment.read_text().replace('doc_editor="codex"','doc_editor="claude"'),encoding='utf-8')
        approval=self.root/'reports/role-approvals.json';data=json.loads(approval.read_text())
        data['approvals']['M1'].update(agent='claude',model='fixture-claude',family='anthropic')
        approval.write_text(json.dumps(data),encoding='utf-8');self.owner.commit()
        self.owner.base=git(self.root,'rev-parse','HEAD')
        self.owner.task.update(base=self.owner.base,approval_ref=self.owner.base+':reports/role-approvals.json#M1')
        self.worker=self.state/'claude-worker';git(self.root,'clone','--quiet','--no-hardlinks',str(self.root),str(self.worker))
        self.owner.workspace=self.worker
        os.environ.update(P2_WORKSPACE=str(self.worker.resolve()),P2_EXPECTED_WORKSPACE=str(self.worker.resolve()))
        before_worker=files(self.worker);before_index=(self.root/'.git/index').read_bytes()
        for mode,status,outcome,exit_code in [('idle','observed','idle_only',0),('cap','capped','interrupt_requested',3)]:
            with self.subTest(mode=mode):
                prior=self.root/'reports/executions'
                if prior.exists():prior.rename(self.state/('claude-prior-'+mode))
                self.p1_setup(mode)
                # p1_setup already supplies a matching foreign Codex log.
                p=self.p1_cli();self.assertEqual(p.returncode,exit_code,p.stdout+p.stderr)
                result=json.loads(p.stdout);run=result['execution_id']
                self.assertEqual(result['status'],status);self.assertEqual(result['outcome'],outcome)
                self.assertEqual(result['usage_status'],'missing');self.assertTrue(result['advice'])
                self.assertTrue(result['requires_manual_confirmation'])
                self.assertEqual(self.p1_events(run,'model_usage'),[])
                self.assertEqual(self.p1_events(run,'gate_run'),[])
                obs=self.p1_events(run,'execution_observation');self.assertEqual(len(obs),1)
                self.assertEqual(obs[0]['terminal'],'term_fixture')
                self.assertEqual(obs[0]['workspace'],str(self.worker.resolve()))
                contract=json.loads((prior/run/'contract.json').read_text(encoding='utf-8'))
                self.assertEqual(contract['agent'],'claude');self.assertFalse(contract['accepted'])
                self.assertEqual(contract['runtime_verification'],'unverified')
                self.assertEqual(files(self.worker),before_worker)
                self.assertEqual((self.root/'.git/index').read_bytes(),before_index)
                args=self.launches()[-1]['args'];self.assertEqual(args[args.index('--agent')+1],'claude')
        self.assertEqual(len(self.launches()),2)
        self.assertEqual((self.state/'interrupts').read_text().count('interrupt'),1)

if __name__=='__main__':unittest.main()
