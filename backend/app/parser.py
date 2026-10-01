import re

from .ctp import get_contract_info
from .models import ParseResult, ParsedInstruction, RawMessage
from .utils import normalize_cn_punctuation, safe_strip, to_hhmmss


BUY = "买"
SELL = "卖"
OPEN = "开"
CLOSE = "平"
SPECULATION = "投机"
HEDGE = "套保"

CLIENT_ACCOUNT_RE = re.compile(r"客户账号[^\d]*(\d+)")
INSTRUCTION_SPLIT_RE = re.compile(r"(?=[买卖])")
SYMBOL_RE = re.compile(r"([A-Za-z]{1,3}\d{3,4})", re.IGNORECASE)
TIME_RANGE_RE = re.compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")
LOTS_RE = re.compile(r"([1-9]\d*)\s*手")
TWAP_RE = re.compile(r"twap\s*增强", re.IGNORECASE)


def normalize_text(text: str) -> str:
    text = normalize_cn_punctuation(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return safe_strip(text)


def extract_client_account(text: str) -> str:
    m = CLIENT_ACCOUNT_RE.search(text)
    if not m:
        raise ValueError("未提取到客户账号")
    account = m.group(1)
    if len(account) < 6:
        raise ValueError("客户账号位数不合法")
    return account


def split_instructions(text: str) -> list[str]:
    chunks: list[str] = []
    for line in text.split("\n"):
        line = safe_strip(line)
        if not line:
            continue
        if line.startswith("客户账号"):
            continue
        if BUY not in line and SELL not in line:
            continue

        line_chunks = [safe_strip(x) for x in INSTRUCTION_SPLIT_RE.split(line) if safe_strip(x)]
        for chunk in line_chunks:
            if chunk and chunk[0] in (BUY, SELL):
                chunks.append(chunk)
    return chunks


def parse_side(chunk: str) -> str:
    if not chunk:
        raise ValueError("空指令")
    if chunk[0] not in (BUY, SELL):
        raise ValueError("未提取到买卖方向")
    return chunk[0]


def parse_offset(chunk: str) -> str:
    for ch in chunk:
        if ch in (OPEN, CLOSE):
            return ch
    raise ValueError("未提取到开平方向")


def parse_symbol(chunk: str) -> str:
    m = SYMBOL_RE.search(chunk)
    if not m:
        raise ValueError("未提取到合约")
    return m.group(1)


def parse_time_range(chunk: str) -> tuple[str, str]:
    m = TIME_RANGE_RE.search(chunk)
    if not m:
        raise ValueError("未提取到时间")
    start = f"{m.group(1)}:{m.group(2)}:00"
    end = f"{m.group(3)}:{m.group(4)}:00"
    return to_hhmmss(start), to_hhmmss(end)


def parse_lots(chunk: str) -> int:
    m = LOTS_RE.search(chunk)
    if not m:
        raise ValueError("未提取到手数")
    return int(m.group(1))


def parse_hedge_flag(chunk: str) -> str | None:
    if SPECULATION in chunk:
        return SPECULATION
    if HEDGE in chunk:
        return HEDGE
    return None


def parse_twap(chunk: str) -> None:
    if not TWAP_RE.search(chunk):
        raise ValueError("未提取到twap增强")


def parse_single_instruction(chunk: str) -> ParsedInstruction:
    side = parse_side(chunk)
    offset = parse_offset(chunk)
    parse_twap(chunk)
    symbol = parse_symbol(chunk)
    start_time, end_time = parse_time_range(chunk)
    lots = parse_lots(chunk)
    hedge_flag = parse_hedge_flag(chunk)
    inst = ParsedInstruction(
        side=side,
        offset=offset,
        symbol=symbol,
        start_time=start_time,
        end_time=end_time,
        lots=lots,
        hedge_flag=hedge_flag,
    )
    validate_instruction(inst)
    return inst


def _time_to_minutes(value: str) -> int:
    hh, mm, _ss = value.split(":")
    return int(hh) * 60 + int(mm)


def validate_instruction(inst: ParsedInstruction) -> None:
    if inst.lots <= 0:
        raise ValueError("手数必须大于0")
    start_min = _time_to_minutes(inst.start_time)
    end_min = _time_to_minutes(inst.end_time)
    if start_min >= end_min:
        raise ValueError("开始时间必须早于结束时间")

    resp = get_contract_info(inst.symbol)
    if not resp["exists"]:
        raise ValueError(f"合约 {inst.symbol} 不存在")
    inst.exchange = resp["exchange"]
    if resp.get("symbol"):
        inst.symbol = resp["symbol"]


def parse_message(raw: RawMessage) -> ParseResult:
    content = normalize_text(raw.content)
    errors: list[str] = []

    try:
        account = extract_client_account(content)
    except ValueError as exc:
        return ParseResult(ok=False, errors=[str(exc)])

    chunks = split_instructions(content)
    if not chunks:
        return ParseResult(ok=False, client_account=account, errors=["未识别到下单指令"])

    instructions: list[ParsedInstruction] = []
    for idx, chunk in enumerate(chunks, start=1):
        try:
            inst = parse_single_instruction(chunk)
            instructions.append(inst)
        except ValueError as exc:
            errors.append(f"第{idx}条指令识别失败: {exc}")

    if errors:
        return ParseResult(ok=False, client_account=account, instructions=instructions, errors=errors)

    return ParseResult(ok=True, client_account=account, instructions=instructions)
