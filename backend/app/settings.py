from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="env.ini", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"
    app_host: str = "0.0.0.0"
    app_port: int = 8080

    webhook_url: str = "https://example.local/webhook/send"
    webhook_upload_url: str = "https://example.local/webhook/upload"
    webhook_http_timeout: int = 20

    ctp_enabled: bool = True
    ctp_host: str = "127.0.0.1"
    ctp_port: int = 12345
    ctp_broker_id: str = "2071"
    ctp_user_id: str = ""
    ctp_password: str = ""
    ctp_app_id: str = ""
    ctp_auth_code: str = ""
    ctp_max_retries: int = 2
    ctp_connect_timeout: int = 10
    ctp_query_timeout: int = 30
    ctp_load_contracts_timeout: int = 60
    ctp_logout_timeout: int = 5

    bot_enabled_types: str = "generic_http,wecom_websocket_client"

    generic_http_enabled: bool = True
    generic_http_default_reply_url: str = ""
    generic_http_timeout: int = 10

    wecom_ws_enabled: bool = True
    wecom_ws_url: str = "wss://openws.work.weixin.qq.com"
    wecom_bot_id: str = ""
    wecom_bot_secret: str = ""
    wecom_allowed_chatids: str = ""
    wecom_group_text_strip_mentions: str = ""
    wecom_ws_reconnect_interval: int = 5
    wecom_ws_send_timeout: int = 10
    wecom_heartbeat_interval: int = 30
    wecom_media_chunk_size: int = 512 * 1024
    wecom_response_timeout: int = 10
    wecom_single_file_enabled: bool = True

    twap_api_enabled: bool = False
    xgo_ip: str = ""
    xgo_port: int = 0
    xgo_query_timeout: int = 300
    xgo_log_level: str = "info"
    xgo_auto_reconnect: bool = True
    xgo_account_code: str = ""
    xgo_login_key: str = ""
    xgo_investor_code: str = ""
    xgo_app_code: str = ""
    xgo_auth_code: str = ""
    xgo_broker_id: str = ""
    xgo_counter_code: str = ""
    xgo_counter_account: str = ""
    xgo_request_timeout: int = 15

    file_store_dir: str = "./data/output"
    file_download_timeout: int = 30
    instruction_parser_static_info_path: str = "./config/instruction_parser_static_info.json"

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (Path.cwd() / path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
