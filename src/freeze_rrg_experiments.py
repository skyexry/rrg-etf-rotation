"""Archive existing RRG outputs, code and input databases with verified hashes."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = ROOT / 'experiments_frozen' / ('before_report_settings_' + stamp)
    dest.mkdir(parents=True, exist_ok=False)
    files = []
    for rel in ['src', 'vendor/quantsplaybook_rrg', 'results/rrg',
                'data/rrg/author_source_documentation']:
        for path in (ROOT / rel).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                files.append((path, path.relative_to(ROOT)))
    for rel in ['requirements_rrg.txt', 'RRG_IMPLEMENTATION.txt',
                'data/rrg/snapshot_report.json', 'data/rrg/environment_lock.txt',
                'data/rrg/rrg_inputs.duckdb']:
        path = ROOT / rel
        if path.exists():
            files.append((path, Path(rel)))
    author_env = os.environ.get('RRG_AUTHOR_DB')
    author = Path(author_env).expanduser() if author_env else None
    if author and author.exists():
        files.append((author, Path('input_databases/author_etf_rrg.duckdb')))
    manifest = []
    for source, relative in sorted(files, key=lambda pair: str(pair[1])):
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = sha(source)
        shutil.copy2(source, target)
        copied = sha(target)
        if before != copied or before != sha(source):
            raise RuntimeError(f'Input changed during freezing: {relative}')
        manifest.append({'path': str(relative), 'bytes': target.stat().st_size,
                         'sha256': copied, 'source': str(source)})
    record = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'purpose': 'Freeze all existing RRG experiments before report-setting replication.',
              'files': manifest, 'verified': True}
    (dest / 'MANIFEST.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    (dest / 'README.txt').write_text(
        '当前RRG实验冻结版本。包含结果、源代码、依赖记录及两份输入数据库。\n'
        'MANIFEST.json记录每个文件的SHA256，复制后与原件逐一核对。\n'
        '后续研报设定实验另存，冻结目录仅用于回溯，原实验文件保留。\n')
    print(json.dumps({'archive': str(dest), 'files': len(manifest),
                      'bytes': sum(x['bytes'] for x in manifest), 'verified': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
