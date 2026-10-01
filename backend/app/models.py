from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .instruction_parser import InstructionParseResult


class SourceMeta(BaseModel):
    group_id: str = ""
    user_id: str = ""
    user_name: str = ""
    send_time: datetime = Field(default_factory=datetime.utcnow)


class RawMessage(BaseModel):
    message_id: str
    content: str
    source: SourceMeta = Field(default_factory=SourceMeta)


class ParsedInstruction(BaseModel):
    side: Literal["买", "卖"]
    offset: Literal["开", "平"]
    symbol: str
    exchange: str | None = None
    start_time: str
    end_time: str
    lots: int
    hedge_flag: Literal["投机", "套保"] | None = None


class ParseResult(BaseModel):
    ok: bool
    client_account: str | None = None
    instructions: list[ParsedInstruction] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ProcessResult(BaseModel):
    ok: bool
    parse_result: ParseResult | None = None
    instruction_parse_result: InstructionParseResult | None = None
    file_path: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    callback_text: str


class FileProcessResult(BaseModel):
    ok: bool
    callback_text: str
    original_file_path: str | None = None
    converted_file_path: str | None = None
