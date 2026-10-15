import logging
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlparse

from .ctp import get_contract_info
from .file_converter import convert_xlsx_to_order_csv
from .fileio import get_file_download_client
from .instruction_parser import InstructionParseResult, InstructionParser, OrderCsvBuilder, StaticInfo
from .models import FileProcessResult, ProcessResult, RawMessage
from .settings import get_settings
from .storage import build_output_filename, ensure_output_dir, save_bytes
from .wecom.file_decryptor import decrypt_wecom_file_bytes_with_key, validate_decrypted_xlsx_bytes
from .wecom.models import BotInboundMessage
from .wecom.response import get_wecom_response_client
from .wecom.webhook import get_wecom_webhook_client

logger = logging.getLogger(__name__)


class MessageProcessingService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.static_info = self._load_instruction_parser_static_info()
        self.parser = InstructionParser(static_info=self.static_info)
        self.csv_builder = OrderCsvBuilder()
        self.file_download_client = get_file_download_client()
        self.webhook_client = get_wecom_webhook_client()
        self.response_client = get_wecom_response_client()

    def process_text_message(self, raw: RawMessage) -> ProcessResult:
        command_result = self._process_static_info_command(raw)
        if command_result is not None:
            return command_result

        instruction_result = self.parser.parse(raw.content, raw.source.user_id)
        output_dir = Path(self.settings.file_store_dir) / "text_message"
        ensure_output_dir(str(output_dir))
        output_base_path = output_dir / "instruction.csv"
        file_path_map = self.csv_builder.write_csv(instruction_result, str(output_base_path))
        file_paths = [path for _file_type, path in sorted(file_path_map.items())]

        return ProcessResult(
            ok=instruction_result.success,
            instruction_parse_result=instruction_result,
            file_path=file_paths[0] if file_paths else None,
            file_paths=file_paths,
            callback_text=build_instruction_markdown(instruction_result),
        )

    def process_file_message(self, inbound: BotInboundMessage) -> FileProcessResult:
        result = FileProcessResult(ok=False, callback_text="文件处理失败")

        try:
            response_url = inbound.response_url.strip()
            if not response_url:
                raise ValueError("未提取到 response_url")
            if not inbound.file_url.strip():
                raise ValueError("未提取到文件下载地址")

            original_file_path = self._download_inbound_file(inbound)
            converted_file_path = self._build_converted_csv_path(original_file_path)
            converted_ok, converted_result = convert_xlsx_to_order_csv(
                original_file_path,
                converted_file_path,
                self._resolve_exchange_for_contract,
            )
            if not converted_ok:
                raise ValueError(converted_result)

            self.webhook_client.send_markdown(
                _build_file_received_markdown(
                    original_file_path=original_file_path,
                    received_at=datetime.now(),
                )
            )

            original_media_id = self.webhook_client.upload_file(original_file_path)
            self.webhook_client.send_file(original_media_id)

            converted_media_id = self.webhook_client.upload_file(converted_result)
            self.webhook_client.send_file(converted_media_id)

            result = FileProcessResult(
                ok=True,
                callback_text="已经将文件发送到群聊",
                original_file_path=original_file_path,
                converted_file_path=converted_result,
            )
        except Exception as exc:
            result = FileProcessResult(
                ok=False,
                callback_text=_normalize_file_error(exc),
                original_file_path=result.original_file_path,
                converted_file_path=result.converted_file_path,
            )

        if inbound.response_url.strip():
            try:
                self.response_client.send_markdown(inbound.response_url, result.callback_text)
            except Exception as exc:
                logger.error("response_url reply failed: %s", exc)

        return result

    def process_message(self, raw: RawMessage) -> ProcessResult:
        return self.process_text_message(raw)

    def _load_instruction_parser_static_info(self) -> StaticInfo:
        configured_path = self.settings.instruction_parser_static_info_path.strip()
        if not configured_path:
            return StaticInfo()

        static_info_path = self.settings.resolve_path(configured_path)
        return StaticInfo.load_from_json(static_info_path)

    def _process_static_info_command(self, raw: RawMessage) -> ProcessResult | None:
        normalized = raw.content.strip()
        if not normalized:
            return None

        if _is_get_setting_command(normalized):
            return ProcessResult(
                ok=True,
                callback_text=_build_current_setting_markdown_for_user(self.static_info, raw.source.user_id),
            )

        if _is_set_setting_command(normalized):
            return self._update_static_info_from_command(normalized, raw.source.user_id)

        return None

    def _update_static_info_from_command(self, content: str, user_id: str) -> ProcessResult:
        working_copy = self.static_info.clone()
        lines = content.splitlines()[1:]
        if not any(line.strip() for line in lines):
            return ProcessResult(
                ok=False,
                callback_text="**设置失败**\n- 未提供任何设置项",
            )

        updated_lines, errors = working_copy.apply_chat_setting_lines(lines, user_id=user_id)
        if errors:
            markdown_lines = [
                "**设置失败**",
                *[f"- {error}" for error in errors],
            ]
            return ProcessResult(ok=False, callback_text="\n".join(markdown_lines).strip())

        static_info_path = self.settings.resolve_path(self.settings.instruction_parser_static_info_path)
        try:
            working_copy.save_to_json(static_info_path)
        except Exception as exc:
            return ProcessResult(
                ok=False,
                callback_text=f"**设置失败**\n- 写入配置文件失败: {exc}",
            )

        self.static_info.replace_with(working_copy)

        markdown_lines = [
            "**设置成功**",
            *(updated_lines or self.static_info.format_supported_settings()),
        ]
        return ProcessResult(ok=True, callback_text="\n".join(markdown_lines).strip())

    def _download_inbound_file(self, inbound: BotInboundMessage) -> str:
        base_dir = Path(self.settings.file_store_dir) / "file_message"
        ensure_output_dir(str(base_dir))

        download_result = self.file_download_client.download(inbound.file_url)
        file_name = _sanitize_file_name(_derive_source_filename(inbound, download_result.filename))
        original_path = base_dir / f"{file_name}"
        file_bytes = decrypt_wecom_file_bytes_with_key(download_result.content, inbound.file_aes_key)
        validate_decrypted_xlsx_bytes(file_bytes)
        return save_bytes(file_bytes, str(original_path))

    def _build_converted_csv_path(self, original_file_path: str) -> str:
        source_path = Path(original_file_path)
        if source_path.suffix.lower() == ".csv":
            csv_name = build_output_filename(prefix=source_path.stem, suffix=".csv")
            return str(source_path.with_name(csv_name))
        return str(source_path.with_suffix(".csv"))

    def _resolve_exchange_for_contract(self, symbol: str) -> str:
        info = get_contract_info(symbol)
        if not info.get("exists"):
            raise ValueError(f"未找到合约对应交易所, 合约={symbol}")
        exchange = str(info.get("exchange") or "").strip()
        if not exchange:
            raise ValueError(f"未找到合约对应交易所, 合约={symbol}")
        return exchange


@lru_cache(maxsize=1)
def get_message_processing_service() -> MessageProcessingService:
    return MessageProcessingService()


def process_text_message(raw: RawMessage) -> ProcessResult:
    return get_message_processing_service().process_text_message(raw)


def process_file_message(inbound: BotInboundMessage) -> FileProcessResult:
    return get_message_processing_service().process_file_message(inbound)


def process_message(raw: RawMessage) -> ProcessResult:
    return get_message_processing_service().process_message(raw)


def _is_get_setting_command(content: str) -> bool:
    return content.strip().lower() == ":get setting"


def _is_set_setting_command(content: str) -> bool:
    first_line = content.splitlines()[0].strip().lower() if content.splitlines() else ""
    return first_line == ":setting"


def _build_current_setting_markdown(static_info: StaticInfo) -> str:
    lines = ["**当前静态设置**", *static_info.format_supported_settings()]
    return "\n".join(lines).strip()


def build_instruction_markdown(result: InstructionParseResult) -> str:
    total_count = len(result.groups)
    failed_count = sum(1 for group in result.groups if group.error.strip())
    success_count = total_count - failed_count
    status_text = "解析成功" if result.success else "解析失败"

    lines = [
        f"**{status_text}**",
        f"总条数: {total_count}",
        f"成功条数: {success_count}",
        f"失败条数: {failed_count}",
    ]

    for index, group in enumerate(result.groups, start=1):
        lines.append("")
        lines.append(f"**第{index}条**")
        lines.append(f"> 原文: `{group.raw_instruction or ''}`")
        if group.message.strip():
            lines.append(f"> message: {group.message}")
        if group.warning.strip():
            lines.append(f"> warning: {group.warning}")
        if group.error.strip():
            lines.append(f"> error: {group.error}")

    return "\n".join(lines).strip()


def _build_file_received_markdown(original_file_path: str, received_at: datetime) -> str:
    file_name = Path(original_file_path).name
    received_time = received_at.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"**单聊收到新的客户文件**\n"
        f"文件名: {file_name}\n"
        f"时间: {received_time}"
    )


def _derive_source_filename(inbound: BotInboundMessage, downloaded_filename: str = "") -> str:
    if downloaded_filename.strip():
        return _ensure_xlsx_suffix(Path(downloaded_filename).name)

    parsed = urlparse(inbound.file_url)
    candidate = Path(unquote(parsed.path)).name
    if candidate:
        return _ensure_xlsx_suffix(candidate)
    return build_output_filename(prefix=inbound.message_id, suffix=".xlsx")


def _sanitize_file_name(file_name: str) -> str:
    sanitized = re.sub(r'[\\/:*?"<>|]+', "_", file_name).strip(" .")
    return sanitized or "source.xlsx"


def _build_current_setting_markdown_for_user(static_info: StaticInfo, user_id: str = "") -> str:
    lines = ["**当前静态设置**"]
    user_default_account = static_info.get_default_account_for_user_id(user_id)
    if user_default_account:
        lines.append(f"默认资金账号={user_default_account}")
    if user_id.strip():
        lines.append(f"允许平今手数阈值={static_info.get_auto_offset_threshold(None, user_id)}")

    for line in static_info.format_supported_settings():
        if user_default_account and line.startswith("默认资金账号="):
            continue
        if line.startswith("允许平今手数阈值="):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _ensure_xlsx_suffix(file_name: str) -> str:
    if Path(file_name).suffix:
        return file_name
    return f"{file_name}.xlsx"


def _normalize_file_error(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return "文件处理失败"
    return message
