"""Conservative public-file gate; prints file/category, never secret values.

This complements GitHub secret scanning and human review. It cannot detect
every possible secret and is not permission to publish arbitrary local data.
"""
import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {'UserData', '.venv', '.tools', '.build', '.superpowers', '.codex',
                '__pycache__', 'build', 'dist', 'outputs'}
SECRET = re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
ABSOLUTE = re.compile(r'(?<![A-Za-z0-9])(?:[A-Za-z]:[/\\](?:[^\s\"\']{2,})|/(?:home|Users)/[^/\s]+)')
EMAIL = re.compile(r'[A-Za-z0-9_.+-]+@(?:gmail|qq|outlook|hotmail|163|126)\.(?:com|cn)', re.I)


def inspect_file(name: str, content: bytes) -> list[str]:
    path = PurePosixPath(name)
    findings = []
    if (any(part in PRIVATE_DIRS for part in path.parts) or path.name.startswith('.env')
            or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.bin', '.log', '.exe',
                                      '.msi', '.pfx', '.pem', '.key', '.zip', '.argosmodel'}):
        findings.append('private-or-generated-file')
    if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.ico', '.ttf'}:
        return findings
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        return findings + ['unexpected-nontext-file']
    if SECRET.search(text):
        findings.append('credential')
    if ABSOLUTE.search(text):
        findings.append('absolute-path')
    # Font copyright notices are third-party license text, not personal app data.
    if not path.name.endswith('-OFL.txt') and EMAIL.search(text):
        findings.append('personal-email')
    return findings


def candidates(root: Path, tracked: bool):
    if tracked:
        result = subprocess.run(['git', '-c', 'core.quotepath=false', 'ls-files', '-z'],
                                cwd=root, check=True, capture_output=True)
        return [root / name.decode('utf-8') for name in result.stdout.split(b'\0') if name]
    return [path for path in root.rglob('*') if path.is_file()
            and not any(part in PRIVATE_DIRS or part == '.git' or part.endswith('.egg-info')
                        for part in path.relative_to(root).parts)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Check public StarTrail files')
    parser.add_argument('--tracked', action='store_true', help='Check every Git-tracked file, including forced additions')
    args = parser.parse_args(argv)
    files = candidates(ROOT, args.tracked)
    bad = 0
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        findings = inspect_file(relative, path.read_bytes())
        if findings:
            bad += 1
            print(relative + ': ' + ', '.join(findings))
    print(f'Public file check: {len(files)} files, {bad} flagged')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
