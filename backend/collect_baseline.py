import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

from app.core.ck import ClickHouseClient
from app.core.config import BACKEND_ROOT, Settings
from app.services.metadata import SOURCE_PATHS, SNAPSHOT_PATH, STATUS_PATH, atomic_json, compile_snapshot, read_yaml
from app.services.scan import baseline_markdown, compare_baseline, scan_live, scope_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('D:/ds_ck'))
    parser.add_argument('--credentials-env', type=Path, default=BACKEND_ROOT / '.env')
    args = parser.parse_args()
    env = dotenv_values(args.credentials_env)
    read_user = env.get('CK_READ_USER') or env.get('CK_USER')
    read_password = env.get('CK_READ_PASSWORD') or env.get('CK_PASSWORD')
    if not read_user:
        parser.error('Read-only ClickHouse credentials are required in --credentials-env')
    settings = Settings(ck_scheme=env.get('CK_SCHEME') or 'http',
                        ck_host=env.get('CK_HOST') or '127.0.0.1',
                        ck_port=int(env.get('CK_PORT') or 8123),
                        ck_read_user=read_user, ck_read_password=read_password or '')
    catalog = read_yaml(args.root / SOURCE_PATHS['catalog'])
    detail = read_yaml(args.root / SOURCE_PATHS['detail'])
    scope = scope_config()
    live = scan_live(ClickHouseClient(settings), catalog, scope.get('discover_databases'))
    report = compare_baseline(catalog, detail, live, scope)
    report['sources'] = {name: {'path': str(args.root / relative),
                                'sha256': hashlib.sha256((args.root / relative).read_bytes()).hexdigest()}
                         for name, relative in SOURCE_PATHS.items()}
    snapshot = compile_snapshot(args.root, live)
    atomic_json(SNAPSHOT_PATH, snapshot)
    atomic_json(STATUS_PATH, {'snapshot_status': snapshot['snapshot_status'],
                              'last_success': snapshot['generated_at'], 'last_error': None})
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    directory = Path(__file__).resolve().parents[1] / 'baseline'
    atomic_json(directory / f'{stamp}.json', report)
    (directory / f'{stamp}.md').write_text(baseline_markdown(report), encoding='utf-8')
    print(json.dumps({'report': str(directory / f'{stamp}.json'), 'counts': report['counts']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
