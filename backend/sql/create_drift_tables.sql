CREATE TABLE IF NOT EXISTS SmartData.meta_schema_drift_di
(
    check_id String,
    finding_id String,
    check_time DateTime64(3, 'Asia/Shanghai'),
    layer LowCardinality(String),
    check_item LowCardinality(String),
    severity LowCardinality(String),
    database String,
    table_name String,
    column_name String DEFAULT '',
    authoritative LowCardinality(String),
    fix_action LowCardinality(String),
    expected String DEFAULT '',
    actual String DEFAULT '',
    message String DEFAULT ''
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(check_time)
ORDER BY (check_id, finding_id, check_time);

CREATE TABLE IF NOT EXISTS SmartData.meta_schema_drift_run
(
    check_id String,
    run_time DateTime64(3, 'Asia/Shanghai'),
    event_time DateTime64(3, 'Asia/Shanghai'),
    event_order UInt8,
    finish_time Nullable(DateTime64(3, 'Asia/Shanghai')),
    trigger LowCardinality(String),
    status LowCardinality(String),
    duration_ms UInt32 DEFAULT 0,
    tables_checked UInt32 DEFAULT 0,
    tables_live UInt32 DEFAULT 0,
    err_cnt UInt32 DEFAULT 0,
    warn_cnt UInt32 DEFAULT 0,
    info_cnt UInt32 DEFAULT 0,
    details_count UInt32 DEFAULT 0,
    err_message String DEFAULT ''
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(run_time)
ORDER BY (run_time, check_id, event_order, event_time);
