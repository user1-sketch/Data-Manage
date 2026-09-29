import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.services.metadata import MetadataStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    settings = get_settings()
    if args.root:
        settings.schema_yaml_root = args.root
    store = MetadataStore()
    status = store.refresh()
    print(json.dumps({**status, 'tables': store.snapshot.get('table_count') if store.snapshot else None,
                      'columns': store.snapshot.get('column_count') if store.snapshot else None},
                     ensure_ascii=False))
    if status['last_error']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
