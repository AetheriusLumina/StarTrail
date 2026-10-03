"""Download pinned offline models; no application/user credentials are involved."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXTRACTED = 512 * 1024 * 1024


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or '\\' in value or path.is_absolute() or '..' in path.parts or ':' in value:
        raise ValueError('UNSAFE_PATH')
    return path


def download(url: str, destination: Path) -> None:
    if urlsplit(url).scheme != 'https':
        raise ValueError('HTTPS_REQUIRED')
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='download-', dir=destination.parent) as temp:
        pending = Path(temp) / 'archive'
        with urlopen(Request(url, headers={'User-Agent': 'StarTrail-model-preparation'}), timeout=30) as response, pending.open('wb') as output:
            if urlsplit(response.geturl()).scheme != 'https':
                raise ValueError('HTTPS_REQUIRED')
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > MAX_ARCHIVE or time.monotonic() - started > 600:
                    raise ValueError('DOWNLOAD_LIMIT')
                output.write(block)
        pending.replace(destination)


def prepare_archive(archive: Path, model: dict, destination: Path, required_files: list[str]) -> list[dict]:
    if archive.stat().st_size > MAX_ARCHIVE or digest(archive) != model['sha256']:
        raise ValueError('TRANSLATION_ARCHIVE_HASH_MISMATCH')
    safe_relative(model['prefix'])
    relatives = [safe_relative(value) for value in required_files]
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with zipfile.ZipFile(archive) as zipped:
        try:
            entries = [zipped.getinfo(model['prefix'] + value) for value in required_files]
        except KeyError as exc:
            raise ValueError('TRANSLATION_MODEL_FILE_MISSING') from exc
        if any(entry.is_dir() for entry in entries):
            raise ValueError('TRANSLATION_MODEL_FILE_MISSING')
        if sum(entry.file_size for entry in entries) > MAX_EXTRACTED:
            raise ValueError('EXTRACTION_LIMIT')
        # Stage all required members before replacing any existing files.
        with tempfile.TemporaryDirectory(prefix='model-', dir=destination.parent) as temp:
            staging = Path(temp)
            for relative, entry in zip(relatives, entries):
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                records.append({'file': relative.as_posix(), 'sha256': digest(target)})
            for relative in relatives:
                if not destination.joinpath(*relative.parts).resolve().is_relative_to(destination):
                    raise ValueError('UNSAFE_PATH')
            for relative in relatives:
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                staging.joinpath(*relative.parts).replace(target)
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Prepare StarTrail offline translation models')
    parser.add_argument('--archive-dir', type=Path, default=ROOT / '.tools/downloads')
    parser.add_argument('--models-dir', type=Path, default=ROOT / '.tools/translation-models')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args(argv)
    manifest = json.loads((ROOT / 'packaging/translation-manifest.json').read_text(encoding='utf-8'))
    records = []
    for model in manifest['models']:
        if model['name'] not in ('en-zh', 'zh-en'):
            raise ValueError('UNKNOWN_MODEL')
        archive = args.archive_dir.joinpath(*safe_relative(model['archive']).parts)
        if not archive.is_file():
            if args.offline:
                raise ValueError('OFFLINE_ARCHIVE_MISSING')
            print('Downloading model:', model['name'], flush=True)
            download(model['source'], archive)
        prepared = prepare_archive(archive, model, args.models_dir / model['name'], manifest['required_files'])
        records.extend({'model': model['name'], **item} for item in prepared)
        print('Verified model:', model['name'], flush=True)
    manifest['files'] = records
    args.models_dir.mkdir(parents=True, exist_ok=True)
    marker = args.models_dir / 'manifest.json'
    pending = marker.with_suffix('.pending')
    pending.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    pending.replace(marker)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
