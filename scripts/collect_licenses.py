"""Collect distribution notices; public manifest contains no machine paths."""
import argparse
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('ctranslate2', 'sentencepiece', 'numpy', 'PyYAML', 'PyInstaller',
            'pyinstaller-hooks-contrib', 'altgraph', 'packaging', 'pefile',
            'pywin32-ctypes', 'setuptools')


def copy(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def write_manifest(output: Path, packages: list[dict]):
    files = [{'file': path.relative_to(output).as_posix(),
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in sorted(output.rglob('*')) if path.is_file()
             and path.name != 'manifest.json']
    (output / 'manifest.json').write_text(json.dumps(
        {'packages': packages, 'files': files}, ensure_ascii=False, indent=2),
        encoding='utf-8')


def collect(output: Path, models: Path):
    required = [ROOT / 'LICENSE', ROOT / 'docs/THIRD_PARTY_NOTICES.md',
                ROOT / 'packaging/licenses/CTranslate2-MIT.txt',
                ROOT / 'packaging/licenses/SentencePiece-Apache.txt',
                ROOT / 'packaging/licenses/Python-PSF.txt',
                ROOT / 'packaging/licenses/Intel-LICENSE.txt']
    if any(not path.is_file() for path in required):
        raise FileNotFoundError('PUBLIC_NOTICES_MISSING')
    output.mkdir(parents=True, exist_ok=True)
    for source in [ROOT / 'LICENSE', ROOT / 'docs/THIRD_PARTY_NOTICES.md']:
        copy(source, output / source.name)
    for source in (ROOT / 'packaging/licenses').iterdir():
        if source.is_file():
            copy(source, output / 'upstream' / source.name)
    for source in (ROOT / 'github_radar/web_assets/fonts').glob('*-OFL.txt'):
        copy(source, output / 'fonts' / source.name)
    copy(ROOT / 'github_radar/web_assets/fonts/sources.json', output / 'fonts/sources.json')
    for model in ('en-zh', 'zh-en'):
        for name in ('README.md', 'metadata.json'):
            copy(models / model / name, output / 'models' / model / name)
    copy(ROOT / 'packaging/translation-manifest.json', output / 'models/sources.json')
    packages = []
    for name in PACKAGES:
        info = distribution(name)
        packages.append({'name': info.metadata['Name'], 'version': info.version})
        for file in info.files or []:
            # Only package-supplied legal files, never local metadata/configuration.
            if file.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                source = Path(info.locate_file(file))
                if source.is_file():
                    relative = Path(*file.parts[file.parts.index(next(
                        part for part in file.parts if part.endswith('.dist-info')))+1:]) if any(
                            part.endswith('.dist-info') for part in file.parts) else Path(file.name)
                    if relative.is_absolute() or '..' in relative.parts:
                        raise ValueError('UNSAFE_NOTICE_PATH')
                    copy(source, output / 'packages' / name / relative)
    write_manifest(output, packages)


def main():
    parser = argparse.ArgumentParser(description='Collect StarTrail binary notices')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    args = parser.parse_args()
    collect(args.output, args.models)
    print('Redistribution notices collected')


if __name__ == '__main__':
    main()
