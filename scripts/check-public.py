"""Publication guardrails: report locations, never matching private values.

This complements a human review. It is not a guarantee that all sensitive prose
or every possible credential format has been recognised.
"""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'private key': r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'credential-shaped value': r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{30,}|\b\d{8,12}:[A-Za-z0-9_-]{30,}\b',
    'personal absolute path': r'/(?:Users|home)/[A-Za-z0-9_.-]+/',
    'internal service URL': r'https?://(?!example\.home\.arpa\b)[A-Za-z0-9.-]+\.home\.arpa\b',
    'installation workspace': r'\bworkspace_[a-z0-9]{16,}\b',
    'installation container': r'\b(?:server|postgres|n8n)-[a-z0-9]{16,}\b',
}
PRIVATE_PARTS = {'.radar', '.repo-canvas', '.twenty', '.codex', '.venv', 'node_modules'}


def scan(name, data):
    path = Path(name)
    findings = []
    if (PRIVATE_PARTS.intersection(path.parts) or path.name == '.env'
            or (path.name.startswith('.env.') and path.name != '.env.example')
            or path.suffix in {'.session', '.sqlite', '.sqlite3', '.db'}
            or (path.parts[0] == 'feedback' and path.suffix == '.json')):
        findings.append('private/runtime file')
    text = data.decode('utf-8', errors='replace')
    for kind, pattern in PATTERNS.items():
        if re.search(pattern, text):
            findings.append(kind)
    if name.startswith(('src/', 'scripts/')) and re.search(r'(?<!\d)-100\d{7,}', text):
        findings.append('embedded live-source identifier')
    if name.startswith(('src/', 'scripts/')) and re.search(r'\b(?:10\.\d+|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+\b', text):
        findings.append('embedded private-network address')
    return findings


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true', help='also inspect every reachable Git blob')
    args = parser.parse_args()
    findings = []
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').decode().split('\0')
    checked = 0
    for name in sorted(set(filter(None, names))):
        path = ROOT / name
        if not path.is_file():  # A staged removal in a preparation worktree.
            continue
        checked += 1
        findings += [f'{name}: {kind}' for kind in scan(name, path.read_bytes())]
    blobs = 0
    if args.history:
        objects = git('rev-list', '--objects', '--all').decode().splitlines()
        process = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for row in objects:
                sha, _, name = row.partition(' ')
                process.stdin.write((sha + '\n').encode())
                process.stdin.flush()
                header = process.stdout.readline().decode().split()
                if len(header) != 3:
                    raise RuntimeError('Cannot inspect a Git object')
                size = int(header[2])
                data = process.stdout.read(size)
                if process.stdout.read(1) != b'\n':
                    raise RuntimeError('Truncated Git object')
                if header[1] == 'blob' and name:
                    blobs += 1
                    findings += [f'history:{sha[:8]}:{name}: {kind}' for kind in scan(name, data)]
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.wait() != 0:
                raise RuntimeError('Git history inspection failed')
    if findings:
        raise SystemExit('\n'.join(sorted(set(findings))))
    print(f'Publication guardrails passed: {checked} files, {blobs} historical blobs; human review still required for prose.')


if __name__ == '__main__':
    main()
