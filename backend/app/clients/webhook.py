from __future__ import annotations

from pathlib import Path

import httpx

from ..settings import get_settings


class WebhookClient:
    def __init__(self, upload_url: str, send_url: str, timeout: int):
        self._upload_url = upload_url
        self._send_url = send_url
        self._timeout = timeout

    def upload_file(self, file_path: str) -> str:
        file_name = Path(file_path).name
        if not self._upload_url:
            raise RuntimeError("webhook upload url 未配置")

        with Path(file_path).open("rb") as media_file:
            files = {
                "media": (file_name, media_file, "application/octet-stream"),
            }
            try:
                response = httpx.post(
                    self._upload_url,
                    files=files,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RuntimeError(f"上传文件到 webhook 失败: {exc}") from exc

        if payload.get("errcode") != 0:
            raise RuntimeError(f"上传文件到 webhook 失败: {payload}")

        media_id = str(payload.get("media_id", "")).strip()
        if not media_id:
            raise RuntimeError(f"上传文件到 webhook 失败: 未返回 media_id, 响应={payload}")
        return media_id

    def send_markdown(self, content: str, mentioned_list: list[str] | None = None) -> None:
        _ = mentioned_list
        if not self._send_url:
            raise RuntimeError("webhook send url 未配置")

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        }

        try:
            response = httpx.post(
                self._send_url,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"发送 webhook 文本消息失败: {exc}") from exc

        if body.get("errcode") != 0:
            raise RuntimeError(f"发送 webhook 文本消息失败: {body}")

    def send_file(self, media_id: str) -> None:
        if not self._send_url:
            raise RuntimeError("webhook send url 未配置")

        payload = {
            "msgtype": "file",
            "file": {
                "media_id": media_id,
            },
        }

        try:
            response = httpx.post(
                self._send_url,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"发送 webhook 文件消息失败: {exc}") from exc

        if body.get("errcode") != 0:
            raise RuntimeError(f"发送 webhook 文件消息失败: {body}")


def get_webhook_client() -> WebhookClient:
    settings = get_settings()
    return WebhookClient(
        upload_url=settings.webhook_upload_url,
        send_url=settings.webhook_url,
        timeout=settings.webhook_http_timeout,
    )
