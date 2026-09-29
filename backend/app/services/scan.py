import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.core.ck import ClickHouseClient, quote_ident
from app.core.config import BACKEND_ROOT
from app.services.metadata import read_yaml, table_map, utc_now, walk_tables


def scope_config() -> dict:
    return read_yaml(BACKEND_ROOT / 'scope.yaml')


def declared_tables(catalog: dict) -> dict:
    return {f"{db}.{row['name']}": {'layer': layer, 'row': row}
            for layer, db, row in walk_tables(catalog)}


def scan_live(client: ClickHouseClient, catalog: dict, extra_databases: list[str] | None = None) -> dict:
    declared = declared_tables(catalog)
    databases = sorted({item.split('.', 1)[0] for item in declared} | set(extra_databases or []))
    if not databases:
        raise ValueError('Empty scan scope')
    for db in databases:
        quote_ident(db)
    literals = ','.join(f"'{db}'" for db in databases)
    existing = {row['name'] for row in client.query(
        f'SELECT name FROM system.databases WHERE name IN ({literals})')}
    missing = sorted(set(databases) - existing)
    if missing:
        raise RuntimeError(f'Declared databases unavailable: {missing}')
    tables = client.query(
        'SELECT database, name, engine, partition_key, sorting_key, comment '
        f'FROM system.tables WHERE database IN ({literals}) ORDER BY database, name', timeout=30)
    columns = client.query(
        'SELECT database, table, name, type, position, comment '
        f'FROM system.columns WHERE database IN ({literals}) ORDER BY database, table, position', timeout=40)
    table_map_live = {f"{row['database']}.{row['name']}": row for row in tables}
    column_map_live = {}
    for row in columns:
        column_map_live.setdefault(f"{row['database']}.{row['table']}", []).append(row)
    return {'snapshot_at': utc_now(), 'databases': databases,
            'database_status': {db: {'query_ok': True,
                                     'table_count': sum(t['database'] == db for t in tables),
                                     'column_count': sum(c['database'] == db for c in columns)}
                                for db in databases},
            'tables': table_map_live, 'columns': column_map_live}


def compare_baseline(catalog: dict, detail: dict, live: dict, scope: dict) -> dict:
    declared = declared_tables(catalog)
    detailed = table_map(detail)
    observed = live['tables']
    exclude_exact = set(scope.get('exclude_exact') or [])
    patterns = [re.compile(p) for p in (scope.get('exclude_name_patterns') or [])]
    def exclusion(table_id):
        if table_id in exclude_exact:
            return 'exclude_exact'
        name = table_id.split('.', 1)[1]
        for pattern in patterns:
            if pattern.search(name):
                return pattern.pattern
        return None

    declared_ids = set(declared)
    observed_ids = set(observed)
    missing_tables = sorted(declared_ids - observed_ids)
    candidates = []
    excluded = []
    for table_id in sorted(observed_ids - declared_ids):
        why = exclusion(table_id)
        (excluded if why else candidates).append({'table': table_id, 'reason': why} if why
                                                   else {'table': table_id})
    missing_columns = []
    unrecorded_columns = []
    type_diff = []
    physical_diff = []
    for table_id in sorted(declared_ids & observed_ids):
        if table_id not in detailed:
            continue
        cat = declared[table_id]['row']
        det = detailed[table_id][1]
        dc = {str(c['name']): c for c in det.get('columns') or [] if 'name' in c}
        lc = {str(c['name']): c for c in live['columns'].get(table_id, [])}
        for name in sorted(dc.keys() - lc.keys()):
            missing_columns.append({'table': table_id, 'column': name,
                                    'declared_type': dc[name].get('type')})
        for name in sorted(lc.keys() - dc.keys()):
            unrecorded_columns.append({'table': table_id, 'column': name,
                                       'observed_type': lc[name].get('type')})
        for name in sorted(dc.keys() & lc.keys()):
            expected = ''.join(str(dc[name].get('type', '')).split())
            actual = ''.join(str(lc[name].get('type', '')).split())
            if expected != actual:
                type_diff.append({'table': table_id, 'column': name,
                                  'declared_type': dc[name].get('type'),
                                  'observed_type': lc[name].get('type'),
                                  'nullable_difference': expected.removeprefix('Nullable(').removesuffix(')') == actual
                                  or actual.removeprefix('Nullable(').removesuffix(')') == expected})
        for design, actual in (('engine', 'engine'), ('partition', 'partition_key'),
                               ('order_by', 'sorting_key')):
            if design in cat and str(cat[design]).strip() != str(observed[table_id][actual]).strip():
                physical_diff.append({'table': table_id, 'field': design,
                                      'declared': cat[design], 'observed': observed[table_id][actual]})
    detail_only = sorted(set(detailed) - declared_ids)
    result = {
        'scanned_at': live['snapshot_at'], 'databases': live['database_status'],
        'scope': {'declared_databases': sorted({x.split('.', 1)[0] for x in declared}),
                  'scanned_databases': live['databases'], 'exclusions': scope},
        'counts': {'catalog_tables': len(declared), 'detail_tables': len(detailed),
                   'detail_columns': sum(len(row.get('columns') or []) for _, row in detailed.values()),
                   'live_objects': len(observed), 'declared_missing': len(missing_tables),
                   'candidates': len(candidates), 'excluded': len(excluded),
                   'columns_unrecorded': len(unrecorded_columns), 'columns_missing_live': len(missing_columns),
                   'type_differences': len(type_diff), 'physical_differences': len(physical_diff)},
        'declared_missing': missing_tables, 'candidates': candidates, 'excluded': excluded,
        'columns_unrecorded': unrecorded_columns, 'columns_missing_live': missing_columns,
        'type_differences': type_diff, 'physical_differences': physical_diff,
        'detail_only': detail_only,
    }
    return result


def baseline_markdown(report: dict) -> str:
    counts = report['counts']
    lines = [f"# Schema 扫描基线（{report['scanned_at']}）", '',
             '## 扫描库', '', '| 库 | 对象 | 列 | 状态 |', '|---|---:|---:|---|']
    for db, item in report['databases'].items():
        lines.append(f"| {db} | {item['table_count']} | {item['column_count']} | 成功 |")
    lines += ['', '## 计数', '', '| 指标 | 数量 |', '|---|---:|']
    lines += [f'| {k} | {v} |' for k, v in counts.items()]
    for title, key in [('已声明但活库无', 'declared_missing'), ('发现候选', 'candidates'),
                       ('明确排除', 'excluded'), ('活库有但 detail 未录列', 'columns_unrecorded'),
                       ('detail 有但活库无列', 'columns_missing_live'), ('类型差异', 'type_differences'),
                       ('物理属性待核实', 'physical_differences')]:
        lines += ['', f'## {title}', '']
        for item in report[key]:
            lines.append(f'- `{item}`')
    return '\n'.join(lines) + '\n'
