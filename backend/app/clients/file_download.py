from __future__ import annotations

import httpx

from ..settings import get_settings


class FileDownloadClient:
    def __init__(self, timeout: int):
        self._timeout = timeout

    def download(self, url: str) -> bytes:
        if not url:
            raise RuntimeError("文件下载地址为空")

        try:
            response = httpx.get(
                url,
                follow_redirects=True,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"下载原文件失败: {exc}") from exc

        data = response.content
        if not data:
            raise RuntimeError("下载原文件失败: 文件内容为空")
        return data


def get_file_download_client() -> FileDownloadClient:
    return FileDownloadClient(timeout=get_settings().file_download_timeout)
