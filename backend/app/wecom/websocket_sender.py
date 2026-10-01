from typing import Protocol
from uuid import uuid4

from .errors import BotSendError
from .media import WeComMediaClient
from .models import BotReplyTarget


class WeComConnectionManager(Protocol):
    def send_json(self, payload: dict) -> None:
        ...

    def send_json_and_wait(self, payload: dict, timeout: int | None = None) -> dict:
        ...


class WeComWebSocketSender:
    def __init__(self, connection_manager: WeComConnectionManager, media_client: WeComMediaClient | None = None):
        self._connection_manager = connection_manager
        self._media_client = media_client or WeComMediaClient(connection_manager)

    def send_text(self, target: BotReplyTarget, content: str) -> None:
        self._connection_manager.send_json(self._build_text_payload(target, content))

    def send_file(self, target: BotReplyTarget, file_path: str) -> None:
        media_id = self._media_client.upload_file(file_path)
        response = self._connection_manager.send_json_and_wait(self._build_file_payload(target, media_id))
        body = response.get("body") or {}
        errcode = response.get("errcode", body.get("errcode", 0))
        if errcode != 0:
            errmsg = response.get("errmsg") or body.get("errmsg") or response
            raise BotSendError(f"wecom file message send failed: {errmsg}")

    def _build_text_payload(self, target: BotReplyTarget, content: str) -> dict:
        return {
            "cmd": "aibot_send_msg",
            "headers": {"req_id": self._new_req_id()},
            "body": {
                **self._active_body_route(target),
                "msgtype": "markdown",
                "markdown": {"content": content},
            },
        }

    def _build_file_payload(self, target: BotReplyTarget, media_id: str) -> dict:
        return {
            "cmd": "aibot_send_msg",
            "headers": {"req_id": self._new_req_id()},
            "body": {
                **self._active_body_route(target),
                "msgtype": "file",
                "file": {"media_id": media_id},
            },
        }

    def _active_body_route(self, target: BotReplyTarget) -> dict:
        if not target.conversation_id or not target.chat_type:
            raise BotSendError("wecom active send requires chatid and chattype")
        return {"chatid": target.conversation_id, "chat_type": self._chat_type_value(target.chat_type)}

    def _chat_type_value(self, chat_type: str) -> int:
        if chat_type == "single":
            return 1
        if chat_type == "group":
            return 2
        return 0

    def _new_req_id(self) -> str:
        return uuid4().hex
