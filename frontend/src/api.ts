export type SnapshotState = 'fresh' | 'stale' | 'missing';
export type DriftStatus = 'success' | 'error' | 'partial' | 'started' | 'timed_out' | 'never_run' | 'unavailable';

export interface Column { name: string; type: string; desc?: string | null; position: number; is_key_field: boolean }
export interface TableRow {
  id: string; db: string; name: string; layer: string; purpose?: string | null;
  engine?: string | null; partition?: string | null; order_by?: string | null;
  grain?: string | null; unique_key?: string | null; version_column?: string | null;
  upstream?: string[] | null; downstream?: string[] | null;
  columns_incomplete: boolean; not_in_live: boolean; columns: Column[];
  provenance: Record<string, string>;
}
export interface Finding {
  layer: string; check_item: string; severity: string; database: string;
  table_name: string; column_name: string; authoritative: string;
  expected: string; actual: string; fix_action: string; message: string;
}
export interface Run {
  check_id: string; run_time: string; status: DriftStatus; trigger: string;
  details_count: number; tables_checked: number; tables_live: number;
  err_cnt: number; warn_cnt: number; info_cnt: number; err_message: string;
}

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json() as Promise<T>;
}

export async function post<T>(path: string): Promise<T> {
  const response = await fetch(path, { method: 'POST' });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json() as Promise<T>;
}

export function tablePath(row: Pick<TableRow, 'db' | 'name'>): string {
  return `/table/${encodeURIComponent(row.db)}/${encodeURIComponent(row.name)}`;
}

export function formatTime(value?: string | null): string {
  if (!value) return '暂无';
  const date = new Date(value.replace(' ', 'T'));
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
