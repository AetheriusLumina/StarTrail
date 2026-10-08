"""Read-only developer diagnostics. Never read credential files or arbitrary settings."""
import argparse,json,sqlite3
from pathlib import Path
from contextlib import closing

def read_diagnostics(data_dir):
    path=Path(data_dir).resolve()/'radar.db'
    if not path.is_file():raise FileNotFoundError('The selected data directory has no radar.db')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={'format':1,'note':'Publication snapshots start with this feature. Old evidence is not reconstructed; refreshed caches are not historical proof.'}
        result['timing']={r['name']:json.loads(r['value']) for r in db.execute("SELECT name,value FROM settings WHERE name='search_refresh_timing'")}
        result['saved_runs']=list(map(dict,db.execute('SELECT local_date,completed_at FROM daily_runs ORDER BY local_date DESC LIMIT 30')))
        result['module_progress']=[json.loads(r[0]) for r in db.execute('SELECT payload FROM search_runs ORDER BY rowid DESC LIMIT 50')] if 'search_runs' in tables else []
        result['publication_log']=[json.loads(r[0]) for r in db.execute('SELECT payload FROM search_publication_log ORDER BY id DESC LIMIT 50')] if 'search_publication_log' in tables else []
        return result

def main():
    parser=argparse.ArgumentParser(description='Export private developer update diagnostics; read-only database access')
    parser.add_argument('--data-dir',required=True)
    args=parser.parse_args()
    print(json.dumps(read_diagnostics(args.data_dir),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
