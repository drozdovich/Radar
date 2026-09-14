"""Portable checks for release versions, documentation and the reviewed data map."""
import json
import re
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
errors = []
project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
expected = project['version']
versions = {
    '__init__': re.search(r'__version__ = "([^"]+)"', (ROOT / 'src/telegram_project_radar/__init__.py').read_text())[1],
    'npm': json.loads((ROOT / 'package.json').read_text())['version'],
    'npm lock': json.loads((ROOT / 'package-lock.json').read_text())['version'],
    'npm lock root': json.loads((ROOT / 'package-lock.json').read_text())['packages']['']['version'],
    'uv lock': next(p['version'] for p in tomllib.loads((ROOT / 'uv.lock').read_text())['package'] if p['name'] == project['name']),
}
errors.extend(f'{name} version differs from {expected}' for name, version in versions.items() if version != expected)
app = json.loads((ROOT / 'apps/twenty-review/package.json').read_text())
app_lock = json.loads((ROOT / 'apps/twenty-review/package-lock.json').read_text())
if app_lock['version'] != app['version'] or app_lock['packages']['']['version'] != app['version']:
    errors.append('Review package and lock versions differ')

private_refs = 0
docs = [*ROOT.glob('*.md'), *ROOT.glob('docs/*.md'), ROOT / 'apps/twenty-review/README.md']
for doc in docs:
    for angle, plain in re.findall(r'\]\((?:<([^>]+)>|([^\s)]+))\)', doc.read_text()):
        ref = angle or plain
        if ref.startswith('#') or urlsplit(ref).scheme:
            continue
        target = (doc.parent / unquote(ref.split('#')[0])).resolve()
        if not target.is_relative_to(ROOT) or not target.exists():
            errors.append(f'{doc.relative_to(ROOT)}: unavailable repository link {ref}')

model = json.loads((ROOT / 'docs/repo-map.json').read_text())
for item in model['entities']:
    if item.get('path') and not (ROOT / item['path']).exists():
        errors.append(f'Map path missing: {item["path"]}')
tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
for name in filter(None, tracked):
    path = Path(name)
    if any(part in {'.radar', '.repo-canvas', '.twenty', '.codex', 'node_modules', '.venv'} for part in path.parts):
        errors.append(f'Private/runtime path tracked: {name}')
    if path.name == '.env' or (path.name.startswith('.env.') and path.name != '.env.example'):
        errors.append(f'Environment file tracked: {name}')
if errors:
    raise SystemExit('\n'.join(errors))
print(f'Repository OK: Radar {expected}, Review {app["version"]}; {len(docs)} documents; {private_refs} documented private profile reference')
