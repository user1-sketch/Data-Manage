import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert, Button, Card, Descriptions, Empty, Input, Layout, Menu, Select,
  Space, Spin, Statistic, Table, Tabs, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import ReactECharts from 'echarts-for-react';
import dagre from 'dagre';
import { Background, Controls, MiniMap, ReactFlow } from '@xyflow/react';
import type { Edge, Node } from '@xyflow/react';
import { Link, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { formatTime, get, post, tablePath } from './api';
import type { Column, DriftStatus, Finding, Run, TableRow } from './api';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;

type OverviewData = {
  table_count: number; column_count: number; by_layer: Record<string, number>;
  snapshot_status: string; generated_at: string; clickhouse_sampled_at?: string | null;
  drift_status: DriftStatus; drift_count: number | null;
};
type ListData<T> = { total: number; page: number; size: number; items: T[] };
type LineageData = {
  nodes: { id: string; layer: string }[];
  edges: { from: string; to: string; source_type: string; evidence?: string }[];
  available_types: string[];
};
type DriftData = { run_status: DriftStatus; run?: Run; findings: Finding[]; last_success_at?: string | null };
type MonitorRule = {
  biz_date: string; workflow_name: string; rule_name: string; status: string;
  last_run: string; message: string; actual_value: string; threshold: string;
  run_count: number; failed_runs: number;
};
type QualityData = {
  monitor: {
    daily: { biz_date: string; observed_rules: number; passed_rules: number;
      failed_rules: number; unknown_rules: number; coverage: number | null;
      pass_rate: number | null; due_rules?: number }[];
    rules: MonitorRule[]; schedule_status: string;
  };
  dq: { latest_batch: string | null; rules: {
    check_batch: string; task_name: string; run_group: string; rule_code: string;
    dimension: string; status: string; last_checked: string; err_msg: string;
  }[] };
};

function ErrorBox({ error }: { error: unknown }) {
  return <Alert type="error" showIcon message="加载失败" description={String(error)} />;
}

function LoadState({ loading, error, children }: { loading: boolean; error: unknown; children: React.ReactNode }) {
  if (loading) return <div className="center"><Spin size="large" /></div>;
  if (error) return <ErrorBox error={error} />;
  return <>{children}</>;
}

function StatusTag({ status }: { status: string }) {
  const colors: Record<string, string> = {
    fresh: 'green', success: 'green', pass: 'green', ok: 'green',
    fail: 'red', error: 'red', stale: 'orange', partial: 'orange',
    started: 'blue', pending: 'blue', no_result: 'orange',
    timed_out: 'red', unavailable: 'red', never_run: 'default', unknown: 'default',
  };
  const labels: Record<string, string> = {
    fresh: '新鲜', stale: '已过期', success: '成功', pass: '通过', ok: '通过',
    fail: '失败', error: '失败', partial: '部分完成', started: '运行中',
    pending: '待执行', no_result: '无结果', timed_out: '超时',
    unavailable: '不可用', never_run: '从未运行', unknown: '未知',
  };
  return <Tag color={colors[status] || 'default'}>{labels[status] || status}</Tag>;
}

function PageTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return <div className="page-title"><Title level={2}>{title}</Title>{subtitle && <Text type="secondary">{subtitle}</Text>}</div>;
}

function Overview() {
  const query = useQuery({ queryKey: ['overview'], queryFn: () => get<OverviewData>('/api/overview') });
  const data = query.data;
  const layers = data ? Object.entries(data.by_layer).sort((a, b) => b[1] - a[1]) : [];
  return <>
    <PageTitle title="总览" subtitle="资产、快照和最新检测状态" />
    <LoadState loading={query.isLoading} error={query.error}>
      {data && <>
        {data.drift_status !== 'success' && <Alert showIcon type="warning" message="漂移检测尚无可信的最新结果" description={`当前状态：${data.drift_status}。旧结果不会计为 0 漂移。`} />}
        <div className="stat-grid">
          <Card><Statistic title="纳管表" value={data.table_count} /></Card>
          <Card><Statistic title="声明列" value={data.column_count} /></Card>
          <Card><Statistic title="最新漂移项" value={data.drift_count ?? '—'} /></Card>
          <Card><Statistic title="快照状态" value={data.snapshot_status === 'fresh' ? '新鲜' : '过期'} /></Card>
        </div>
        <div className="two-columns">
          <Card title="分层分布">
            <ReactECharts style={{ height: 320 }} option={{
              tooltip: { trigger: 'item' },
              legend: { bottom: 0 },
              series: [{ type: 'pie', radius: ['42%', '68%'],
                data: layers.map(([name, value]) => ({ name, value })) }],
            }} />
          </Card>
          <Card title="数据时间">
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="元数据编译">{formatTime(data.generated_at)}</Descriptions.Item>
              <Descriptions.Item label="ClickHouse 采样">{formatTime(data.clickhouse_sampled_at)}</Descriptions.Item>
              <Descriptions.Item label="漂移批次"><StatusTag status={data.drift_status} /></Descriptions.Item>
            </Descriptions>
            <div className="card-action"><Link to="/drift">查看漂移中心 →</Link></div>
          </Card>
        </div>
      </>}
    </LoadState>
  </>;
}

function Catalog() {
  const [search, setSearch] = useState('');
  const [layer, setLayer] = useState<string>();
  const [db, setDb] = useState<string>();
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), size: '50' });
  if (search) params.set('q', search);
  if (layer) params.set('layer', layer);
  if (db) params.set('db', db);
  const query = useQuery({ queryKey: ['tables', search, layer, db, page],
    queryFn: () => get<ListData<TableRow>>(`/api/tables?${params}`) });
  const all = useQuery({ queryKey: ['tables', 'filters'],
    queryFn: () => get<ListData<TableRow>>('/api/tables?size=200') });
  const layers = [...new Set((all.data?.items || []).map(r => r.layer))].sort();
  const dbs = [...new Set((all.data?.items || []).map(r => r.db))].sort();
  const columns: ColumnsType<TableRow> = [
    { title: '表', dataIndex: 'name', render: (_, row) => <Link to={tablePath(row)}>{row.name}</Link> },
    { title: '数据库', dataIndex: 'db', width: 170 },
    { title: '层', dataIndex: 'layer', width: 95, render: (value: string) => <Tag>{value}</Tag> },
    { title: '引擎', dataIndex: 'engine', width: 180, render: (value: string | null) => value || '未采样' },
    { title: '用途', dataIndex: 'purpose', ellipsis: true },
    { title: '状态', width: 125, render: (_, row) => row.not_in_live ? <Tag color="orange">上次采样未见</Tag> : row.columns_incomplete ? <Tag color="orange">列不完整</Tag> : <Tag color="green">已声明</Tag> },
  ];
  return <>
    <PageTitle title="资产目录" subtitle="浏览 catalog 声明的表与 detail 列字典" />
    <Card>
      <Space wrap className="filter-bar">
        <Input.Search allowClear placeholder="搜索表名或用途" value={search} onChange={e => { setSearch(e.target.value); setPage(1); }} style={{ width: 280 }} />
        <Select allowClear placeholder="分层" value={layer} options={layers.map(value => ({ value }))} onChange={value => { setLayer(value); setPage(1); }} style={{ width: 140 }} />
        <Select allowClear placeholder="数据库" value={db} options={dbs.map(value => ({ value }))} onChange={value => { setDb(value); setPage(1); }} style={{ width: 190 }} />
      </Space>
      <LoadState loading={query.isLoading} error={query.error}>
        <Table rowKey="id" columns={columns} dataSource={query.data?.items || []}
          scroll={{ x: 920 }} pagination={{ current: page, pageSize: 50, total: query.data?.total || 0,
            showTotal: total => `共 ${total} 张表`, onChange: setPage }} />
      </LoadState>
    </Card>
  </>;
}

function TableDetail() {
  const { db, name } = useParams();
  const query = useQuery({ queryKey: ['table', db, name],
    queryFn: () => get<TableRow>(`/api/tables/${encodeURIComponent(db || '')}/${encodeURIComponent(name || '')}`) });
  const row = query.data;
  const columns: ColumnsType<Column> = [
    { title: '序号', dataIndex: 'position', width: 75 },
    { title: '列名', dataIndex: 'name', width: 250, render: (value: string, col) => <Space>{value}{col.is_key_field && <Tag color="blue">业务关键字段</Tag>}</Space> },
    { title: '类型', dataIndex: 'type', width: 190 },
    { title: '说明', dataIndex: 'desc', render: (value: string | null) => value || <Text type="secondary">未说明</Text> },
  ];
  return <>
    <PageTitle title={row?.id || '表详情'} subtitle="字段级来源和声明血缘" />
    <LoadState loading={query.isLoading} error={query.error}>
      {row && <>
        {row.not_in_live && <Alert showIcon type="warning" message="最近一次成功活库采样未找到该表；请结合采样时间核实" />}
        <Card title="表信息">
          <Descriptions bordered column={{ xs: 1, md: 2 }} size="small">
            <Descriptions.Item label="分层">{row.layer} <Tag>catalog</Tag></Descriptions.Item>
            <Descriptions.Item label="引擎">{row.engine || '未采样'} <Tag>ClickHouse</Tag></Descriptions.Item>
            <Descriptions.Item label="分区">{row.partition || '—'} <Tag>ClickHouse</Tag></Descriptions.Item>
            <Descriptions.Item label="排序键">{row.order_by || '—'} <Tag>ClickHouse</Tag></Descriptions.Item>
            <Descriptions.Item label="粒度">{row.grain || '—'} <Tag>catalog</Tag></Descriptions.Item>
            <Descriptions.Item label="声明唯一键">{row.unique_key || '—'} <Tag>catalog</Tag></Descriptions.Item>
            <Descriptions.Item label="版本列">{row.version_column || '—'} <Tag>catalog</Tag></Descriptions.Item>
            <Descriptions.Item label="用途">{row.purpose || '—'} <Tag>catalog</Tag></Descriptions.Item>
          </Descriptions>
        </Card>
        <Card title={`列字典（${row.columns.length}）`} extra={<Tag>detail</Tag>}>
          {row.columns_incomplete && <Alert showIcon type="warning" message="detail 中缺少此表的列字典" />}
          <Table rowKey="name" columns={columns} dataSource={row.columns} size="small" scroll={{ x: 850 }} pagination={{ pageSize: 50 }} />
        </Card>
        <div className="two-columns">
          <Card title="上游（声明血缘）">{row.upstream?.length ? row.upstream.map(x => <Tag key={x}>{x}</Tag>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}</Card>
          <Card title="下游（声明血缘）">{row.downstream?.length ? row.downstream.map(x => <Tag key={x}>{x}</Tag>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}</Card>
        </div>
        <div className="card-action"><Link to={`/lineage?root=${encodeURIComponent(row.id)}`}>查看血缘子图 →</Link></div>
      </>}
    </LoadState>
  </>;
}

function Lineage() {
  const navigate = useNavigate();
  const location = useLocation();
  const root = new URLSearchParams(location.search).get('root');
  const path = root ? `/api/lineage?root=${encodeURIComponent(root)}&up=2&down=2` : '/api/lineage/full';
  const query = useQuery({ queryKey: ['lineage', root], queryFn: () => get<LineageData>(path) });
  const layout = useMemo(() => {
    const graph = query.data;
    if (!graph) return { nodes: [] as Node[], edges: [] as Edge[] };
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: 'LR', ranksep: 105, nodesep: 28 });
    graph.nodes.forEach(node => g.setNode(node.id, { width: 220, height: 52 }));
    graph.edges.forEach(edge => g.setEdge(edge.from, edge.to));
    dagre.layout(g);
    const nodes: Node[] = graph.nodes.map(node => {
      const point = g.node(node.id);
      return { id: node.id, data: { label: `${node.layer} · ${node.id}`, layer: node.layer },
        position: { x: point.x - 110, y: point.y - 26 },
        style: { width: 220, border: '1px solid #cad7e7', borderRadius: 8,
          background: node.layer === 'EXTERNAL' ? '#f3f4f6' : '#ffffff',
          fontSize: 12, padding: 9, boxShadow: '0 2px 8px #1b305014' } };
    });
    const edges: Edge[] = graph.edges.map((edge, index) => ({
      id: `${edge.from}|${edge.to}|${index}`, source: edge.from, target: edge.to,
      animated: false, label: edge.source_type, labelStyle: { fontSize: 10 },
      style: { stroke: '#91a8c4' },
    }));
    return { nodes, edges };
  }, [query.data]);
  return <>
    <PageTitle title="血缘图" subtitle="当前仅显示 catalog 人工声明的血缘；点击节点可查看表详情" />
    {root && <div className="filter-bar"><Button onClick={() => navigate('/lineage')}>显示全图</Button> <Tag>{root}</Tag></div>}
    <LoadState loading={query.isLoading} error={query.error}>
      {query.data && <Card extra={<Space>血缘来源：{query.data.available_types.map(x => <Tag color="blue" key={x}>{x}</Tag>)}</Space>}>
        <div className="graph-canvas">
          <ReactFlow nodes={layout.nodes} edges={layout.edges} fitView minZoom={0.1}
            onNodeClick={(_, node) => {
              if (node.data.layer === 'EXTERNAL') return;
              const split = node.id.indexOf('.');
              if (split > 0) navigate(tablePath({ db: node.id.slice(0, split), name: node.id.slice(split + 1) }));
            }}>
            <Background /><Controls /><MiniMap pannable zoomable />
          </ReactFlow>
        </div>
      </Card>}
    </LoadState>
  </>;
}

function Drift() {
  const client = useQueryClient();
  const [layer, setLayer] = useState('all');
  const latest = useQuery({ queryKey: ['drift', 'latest'], queryFn: () => get<DriftData>('/api/drift/latest'), refetchInterval: 30_000 });
  const runs = useQuery({ queryKey: ['drift', 'runs'], queryFn: () => get<Run[]>('/api/drift/runs?days=30') });
  const refresh = useMutation({ mutationFn: () => post<DriftData>('/api/drift/refresh'),
    onSettled: () => { void client.invalidateQueries({ queryKey: ['drift'] }); void client.invalidateQueries({ queryKey: ['overview'] }); } });
  const findingColumns: ColumnsType<Finding> = [
    { title: '级别', dataIndex: 'severity', width: 86, render: (v: string) => <Tag color={v === 'error' ? 'red' : v === 'warn' ? 'orange' : 'blue'}>{v}</Tag> },
    { title: '检测项', dataIndex: 'check_item', width: 190 },
    { title: '表', width: 285, render: (_, r) => <Link to={`/table/${encodeURIComponent(r.database)}/${encodeURIComponent(r.table_name)}`}>{r.database}.{r.table_name}</Link> },
    { title: '列/属性', dataIndex: 'column_name', width: 175 },
    { title: '声明/权威值', dataIndex: 'expected', ellipsis: true },
    { title: '实况/对照值', dataIndex: 'actual', ellipsis: true },
    { title: '建议', dataIndex: 'fix_action', width: 130 },
  ];
  const filtered = (latest.data?.findings || []).filter(r => layer === 'all' || r.layer === layer);
  return <>
    <PageTitle title="漂移中心" subtitle="按批次记录执行状态；失败和缺跑均不显示为零漂移" />
    <Space className="filter-bar"><Button type="primary" loading={refresh.isPending} onClick={() => refresh.mutate()}>手动检测</Button><Text type="secondary">检测会写入独立的漂移结果表</Text></Space>
    {refresh.error && <ErrorBox error={refresh.error} />}
    <LoadState loading={latest.isLoading} error={latest.error}>
      {latest.data && <>
        {latest.data.run_status !== 'success' && <Alert showIcon type="warning" message="最近批次不可作为当前漂移结论" description={`状态：${latest.data.run_status}；最近成功：${formatTime(latest.data.last_success_at)}`} />}
        <Card title="最近检测批次">
          {latest.data.run ? <Descriptions bordered size="small" column={{ xs: 1, md: 3 }}>
            <Descriptions.Item label="状态"><StatusTag status={latest.data.run_status} /></Descriptions.Item>
            <Descriptions.Item label="开始时间">{formatTime(latest.data.run.run_time)}</Descriptions.Item>
            <Descriptions.Item label="触发方式">{latest.data.run.trigger}</Descriptions.Item>
            <Descriptions.Item label="覆盖声明表">{latest.data.run.tables_checked}</Descriptions.Item>
            <Descriptions.Item label="活库对象">{latest.data.run.tables_live}</Descriptions.Item>
            <Descriptions.Item label="明细">{latest.data.run_status === 'success' ? latest.data.run.details_count : '未验证'}</Descriptions.Item>
            <Descriptions.Item label="错误 / 警告 / 提示">{latest.data.run.err_cnt} / {latest.data.run.warn_cnt} / {latest.data.run.info_cnt}</Descriptions.Item>
            {latest.data.run.err_message && <Descriptions.Item label="错误摘要">{latest.data.run.err_message}</Descriptions.Item>}
          </Descriptions> : <Empty description="暂无批次" />}
        </Card>
        <Card title="漂移明细" extra={<Select value={layer} onChange={setLayer} style={{ width: 130 }} options={['all', 'L1', 'L2', 'L3'].map(value => ({ value, label: value === 'all' ? '全部层级' : value }))} />}>
          {latest.data.run_status === 'success' ? <Table rowKey={r => `${r.layer}|${r.check_item}|${r.database}|${r.table_name}|${r.column_name}`}
            columns={findingColumns} dataSource={filtered} scroll={{ x: 1250 }} pagination={{ pageSize: 20 }} /> :
            <Empty description="最近批次未成功，暂无可信明细" />}
        </Card>
      </>}
    </LoadState>
    <Card title="近 30 天批次记录">
      {runs.error ? <ErrorBox error={runs.error} /> : <Table rowKey="check_id" loading={runs.isLoading} dataSource={runs.data || []} size="small" pagination={{ pageSize: 10 }}
        columns={[{ title: '时间', dataIndex: 'run_time', render: formatTime },
          { title: '状态', dataIndex: 'status', render: (v: string) => <StatusTag status={v} /> },
          { title: '触发', dataIndex: 'trigger' }, { title: '覆盖表', dataIndex: 'tables_checked' },
          { title: '错误', dataIndex: 'err_cnt' }, { title: '警告', dataIndex: 'warn_cnt' },
          { title: '提示', dataIndex: 'info_cnt' }]} />}
    </Card>
  </>;
}

function Quality() {
  const [days, setDays] = useState(7);
  const query = useQuery({ queryKey: ['quality', days], queryFn: () => get<QualityData>(`/api/quality/summary?days=${days}`) });
  const monitorColumns: ColumnsType<MonitorRule> = [
    { title: '业务日', dataIndex: 'biz_date', width: 120 },
    { title: '工作流', dataIndex: 'workflow_name', width: 160 },
    { title: '规则', dataIndex: 'rule_name', width: 260 },
    { title: '最新状态', dataIndex: 'status', width: 110, render: (v: string) => <StatusTag status={v} /> },
    { title: '最近运行', dataIndex: 'last_run', width: 185, render: formatTime },
    { title: '运行次数', dataIndex: 'run_count', width: 95 },
    { title: '历史失败运行', dataIndex: 'failed_runs', width: 110 },
    { title: '说明', dataIndex: 'message', ellipsis: true },
  ];
  const daily = query.data?.monitor.daily || [];
  return <>
    <PageTitle title="质量监控" subtitle="monitor 按业务日、工作流和规则取最新结果；DQ 按批次展示" />
    <div className="filter-bar"><Select value={days} onChange={setDays} options={[3, 7, 14, 30].map(value => ({ value, label: `近 ${value} 天` }))} /></div>
    <LoadState loading={query.isLoading} error={query.error}>
      {query.data && <>
        {query.data.monitor.schedule_status !== 'verified' && <Alert showIcon type="warning" message="预期规则时刻尚未核实" description="当前只展示已观测规则。覆盖率与通过率暂不计算，不能据此判断全部规则已通过。" />}
        <Card title="每日观测结果">
          <ReactECharts style={{ height: 270 }} option={{
            tooltip: { trigger: 'axis' }, legend: { data: ['通过', '失败', '未知'] },
            xAxis: { type: 'category', data: [...daily].reverse().map(r => r.biz_date) },
            yAxis: { type: 'value', minInterval: 1 },
            series: [
              { name: '通过', type: 'bar', stack: 'status', data: [...daily].reverse().map(r => r.passed_rules) },
              { name: '失败', type: 'bar', stack: 'status', data: [...daily].reverse().map(r => r.failed_rules) },
              { name: '未知', type: 'bar', stack: 'status', data: [...daily].reverse().map(r => r.unknown_rules) },
            ],
          }} />
          <Table rowKey="biz_date" size="small" pagination={false} dataSource={daily} scroll={{ x: 850 }}
            columns={[{ title: '业务日', dataIndex: 'biz_date' }, { title: '已观测规则', dataIndex: 'observed_rules' },
              { title: '通过', dataIndex: 'passed_rules' }, { title: '失败', dataIndex: 'failed_rules' },
              { title: '未知', dataIndex: 'unknown_rules' },
              { title: '覆盖率', dataIndex: 'coverage', render: (v: number | null) => v == null ? '待核实' : `${(v * 100).toFixed(1)}%` },
              { title: '通过率', dataIndex: 'pass_rate', render: (v: number | null) => v == null ? '待核实' : `${(v * 100).toFixed(1)}%` }]} />
        </Card>
        <Card title="Monitor 规则最新结果">
          <Table rowKey={r => `${r.biz_date}|${r.workflow_name}|${r.rule_name}`} columns={monitorColumns}
            dataSource={query.data.monitor.rules} scroll={{ x: 1220 }} pagination={{ pageSize: 20 }} />
        </Card>
        <Card title={`DQ 批次结果 · 最新批次 ${query.data.dq.latest_batch || '暂无'}`}>
          <Table rowKey={r => `${r.check_batch}|${r.task_name}|${r.run_group}|${r.rule_code}|${r.dimension}`}
            dataSource={query.data.dq.rules} scroll={{ x: 980 }} pagination={{ pageSize: 20 }}
            columns={[{ title: '批次', dataIndex: 'check_batch', width: 180 },
              { title: '任务', dataIndex: 'task_name', width: 155 },
              { title: '规则', dataIndex: 'rule_code', width: 165 },
              { title: '维度', dataIndex: 'dimension', width: 145 },
              { title: '状态', dataIndex: 'status', width: 95, render: (v: string) => <StatusTag status={v} /> },
              { title: '检查时间', dataIndex: 'last_checked', width: 180, render: formatTime },
              { title: '错误', dataIndex: 'err_msg', ellipsis: true }]} />
        </Card>
      </>}
    </LoadState>
  </>;
}

function MetaPanel({ kind }: { kind: string }) {
  const query = useQuery({ queryKey: ['meta', kind], queryFn: () => get<unknown>(`/api/meta/${kind}`) });
  return <LoadState loading={query.isLoading} error={query.error}>
    <pre className="json-panel">{JSON.stringify(query.data, null, 2)}</pre>
  </LoadState>;
}

function Meta() {
  return <>
    <PageTitle title="语义与编译诊断" subtitle="查看指标、术语、规则，以及每个字段的权威来源和冲突" />
    <Card><Tabs items={[
      { key: 'metrics', label: '指标', children: <MetaPanel kind="metrics" /> },
      { key: 'glossary', label: '术语', children: <MetaPanel kind="glossary" /> },
      { key: 'rules', label: '强制规则', children: <MetaPanel kind="rules" /> },
      { key: 'known-issues', label: '已知问题', children: <MetaPanel kind="known-issues" /> },
      { key: 'provenance', label: '来源与冲突', children: <MetaPanel kind="provenance" /> },
    ]} /></Card>
  </>;
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const health = useQuery({ queryKey: ['health'], queryFn: () => get<{
    snapshot_status: string; last_error?: string | null; drift?: { run_status: DriftStatus };
  }>('/api/health'), refetchInterval: 60_000 });
  const selected = location.pathname.startsWith('/table/') ? '/catalog' :
    ['/', '/catalog', '/lineage', '/drift', '/quality', '/meta'].find(x => x === location.pathname) || '/';
  return <Layout className="shell">
    <Sider theme="light" breakpoint="lg" collapsedWidth="0" width={224} className="sidebar">
      <div className="brand"><span className="brand-mark">数</span><span>数据治理平台</span></div>
      <Menu mode="inline" selectedKeys={[selected]} onClick={({ key }) => navigate(key)}
        items={[
          { key: '/', label: '总览' }, { key: '/catalog', label: '资产目录' },
          { key: '/lineage', label: '血缘图' }, { key: '/drift', label: '漂移中心' },
          { key: '/quality', label: '质量监控' }, { key: '/meta', label: '语义与诊断' },
        ]} />
      <div className="sidebar-foot">本机一期 · 声明元数据</div>
    </Sider>
    <Layout>
      <Header className="topbar"><Text strong>数据 Schema 可视化治理</Text><Space>
        {health.data && <><StatusTag status={health.data.snapshot_status} />
          <StatusTag status={health.data.drift?.run_status || 'unavailable'} /></>}
      </Space></Header>
      <Content className="content">
        {health.data?.snapshot_status === 'stale' && <Alert showIcon type="warning" message="元数据快照已过期" description={health.data.last_error || 'ClickHouse 采样未更新'} />}
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/catalog" element={<Catalog />} />
          <Route path="/table/:db/:name" element={<TableDetail />} />
          <Route path="/lineage" element={<Lineage />} />
          <Route path="/drift" element={<Drift />} />
          <Route path="/quality" element={<Quality />} />
          <Route path="/meta" element={<Meta />} />
          <Route path="*" element={<Empty description="页面不存在" />} />
        </Routes>
      </Content>
    </Layout>
  </Layout>;
}
