from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from ..settings import get_settings
from .errors import BotSendError

logger = logging.getLogger(__name__)


class WeComMediaConnectionManager(Protocol):
    def send_json_and_wait(self, payload: dict, timeout: int | None = None) -> dict:
        ...


class WeComMediaClient:
    max_chunk_size = 512 * 1024
    max_chunk_count = 100
    max_file_size = 20 * 1024 * 1024
    min_file_size = 5

    def __init__(self, connection_manager: WeComMediaConnectionManager, chunk_size: int | None = None):
        self._connection_manager = connection_manager
        self._chunk_size = chunk_size or get_settings().wecom_media_chunk_size
        if self._chunk_size <= 0 or self._chunk_size > self.max_chunk_size:
            raise BotSendError("wecom media chunk size must be between 1 and 512KB")

    def upload_file(self, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise BotSendError(f"wecom upload file not found: {file_path}")
        total_size = path.stat().st_size
        if total_size < self.min_file_size:
            raise BotSendError("wecom upload file must be at least 5 bytes")
        if total_size > self.max_file_size:
            raise BotSendError("wecom upload file exceeds 20MB")

        chunk_count = (total_size + self._chunk_size - 1) // self._chunk_size
        if chunk_count > self.max_chunk_count:
            raise BotSendError("wecom upload file exceeds 100 chunks")

        logger.info("upload start file=%s size=%s chunks=%s", path, total_size, chunk_count)
        upload_id = self._init_upload(path.name, total_size, chunk_count)
        self._upload_chunks(path, upload_id)
        media_id = self._finish_upload(upload_id)
        logger.info("upload finish file=%s media_id=%s", path, media_id)
        return media_id

    def _init_upload(self, filename: str, total_size: int, total_chunks: int) -> str:
        payload = {
            "cmd": "aibot_upload_media_init",
            "headers": {"req_id": self._new_req_id()},
            "body": {
                "type": "file",
                "filename": filename,
                "total_size": total_size,
                "total_chunks": total_chunks,
            },
        }
        response = self._send_upload_payload(payload)
        upload_id = str((response.get("body") or {}).get("upload_id", ""))
        if not upload_id:
            raise BotSendError(f"wecom upload init missing upload_id: {response}")
        logger.info("upload init ok upload_id=%s", upload_id)
        return upload_id

    def _upload_chunks(self, path: Path, upload_id: str) -> None:
        with path.open("rb") as fh:
            chunk_index = 0
            while True:
                chunk = fh.read(self._chunk_size)
                if not chunk:
                    break
                payload = {
                    "cmd": "aibot_upload_media_chunk",
                    "headers": {"req_id": self._new_req_id()},
                    "body": {
                        "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "base64_data": base64.b64encode(chunk).decode("ascii"),
                    },
                }
                self._send_upload_payload(payload)
                logger.info("upload chunk ok upload_id=%s chunk_index=%s", upload_id, chunk_index)
                chunk_index += 1

    def _finish_upload(self, upload_id: str) -> str:
        payload = {
            "cmd": "aibot_upload_media_finish",
            "headers": {"req_id": self._new_req_id()},
            "body": {"upload_id": upload_id},
        }
        response = self._send_upload_payload(payload)
        media_id = str((response.get("body") or {}).get("media_id", ""))
        if not media_id:
            raise BotSendError(f"wecom upload finish missing media_id: {response}")
        return media_id

    def _send_upload_payload(self, payload: dict) -> dict:
        timeout = get_settings().wecom_ws_send_timeout
        response = self._connection_manager.send_json_and_wait(payload, timeout=timeout)
        body = response.get("body") or {}
        errcode = response.get("errcode", body.get("errcode", 0))
        if errcode != 0:
            errmsg = response.get("errmsg") or body.get("errmsg") or response
            raise BotSendError(f"wecom upload failed: {errmsg}")
        return response

    def _new_req_id(self) -> str:
        return uuid4().hex
