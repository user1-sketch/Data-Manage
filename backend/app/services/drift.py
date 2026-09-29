import hashlib
import json
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.ck import ClickHouseClient, ClickHouseError
from app.core.config import get_settings
from app.services.metadata import SOURCE_PATHS, read_yaml
from app.services.scan import compare_baseline, scan_live, scope_config


_lock = threading.Lock()
RUN_TABLE = 'SmartData.meta_schema_drift_run'
DETAIL_TABLE = 'SmartData.meta_schema_drift_di'


def ck_now() -> str:
    """DateTime64(3, Asia/Shanghai) JSONEachRow-compatible representation."""
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def findings_from_baseline(report: dict) -> list[dict]:
    findings = []
    def add(layer, item, severity, table_id, column='', authoritative='', expected='', actual='', action='', message=''):
        db, table = table_id.split('.', 1)
        identity = '|'.join((layer, item, table_id, column))
        findings.append({
            'finding_id': hashlib.sha256(identity.encode()).hexdigest()[:24],
            'layer': layer, 'check_item': item, 'severity': severity,
            'database': db, 'table_name': table, 'column_name': column,
            'authoritative': authoritative, 'expected': str(expected), 'actual': str(actual),
            'fix_action': action, 'message': message,
        })
    for table_id in report['declared_missing']:
        add('L1', 'table_missing', 'error', table_id, authoritative='catalog',
            expected='declared', actual='absent', action='investigate')
    for row in report['candidates']:
        add('L1', 'table_unmanaged', 'info', row['table'], authoritative='clickhouse',
            expected='present', actual='not_declared', action='investigate')
    for row in report['columns_unrecorded']:
        add('L2', 'column_unrecorded', 'warn', row['table'], row['column'], 'clickhouse',
            row['observed_type'], 'not_declared', 'patch_detail')
    for row in report['columns_missing_live']:
        add('L2', 'column_missing', 'warn', row['table'], row['column'], 'detail',
            row['declared_type'], 'absent', 'investigate')
    for row in report['type_differences']:
        add('L2', 'nullable_difference' if row['nullable_difference'] else 'type_mismatch',
            'warn', row['table'], row['column'], 'detail', row['declared_type'],
            row['observed_type'], 'investigate')
    for row in report['physical_differences']:
        add('L3', 'physical_attribute_review', 'warn', row['table'], row['field'],
            'clickhouse', row['observed'], row['declared'], 'investigate',
            '运行态物理属性与 YAML 声明不同，需核实 DDL 与设计历史')
    return findings


def _insert_json_rows(client: ClickHouseClient, table: str, rows: list[dict]):
    if not rows:
        return
    body = f'INSERT INTO {table} FORMAT JSONEachRow\n' + '\n'.join(
        json.dumps(row, ensure_ascii=False) for row in rows)
    client.execute(body, timeout=30)


def _event(check_id, run_time, trigger, status, *, counts=None, duration_ms=0,
           tables_checked=0, tables_live=0, details_count=0, error=''):
    counts = counts or {}
    now = ck_now()
    return {'check_id': check_id, 'run_time': run_time, 'event_time': now,
            'event_order': 0 if status == 'started' else 1,
            'finish_time': None if status == 'started' else now,
            'trigger': trigger, 'status': status, 'duration_ms': duration_ms,
            'tables_checked': tables_checked, 'tables_live': tables_live,
            'err_cnt': counts.get('error', 0), 'warn_cnt': counts.get('warn', 0),
            'info_cnt': counts.get('info', 0), 'details_count': details_count,
            'err_message': error[:1000]}


def run_check(trigger: str = 'manual') -> dict:
    if not _lock.acquire(blocking=False):
        return {'run_status': 'already_running'}
    started_written = False
    try:
        started = time.monotonic()
        check_id = str(uuid.uuid4())
        run_time = ck_now()
        settings = get_settings()
        read_client = ClickHouseClient(settings)
        write_client = ClickHouseClient(settings, write=True)
        _insert_json_rows(write_client, RUN_TABLE, [_event(check_id, run_time, trigger, 'started')])
        started_written = True
        catalog = read_yaml(settings.schema_yaml_root / SOURCE_PATHS['catalog'])
        detail = read_yaml(settings.schema_yaml_root / SOURCE_PATHS['detail'])
        live = scan_live(read_client, catalog, scope_config().get('discover_databases'))
        report = compare_baseline(catalog, detail, live, scope_config())
        findings = findings_from_baseline(report)
        stamp = ck_now()
        rows = [{**finding, 'check_id': check_id, 'check_time': stamp} for finding in findings]
        _insert_json_rows(write_client, DETAIL_TABLE, rows)
        verified = read_client.query(
            f"SELECT count() AS total, uniqExact(finding_id) AS unique_count "
            f"FROM {DETAIL_TABLE} WHERE check_id = '{check_id}'")[0]
        expected = len(rows)
        if int(verified['total']) != expected or int(verified['unique_count']) != expected:
            raise RuntimeError(f'Detail verification failed: {verified}')
        counts = Counter(row['severity'] for row in rows)
        end = _event(check_id, run_time, trigger, 'success', counts=counts,
                     duration_ms=int((time.monotonic() - started) * 1000),
                     tables_checked=report['counts']['catalog_tables'],
                     tables_live=report['counts']['live_objects'], details_count=expected)
        _insert_json_rows(write_client, RUN_TABLE, [end])
        return {'run_status': 'success', **end, 'findings': findings}
    except Exception as exc:
        if started_written:
            try:
                _insert_json_rows(write_client, RUN_TABLE, [
                    _event(check_id, run_time, trigger, 'error',
                           duration_ms=int((time.monotonic() - started) * 1000), error=str(exc))])
            except Exception:
                pass
        raise
    finally:
        _lock.release()


def list_runs(days: int = 30) -> list[dict]:
    client = ClickHouseClient(get_settings())
    days = max(1, min(int(days), 90))
    rows = client.query(
        'SELECT check_id, min(run_time) AS first_run_time, '
        'argMax(status, tuple(event_order,event_time)) AS status, '
        'argMax(trigger, tuple(event_order,event_time)) AS trigger, '
        'argMax(details_count, tuple(event_order,event_time)) AS details_count, '
        'argMax(tables_checked, tuple(event_order,event_time)) AS tables_checked, '
        'argMax(tables_live, tuple(event_order,event_time)) AS tables_live, '
        'argMax(err_cnt, tuple(event_order,event_time)) AS err_cnt, '
        'argMax(warn_cnt, tuple(event_order,event_time)) AS warn_cnt, '
        'argMax(info_cnt, tuple(event_order,event_time)) AS info_cnt, '
        'argMax(err_message, tuple(event_order,event_time)) AS err_message '
        f'FROM {RUN_TABLE} WHERE run_time >= now() - INTERVAL {days} DAY '
        'GROUP BY check_id ORDER BY first_run_time DESC LIMIT 500')
    for row in rows:
        row['run_time'] = row.pop('first_run_time')
        if row['status'] == 'started':
            parsed = datetime.fromisoformat(row['run_time'].replace(' ', 'T'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
            if datetime.now(ZoneInfo('Asia/Shanghai')) - parsed > timedelta(minutes=10):
                row['status'] = 'timed_out'
    return rows


def latest_run() -> dict:
    runs = list_runs(90)
    if not runs:
        return {'run_status': 'never_run', 'findings': [], 'last_success_at': None}
    latest = runs[0]
    success = next((r for r in runs if r['status'] == 'success'), None)
    findings = []
    if latest['status'] == 'success':
        client = ClickHouseClient(get_settings())
        findings = client.query(
            'SELECT layer, check_item, severity, database, table_name, column_name, '
            'authoritative, fix_action, expected, actual, message '
            f"FROM {DETAIL_TABLE} WHERE check_id = '{latest['check_id']}' ORDER BY layer, database, table_name")
    return {'run_status': latest['status'], 'run': latest, 'findings': findings,
            'last_success_at': success['run_time'] if success else None}
