"""Grant metadata visibility to an already provisioned platform reader."""

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values

from app.core.ck import ClickHouseClient, quote_ident
from app.core.config import Settings
from provision_clickhouse import METADATA_DATABASES


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--admin-env', type=Path, required=True)
    parser.add_argument('--runtime-env', type=Path, default=Path(__file__).with_name('.env'))
    args = parser.parse_args()
    admin_env = dotenv_values(args.admin_env)
    runtime_env = dotenv_values(args.runtime_env)
    username = runtime_env.get('CK_READ_USER')
    if not username or not username.startswith('gov_platform_ro_'):
        parser.error('Expected a provisioned platform read account')
    admin = ClickHouseClient(Settings(
        ck_scheme=admin_env.get('CK_SCHEME') or 'http',
        ck_host=admin_env.get('CK_HOST') or '127.0.0.1',
        ck_port=int(admin_env.get('CK_PORT') or 8123),
        ck_write_user=admin_env.get('CK_USER') or admin_env.get('CK_READ_USER') or '',
        ck_write_password=admin_env.get('CK_PASSWORD') or admin_env.get('CK_READ_PASSWORD') or '',
    ), write=True)
    for database in METADATA_DATABASES:
        admin.execute(f'GRANT SHOW TABLES ON {quote_ident(database)}.* TO {quote_ident(username)}')
        admin.execute(f'GRANT SHOW COLUMNS ON {quote_ident(database)}.* TO {quote_ident(username)}')
    print(json.dumps({'status': 'success', 'user': username,
                      'databases': METADATA_DATABASES}, ensure_ascii=False))


if __name__ == '__main__':
    main()
