from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

WECOM_BOT_TYPE = "wecom_websocket_client"


class BotInboundMessage(BaseModel):
    message_id: str
    bot_type: str
    channel: Literal["http", "websocket"]
    message_type: Literal["text", "file"] = "text"
    conversation_id: str = ""
    user_id: str = ""
    user_name: str = ""
    content: str = ""
    file_url: str = ""
    file_aes_key: str = ""
    file_name: str = ""
    response_url: str = ""
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=datetime.utcnow)


class BotReplyTarget(BaseModel):
    bot_type: str
    channel: Literal["http", "websocket"]
    conversation_id: str = ""
    user_id: str = ""
    reply_url: str | None = None
    connection_id: str | None = None
    req_id: str | None = None
    chat_type: str | None = None


class BotOutboundMessage(BaseModel):
    message_id: str
    target: BotReplyTarget
    text: str
    file_path: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    should_dispatch: bool = True

__all__ = [
    "WECOM_BOT_TYPE",
    "BotInboundMessage",
    "BotOutboundMessage",
    "BotReplyTarget",
]
