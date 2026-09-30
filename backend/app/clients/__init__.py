from .ctp import CTPClient, CTPClientSpi, CTP_IMPORT_ERROR, api, verify_contract
from .file_download import FileDownloadClient, get_file_download_client
from .response import ResponseUrlClient, get_response_url_client
from .webhook import WebhookClient, get_webhook_client
from ..settings import get_settings


def upload_file_to_webhook(file_path: str) -> str:
    return get_webhook_client().upload_file(file_path)


def send_webhook_text(content: str, mentioned_list: list[str] | None = None) -> None:
    get_webhook_client().send_markdown(content, mentioned_list=mentioned_list)


def send_webhook_file(media_id: str) -> None:
    get_webhook_client().send_file(media_id)


def send_response_markdown(response_url: str, content: str) -> None:
    get_response_url_client().send_markdown(response_url, content)


def download_file(url: str) -> bytes:
    return get_file_download_client().download(url)


__all__ = [
    "api",
    "CTP_IMPORT_ERROR",
    "CTPClient",
    "CTPClientSpi",
    "verify_contract",
    "WebhookClient",
    "ResponseUrlClient",
    "FileDownloadClient",
    "get_webhook_client",
    "get_response_url_client",
    "get_file_download_client",
    "upload_file_to_webhook",
    "send_webhook_text",
    "send_webhook_file",
    "send_response_markdown",
    "download_file",
    "get_settings",
]
