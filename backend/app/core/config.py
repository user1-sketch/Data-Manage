from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / '.env', extra='ignore')

    schema_yaml_root: Path = Path('D:/ds_ck')
    ck_scheme: str = 'http'
    ck_host: str = '127.0.0.1'
    ck_port: int = 8123
    ck_read_user: str = ''
    ck_read_password: str = ''
    ck_write_user: str = ''
    ck_write_password: str = ''
    ck_database: str = 'SmartData'
    host: str = '127.0.0.1'
    port: int = 8787
    enable_scheduler: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
