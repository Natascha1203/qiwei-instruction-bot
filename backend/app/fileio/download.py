from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

import httpx

from ..settings import get_settings


@dataclass(frozen=True)
class DownloadResult:
    content: bytes
    filename: str = ""


class FileDownloadClient:
    def __init__(self, timeout: int):
        self._timeout = timeout

    def download(self, url: str) -> DownloadResult:
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
        return DownloadResult(
            content=data,
            filename=_extract_filename_from_response(response),
        )


def _extract_filename_from_response(response: httpx.Response) -> str:
    content_disposition = response.headers.get("Content-Disposition", "").strip()
    if not content_disposition:
        return ""

    filename_star_match = re.search(r"filename\*\s*=\s*([^;]+)", content_disposition, flags=re.IGNORECASE)
    if filename_star_match:
        raw_value = filename_star_match.group(1).strip().strip('"')
        if "''" in raw_value:
            _, encoded_value = raw_value.split("''", 1)
            return _normalize_filename_candidate(unquote(encoded_value))
        return _normalize_filename_candidate(unquote(raw_value))

    filename_match = re.search(r'filename\s*=\s*(".*?"|[^;]+)', content_disposition, flags=re.IGNORECASE)
    if not filename_match:
        return ""

    raw_value = filename_match.group(1).strip().strip('"')
    return _normalize_filename_candidate(unquote(raw_value))


def _normalize_filename_candidate(value: str) -> str:
    candidate = value.strip().replace("\\", "/")
    if not candidate:
        return ""
    return candidate.rsplit("/", 1)[-1].strip()


def get_file_download_client() -> FileDownloadClient:
    return FileDownloadClient(timeout=get_settings().file_download_timeout)
