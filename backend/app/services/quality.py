from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.ck import ClickHouseClient
from app.core.config import BACKEND_ROOT, get_settings
from app.services.metadata import read_yaml


def _days(value: int) -> int:
    if not 1 <= value <= 90:
        raise ValueError('days must be 1..90')
    return value


def monitor_rows(days: int, client: ClickHouseClient | None = None) -> list[dict]:
    days = _days(days)
    client = client or ClickHouseClient(get_settings())
    return client.query(
        'SELECT biz_date, workflow_name, rule_name, '
        'argMax(tuple(lower(status), message, toString(actual_value), toString(threshold)), run_time) AS result, '
        'max(run_time) AS last_run, count() AS run_count, '
        "countIf(lower(status) IN ('fail','error')) AS failed_runs "
        'FROM SmartData.monitor_rule_result '
        f'WHERE biz_date >= today() - {days - 1} '
        'GROUP BY biz_date, workflow_name, rule_name '
        'ORDER BY biz_date DESC, workflow_name, rule_name')


def dq_rows(days: int, client: ClickHouseClient | None = None) -> list[dict]:
    days = _days(days)
    client = client or ClickHouseClient(get_settings())
    return client.query(
        'SELECT check_batch, task_name, run_group, rule_code, dimension, '
        'argMax(tuple(lower(status), rule_name, err_msg), checked_at) AS result, '
        'max(checked_at) AS last_checked '
        'FROM SmartData.ods_dq_result '
        f'WHERE checked_at >= now() - INTERVAL {days} DAY '
        'GROUP BY check_batch, task_name, run_group, rule_code, dimension '
        'ORDER BY last_checked DESC LIMIT 10000')


def summary(days: int = 7, client: ClickHouseClient | None = None) -> dict:
    monitor = monitor_rows(days, client)
    dq = dq_rows(days, client)
    schedule = read_yaml(BACKEND_ROOT / 'quality_schedule.yaml')
    verified = schedule.get('verification_status') == 'verified'
    now = datetime.now(ZoneInfo(schedule.get('timezone', 'Asia/Shanghai')))
    by_date = defaultdict(list)
    for row in monitor:
        status, message, actual, threshold = row['result']
        row['status'] = 'pass' if status in ('pass', 'ok', 'success') else (
            'fail' if status in ('fail', 'error') else 'unknown')
        row['message'] = message
        row['actual_value'] = actual
        row['threshold'] = threshold
        by_date[row['biz_date']].append(row)
    daily = []
    for offset in range(days):
        biz_day = now.date() - timedelta(days=offset)
        biz_date = biz_day.isoformat()
        rows = by_date.get(biz_date, [])
        counts = Counter(row['status'] for row in rows)
        day = {'biz_date': biz_date, 'observed_rules': len(rows),
               'passed_rules': counts['pass'], 'failed_rules': counts['fail'],
               'unknown_rules': counts['unknown'], 'coverage': None,
               'pass_rate': None, 'schedule_verified': verified}
        if verified:
            observed = {(row['workflow_name'], row['rule_name']): row for row in rows}
            due_keys = set()
            for rule in schedule['rules']:
                if not rule.get('enabled', True):
                    continue
                if rule.get('valid_from') and biz_date < rule['valid_from']:
                    continue
                if rule.get('valid_to') and biz_date > rule['valid_to']:
                    continue
                key = (rule['workflow'], rule['rule'])
                due_at = time.fromisoformat(rule['due_after'])
                is_due = biz_day < now.date() or now.time() >= due_at
                if is_due:
                    due_keys.add(key)
                if key not in observed:
                    monitor.append({'biz_date': biz_date, 'workflow_name': key[0],
                                    'rule_name': key[1],
                                    'status': 'no_result' if is_due else 'pending',
                                    'last_run': None, 'run_count': 0, 'failed_runs': 0,
                                    'message': '已到期但无规则结果' if is_due else '尚未到执行时刻',
                                    'actual_value': None, 'threshold': None})
            due_count = len(due_keys)
            day['due_rules'] = due_count
            day['no_result_rules'] = len(due_keys - observed.keys())
            day['coverage'] = len(due_keys & observed.keys()) / due_count if due_count else None
            day['pass_rate'] = sum(observed[key]['status'] == 'pass'
                                  for key in due_keys & observed.keys()) / due_count if due_count else None
        daily.append(day)
    for row in dq:
        status, rule_name, error = row['result']
        row['status'] = status
        row['rule_name'] = rule_name
        row['err_msg'] = error
    return {'monitor': {'daily': daily, 'rules': monitor, 'schedule_status': schedule['verification_status']},
            'dq': {'latest_batch': dq[0]['check_batch'] if dq else None, 'rules': dq}}
