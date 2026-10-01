import logging
import threading

from ..models import RawMessage, SourceMeta
from ..settings import get_settings
from .adapter import WeComWebSocketAdapter
from .models import BotOutboundMessage, WECOM_BOT_TYPE
from .websocket_sender import WeComWebSocketSender

logger = logging.getLogger(__name__)
_busy_lock = threading.Lock()
_is_processing_message = False


def _allowed_chatids() -> set[str]:
    return {item.strip() for item in get_settings().wecom_allowed_chatids.split(",") if item.strip()}


def _try_acquire_processing_slot() -> bool:
    global _is_processing_message
    with _busy_lock:
        if _is_processing_message:
            return False
        _is_processing_message = True
        return True


def _release_processing_slot() -> None:
    global _is_processing_message
    with _busy_lock:
        _is_processing_message = False


def process_wecom_message(payload: dict, connection_manager) -> BotOutboundMessage:
    # 延迟导入打破 app.services <-> app.wecom 循环依赖
    from ..services import process_file_message, process_text_message

    adapter = WeComWebSocketAdapter(_allowed_chatids())
    sender = WeComWebSocketSender(connection_manager)
    inbound = adapter.parse_inbound(payload)
    target = adapter.build_reply_target(inbound)

    if not _try_acquire_processing_slot():
        logger.info("message ignored while busy: message_id=%s", inbound.message_id)
        sender.send_text(target, "服务器忙...")
        return BotOutboundMessage(
            message_id=inbound.message_id,
            target=target,
            text="服务器忙...",
            file_path=None,
            file_paths=[],
            should_dispatch=False,
        )

    try:
        if inbound.message_type == "file":
            try:
                result = process_file_message(inbound)
            except Exception as exc:
                logger.exception("process_file_message failed: %s", exc)
                return BotOutboundMessage(
                    message_id=inbound.message_id,
                    target=target,
                    text="文件处理失败",
                    file_path=None,
                    file_paths=[],
                    should_dispatch=False,
                )
            return BotOutboundMessage(
                message_id=inbound.message_id,
                target=target,
                text=result.callback_text,
                file_path=None,
                file_paths=[],
                should_dispatch=False,
            )

        raw = RawMessage(
            message_id=inbound.message_id,
            content=inbound.content,
            source=SourceMeta(
                group_id=inbound.conversation_id,
                user_id=inbound.user_id,
                user_name=inbound.user_name,
                send_time=inbound.received_at,
            ),
        )
        try:
            result = process_text_message(raw)
        except Exception as exc:
            logger.exception("process_text_message failed: %s", exc)
            return BotOutboundMessage(
                message_id=inbound.message_id,
                target=target,
                text="识别失败。原因: 导出文件失败",
                file_path=None,
                file_paths=[],
            )

        outbound = BotOutboundMessage(
            message_id=inbound.message_id,
            target=target,
            text=result.callback_text,
            file_path=result.file_path,
            file_paths=result.file_paths,
        )
        dispatch_wecom_reply(outbound, sender)
        return outbound
    finally:
        _release_processing_slot()


def dispatch_wecom_reply(outbound: BotOutboundMessage, sender: WeComWebSocketSender) -> None:
    if not outbound.should_dispatch:
        return

    logger.info("sending text reply message_id=%s", outbound.message_id)
    sender.send_text(outbound.target, outbound.text)
    for file_path in _iter_reply_file_paths(outbound):
        logger.info("sending file reply message_id=%s file=%s", outbound.message_id, file_path)
        sender.send_file(outbound.target, file_path)
        logger.info("file reply sent message_id=%s file=%s", outbound.message_id, file_path)


def _iter_reply_file_paths(outbound: BotOutboundMessage) -> list[str]:
    ordered_paths: list[str] = []
    for file_path in [outbound.file_path, *outbound.file_paths]:
        normalized = (file_path or "").strip()
        if normalized and normalized not in ordered_paths:
            ordered_paths.append(normalized)
    return ordered_paths


def handle_wecom_payload(payload: dict, connection_manager) -> BotOutboundMessage:
    return process_wecom_message(payload, connection_manager)
