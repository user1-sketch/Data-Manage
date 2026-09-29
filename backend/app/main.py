from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.ck import ClickHouseClient, ClickHouseError
from app.core.config import BACKEND_ROOT, get_settings
from app.services import drift, quality
from app.services.metadata import store


scheduler = BackgroundScheduler(timezone='Asia/Shanghai')


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.refresh_from_sources()
    scheduler.add_job(store.refresh_if_changed, 'interval', seconds=60,
                      max_instances=1, id='metadata_refresh', replace_existing=True)
    if get_settings().enable_scheduler:
        scheduler.add_job(drift.run_check, 'cron', hour=9, minute=30,
                          kwargs={'trigger': 'scheduled'}, max_instances=1,
                          id='daily_schema_drift', replace_existing=True)
    scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title='数据治理平台', lifespan=lifespan)


def snapshot():
    try:
        return store.get()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def ck_error(exc: Exception):
    raise HTTPException(503, str(exc)) from exc


@app.get('/api/health')
def health():
    state = dict(store.status)
    state['generated_at'] = store.snapshot.get('generated_at') if store.snapshot else None
    state['clickhouse_sampled_at'] = (store.snapshot or {}).get('sources', {}).get('clickhouse', {}).get('snapshot_at')
    try:
        state['clickhouse_connected'] = bool(ClickHouseClient(get_settings()).query('SELECT 1 AS ok'))
    except ClickHouseError:
        state['clickhouse_connected'] = False
    try:
        state['drift'] = drift.latest_run()
        state['drift'].pop('findings', None)
    except ClickHouseError:
        state['drift'] = {'run_status': 'unavailable'}
    return state


@app.get('/api/overview')
def overview():
    data = snapshot()
    by_layer = {}
    for table in data['tables']:
        by_layer[table['layer']] = by_layer.get(table['layer'], 0) + 1
    try:
        latest = drift.latest_run()
    except ClickHouseError:
        latest = {'run_status': 'unavailable', 'findings': []}
    return {'table_count': data['table_count'], 'column_count': data['column_count'],
            'by_layer': by_layer, 'snapshot_status': store.status['snapshot_status'],
            'generated_at': data['generated_at'], 'clickhouse_sampled_at': data['sources']['clickhouse']['snapshot_at'],
            'drift_status': latest['run_status'],
            'drift_count': len(latest['findings']) if latest['run_status'] == 'success' else None}


@app.get('/api/tables')
def tables(layer: str | None = None, db: str | None = None, q: str | None = None,
           incomplete: bool | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    rows = snapshot()['tables']
    if layer:
        rows = [r for r in rows if r['layer'].casefold() == layer.casefold()]
    if db:
        rows = [r for r in rows if r['db'].casefold() == db.casefold()]
    if q:
        rows = [r for r in rows if q.casefold() in (r['id'] + ' ' + str(r.get('purpose'))).casefold()]
    if incomplete is not None:
        rows = [r for r in rows if r['columns_incomplete'] == incomplete]
    return {'total': len(rows), 'page': page, 'size': size,
            'items': [{k: v for k, v in r.items() if k != 'columns'} for r in rows[(page-1)*size:page*size]]}


@app.get('/api/tables/{db}/{name}')
def table_detail(db: str, name: str):
    row = next((r for r in snapshot()['tables'] if r['db'] == db and r['name'] == name), None)
    if row is None:
        raise HTTPException(404, 'Table not found')
    return row


@app.get('/api/search')
def search(q: str = Query(min_length=1), scope: str = 'all', limit: int = Query(50, ge=1, le=200)):
    data = snapshot()
    needle = q.casefold()
    results = []
    if scope in ('all', 'table'):
        for table in data['tables']:
            if needle in (table['id'] + ' ' + str(table.get('purpose'))).casefold():
                results.append({'scope': 'table', 'table': table['id'], 'label': table['name']})
    if scope in ('all', 'column'):
        for table in data['tables']:
            for col in table['columns']:
                if needle in (col['name'] + ' ' + col['type'] + ' ' + str(col.get('desc'))).casefold():
                    results.append({'scope': 'column', 'table': table['id'], 'label': col['name'], 'type': col['type']})
    if scope in ('all', 'metric', 'glossary'):
        for kind in ('metrics', 'glossary'):
            if scope not in ('all', kind.removesuffix('s')):
                continue
            for key, value in data[kind].items():
                if needle in (key + ' ' + str(value)).casefold():
                    results.append({'scope': kind.removesuffix('s'), 'label': key, 'value': value})
    return {'total': len(results), 'items': results[:limit]}


@app.get('/api/lineage/full')
def full_lineage():
    return snapshot()['lineage']


@app.get('/api/lineage')
def lineage(root: str, up: int = Query(2, ge=0, le=10), down: int = Query(2, ge=0, le=10)):
    graph = snapshot()['lineage']
    selected = {root}
    for direction, depth in (('up', up), ('down', down)):
        frontier = {root}
        for _ in range(depth):
            adjacent = {e['from'] if direction == 'up' else e['to'] for e in graph['edges']
                        if (e['to'] if direction == 'up' else e['from']) in frontier}
            selected.update(adjacent)
            frontier = adjacent
    return {'nodes': [n for n in graph['nodes'] if n['id'] in selected],
            'edges': [e for e in graph['edges'] if e['from'] in selected and e['to'] in selected],
            'available_types': graph['available_types']}


@app.get('/api/meta/{kind}')
def meta(kind: str):
    data = snapshot()
    lookup = {'metrics': 'metrics', 'glossary': 'glossary', 'rules': 'mandatory_rules',
              'known-issues': 'known_issues', 'provenance': None}
    if kind not in lookup:
        raise HTTPException(404, 'Unknown metadata kind')
    if kind == 'provenance':
        return {'route_table_version': data['route_table_version'], 'routes': data['route_table'],
                'shadow_conflicts': data['shadow_conflicts'],
                'missing_authoritative': data['missing_authoritative']}
    return data[lookup[kind]]


@app.get('/api/drift/latest')
def drift_latest():
    try:
        return drift.latest_run()
    except ClickHouseError as exc:
        ck_error(exc)


@app.get('/api/drift/runs')
def drift_runs(days: int = Query(30, ge=1, le=90)):
    try:
        return drift.list_runs(days)
    except ClickHouseError as exc:
        ck_error(exc)


@app.get('/api/drift/history')
def drift_history(days: int = Query(30, ge=1, le=90)):
    try:
        return [{'run_time': r['run_time'], 'status': r['status'],
                 'count': r['err_cnt'] + r['warn_cnt'] + r['info_cnt'] if r['status'] == 'success' else None}
                for r in drift.list_runs(days)]
    except ClickHouseError as exc:
        ck_error(exc)


@app.post('/api/drift/refresh')
def drift_refresh():
    try:
        result = drift.run_check('manual')
        if result['run_status'] == 'already_running':
            raise HTTPException(409, 'Drift check already running')
        return result
    except ClickHouseError as exc:
        ck_error(exc)


@app.get('/api/quality/summary')
def quality_summary(days: int = Query(7, ge=1, le=90)):
    try:
        return quality.summary(days)
    except ClickHouseError as exc:
        ck_error(exc)


@app.get('/api/quality/rules')
def quality_rules():
    return quality_summary(7)['monitor']['rules']


@app.get('/api/quality/failures')
def quality_failures(days: int = Query(3, ge=1, le=90), source: str = 'monitor'):
    result = quality_summary(days)
    if source not in ('monitor', 'dq'):
        raise HTTPException(400, 'Invalid source')
    return [r for r in result[source]['rules'] if r['status'] in ('fail', 'error')]


DIST = BACKEND_ROOT.parent / 'frontend' / 'dist'
if DIST.exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def frontend(path: str):
        requested = (DIST / path).resolve()
        if requested.is_file() and DIST in requested.parents:
            return FileResponse(requested)
        return FileResponse(DIST / 'index.html')
