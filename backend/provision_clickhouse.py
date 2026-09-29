"""Provision the approved drift tables and least-privilege runtime accounts.

No credentials are printed. Run with --apply only after reviewing the SQL file.
"""

import argparse
import json
import os
import secrets
import string
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

from app.core.ck import ClickHouseClient, quote_ident
from app.core.config import BACKEND_ROOT, Settings


DDL_PATH = BACKEND_ROOT / 'sql' / 'create_drift_tables.sql'
ENV_PATH = BACKEND_ROOT / '.env'
READ_TABLES = [
    'system.databases', 'system.tables', 'system.columns',
    'SmartData.monitor_rule_result', 'SmartData.ods_dq_result',
    'SmartData.meta_schema_drift_di', 'SmartData.meta_schema_drift_run',
]
WRITE_TABLES = [
    'SmartData.meta_schema_drift_di', 'SmartData.meta_schema_drift_run',
]
METADATA_DATABASES = ['SmartData', 'cs_selfop_ods', 'ods_selection',
                      'cs_selfop_dwd', 'cs_selfop_ads']


def new_password() -> str:
    # This ClickHouse deployment requires 8–16 characters and a special character.
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(15)) + '!'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--admin-env', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if ENV_PATH.exists():
        parser.error(f'{ENV_PATH} already exists; refusing to replace runtime credentials')
    ddl = [statement.strip() for statement in DDL_PATH.read_text(encoding='utf-8').split(';')
           if statement.strip()]
    if len(ddl) != 2 or not all(statement.startswith('CREATE TABLE IF NOT EXISTS SmartData.meta_schema_drift_')
                                for statement in ddl):
        parser.error('DDL file does not contain exactly the two expected CREATE TABLE statements')
    if not args.apply:
        print(json.dumps({'mode': 'dry_run', 'tables': WRITE_TABLES,
                          'read_grants': READ_TABLES, 'write_grants': WRITE_TABLES,
                          'runtime_env': str(ENV_PATH)}, ensure_ascii=False))
        return

    admin_env = dotenv_values(args.admin_env)
    admin_user = admin_env.get('CK_USER') or admin_env.get('CK_READ_USER')
    admin_password = admin_env.get('CK_PASSWORD') or admin_env.get('CK_READ_PASSWORD')
    if not admin_user or not admin_password:
        parser.error('Admin ClickHouse credentials are missing from --admin-env')
    scheme = admin_env.get('CK_SCHEME') or 'http'
    host = admin_env.get('CK_HOST') or '127.0.0.1'
    port = int(admin_env.get('CK_PORT') or 8123)
    admin = ClickHouseClient(Settings(ck_scheme=scheme, ck_host=host, ck_port=port,
                                      ck_write_user=admin_user, ck_write_password=admin_password), write=True)
    suffix = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + secrets.token_hex(3)
    read_user = quote_ident(f'gov_platform_ro_{suffix}')
    write_user = quote_ident(f'gov_platform_wo_{suffix}')
    read_password = new_password()
    write_password = new_password()
    created = []
    try:
        for user, password in ((read_user, read_password), (write_user, write_password)):
            admin.execute(f"CREATE USER {user} IDENTIFIED WITH sha256_password BY '{password}'")
            created.append(user)
        for statement in ddl:
            admin.execute(statement, timeout=40)
        for table in READ_TABLES:
            admin.execute(f'GRANT SELECT ON {table} TO {read_user}')
        for database in METADATA_DATABASES:
            admin.execute(f'GRANT SHOW TABLES ON {database}.* TO {read_user}')
            admin.execute(f'GRANT SHOW COLUMNS ON {database}.* TO {read_user}')
        for table in WRITE_TABLES:
            admin.execute(f'GRANT INSERT ON {table} TO {write_user}')

        reader = ClickHouseClient(Settings(ck_scheme=scheme, ck_host=host, ck_port=port,
                                           ck_read_user=read_user.strip('`'),
                                           ck_read_password=read_password))
        for table in READ_TABLES:
            reader.query(f'SELECT * FROM {table} LIMIT 0')
        observed = {row['name'] for row in reader.query(
            "SELECT name FROM system.tables WHERE database='SmartData' "
            "AND name IN ('meta_schema_drift_di','meta_schema_drift_run')")}
        if observed != {'meta_schema_drift_di', 'meta_schema_drift_run'}:
            raise RuntimeError('Drift table verification failed')

        contents = '\n'.join([
            'SCHEMA_YAML_ROOT=D:\\ds_ck', f'CK_SCHEME={scheme}', f'CK_HOST={host}',
            f'CK_PORT={port}', f'CK_READ_USER={read_user.strip("`")}',
            f'CK_READ_PASSWORD={read_password}', f'CK_WRITE_USER={write_user.strip("`")}',
            f'CK_WRITE_PASSWORD={write_password}', 'CK_DATABASE=SmartData',
            'HOST=127.0.0.1', 'PORT=8787', 'ENABLE_SCHEDULER=false', '',
        ])
        temporary = ENV_PATH.with_suffix('.env.tmp')
        temporary.write_text(contents, encoding='utf-8')
        os.replace(temporary, ENV_PATH)
        print(json.dumps({'status': 'success', 'tables': sorted(observed),
                          'read_user': read_user.strip('`'),
                          'write_user': write_user.strip('`'),
                          'runtime_env': str(ENV_PATH)}, ensure_ascii=False))
    except Exception as exc:
        for user in reversed(created):
            try:
                admin.execute(f'DROP USER IF EXISTS {user}')
            except Exception:
                pass
        cause = exc.__cause__
        response = getattr(cause, 'response', None)
        detail = getattr(response, 'text', '') or str(exc)
        for secret in (read_password, write_password):
            detail = detail.replace(secret, '[REDACTED]')
        raise RuntimeError(f'Provisioning failed: {detail[:1200]}') from None


if __name__ == '__main__':
    main()
