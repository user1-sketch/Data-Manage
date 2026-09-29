"""Run the two quality queries with an existing read account; prints counts only."""

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values

from app.core.ck import ClickHouseClient
from app.core.config import BACKEND_ROOT, Settings
from app.services.quality import summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials-env', type=Path, default=BACKEND_ROOT / '.env')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()
    env = dotenv_values(args.credentials_env)
    user = env.get('CK_READ_USER') or env.get('CK_USER')
    password = env.get('CK_READ_PASSWORD') or env.get('CK_PASSWORD') or ''
    if not user:
        parser.error('Read-only ClickHouse credentials are required')
    settings = Settings(ck_scheme=env.get('CK_SCHEME') or 'http',
                        ck_host=env.get('CK_HOST') or '127.0.0.1',
                        ck_port=int(env.get('CK_PORT') or 8123),
                        ck_read_user=user, ck_read_password=password)
    result = summary(args.days, ClickHouseClient(settings))
    print(json.dumps({'monitor_rows': len(result['monitor']['rules']),
                      'monitor_days': len(result['monitor']['daily']),
                      'dq_rows': len(result['dq']['rules']),
                      'schedule_status': result['monitor']['schedule_status'],
                      'coverage_available': any(day['coverage'] is not None
                                                for day in result['monitor']['daily'])},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
