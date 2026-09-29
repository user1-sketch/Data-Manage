"""Read-only ClickHouse account diagnostics; never prints passwords."""

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values

from app.core.ck import ClickHouseClient
from app.core.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials-env', type=Path, required=True)
    parser.add_argument('--counts-only', action='store_true')
    args = parser.parse_args()
    env = dotenv_values(args.credentials_env)
    client = ClickHouseClient(Settings(
        ck_scheme=env.get('CK_SCHEME') or 'http',
        ck_host=env.get('CK_HOST') or '127.0.0.1',
        ck_port=int(env.get('CK_PORT') or 8123),
        ck_read_user=env.get('CK_USER') or env.get('CK_READ_USER') or '',
        ck_read_password=env.get('CK_PASSWORD') or env.get('CK_READ_PASSWORD') or '',
    ))
    queries = ('SELECT version() AS version, currentUser() AS account',
                'SHOW GRANTS',
                'DESCRIBE TABLE system.users',
                'SELECT name, storage FROM system.users WHERE name = currentUser()',
                'SELECT * FROM system.user_directories',
                "SELECT database, count() AS n FROM system.tables WHERE database IN ('SmartData','cs_selfop_ods','ods_selection','cs_selfop_dwd','cs_selfop_ads') GROUP BY database ORDER BY database",
                "SELECT database, count() AS n FROM system.columns WHERE database IN ('SmartData','cs_selfop_ods','ods_selection','cs_selfop_dwd','cs_selfop_ads') GROUP BY database ORDER BY database",
                "SELECT name FROM system.tables WHERE database='SmartData' "
                "AND name IN ('meta_schema_drift_di','meta_schema_drift_run')")
    for sql in queries[5:7] if args.counts_only else queries:
        try:
            print(json.dumps({'query': sql, 'rows': client.query(sql)}, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({'query': sql, 'error': str(exc)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
