"""Package runnable source and a consistent copy of the supplied historical data; exclude secrets."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import sqlite3
import tempfile


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    output = root / 'dist' / 'product-monitor-source.zip'
    output.parent.mkdir(exist_ok=True)
    roots = ['monitor', 'adapters', 'admin', 'crawl4ai_run', 'scripts', 'tests', 'config', 'docs', 'chrome-extension',
             'legacy/runs', 'legacy/snapshots.original.db', 'legacy/run_crawl4ai.original.py',
             'legacy/diff_sqlite.original.py', 'legacy/monitor_15min.original.sh', 'diff_sqlite.py',
             'README.md', 'LICENSE', 'NOTICE', 'Dockerfile', 'compose.yaml', 'requirements.txt', 'requirements-collector.txt',
             '.dockerignore', '.gitignore', '.env.monitor.example', 'data/crawl4ai_products.json']
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for entry in roots:
            item = root / entry
            paths = item.rglob('*') if item.is_dir() else [item]
            for file in paths:
                if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc' and file.name!='config.local.json':
                    archive.write(file, file.relative_to(root))
        # Do not copy the active database file while WAL writes may be outstanding.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshots.db'
            source = sqlite3.connect(root / 'data' / 'snapshots.db')
            target = sqlite3.connect(path)
            try: source.backup(target)
            finally: target.close(); source.close()
            archive.write(path, 'data/snapshots.db')
    print(output)
