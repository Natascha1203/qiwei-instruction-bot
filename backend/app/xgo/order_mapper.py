from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..instruction_parser import TwapRow

ORDER_TIME_FORMAT = "%Y-%m-%d %H:%M:%S.000+0800"

# CSV 模板里套保写作 Hedging，xGo 枚举名是 Hedge
HEDGE_FLAG_TO_XGO = {
    "Speculation": "Speculation",
    "Hedging": "Hedge",
}


@dataclass
class TwapOrderSpec:
    formal_name: str
    caption: str
    start_timing: str
    start_dt: datetime | None
    stop_timing: str
    stop_dt: datetime | None
    duration: timedelta | None
    target_unit: int
    slice_count: int
    contract_code: str
    order_ratio: int
    side: str
    offset: str
    hedge_flag: str
    investor_code: str
    counter_code: str
    counter_account: str
    broker_code: str
    trading_style_code: str


def build_twap_order_spec(
    row: TwapRow,
    *,
    default_investor_code: str = "",
    now: datetime | None = None,
) -> TwapOrderSpec:
    now = now or datetime.now()

    contract_code = row.contract_code.strip()
    if not contract_code:
        raise ValueError("缺少合约代码")
    if "." not in contract_code:
        raise ValueError(f"缺少交易所信息, 合约代码={contract_code}")

    if row.side not in ("Buy", "Sell"):
        raise ValueError(f"缺少买卖方向, 合约代码={contract_code}")
    if row.offset not in ("Open", "Close"):
        raise ValueError(f"缺少开平方向, 合约代码={contract_code}")

    target_unit = _parse_positive_int(row.target_unit, "交易手数", contract_code)
    slice_count = _parse_positive_int(row.slice_count, "切片个数", contract_code)

    investor_code = row.investor_code.strip() or default_investor_code.strip()
    if not investor_code:
        raise ValueError(f"缺少资产账号(investor_code), 合约代码={contract_code}")

    start_immediately = row.start_timing == "Immediately"
    start_dt = _parse_order_time(row.start_time)
    stop_dt = _parse_order_time(row.stop_time)
    if not start_immediately and start_dt is None and stop_dt is None:
        raise ValueError(f"缺少时间信息, 合约代码={contract_code}")
    if start_immediately:
        start_dt = None

    order_ratio = int(row.order_ratio.strip()) if row.order_ratio.strip() else 1

    return TwapOrderSpec(
        formal_name=row.formal_name.strip(),
        caption=row.caption,
        start_timing="Immediately" if start_immediately else "CustomTime",
        start_dt=start_dt,
        # CSV 的结束时机列恒为 CustomTime，是否有结束时间以结束时间列为准
        stop_timing="CustomTime" if stop_dt is not None else "TodayClose",
        stop_dt=stop_dt,
        duration=_derive_duration(start_immediately, start_dt, stop_dt, now),
        target_unit=target_unit,
        slice_count=slice_count,
        contract_code=contract_code,
        order_ratio=order_ratio,
        side=row.side,
        offset=row.offset,
        hedge_flag=HEDGE_FLAG_TO_XGO.get(row.hedge_flag, "Hedge"),
        investor_code=investor_code,
        counter_code=row.counter_code.strip(),
        counter_account=row.counter_account.strip(),
        broker_code=row.broker_code.strip(),
        trading_style_code=row.trading_style_code.strip(),
    )


def _derive_duration(
    start_immediately: bool,
    start_dt: datetime | None,
    stop_dt: datetime | None,
    now: datetime,
) -> timedelta | None:
    # 开始时间/结束时间/执行时长至少填两个：立即开始且无开始时间时，用结束时间补时长
    if start_dt is not None and stop_dt is not None:
        return None
    if start_immediately and stop_dt is not None:
        return stop_dt - now
    return None


def _parse_positive_int(value: str, label: str, contract_code: str) -> int:
    text = (value or "").strip()
    if text.isdigit() and int(text) > 0:
        return int(text)
    raise ValueError(f"{label}无效, 合约代码={contract_code}, 数值={text}")


def _parse_order_time(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, ORDER_TIME_FORMAT)
    except ValueError as exc:
        raise ValueError(f"时间格式无效: {text}") from exc
