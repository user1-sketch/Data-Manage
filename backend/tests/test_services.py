import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services import drift, metadata, quality


class DriftLockTests(unittest.TestCase):
    def test_client_creation_failure_releases_lock(self):
        with patch.object(drift, 'ClickHouseClient', side_effect=RuntimeError('unconfigured')):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, 'unconfigured'):
                    drift.run_check()
        self.assertFalse(drift._lock.locked())

    def test_success_event_follows_verified_details(self):
        writes = []

        class FakeClient:
            def __init__(self, write=False):
                self.write = write

            def execute(self, sql, **_kwargs):
                writes.append(sql)

            def query(self, _sql):
                return [{'total': 1, 'unique_count': 1}]

        report = {'counts': {'catalog_tables': 1, 'live_objects': 1}}
        finding = {'finding_id': 'abc', 'severity': 'warn', 'layer': 'L1',
                   'check_item': 'sample', 'database': 'Demo', 'table_name': 'sample',
                   'column_name': '', 'authoritative': 'catalog', 'expected': '',
                   'actual': '', 'fix_action': 'investigate', 'message': ''}
        with patch.object(drift, 'ClickHouseClient', side_effect=lambda _settings, write=False: FakeClient(write)), \
             patch.object(drift, 'read_yaml', return_value={}), \
             patch.object(drift, 'scope_config', return_value={}), \
             patch.object(drift, 'scan_live', return_value={}), \
             patch.object(drift, 'compare_baseline', return_value=report), \
             patch.object(drift, 'findings_from_baseline', return_value=[finding]):
            result = drift.run_check()
        self.assertEqual(result['run_status'], 'success')
        self.assertEqual(len(writes), 3)
        self.assertIn('"status": "started"', writes[0])
        self.assertIn('"finding_id": "abc"', writes[1])
        self.assertIn('"status": "success"', writes[2])

    def test_zero_findings_still_writes_success(self):
        writes = []

        class FakeClient:
            def execute(self, sql, **_kwargs):
                writes.append(sql)

            def query(self, _sql):
                return [{'total': 0, 'unique_count': 0}]

        report = {'counts': {'catalog_tables': 1, 'live_objects': 1}}
        with patch.object(drift, 'ClickHouseClient', side_effect=lambda *_args, **_kwargs: FakeClient()), \
             patch.object(drift, 'read_yaml', return_value={}), \
             patch.object(drift, 'scope_config', return_value={}), \
             patch.object(drift, 'scan_live', return_value={}), \
             patch.object(drift, 'compare_baseline', return_value=report), \
             patch.object(drift, 'findings_from_baseline', return_value=[]):
            result = drift.run_check()
        self.assertEqual(result['details_count'], 0)
        self.assertEqual(len(writes), 2)
        self.assertIn('"status": "success"', writes[1])

    def test_failed_scan_writes_error_after_started(self):
        writes = []

        class FakeClient:
            def execute(self, sql, **_kwargs):
                writes.append(sql)

        with patch.object(drift, 'ClickHouseClient', side_effect=lambda *_args, **_kwargs: FakeClient()), \
             patch.object(drift, 'read_yaml', return_value={}), \
             patch.object(drift, 'scope_config', return_value={}), \
             patch.object(drift, 'scan_live', side_effect=RuntimeError('scan failed')):
            with self.assertRaisesRegex(RuntimeError, 'scan failed'):
                drift.run_check()
        self.assertEqual(len(writes), 2)
        self.assertIn('"status": "started"', writes[0])
        self.assertIn('"status": "error"', writes[1])
        self.assertFalse(drift._lock.locked())


class QualityScheduleTests(unittest.TestCase):
    def test_due_rule_without_result_reduces_coverage(self):
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        schedule = {
            'timezone': 'Asia/Shanghai', 'verification_status': 'verified',
            'rules': [
                {'workflow': 'monitor_dawn', 'rule': 'observed', 'due_after': '00:00'},
                {'workflow': 'monitor_dawn', 'rule': 'missing', 'due_after': '00:00'},
            ],
        }
        observed = [{
            'biz_date': today, 'workflow_name': 'monitor_dawn', 'rule_name': 'observed',
            'result': ['ok', '', '1', '1'], 'last_run': '2026-09-29 00:01:00',
            'run_count': 3, 'failed_runs': 1,
        }]
        with patch.object(quality, 'monitor_rows', return_value=observed), \
             patch.object(quality, 'dq_rows', return_value=[]), \
             patch.object(quality, 'read_yaml', return_value=schedule):
            result = quality.summary(1)
        day = result['monitor']['daily'][0]
        self.assertEqual(day['due_rules'], 2)
        self.assertEqual(day['coverage'], 0.5)
        self.assertEqual(day['pass_rate'], 0.5)
        self.assertEqual(day['no_result_rules'], 1)
        self.assertTrue(any(row['status'] == 'no_result' for row in result['monitor']['rules']))


class MetadataFailureTests(unittest.TestCase):
    def test_invalid_source_keeps_previous_snapshot_and_marks_stale(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data_foundation').mkdir()
            (root / 'AI_Schema_catalog_v1.yaml').write_text(
                'tables:\n  ODS:\n    - name: sample\n      database: Demo\n      purpose: test\n', encoding='utf-8')
            detail = root / 'AI_Schema_v4_20260814.yaml'
            detail.write_text(
                'tables:\n  ODS:\n    - name: sample\n      database: Demo\n      columns:\n        - name: id\n          type: UInt64\n', encoding='utf-8')
            (root / 'data_foundation' / 'data_foundation.yaml').write_text(
                'metrics: {}\nglossary: {}\n', encoding='utf-8')
            live = {'status': 'fresh', 'snapshot_at': datetime.now().astimezone().isoformat(),
                    'tables': {'Demo.sample': {'engine': 'MergeTree',
                                               'partition_key': '', 'sorting_key': 'id'}}}
            with patch.object(metadata, 'SNAPSHOT_PATH', root / 'metadata.json'), \
                 patch.object(metadata, 'STATUS_PATH', root / 'status.json'), \
                 patch.object(metadata, 'get_settings', return_value=SimpleNamespace(schema_yaml_root=root)):
                store = metadata.MetadataStore()
                self.assertEqual(store.refresh(live)['snapshot_status'], 'fresh')
                original = store.get()
                detail.write_text('tables: [', encoding='utf-8')
                self.assertEqual(store.refresh(live)['snapshot_status'], 'stale')
                self.assertIs(store.get(), original)
                self.assertIsNotNone(store.status['last_error'])


if __name__ == '__main__':
    unittest.main()
