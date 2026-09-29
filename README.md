# 数据治理平台

本机一期实现：读取 `D:\ds_ck` 的三份 YAML，编译表/列/指标/血缘快照；按配置扫描 ClickHouse，展示漂移批次和质量结果。平台不修改源 YAML 或业务表。源文件可能持续变化，表/列数量以 `/api/overview` 的当前快照及其编译时间为准。

## 本机启动

1. 在 `backend` 创建虚拟环境并安装 `requirements.txt`；在 `frontend` 执行 `npm install` 和 `npm run build`。Node 依赖会生成 `package-lock.json`，应随项目提交。
2. 从 `backend/.env.example` 复制为 `backend/.env`，填写 `SCHEMA_YAML_ROOT`。若需要活库采样和质量查询，填写 ClickHouse **只读账号**；若要启用漂移写入，再填写只能 INSERT 两张新表的**独立写账号**。不要把密码写入仓库。
3. 在项目根目录运行 `python start.py`，访问 `http://127.0.0.1:8787`。开发前端可在 `frontend` 运行 `npm run dev`，Vite 将 `/api` 代理至 8787。

本机服务固定绑定回环地址。没有 CK 凭据时目录仍可读取最近编译快照，页面会标为 `stale`，漂移与质量接口会显示不可用；这不是“0 漂移”或“全部通过”。

## 数据源和范围

- 元数据文件：`AI_Schema_catalog_v1.yaml`、`AI_Schema_v4_20260814.yaml`、`data_foundation/data_foundation.yaml`。字段权威见 `backend/route_table.yaml`。
- 声明表及库从 catalog 的结构化 `tables` 自动读取；额外发现库、排除项见 `backend/scope.yaml`。当前声明涉及 `SmartData`、`cs_selfop_ods`、`ods_selection`、`cs_selfop_dwd`、`cs_selfop_ads`。
- 只读基线任务：在 `backend` 运行 `.venv\Scripts\python.exe collect_baseline.py`。报告写入根目录 `baseline/`，该目录被 Git 忽略。历史 90 表/2326 列及旧漂移数字不作为验收口径。
- 只读质量查询验收：在 `backend` 运行 `.venv\Scripts\python.exe verify_quality_readonly.py`。可用 `--credentials-env <路径>` 指向已有 CK 凭据文件；脚本只输出行数和调度核实状态，不打印密码或业务明细。
- 手动离线编译：在 `backend` 运行 `.venv\Scripts\python.exe compile_metadata.py`。快照写入 `backend/var/`，该目录被 Git 忽略。服务启动后每分钟检查源哈希，并在源变化或活库采样超时后重编；失败保留旧快照并标记过期。

## 漂移结果表与账号

由部署账号审查并执行 `backend/sql/create_drift_tables.sql`，创建 `SmartData.meta_schema_drift_di` 和 `SmartData.meta_schema_drift_run`。运行时读账号只需读系统元数据、两张质量表及两张漂移表；写账号只需向两张漂移表 INSERT。运行时账号不应有 CREATE、ALTER、DELETE 或业务表写权限。

配置完成后调用 `POST /api/drift/refresh`；同一时刻只允许一个批次。批次先写 `started`，明细写入并回查一致后才写 `success`。失败、超时及无最近成功批次会在页面告警。确认手动检测及异常路径通过后，再在 `.env` 设置 `ENABLE_SCHEDULER=true`，每天北京时间 09:30 自动执行。

## 质量口径

Monitor 按业务日、工作流和规则取最新结果；历史失败运行次数单独展示。`backend/quality_schedule.yaml` 目前是从旧脚本和 catalog cron 快照整理的**候选清单**，状态为 `pending_ds_review`。在 DS 当前启停、执行时刻、节假日与容忍延迟核实前，API 不计算完整覆盖率或通过率。DQ 按批次聚合，`pass` 状态按小写处理。

## 常用检查

```powershell
cd backend
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m compileall -q .

cd ..\frontend
npm run build
```

`GET /api/health` 返回快照状态、活库连接和最近漂移批次；`GET /api/overview` 返回资产计数。`/api/tables`、`/api/search`、`/api/lineage`、`/api/drift/latest`、`/api/quality/summary` 等接口可在 `/docs` 查看。

## 故障处理

- **快照 `stale`**：检查 `SCHEMA_YAML_ROOT`、三个文件是否存在以及 CK 读账号连通性；查看 `/api/health` 的 `last_error`。旧快照仍可浏览，但物理属性仅代表上次采样时刻。
- **漂移接口 503**：检查两张结果表是否已创建、读账号权限和 CK 连接。未建表时不启用定时器。
- **手动刷新失败**：检查写账号 INSERT 权限与读账号回查权限；不能把失败解释为零漂移。`started` 超过十分钟未终止会显示超时。
- **质量覆盖率为空**：`quality_schedule.yaml` 未经 DS 核实，属预期状态。
- **中文路径启动异常**：从项目根目录运行 `python start.py`；前端构建使用 Node 18+。避免使用非 UTF-8 的 `.bat` 文件。
