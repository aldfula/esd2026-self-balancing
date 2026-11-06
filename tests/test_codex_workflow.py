"""Behavior tests use disposable repositories; never access GitHub."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / '.codex/hooks/workflow.py'
spec = importlib.util.spec_from_file_location('workflow', SCRIPT)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.remote = self.base / 'remote.git'
        self.repo = self.base / 'student'
        self.peer = self.base / 'pi'
        self.run_git(self.base, 'init', '--bare', str(self.remote))
        self.run_git(self.base, 'clone', str(self.remote), str(self.repo))
        self.run_git(self.repo, 'config', 'user.email', 'test@example.invalid')
        self.run_git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'handoff.md').write_text('initial\n')
        self.commit(self.repo)
        self.run_git(self.repo, 'push', '-u', 'origin', 'HEAD')
        self.run_git(self.base, 'clone', str(self.remote), str(self.peer))
        self.run_git(self.peer, 'config', 'user.email', 'test@example.invalid')
        self.run_git(self.peer, 'config', 'user.name', 'Test')
        self.event = {'hook_event_name': 'SessionStart', 'source': 'startup',
                      'session_id': 'test_session', 'cwd': str(self.repo)}
        self.run_git(self.repo, 'config', 'codexWorkflow.autoPull', 'true')

    def run_git(self, cwd, *args):
        return subprocess.check_output(['git', '-C', str(cwd), *args], stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self, root):
        self.run_git(root, 'add', '--all')
        self.run_git(root, 'commit', '-m', 'test: update fixture')

    def advance_peer(self):
        (self.peer / 'new.txt').write_text('remote work\n')
        self.commit(self.peer)
        self.run_git(self.peer, 'push')

    def inspect(self, source='startup'):
        original = w.git
        def routed(root, *args, **kwargs):
            # Only replace URL validation; pull still operates on a real local remote.
            if args[:2] == ('remote', 'get-url'):
                return subprocess.CompletedProcess([], 0, 'https://github.com/aldfula/esd2026-self-balancing.git\n', '')
            return original(root, *args, **kwargs)
        with patch.object(w, 'git', side_effect=routed):
            return w.inspect(self.repo, {**self.event, 'source': source})

    def test_clean_fast_forward(self):
        self.advance_peer()
        self.assertIn('completed', self.inspect())
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD'), self.run_git(self.peer, 'rev-parse', 'HEAD'))

    def test_dirty_and_untracked_block_pull(self):
        self.advance_peer()
        before = self.run_git(self.repo, 'rev-parse', 'HEAD')
        for name in ('handoff.md', 'untracked.txt'):
            path = self.repo / name
            path.write_text('local work\n')
            self.assertIn('PULL BLOCKED', self.inspect())
            self.assertEqual(before, self.run_git(self.repo, 'rev-parse', 'HEAD'))
            self.assertEqual(path.read_text(), 'local work\n')
            if name == 'handoff.md':
                self.run_git(self.repo, 'restore', name)

    def test_divergence_does_not_merge_or_rebase(self):
        self.advance_peer()
        (self.repo / 'local.txt').write_text('local\n')
        self.commit(self.repo)
        before = self.run_git(self.repo, 'rev-parse', 'HEAD')
        self.run_git(self.repo, 'config', 'pull.rebase', 'true')
        self.assertIn('PULL FAILED', self.inspect())
        self.assertEqual(before, self.run_git(self.repo, 'rev-parse', 'HEAD'))

    def test_detached_and_no_upstream(self):
        self.run_git(self.repo, 'checkout', '--detach')
        self.assertIn('PULL BLOCKED', self.inspect())
        self.run_git(self.repo, 'checkout', '-b', 'new-branch')
        self.assertIn('PULL BLOCKED', self.inspect())

    def test_compact_does_not_pull(self):
        self.advance_peer()
        self.assertIn('Context refresh only', self.inspect('compact'))
        self.assertFalse((self.repo / 'new.txt').exists())

    def test_disabled_and_unexpected_remote(self):
        self.run_git(self.repo, 'config', 'codexWorkflow.autoPull', 'false')
        self.assertIn('Auto-pull disabled', self.inspect())
        self.run_git(self.repo, 'config', 'codexWorkflow.autoPull', 'true')
        self.assertIn('outside the project allowlist', w.inspect(self.repo, self.event))

    def test_ongoing_operation(self):
        (self.repo / '.git/MERGE_HEAD').write_text(self.run_git(self.repo, 'rev-parse', 'HEAD') + '\n')
        self.assertIn('PULL BLOCKED', self.inspect())

    def test_all_history_and_handoff_anchor(self):
        self.assertIn('1 commits', self.inspect())
        head = self.run_git(self.repo, 'rev-parse', 'HEAD')
        (self.repo / 'handoff.md').write_text('**Last session close**: measured (commit ' + head + ')\n')
        self.commit(self.repo)
        self.assertIn('git log ' + head + '..HEAD: 1 commits', self.inspect())

    def enable_logs(self):
        self.run_git(self.repo, 'config', 'codexWorkflow.recordPrompts', 'true')

    def test_log_roundtrip_permissions_and_git_exclusion(self):
        self.enable_logs()
        prompt = '  진행\n줄바꿈 | "quote"\n$(touch NEVER)  '
        event = {**self.event, 'hook_event_name': 'UserPromptSubmit', 'prompt': prompt}
        w.append_event(self.repo, event)
        log = self.repo / '.git/codex-workflow/test_session.jsonl'
        row = json.loads(log.read_text())
        self.assertEqual(prompt, row['prompt'])
        self.assertRegex(row['received_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+0900$')
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)
        self.assertEqual(self.run_git(self.repo, 'status', '--porcelain'), '')
        self.assertFalse((self.repo / 'NEVER').exists())

    def test_redaction(self):
        text = 'ghp_' + 'X'*36 + '\npassword=sample\n-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n10.0.0.1'
        result, count = w.redact(text)
        self.assertGreaterEqual(count, 4)
        for secret in ('X'*36, 'sample', 'abc', '10.0.0.1'):
            self.assertNotIn(secret, result)

    def test_disabled_logging_and_bad_id(self):
        self.assertIn('disabled', w.append_event(self.repo, self.event))
        self.assertFalse((self.repo / '.git/codex-workflow').exists())
        self.enable_logs()
        with self.assertRaises(w.WorkflowError):
            w.append_event(self.repo, {**self.event, 'session_id': '../../escape'})

    def test_symlink_log_rejected(self):
        self.enable_logs()
        directory = w.private_dir(self.repo)
        target = self.base / 'target'
        target.write_text('unchanged')
        (directory / 'test_session.jsonl').symlink_to(target)
        with self.assertRaises(OSError):
            w.append_event(self.repo, self.event)
        self.assertEqual(target.read_text(), 'unchanged')

    def test_cli_json_from_subdirectory_and_failure_privacy(self):
        subdir = self.repo / 'subdir'
        subdir.mkdir()
        good = subprocess.run(['python3', str(SCRIPT)], input=json.dumps({**self.event, 'cwd': str(subdir)}), text=True, capture_output=True)
        self.assertEqual(good.returncode, 0)
        self.assertEqual(json.loads(good.stdout)['hookSpecificOutput']['hookEventName'], 'SessionStart')
        bad = subprocess.run(['python3', str(SCRIPT)], input='private malformed payload', text=True, capture_output=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn('private malformed payload', bad.stdout + bad.stderr)
        for payload in ('[]', 'null', '"private value"'):
            invalid = subprocess.run(['python3', str(SCRIPT)], input=payload,
                                     text=True, capture_output=True)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn('systemMessage', json.loads(invalid.stdout))
            self.assertEqual(invalid.stderr, '')


if __name__ == '__main__':
    unittest.main()
