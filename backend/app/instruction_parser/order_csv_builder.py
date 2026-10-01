import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .instruction_models import AlgoType, HedgeFlag, InstructionParseResult, LegIndex, Offset, Order, Side


@dataclass(frozen=True)
class TwapRow:
    """一行 TWAP 模板数据，字段顺序与 twap_headers 一致，CSV 写出与 xGo 直发共用。"""

    formal_name: str
    start_timing: str
    start_time: str
    stop_timing: str
    stop_time: str
    target_unit: str
    trading_style_code: str
    caption: str
    contract_code: str
    hedge_flag: str
    order_ratio: str
    side: str
    offset: str
    broker_code: str
    investor_code: str
    counter_code: str
    counter_account: str
    slice_count: str

    def as_csv_row(self) -> list[str]:
        return [
            "TWAP",
            self.formal_name,
            self.start_timing,
            self.start_time,
            self.stop_timing,
            self.stop_time,
            self.target_unit,
            self.trading_style_code,
            self.caption,
            "",
            "",
            self.contract_code,
            self.hedge_flag,
            self.order_ratio,
            self.side,
            self.offset,
            self.broker_code,
            self.investor_code,
            self.counter_code,
            self.counter_account,
            self.slice_count,
        ]


class OrderCsvBuilder:
    """Build target CSV rows from parsed orders."""

    twap_headers = [
        "算法类别",
        "名称",
        "开始时机",
        "开始时间",
        "结束时机",
        "结束时间",
        "交易份数",
        "交易风格",
        "备注",
        "腿序号",
        "报单顺序",
        "合约代码",
        "交易编码",
        "委托比值",
        "买卖方向",
        "开平方向",
        "经纪商",
        "资产账号",
        "交易柜台",
        "柜台帐号",
        "切片个数",
    ]

    extension_headers = [
        "算法类别",
        "交易柜台",
        "柜台帐号",
        "经纪商",
        "资产账号",
        "交易编码",
        "合约代码",
        "买卖方向",
        "开平方向",
        "委托手数",
        "委托价类别",
        "委托价格",
        "有效期类别",
        "成交量类别",
        "最小成交量",
        "行情参考档位",
        "备注",
    ]

    spread_headers = [
        "算法类别",
        "腿序号",
        "名称",
        "结束时间",
        "交易份数",
        "合约代码",
        "交易编码",
        "委托比例",
        "买卖方向",
        "开平方向",
        "交易风格",
        "备注",
        "经纪商",
        "资产账号",
        "交易柜台",
        "柜台帐号",
        "价差公式",
        "价差档位",
        "关系比较",
        "价差",
    ]
    # 按照Order本身进行分类
    def build_rows(self, result: InstructionParseResult) -> dict[str, list[list[str]]]:
        grouped_rows = {"twap": [], "extension": [], "spread": []}
        for group in result.groups:
            orders = group.orders
            if not orders:
                continue
            if not orders[0].is_spread:
                for order in orders:
                    if order.algo_type == AlgoType.TWAP:
                        grouped_rows["twap"].append(
                            self.build_twap_row(order, group.raw_instruction).as_csv_row()
                        )
                    else:
                        grouped_rows["extension"].append(self._build_extension_order_row(order, group.raw_instruction))
            else:   
                grouped_rows["spread"].extend(self._build_spread_rows(orders, group.raw_instruction))
        return grouped_rows

    def write_csv(self, result: InstructionParseResult, output_path: str) -> dict[str, str]:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        grouped_rows = self.build_rows(result)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        written_paths: dict[str, str] = {}

        file_specs = {
            "twap": self.twap_headers,
            "extension": self.extension_headers,
            "spread": self.spread_headers,
        }
        for file_type, headers in file_specs.items():
            rows = grouped_rows[file_type]
            if not rows:
                continue
            target_path = path.with_name(f"{path.stem}_{file_type}_{timestamp}.csv")
            with target_path.open("w", encoding="utf-8", newline="") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(headers)
                writer.writerows(rows)
            written_paths[file_type] = str(target_path)
        return written_paths

    def build_twap_row(self, order: Order, raw_instruction: str) -> TwapRow:
        start_at, start_value = self._resolve_start_fields(order)
        end_at, end_value = self._resolve_end_fields(order)
        lots = self._stringify_int(order.lots)
        return TwapRow(
            formal_name=order.instruction_name or self._default_instruction_name(order),
            start_timing=start_at,
            start_time=start_value,
            stop_timing=end_at or "CustomTime",
            stop_time=end_value,
            target_unit=lots,
            trading_style_code="0000000002",
            caption=raw_instruction,
            contract_code=self._format_contract_code(order),
            hedge_flag=self._resolve_hedge_flag(order),
            order_ratio="",
            side=self._resolve_side(order),
            offset=self._resolve_offset(order),
            broker_code="gtjaqh",
            investor_code=order.account or "",
            counter_code="ctp_prod",
            counter_account="1023017",
            slice_count=lots,
        )

    def _build_extension_order_row(self, order: Order, raw_instruction: str) -> list[str]:
        return [
            "ExtensionOrder",
            "ctp_prod",
            "1023017",
            "gtjaqh",
            order.account or "",
            self._resolve_hedge_flag(order),
            self._format_contract_code(order),
            self._resolve_side(order),
            self._resolve_offset(order),
            self._stringify_int(order.lots),
            "Last" if order.is_limit else "Best",
            self._resolve_extension_price(order),
            "GFD",
            "AnyVolume",
            "0",
            "0",
            raw_instruction,
        ]

    def _build_spread_rows(self, orders: list[Order], raw_instruction: str) -> list[list[str]]:
        spread_price = self._resolve_spread_price(orders)
        end_time = self._resolve_spread_end_time(orders)
        rows: list[list[str]] = []
        for order in orders:
            rows.append(
                [
                    "spread",
                    self._resolve_leg_number(order),
                    order.instruction_name or self._default_instruction_name(order),
                    end_time,
                    self._stringify_int(order.lots),
                    self._format_contract_code(order),
                    self._resolve_hedge_flag(order),
                    "1",
                    self._resolve_side(order),
                    self._resolve_offset(order),
                    "0000000001",
                    raw_instruction,
                    "gtjaqh",
                    order.account or "",
                    "ctp_prod",
                    "1023017",
                    "LeftPrice",
                    "0",
                    "LE",
                    spread_price,
                ]
            )
        return rows

    def _resolve_start_fields(self, order: Order) -> tuple[str, str]:
        if order.start_immediately:
            return "Immediately", ""
        if order.start_time:
            return "CustomTime", order.start_time
        return "", ""

    def _resolve_end_fields(self, order: Order) -> tuple[str, str]:
        if order.end_time:
            return "CustomTime", order.end_time
        return "", ""

    def _resolve_extension_price(self, order: Order) -> str:
        if not order.is_limit:
            return "0"
        if order.limit_price is None:
            return ""
        return self._stringify_number(order.limit_price)

    def _resolve_spread_price(self, orders: list[Order]) -> str:
        first_limit = orders[0].limit_price if orders else None
        return self._stringify_number(first_limit) if first_limit is not None else ""

    def _resolve_spread_end_time(self, orders: list[Order]) -> str:
        for order in orders:
            if order.end_time:
                return order.end_time
        now = datetime.now() # if end time is none ,use 14:50
        return now.replace(hour=14, minute=50, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S.000+0800")

    def _resolve_leg_number(self, order: Order) -> str:
        if order.leg_index == LegIndex.FIRST:
            return "1"
        if order.leg_index == LegIndex.SECOND:
            return "2"
        return ""

    def _resolve_side(self, order: Order) -> str:
        if order.side == Side.BUY:
            return "Buy"
        if order.side == Side.SELL:
            return "Sell"
        return ""

    def _resolve_offset(self, order: Order) -> str:
        if order.offset == Offset.OPEN:
            return "Open"
        if order.offset == Offset.CLOSE:
            return "Close"
        return ""

    def _resolve_hedge_flag(self, order: Order) -> str:
        if order.hedge_flag == HedgeFlag.SPECULATION:
            return "Speculation"
        if order.hedge_flag == HedgeFlag.HEDGING:
            return "Hedging"
        return "Hedging"

    def _format_contract_code(self, order: Order) -> str:
        contract = (order.contract or "").strip().lower()
        exchange = (order.exchange or "").strip().lower()
        if not contract:
            return ""
        if not exchange:
            return contract
        return f"{contract}.{exchange}"

    def _default_instruction_name(self, order: Order) -> str:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        contract = (order.contract or "order").upper()
        side = self._resolve_side(order).lower() or "unknown"
        lots = self._stringify_int(order.lots) or "0"
        return f"{contract}-{side}-{lots}x-{timestamp}"

    def _stringify_int(self, value: int | None) -> str:
        if value is None:
            return ""
        return str(int(value))

    def _stringify_number(self, value: float | int) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
