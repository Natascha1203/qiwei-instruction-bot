from enum import Enum

from pydantic import BaseModel, Field


class AlgoType(str, Enum):
    TWAP = "TWAP"
    EXTENSION_ORDER = "EXTENSION_ORDER"
    SPREAD = "SPREAD"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Offset(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class HedgeFlag(str, Enum):
    SPECULATION = "SPECULATION"
    HEDGING = "HEDGING"


class LegIndex(str, Enum):
    FIRST = "FIRST"
    SECOND = "SECOND"


class Order(BaseModel):
    contract: str  | None = None
    exchange: str | None = None
    algo_type: AlgoType | None = None
    start_time: str | None = None
    end_time: str | None = None
    start_immediately: bool = False
    hedge_flag: HedgeFlag | None = None
    side: Side | None = None
    offset: Offset | None = None
    account: str | None = None
    lots: int | None = None
    is_spread: bool | None = None
    leg_index: LegIndex | None = None
    is_limit: bool | None = None
    limit_price: float | None = None
    instruction_name: str | None = None


class ParsedOrder(BaseModel): # 对应一个指令转换成的下单命令,多腿指令会存在多个命令
    raw_instruction: str = ""
    orders: list[Order] = Field(default_factory=list)
    message: str = ""
    error: str = ""
    warning: str = ""


class InstructionParseResult(BaseModel):
    success: bool
    groups: list[ParsedOrder] = Field(default_factory=list)
