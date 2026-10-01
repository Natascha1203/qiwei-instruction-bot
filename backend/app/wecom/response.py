from __future__ import annotations

import httpx

from ..settings import get_settings


class WeComResponseClient:
    def __init__(self, timeout: int):
        self._timeout = timeout

    def send_markdown(self, response_url: str, content: str) -> None:
        if not response_url:
            raise RuntimeError("response_url 为空")

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        }

        try:
            response = httpx.post(
                response_url,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            if response.content:
                body = response.json()
                if isinstance(body, dict) and body.get("errcode", 0) != 0:
                    raise RuntimeError(f"response_url 回复失败: {body}")
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"response_url 回复失败: {exc}") from exc


def get_wecom_response_client() -> WeComResponseClient:
    return WeComResponseClient(timeout=get_settings().wecom_response_timeout)
