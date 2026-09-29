import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from threading import RLock

import yaml

from app.core.config import BACKEND_ROOT, get_settings


SOURCE_PATHS = {
    'catalog': 'AI_Schema_catalog_v1.yaml',
    'detail': 'AI_Schema_v4_20260814.yaml',
    'foundation': 'data_foundation/data_foundation.yaml',
}
SNAPSHOT_PATH = BACKEND_ROOT / 'var' / 'metadata.json'
STATUS_PATH = BACKEND_ROOT / 'var' / 'status.json'
TABLE_FIELDS = ('purpose', 'upstream', 'downstream', 'key_fields', 'grain',
                'unique_key', 'version_column', 'update', 'notes', 'status')


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def read_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected YAML mapping: {path.name}')
    return value


def walk_tables(source: dict):
    for layer, group in source.get('tables', {}).items():
        chunks = group.items() if isinstance(group, dict) else [('SmartData', group)]
        for default_db, rows in chunks:
            for row in rows:
                if not isinstance(row, dict) or not row.get('name'):
                    raise ValueError(f'Invalid table in layer {layer}')
                yield str(layer), str(row.get('database') or default_db), row


def table_map(source: dict) -> dict:
    result = {}
    for layer, db, row in walk_tables(source):
        key = f"{db}.{row['name']}"
        if key in result:
            raise ValueError(f'Duplicate table: {key}')
        result[key] = (layer, row)
    return result


def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default), encoding='utf-8')
    json.loads(temporary.read_text(encoding='utf-8'))
    os.replace(temporary, path)


def source_hashes(root: Path) -> dict[str, str]:
    paths = {**{name: root / rel for name, rel in SOURCE_PATHS.items()},
             'route_table': BACKEND_ROOT / 'route_table.yaml'}
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in paths.items()}


def compile_snapshot(root: Path, live: dict | None = None) -> dict:
    root = root.resolve()
    sources = {}
    parsed = {}
    for name, rel in SOURCE_PATHS.items():
        path = root / rel
        raw = path.read_bytes()
        parsed[name] = yaml.safe_load(raw)
        if not isinstance(parsed[name], dict):
            raise ValueError(f'Invalid source: {name}')
        sources[name] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}

    route_path = BACKEND_ROOT / 'route_table.yaml'
    route = read_yaml(route_path)
    sources['route_table'] = {'path': str(route_path),
                              'sha256': hashlib.sha256(route_path.read_bytes()).hexdigest()}
    if any(route['fields'].get(field) != 'catalog' for field in TABLE_FIELDS):
        raise ValueError('Route table omits catalog table field')
    catalog = table_map(parsed['catalog'])
    detail = table_map(parsed['detail'])
    foundation = parsed['foundation']
    live_tables = (live or {}).get('tables', {})
    live_columns = (live or {}).get('columns', {})
    missing = []
    conflicts = []
    tables = []
    names = {}
    for table_id in catalog:
        names.setdefault(table_id.split('.', 1)[1], []).append(table_id)
    for table_id, (layer, cat) in catalog.items():
        db, name = table_id.split('.', 1)
        det = detail.get(table_id, (None, {}))[1]
        physical = live_tables.get(table_id)
        row = {
            'id': table_id, 'db': db, 'name': name, 'layer': layer,
            'provenance': {'layer': 'catalog', 'columns': 'detail', 'type': 'detail',
                           'desc': 'detail', 'engine': 'clickhouse', 'partition': 'clickhouse',
                           'order_by': 'clickhouse', 'metrics': 'foundation'},
            'columns_incomplete': not bool(det.get('columns')),
            'not_in_live': live is not None and physical is None,
            'in_foundation': name in foundation.get('databases', {}).get(db, {}).get('tables', {}),
        }
        for field in TABLE_FIELDS:
            row[field] = cat.get(field)
            row['provenance'][field] = 'catalog'
            if field not in cat:
                missing.append({'table': table_id, 'field': field, 'source': 'catalog'})
            elif field in det and det[field] != cat[field]:
                conflicts.append({'table': table_id, 'field': field, 'authoritative': 'catalog',
                                  'authoritative_value': cat[field], 'shadow_source': 'detail',
                                  'shadow_value': det[field]})
        row['engine'] = physical.get('engine') if physical else None
        row['partition'] = physical.get('partition_key') if physical else None
        row['order_by'] = physical.get('sorting_key') if physical else None
        row['engine_declared'] = cat.get('engine')
        row['partition_declared'] = cat.get('partition')
        row['order_by_declared'] = cat.get('order_by')
        row['columns'] = []
        key_names = {str(x.get('name')) for x in (cat.get('key_fields') or []) if isinstance(x, dict)}
        seen_columns = set()
        for position, col in enumerate(det.get('columns') or [], 1):
            if 'name' not in col or 'type' not in col:
                missing.append({'table': table_id, 'field': f'column[{position}]', 'source': 'detail'})
                continue
            if col['name'] in seen_columns:
                raise ValueError(f"Duplicate column: {table_id}.{col['name']}")
            seen_columns.add(col['name'])
            row['columns'].append({'name': col['name'], 'type': col['type'],
                                   'desc': col.get('desc'), 'position': position,
                                   'is_key_field': col['name'] in key_names})
        row['metrics'] = [key for key, metric in foundation.get('metrics', {}).items()
                          if str(metric.get('table', '')) in (name, table_id)]
        tables.append(row)

    edges = {}
    external_nodes = set()
    def resolve(raw, current_db):
        raw = str(raw).strip()
        if '.' in raw and raw in catalog:
            return raw
        local = f'{current_db}.{raw}'
        if local in catalog:
            return local
        matches = names.get(raw, [])
        if len(matches) == 1:
            return matches[0]
        external_nodes.add(raw)
        return raw

    for table in tables:
        for direction, entries in (('upstream', table.get('upstream') or []),
                                   ('downstream', table.get('downstream') or [])):
            if not isinstance(entries, list):
                entries = [entries]
            for entry in entries:
                target = resolve(entry, table['db'])
                start, end = (target, table['id']) if direction == 'upstream' else (table['id'], target)
                key = (start, end)
                edges.setdefault(key, {'from': start, 'to': end, 'source_type': 'declared',
                                       'confidence': 1.0,
                                       'evidence': f"{SOURCE_PATHS['catalog']}#tables.{table['layer']}.{table['id']}.{direction}"})

    live_status = (live or {}).get('status', 'fresh' if live is not None else 'unavailable')
    source_status = {'snapshot_at': (live or {}).get('snapshot_at'), 'status': live_status}
    return {
        'generated_at': utc_now(), 'route_table_version': str(route['version']),
        'sources': {**sources, 'clickhouse': source_status},
        'snapshot_status': 'fresh' if live_status == 'fresh' else 'stale',
        'tables': tables, 'table_count': len(tables),
        'column_count': sum(len(t['columns']) for t in tables),
        'detail_table_count': len(detail),
        'missing_authoritative': missing, 'shadow_conflicts': conflicts,
        'lineage': {'nodes': [{'id': t['id'], 'layer': t['layer']} for t in tables] +
                             [{'id': x, 'layer': 'EXTERNAL'} for x in sorted(external_nodes)],
                    'edges': list(edges.values()), 'available_types': ['declared']},
        'metrics': foundation.get('metrics', {}), 'glossary': foundation.get('glossary', {}),
        'renames': [x for x in foundation.get('lineage', {}).get('pitfalls', [])
                    if '重命名' in str(x)],
        'mandatory_rules': parsed['catalog'].get('mandatory_rules', []),
        'workflows': parsed['catalog'].get('workflows', {}),
        'known_issues': parsed['catalog'].get('known_issues', []),
        'route_table': route['fields'],
    }


class MetadataStore:
    def __init__(self):
        self.lock = RLock()
        self.snapshot = None
        self.status = {'snapshot_status': 'missing', 'last_error': None}
        if SNAPSHOT_PATH.exists():
            self.snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))
            self.status = {'snapshot_status': self.snapshot['snapshot_status'], 'last_error': None}

    def refresh(self, live: dict | None = None) -> dict:
        with self.lock:
            try:
                if live is None and self.snapshot:
                    live = {'status': 'stale',
                            'snapshot_at': self.snapshot.get('sources', {}).get('clickhouse', {}).get('snapshot_at'),
                            'tables': {t['id']: {'engine': t['engine'], 'partition_key': t['partition'],
                                                 'sorting_key': t['order_by']}
                                       for t in self.snapshot['tables'] if t.get('engine') is not None}}
                snapshot = compile_snapshot(get_settings().schema_yaml_root, live)
                atomic_json(SNAPSHOT_PATH, snapshot)
                self.snapshot = snapshot
                self.status = {'snapshot_status': snapshot['snapshot_status'],
                               'last_success': snapshot['generated_at'], 'last_error': None}
            except Exception as exc:
                self.status = {'snapshot_status': 'stale', 'last_error': str(exc),
                               'last_success': self.snapshot.get('generated_at') if self.snapshot else None}
            atomic_json(STATUS_PATH, self.status)
            return self.status

    def refresh_from_sources(self) -> dict:
        """Recompile from source files and sample ClickHouse when credentials are available."""
        live = None
        try:
            from app.core.ck import ClickHouseClient
            from app.services.scan import scan_live, scope_config

            settings = get_settings()
            catalog = read_yaml(settings.schema_yaml_root / SOURCE_PATHS['catalog'])
            live = scan_live(ClickHouseClient(settings), catalog,
                             scope_config().get('discover_databases'))
            live['status'] = 'fresh'
        except Exception:
            # The last physical sample remains visible with stale provenance.
            pass
        return self.refresh(live)

    def refresh_if_changed(self) -> dict:
        """Called by the single process scheduler; a failed probe keeps the last snapshot."""
        try:
            current = source_hashes(get_settings().schema_yaml_root)
            previous = {name: item.get('sha256') for name, item in
                        (self.snapshot or {}).get('sources', {}).items()
                        if isinstance(item, dict)}
            sampled = (self.snapshot or {}).get('sources', {}).get('clickhouse', {}).get('snapshot_at')
            sample_age = (datetime.now().astimezone() - datetime.fromisoformat(sampled)).total_seconds() if sampled else float('inf')
            if (current == {name: previous.get(name) for name in current}
                    and sample_age < 900 and self.status['snapshot_status'] == 'fresh'):
                return self.status
        except Exception as exc:
            self.status = {'snapshot_status': 'stale', 'last_error': str(exc),
                           'last_success': self.snapshot.get('generated_at') if self.snapshot else None}
            atomic_json(STATUS_PATH, self.status)
            return self.status
        return self.refresh_from_sources()

    def get(self) -> dict:
        if self.snapshot is None:
            raise RuntimeError('Metadata snapshot unavailable')
        return self.snapshot


store = MetadataStore()
