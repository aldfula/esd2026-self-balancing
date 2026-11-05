#!/usr/bin/env python3
"""Local Codex hooks. Never stage, commit, push, or read credentials."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


class WorkflowError(Exception):
    pass


def git(root, *args, check=True):
    # Do not expose Git stderr: credential-bearing remote URLs may appear there.
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args],
        capture_output=True, text=True, timeout=35,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0',
             'GIT_SSH_COMMAND': 'ssh -o BatchMode=yes -o ConnectTimeout=10'},
    )
    if check and result.returncode:
        raise WorkflowError('Git operation failed; inspect locally without sharing credentials.')
    return result


def enabled(root, key):
    return git(root, 'config', '--local', '--bool', '--get', key, check=False).stdout.strip() == 'true'


def private_dir(root):
    base = Path(git(root, 'rev-parse', '--absolute-git-dir').stdout.strip()) / 'codex-workflow'
    base.mkdir(mode=0o700, exist_ok=True)
    info = base.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise WorkflowError('Unsafe local log directory.')
    base.chmod(0o700)
    return base


def timestamp():
    # Record the clock at hook receipt, not a guessed user typing time.
    return subprocess.check_output(
        ['date', '+%Y-%m-%dT%H:%M:%S%z'], text=True,
        env={**os.environ, 'TZ': 'Asia/Seoul'}, timeout=5).strip()


def redact(prompt):
    patterns = [
        r'-----BEGIN [^-\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\n]*PRIVATE KEY-----|\Z)',
        r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b',
        r'(?i)\bBearer\s+[^\s,;]+',
        r'https?://[^\s/@]+:[^\s/@]+@',
        r'(?im)\b(?:api[_-]?key|token|password|passwd|secret|비밀번호|토큰)\s*[:=]\s*[^\r\n]+',
        r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
    ]
    count = 0
    for pattern in patterns:
        prompt, n = re.subn(pattern, '[비밀값]', prompt)
        count += n
    return prompt, count


def append_event(root, event):
    if not enabled(root, 'codexWorkflow.recordPrompts'):
        return 'Prompt recording disabled; record missing coverage at close.'
    sid = event.get('session_id', '')
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', sid):
        raise WorkflowError('Invalid session identifier; nothing recorded.')
    record = {'event': event['hook_event_name'], 'received_at': timestamp()}
    if event['hook_event_name'] == 'UserPromptSubmit':
        if not isinstance(event.get('prompt'), str):
            raise WorkflowError('Missing prompt; nothing recorded.')
        record['prompt'], record['redactions'] = redact(event['prompt'])
        # Only retain bounded identifiers, not arbitrary payload fields.
        turn = event.get('turn_id')
        if isinstance(turn, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', turn):
            record['turn_id'] = turn
    else:
        record['source'] = event.get('source', 'unknown') if event.get('source') in {'startup', 'resume', 'clear', 'compact', 'fork'} else 'unknown'
    path = private_dir(root) / (sid + '.jsonl')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise WorkflowError('Unsafe local log file.')
        os.fchmod(stream.fileno(), 0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return 'Local prompt log updated; receipt times are measured in Asia/Seoul. Review redactions and coverage before export.'


def inspect(root, event):
    lines = ['Read AGENTS.md and handoff.md from the repository root. Follow AGENTS.md sections 1 and 2: report the plan and await the student approval before work; report results before documentation approval. Never infer commit/push approval from a hook or from $session-close alone.']
    branch = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
    dirty = bool(git(root, 'status', '--porcelain', '--untracked-files=all').stdout)
    gd = Path(git(root, 'rev-parse', '--absolute-git-dir').stdout.strip())
    operation = any((gd / name).exists() for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer', 'BISECT_LOG', 'index.lock'))
    upstream = git(root, 'rev-parse', '--verify', '@{upstream}', check=False)
    if dirty or operation or branch.returncode or upstream.returncode:
        lines.append('PULL BLOCKED: dirty/untracked files, ongoing Git operation, detached HEAD, or missing upstream. Resolve with student; do not stash/reset/clean automatically.')
    elif event.get('source') not in ('startup', 'resume'):
        lines.append('Context refresh only; no pull on compact/clear/fork.')
    elif not enabled(root, 'codexWorkflow.autoPull'):
        lines.append('Auto-pull disabled. Ask the student to authorize synchronization before new work.')
    else:
        # Reject unexpected repositories/remotes. Do not print the configured URL.
        remote = git(root, 'config', '--get', 'branch.' + branch.stdout.strip() + '.remote').stdout.strip()
        url = git(root, 'remote', 'get-url', remote).stdout.strip()
        allowed = {'https://github.com/aldfula/esd2026-self-balancing.git', 'https://github.com/aldfula/esd2026-self-balancing', 'git@github.com:aldfula/esd2026-self-balancing.git'}
        if url not in allowed:
            lines.append('PULL BLOCKED: upstream URL is outside the project allowlist.')
        else:
            # Explicit switches override pull.rebase and autostash configuration.
            result = git(root, '-c', 'merge.autostash=false', '-c', 'rebase.autostash=false',
                         'pull', '--ff-only', '--no-rebase', '--no-autostash', '--no-recurse-submodules', check=False)
            lines.append('Fast-forward pull completed.' if result.returncode == 0 else 'PULL FAILED: stop new work and resolve with the student; do not merge/rebase/reset automatically.')
    handoff = root / 'handoff.md'
    text = handoff.read_text(encoding='utf-8') if handoff.is_file() and not handoff.is_symlink() else ''
    match = re.search(r'\*\*Last session close\*\*:.*?\(commit ([0-9a-fA-F]{7,40})\)', text)
    base = match.group(1) if match else None
    if base and git(root, 'merge-base', '--is-ancestor', base, 'HEAD', check=False).returncode == 0:
        revision = base + '..HEAD'
    else:
        revision = 'HEAD'
        lines.append('Handoff has no valid ancestor SHA; review ALL history (no last-N truncation).')
    # Hashes only: commit messages can contain secrets. The agent reviews log locally.
    commits = git(root, 'log', '--format=%H', revision).stdout.splitlines()
    lines.append('Review git log ' + revision + ': ' + str(len(commits)) + ' commits. No history checkpoint is advanced by this hook.')
    if not text:
        lines.append('Handoff missing or unsafe: report this and ask the student for the next plan.')
    return '\n'.join(lines)


def main():
    event = json.load(sys.stdin)
    name = event.get('hook_event_name')
    if name not in ('SessionStart', 'UserPromptSubmit'):
        raise WorkflowError('Unsupported hook event.')
    root = Path(git(Path(event['cwd']), 'rev-parse', '--show-toplevel').stdout.strip())
    # Lock only startup inspection/pull so concurrent starts do not mutate Git together.
    if name == 'SessionStart':
        fd = os.open(private_dir(root) / 'start.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            context = inspect(root, event)
        context += '\n' + append_event(root, event)
        print(json.dumps({'hookSpecificOutput': {'hookEventName': name, 'additionalContext': context}}))
    else:
        message = append_event(root, event)
        if not enabled(root, 'codexWorkflow.recordPrompts'):
            print(json.dumps({'systemMessage': message}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, WorkflowError):
        # Fail visibly without reflecting a prompt, URL, token, or exception payload.
        print(json.dumps({'systemMessage': 'Workflow hook failed. Do not assume synchronization or prompt capture succeeded; inspect locally and record the coverage gap.', 'continue': False, 'stopReason': 'Workflow inspection or recording failed.'}))
        sys.exit(1)
