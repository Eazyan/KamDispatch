"""Isolated behavior checks: no model history, API calls, global state or live mutations."""
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from memory import ROOT, MEMORY, SNAPSHOT, choose, identity, manifest, stale, write_checkpoint
from verify import REQUIRED, verify

class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        for name in REQUIRED:
            source=ROOT/name
            if source.exists():
                dest=self.root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        # Only the selected entry files plus placeholder evidence paths: no past chat.
        for page in [self.root/'AGENTS.md', *list((self.root/MEMORY).glob('*.md'))]:
            for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',page.read_text()):
                if re.match(r'https?://|#|mailto:',link):continue
                dest=(page.parent/link.split('#')[0]).resolve()
                if dest.is_relative_to(self.root) and not dest.exists():
                    dest.parent.mkdir(parents=True,exist_ok=True);dest.touch()
        subprocess.run(['git','init','-b','main',str(self.root)],check=True,capture_output=True)
        write_checkpoint(self.root)
    def tearDown(self): self.temp.cleanup()
    def queue_replace(self,old,new):
        p=self.root/MEMORY/'TASKS.md';p.write_text(p.read_text().replace(old,new))
    def test_new_session_selects_real_active_track(self):
        task=choose(self.root)
        self.assertEqual(task['id'],'WEATHER-001')
        self.assertIn('forecast_frames.csv',task['paths'])
        self.assertIn('Следующий шаг',task['ready'])
        self.assertEqual(choose(self.root,'DRIFT')['id'],'DRIFT-001')
        self.assertIn('unittest',choose(self.root,'DRIFT')['evidence'])
    def test_closed_and_blocked_are_never_resumed(self):
        for status in ('done','blocked'):
            self.queue_replace('| WEATHER | P1 | in_progress |',f'| WEATHER | P1 | {status} |')
            self.assertIsNone(choose(self.root,'WEATHER'))
            self.queue_replace(f'| WEATHER | P1 | {status} |','| WEATHER | P1 | in_progress |')
    def test_branch_drift_and_file_drift_are_detected(self):
        subprocess.run(['git','-C',str(self.root),'symbolic-ref','HEAD','refs/heads/codex/test'],check=True)
        self.assertIn('Git branch/SHA changed',stale(self.root))
        self.assertTrue(any('Git branch' in x for x in verify(self.root)))
        p=self.root/MEMORY/'HANDOFF.md';p.write_text(p.read_text()+'\nchanged\n')
        self.assertIn('file metadata changed since checkpoint',stale(self.root))
    def test_verifier_is_read_only_and_repeatable(self):
        before=manifest(self.root)
        self.assertEqual(verify(self.root),[])
        self.assertEqual(verify(self.root),[])
        self.assertEqual(before,manifest(self.root))
    def test_invalid_status_id_and_dependency(self):
        self.queue_replace('| WEATHER | P1 | in_progress |','| WEATHER | P1 | unknown |')
        self.queue_replace('| WEATHER-001 | Офлайн','| WEATHER-BAD | Офлайн')
        errors=verify(self.root,check_state=False)
        self.assertTrue(any('invalid status' in x for x in errors))
        self.assertTrue(any('invalid ID' in x for x in errors))
        self.assertTrue(any('missing dependency' in x for x in errors))
    def test_broken_link_date_and_index_cycle(self):
        p=self.root/MEMORY/'INDEX.md'
        p.write_text(p.read_text().replace('Updated: 2026-11-22','Updated: invalid')+'\n[missing](absent.md)\n[cycle](INDEX.md)\n')
        errors=verify(self.root,check_state=False)
        for expected in ('date','broken/escaping link','cyclic index'):
            self.assertTrue(any(expected in x for x in errors),errors)
    def test_ambiguity_does_not_switch_track(self):
        self.queue_replace('| DRIFT | P1 | todo |','| DRIFT | P1 | in_progress |')
        with self.assertRaises(ValueError):choose(self.root)
    def test_dependencies_block_available_status(self):
        self.queue_replace('| WEATHER | P1 | blocked |','| WEATHER | P1 | todo |')
        self.queue_replace('| WEATHER | P1 | in_progress |','| WEATHER | P1 | blocked |')
        self.assertIsNone(choose(self.root,'WEATHER'))
    def test_hooks_are_bounded_read_only_and_warn_without_retry(self):
        hook=self.root/'.codex/hooks/memory_hook.py'
        for event in ('SessionStart','PreCompact','Stop'):
            before=manifest(self.root)
            result=subprocess.run(['python3',str(hook),event],capture_output=True,text=True,check=True)
            obj=json.loads(result.stdout)
            self.assertEqual(before,manifest(self.root))
            self.assertNotIn('decision',obj);self.assertNotIn('continue',obj)
            if event=='SessionStart':
                self.assertLessEqual(len(obj['hookSpecificOutput']['additionalContext']),2800)
                self.assertIn('WEATHER-001',obj['hookSpecificOutput']['additionalContext'])
        p=self.root/MEMORY/'STATE.md';p.write_text(p.read_text()+'\nchange\n')
        result=subprocess.run(['python3',str(hook),'Stop'],capture_output=True,text=True,check=True)
        self.assertIn('systemMessage',json.loads(result.stdout))
    def test_secrets_and_symlinks_not_in_metadata(self):
        (self.root/'.env').write_text('DO_NOT_READ')
        (self.root/'auth.json').write_text('DO_NOT_READ')
        (self.root/'linked').symlink_to(self.root/'.env')
        files=manifest(self.root)
        for name in ('.env','auth.json','linked'):self.assertNotIn(name,files)

if __name__=='__main__':unittest.main()
