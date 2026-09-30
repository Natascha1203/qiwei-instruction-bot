from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..settings import get_settings
from .errors import BotMessageIgnored, BotPayloadError
from .models import BotInboundMessage, BotReplyTarget, WECOM_BOT_TYPE


class WeComWebSocketAdapter:
    def __init__(self, allowed_chatids: set[str]):
        self._allowed_chatids = allowed_chatids

    def parse_inbound(self, payload: dict) -> BotInboundMessage:
        if payload.get("cmd") not in ("", "aibot_msg_callback", None):
            raise BotMessageIgnored("unsupported wecom cmd")

        headers = payload.get("headers") or {}
        body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
        chatid = str(body.get("chatid", ""))
        chattype = str(body.get("chattype", ""))
        msgtype = str(body.get("msgtype", ""))
        from_user = body.get("from") or {}
        msgid = str(body.get("msgid", ""))
        req_id = str(headers.get("req_id", ""))

        if not msgid:
            raise BotMessageIgnored("wecom message missing msgid")

        raw_payload = dict(payload)
        raw_payload["req_id"] = req_id
        raw_payload["chatid"] = chatid
        raw_payload["chattype"] = chattype

        if chattype == "group":
            if self._allowed_chatids and chatid not in self._allowed_chatids:
                raise BotMessageIgnored("wecom message is not from allowed chat")
            if msgtype != "text":
                raise BotPayloadError("仅支持文本指令")

            text = body.get("text") or {}
            content = self._normalize_group_text_content(str(text.get("content", "")))
            if not content:
                raise BotPayloadError("未提取到消息内容")

            return BotInboundMessage(
                message_id=msgid,
                bot_type=WECOM_BOT_TYPE,
                channel="websocket",
                message_type="text",
                conversation_id=chatid,
                user_id=str(from_user.get("userid", "")),
                user_name="",
                content=content,
                raw_payload=raw_payload,
                received_at=datetime.utcnow(),
            )

        if chattype == "single":
            if msgtype == "text":
                text = body.get("text") or {}
                content = str(text.get("content", "")).strip()
                if not content:
                    raise BotPayloadError("未提取到消息内容")

                return BotInboundMessage(
                    message_id=msgid,
                    bot_type=WECOM_BOT_TYPE,
                    channel="websocket",
                    message_type="text",
                    conversation_id=chatid or str(from_user.get("userid", "")),
                    user_id=str(from_user.get("userid", "")),
                    user_name="",
                    content=content,
                    raw_payload=raw_payload,
                    received_at=datetime.utcnow(),
                )

            if not get_settings().wecom_single_file_enabled:
                raise BotMessageIgnored("wecom single file processing is disabled")
            if msgtype != "file":
                raise BotPayloadError("仅支持单聊文本或文件消息")

            file_payload = body.get("file") or {}
            file_url = str(file_payload.get("url", "")).strip()
            file_aes_key = str(file_payload.get("aeskey", "") or body.get("aeskey", "")).strip()
            response_url = str(body.get("response_url") or payload.get("response_url") or "").strip()
            if not file_url:
                raise BotPayloadError("未提取到文件下载地址")
            if not file_aes_key:
                raise BotPayloadError("未提取到文件解密 aeskey")
            if not response_url:
                raise BotPayloadError("未提取到 response_url")

            return BotInboundMessage(
                message_id=msgid,
                bot_type=WECOM_BOT_TYPE,
                channel="websocket",
                message_type="file",
                conversation_id=chatid,
                user_id=str(from_user.get("userid", "")),
                user_name="",
                file_url=file_url,
                file_aes_key=file_aes_key,
                file_name=self._derive_file_name(file_payload, file_url, msgid),
                response_url=response_url,
                raw_payload=raw_payload,
                received_at=datetime.utcnow(),
            )

        raise BotMessageIgnored("unsupported wecom chat type")

    def _normalize_group_text_content(self, content: str) -> str:
        normalized = content
        for mention in self._group_text_strip_mentions():
            normalized = normalized.replace(mention, "")

        lines = [line.strip() for line in normalized.splitlines()]
        non_empty_lines = [line for line in lines if line]
        return "\n".join(non_empty_lines).strip()

    def _group_text_strip_mentions(self) -> list[str]:
        raw_value = get_settings().wecom_group_text_strip_mentions
        return [item.strip() for item in raw_value.split(",") if item.strip()]

    def build_reply_target(self, inbound: BotInboundMessage) -> BotReplyTarget:
        chat_type = str(inbound.raw_payload.get("chattype", ""))
        conversation_id = inbound.conversation_id
        if chat_type == "single" and not conversation_id:
            conversation_id = inbound.user_id

        return BotReplyTarget(
            bot_type=WECOM_BOT_TYPE,
            channel="websocket",
            conversation_id=conversation_id,
            user_id=inbound.user_id,
            reply_url=inbound.response_url or None,
            connection_id=str(inbound.raw_payload.get("connection_id", "")),
            req_id=None,
            chat_type=chat_type,
        )

    def _derive_file_name(self, file_payload: dict, file_url: str, msgid: str) -> str:
        explicit_name = str(file_payload.get("name") or file_payload.get("filename") or "").strip()
        if explicit_name:
            return self._ensure_xlsx_suffix(Path(explicit_name).name)

        parsed = urlparse(file_url)
        candidate = Path(unquote(parsed.path)).name
        if candidate:
            return self._ensure_xlsx_suffix(candidate)
        return f"{msgid}.xlsx"

    def _ensure_xlsx_suffix(self, file_name: str) -> str:
        if Path(file_name).suffix:
            return file_name
        return f"{file_name}.xlsx"
