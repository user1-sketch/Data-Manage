import json
import re

import requests

from .config import Settings


IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def quote_ident(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f'Invalid ClickHouse identifier: {value!r}')
    return f'`{value}`'


class ClickHouseError(RuntimeError):
    pass


class ClickHouseClient:
    def __init__(self, settings: Settings, *, write: bool = False):
        if settings.ck_scheme not in ('http', 'https'):
            raise ClickHouseError('CK_SCHEME must be http or https')
        user = settings.ck_write_user if write else settings.ck_read_user
        password = settings.ck_write_password if write else settings.ck_read_password
        if not user:
            raise ClickHouseError('ClickHouse account is not configured')
        self.url = f'{settings.ck_scheme}://{settings.ck_host}:{settings.ck_port}/'
        self.auth = (user, password)

    def query(self, sql: str, *, timeout: int = 20) -> list[dict]:
        sql = sql.rstrip().removesuffix(';') + ' FORMAT JSON'
        try:
            response = requests.post(self.url, data=sql.encode('utf-8'), auth=self.auth, timeout=timeout)
            response.raise_for_status()
            return response.json()['data']
        except (requests.RequestException, json.JSONDecodeError, KeyError) as exc:
            raise ClickHouseError(f'ClickHouse query failed: {type(exc).__name__}') from exc

    def execute(self, sql: str, *, timeout: int = 20) -> None:
        try:
            response = requests.post(self.url, data=sql.encode('utf-8'), auth=self.auth, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ClickHouseError(f'ClickHouse write failed: {type(exc).__name__}') from exc
