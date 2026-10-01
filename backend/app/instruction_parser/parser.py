from __future__ import annotations

import logging
import math
import re
import sys
from datetime import datetime, timedelta

CTP_ENABLE = True

from ..ctp import get_contract_info, get_last_error, query_last_price, query_position
from ..utils import normalize_cn_punctuation, safe_strip
from .instruction_models import (
    AlgoType,
    InstructionParseResult,
    LegIndex,
    Offset,
    Order,
    ParsedOrder,
    Side,
)
from .static_info import StaticInfo


logger = logging.getLogger(__name__)


SIDE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?P<token>buy|sell|b|s)(?=\s|/|$)", re.IGNORECASE)
STANDARD_CONTRACT_PATTERN = re.compile(r"\b(?P<product>[A-Z]{1,2})(?P<year>\d{2})(?P<month>\d{2})\b",re.IGNORECASE)
LOTS_PATTERN = re.compile(r"(?<!\d)(?P<lots>\d+)\s*[xX]\b")
LOTS_FALLBACK_PATTERN = re.compile(r"(?:(?<=^)|(?<=\s))(?P<lots>[1-9]\d*)(?:(?=$)|(?=\s))")
MARKET_PATTERN = re.compile(r"(?<![A-Za-z])(at\s+)?(@\s*)?(mkt|market)\b", re.IGNORECASE)
AT_PRICE_PATTERN = re.compile(r"(?<![A-Za-z0-9])at\s+(?P<price>[+-]?\d+(?:\.\d+)?)\s*(?:ob\b)?", re.IGNORECASE)
AT_SYMBOL_PRICE_PATTERN = re.compile(r"@\s*(?P<price>[+-]?\d+(?:\.\d+)?)\s*(?:ob\b)?", re.IGNORECASE)
NEGATIVE_PRICE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?P<price>-\d+(?:\.\d+)?)(?!:)\s*(?:ob\b)?", re.IGNORECASE)
PRINCIPLE_PATTERN = re.compile(r"\(\s*~\s*\$\s*(?P<value>\d+(?:\.\d+)?)\s*m\s*\)", re.IGNORECASE)
OPEN_CLOSE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?P<token>open|close|bo|so|bc|sc)(?=\s|$)", re.IGNORECASE)
TWAP_PATTERN = re.compile(r"\btwap\b", re.IGNORECASE)
ORDER_NAME_PATTERN = re.compile(r"\bOrder\s*\d+\b", re.IGNORECASE)

MONTH_NAME_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


class InstructionParser:
    """Stateful parser for multi-line text instructions."""

    def __init__(self, static_info: StaticInfo | None = None) -> None:
        self.static_info = static_info or StaticInfo()
        self.cur_index = -1
        self.current_user_id = ""
        self.instruction_name_list: list[str] = []
        self.instruction_body_list: list[str] = []
        self.temp_instruction_list: list[str] = []
        self.order_groups: list[ParsedOrder] = []

    def parse(self, text: str, user_id: str = "") -> InstructionParseResult:
        self.reset()
        self.current_user_id = user_id.strip()
        if not self.split(text):
            return InstructionParseResult(success=False, groups=self.order_groups)

        success = True
        for index in range(len(self.temp_instruction_list)):
            result = self.parse_single(index)
            if not result["success"]:
                success = False
        return InstructionParseResult(success=success, groups=self.order_groups)

    def reset(self) -> None:
        self.cur_index = -1
        self.current_user_id = ""
        self.instruction_name_list = []
        self.instruction_body_list = []
        self.temp_instruction_list = []
        self.order_groups = []

    def split(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        items = [line for line in normalized.split("\n") if line]
        if not items:
            return False

        self.instruction_name_list = ["" for _ in items]
        self.instruction_body_list = ["" for _ in items]
        self.temp_instruction_list = items
        self.order_groups = [ParsedOrder(raw_instruction=item) for item in items]
        return True

    def parse_single(self, index: int) -> dict:
        self.cur_index = index
        steps = [
            self.parse_bs,
            self.parse_account_s1,
            self.parse_contract,
            self.parse_hand,
            self.adjust_spread,
            self.parse_time,
            self.parse_price,
            self.parse_principle,
            self.parse_algo,
            self.parse_account_s2,
            self.parse_oc,
        ]

        messages: list[str] = []
        for step in steps:
            result = step()
            if result["success"] and result["message"]:
                messages.append(result["message"])
            if not result["success"]:
                self._append_group_message(index, messages)
                self._append_group_error(index, result["message"])
                return self._failure(result["message"])

        self._append_group_message(index, messages)
        return self._success("\n".join(messages).strip())

    def parse_bs(self) -> dict:
        text = self._get_current_text()
        matches = list(SIDE_PATTERN.finditer(text))
        if not matches:
            return self._failure("未提取到买卖方向")
        if len(matches) > 2:
            return self._failure("买卖方向数量异常")

        sides = [Side.BUY if match.group("token").lower() in {"b", "buy"} else Side.SELL for match in matches]

        instruction_name = safe_strip(text[: matches[0].start()])
        body = safe_strip(text[matches[0].start() :])
        self.instruction_name_list[self.cur_index] = instruction_name
        self.instruction_body_list[self.cur_index] = body
        self._set_current_text(body)

        orders: list[Order] = []
        for idx, side in enumerate(sides, start=1):
            orders.append(
                Order(
                    side=side,
                    is_spread=len(sides) == 2,
                    leg_index=LegIndex.FIRST if idx == 1 and len(sides) == 2 else (LegIndex.SECOND if idx == 2 else None),
                    instruction_name=instruction_name or None,
                )
            )
        self.order_groups[self.cur_index].orders = orders
        return self._success("")

    def parse_contract(self) -> dict:
        orders = self._get_current_orders()
        if not orders:
            return self._failure("未初始化订单对象")

        leg_segments = self._get_leg_segments()
        if len(leg_segments) != len(orders):
            return self._failure("多腿合约数量异常" if len(orders) == 2 else "未提取到合约")

        inferred_product: str | None = None
        replacements: list[tuple[int, int]] = []
        notices: list[str] = []
        for index, segment in enumerate(leg_segments):
            parsed = self._parse_contract_from_segment(segment["text"], inferred_product)
            if not parsed["success"]:
                return self._failure(parsed["message"])

            raw_token = parsed["raw_token"]
            normalized_contract = parsed["normalized_contract"]
            order = orders[index]
            order.contract = str(parsed.get("symbol") or normalized_contract).upper()
            order.exchange = str(parsed.get("exchange") or "")
            inferred_product = parsed["product"]

            if raw_token.upper() != normalized_contract.upper():
                notices.append(f"合约 {raw_token} 已转换为标准合约 {normalized_contract}")

            start, end = parsed["match_span"]
            replacements.append((segment["offset"] + start, segment["offset"] + end))

        self._set_current_text(self._remove_spans(self._get_current_text(), replacements))
        return self._success("\n".join(notices).strip())

    def parse_account_s1(self) -> dict:
        orders = self._get_current_orders()
        if not orders:
            return self._failure("未初始化订单对象")

        full_text = self.order_groups[self.cur_index].raw_instruction if 0 <= self.cur_index < len(self.order_groups) else ""
        matched_value: str | None = None
        if full_text and self.static_info.account_map:
            for key in sorted(self.static_info.account_map.keys(), key=len, reverse=True):
                if key == "default":
                    continue
                if not key:
                    continue
                try:
                    if re.search(key, full_text):
                        matched_value = self.static_info.account_map[key]
                        break
                except re.error:
                    continue

        if matched_value is None:
            matched_value = self.static_info.get_default_account_for_user_id(self.current_user_id)

        if matched_value is None:
            return self._success("无资金账号")

        for order in orders:
            order.account = matched_value
        return self._success("")

    def parse_account_s2(self) -> dict:
        orders = self._get_current_orders()
        if not orders:
            return self._failure("未初始化订单对象")

        instruction_name = self.instruction_name_list[self.cur_index] if 0 <= self.cur_index < len(self.instruction_name_list) else ""
        raw_name = instruction_name.strip()
        corrected_name: str | None = None
        if raw_name:
            delimiter_indexes = [index for index in (raw_name.find(":"), raw_name.find(")")) if index >= 0]
            if delimiter_indexes:
                candidate = raw_name[: min(delimiter_indexes)].strip()
                corrected_name = candidate or None

            if corrected_name is None:
                order_name_match = ORDER_NAME_PATTERN.search(raw_name)
                if order_name_match:
                    corrected_name = order_name_match.group(0).strip()
        else:
            corrected_name = self._build_instruction_name_from_orders(orders)

        if corrected_name:
            self._set_instruction_name(corrected_name, orders)
            instruction_name = corrected_name
        elif instruction_name:
            self._set_instruction_name(instruction_name, orders)
        else:
            return self._success("")

        self._warn_if_instruction_product_mismatch(raw_name, orders)
        self._warn_if_instruction_spread_side_mismatch(raw_name, orders)
        return self._success("")

    def parse_hand(self) -> dict:
        text = self._get_current_text()
        orders = self._get_current_orders()
        if not orders:
            return self._failure("未初始化订单对象")

        if len(orders) == 2:
            leg_segments = self._get_leg_segments()
            if len(leg_segments) != 2:
                return self._failure("多腿手数数量异常")

            replacements: list[tuple[int, int]] = []
            resolved_lots: list[int | None] = []
            for index, segment in enumerate(leg_segments):
                match = LOTS_PATTERN.search(segment["text"])
                if not match:
                    match = LOTS_FALLBACK_PATTERN.search(segment["text"])

                if not match:
                    resolved_lots.append(None)
                    continue

                try:
                    lots = int(match.group("lots"))
                except ValueError:
                    return self._failure("手数格式不合法")

                if lots <= 0:
                    return self._failure("手数必须大于0")

                resolved_lots.append(lots)
                start, end = match.span()
                replacements.append((segment["offset"] + start, segment["offset"] + end))

            first_lots, second_lots = resolved_lots
            if first_lots is None and second_lots is None:
                return self._failure("未提取到手数")
            if first_lots is None:
                resolved_lots[0] = second_lots
            if second_lots is None:
                resolved_lots[1] = resolved_lots[0]

            for index, order in enumerate(orders):
                order.lots = resolved_lots[index]

            
            self._set_current_text(self._remove_spans(text, replacements))
            return self._success("")

        match = LOTS_PATTERN.search(text)
        if not match:
            match = LOTS_FALLBACK_PATTERN.search(text)
        if not match:
            return self._failure("未提取到手数")

        try:
            lots = int(match.group("lots"))
        except ValueError:
            return self._failure("手数格式不合法")

        if lots <= 0:
            return self._failure("手数必须大于0")

        for order in orders:
            order.lots = lots

        self._set_current_text(self._remove_match(text, match))
        return self._success("")

    def adjust_spread(self) -> dict:
        orders = self._get_current_orders()
        if len(orders) != 2:
            return self._success("")

        lots_differ = orders[0].lots != orders[1].lots
        product0 = self._extract_contract_product(orders[0].contract)
        product1 = self._extract_contract_product(orders[1].contract)
        product_differs = product0 != product1
        same_direction = orders[0].side == orders[1].side

        if lots_differ or product_differs or same_direction:
            for order in orders:
                order.is_spread = False
            reasons: list[str] = []
            if lots_differ:
                reasons.append(f"手数不同 ({orders[0].lots} vs {orders[1].lots})")
            if product_differs:
                reasons.append(f"品种不同 ({product0} vs {product1})")
            if same_direction:
                reasons.append(f"买卖方向相同 (均为{orders[0].side.value if orders[0].side else '?'})")
            return self._success(f"两腿订单不符合价差定义({'，'.join(reasons)}), 已取消is_spread标记")

        self._reorder_spread_orders_by_contract_month(orders)
        return self._success("")

    def parse_time(self) -> dict:
        orders = self._get_current_orders()
        if len(orders) == 2 and orders[0].is_spread:
            return self._success("多腿订单当前阶段不解析时间, 已跳过")

        text = self._get_current_text()
        if not orders:
            return self._failure("未初始化订单对象")

        parsed = self._parse_time_text(text)
        has_twap = bool(TWAP_PATTERN.search(text))
        if not parsed["success"]:
            if has_twap:
                return self._failure("TWAP指令未提取到有效时间")
            return self._success("")

        for order in orders:
            order.start_immediately = parsed["start_immediately"]
            order.start_time = None if parsed["start_immediately"] else parsed["start_time"]
            order.end_time = parsed["end_time"]
        self._set_current_text(self._remove_span(text, parsed["span"]))
        return self._success(parsed["message"])

    def parse_price(self) -> dict:
        text = self._get_current_text()
        orders = self._get_current_orders()
        if not orders:
            return self._failure("未初始化订单对象")

        market_match = MARKET_PATTERN.search(text)
        if len(orders) == 2 and orders[0].is_spread and market_match:
            return self._failure("多腿订单不支持市价")

        if len(orders) == 2 and orders[0].is_spread:
            price_match = self._find_price_match_from_end(text)
            if not price_match:
                return self._failure("多腿订单未提取到价差价格")
            price_value = float(price_match.group("price"))
            for order in orders:
                order.is_limit = True
                order.limit_price = price_value
            self._set_current_text(self._remove_match(text, price_match))
            protection_message = self._apply_spread_price_band(orders)
            if protection_message:
                self._append_group_warning(self.cur_index, protection_message)
            # 多腿进行价格修正(让档)
            shifted_message = self._apply_spread_price_shift(orders)
            if shifted_message:
                self._append_group_warning(self.cur_index, shifted_message)
            return self._success("")

        if market_match:
            for order in orders:
                order.is_limit = False
                order.limit_price = None
            self._set_current_text(self._remove_match(text, market_match))
            return self._success("")

        price_match = self._find_price_match_from_end(text)
        if not price_match:
            for order in orders:
                order.is_limit = False
                order.limit_price = None
            return self._success("未指定价格, 默认按市价处理")

        try:
            price_value = float(price_match.group("price"))
        except ValueError:
            return self._failure("价格格式不合法")

        for order in orders:
            order.is_limit = True
            order.limit_price = price_value
        self._set_current_text(self._remove_match(text, price_match))
        # 校验是否超过价格保护带,超过生成提示信息
        protection_message = self._apply_price_band(order)
        return self._success(protection_message)

    def parse_algo(self) -> dict:
        orders = self._get_current_orders()
        if not orders:
            return self._failure("算法类别无法确定")

        current_text = self._get_current_text()
        if len(orders) == 2 and orders[0].is_spread:
            for order in orders:
                order.algo_type = AlgoType.SPREAD
            return self._success("多腿订单算法类别固定为SPREAD")

        messages: list[str] = []

        # TWAP 关键字是全局匹配, 对所有订单生效
        if TWAP_PATTERN.search(current_text):
            for order in orders:
                order.algo_type = AlgoType.TWAP
            return self._success("检测到显式TWAP关键字, 算法类别已设为TWAP")

        for order in orders:
            if order.start_immediately or order.start_time or order.end_time:
                order.algo_type = AlgoType.TWAP
                messages.append("检测到时间字段, 算法类别已设为TWAP")
            else:
                order.algo_type = AlgoType.EXTENSION_ORDER

        if not messages:
            messages.append("未指定算法类别, 默认按ExtensionOrder处理")
        return self._success("\n".join(messages).strip())

    def parse_principle(self) -> dict:
        text = self._get_current_text()
        match = PRINCIPLE_PATTERN.search(text)
        if not match:
            return self._success("")

        self._set_current_text(self._remove_match(text, match))

        try:
            estimated_principle_million = float(match.group("value"))
        except ValueError:
            return self._success("")

        orders = self._get_current_orders()
        if len(orders) != 1 and orders[0].is_spread:
            self._append_group_warning(self.cur_index, "检测到预估名义本金, 目前仅支持单腿名义本金校验")
            return self._success("")

        for order in orders:
            warning = self._build_principle_warning(order, estimated_principle_million)
            if warning:
                self._append_group_warning(self.cur_index, warning)
        
        return self._success("")

    def parse_oc(self) -> dict:
        text = self._get_current_text()
        orders = self._get_current_orders()

        # 单腿 或 价差两腿: 整体匹配开平字段
        if len(orders) == 1 or (len(orders) == 2 and orders[0].is_spread):
            match = OPEN_CLOSE_PATTERN.search(text)
            if not match:
                if CTP_ENABLE:
                    self._apply_auto_offset(orders)
                else:
                    for order in orders:
                        order.offset = Offset.OPEN
                    self._append_group_warning(self.cur_index, "未指定开平方向, 默认按开仓处理")
                return self._success("")

            token = match.group("token").lower()
            offset, expected_side, message = self._decode_oc_token(token)
            for order in orders:
                if expected_side and order.side and order.side != expected_side:
                    return self._failure("开平方向与买卖方向冲突")
                order.offset = offset
            self._set_current_text(self._remove_match(text, match))
            return self._success(message)

        # 非spread, 实际是2个指令: 按 buy/sell 分段分别提取开平字段
        leg_segments = self._get_leg_segments()
        if len(leg_segments) != len(orders):
            return self._failure("无法分段提取开平方向")

        messages: list[str] = []
        replacements: list[tuple[int, int]] = []
        for index, segment in enumerate(leg_segments):
            order = orders[index]
            oc_match = OPEN_CLOSE_PATTERN.search(segment["text"])
            if not oc_match:
                if CTP_ENABLE:
                    self._apply_auto_offset([order])
                else:
                    order.offset = Offset.OPEN
                continue

            token = oc_match.group("token").lower()
            offset, expected_side, msg = self._decode_oc_token(token)
            if msg:
                messages.append(msg)
            if expected_side and order.side and order.side != expected_side:
                return self._failure("开平方向与买卖方向冲突")
            order.offset = offset
            start, end = oc_match.span()
            replacements.append((segment["offset"] + start, segment["offset"] + end))

        if replacements:
            self._set_current_text(self._remove_spans(text, replacements))
        return self._success("\n".join(messages).strip())

    @staticmethod
    def _decode_oc_token(token: str) -> tuple[Offset, Side | None, str]:
        if token == "open":
            return Offset.OPEN, None, ""
        if token == "close":
            return Offset.CLOSE, None, ""
        if token == "bo":
            return Offset.OPEN, Side.BUY, "已根据 BO/SO/BC/SC 自动映射开平方向"
        if token == "so":
            return Offset.OPEN, Side.SELL, "已根据 BO/SO/BC/SC 自动映射开平方向"
        if token == "bc":
            return Offset.CLOSE, Side.BUY, "已根据 BO/SO/BC/SC 自动映射开平方向"
        return Offset.CLOSE, Side.SELL, "已根据 BO/SO/BC/SC 自动映射开平方向"

    def _normalize_text(self, text: str) -> str:
        normalized = normalize_cn_punctuation(text)
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"[ \t]+", " ", normalized) # 去除连续空格
        lines = [safe_strip(line) for line in normalized.split("\n")] # 过滤掉空行
        return "\n".join(line for line in lines if line)

    def _append_group_message(self, index: int, messages: list[str]) -> None:
        if index < 0 or index >= len(self.order_groups):
            return
        combined = "\n".join(message for message in messages if message).strip()
        self.order_groups[index].message = combined

    def _append_group_error(self, index: int, error: str) -> None:
        if not error or index < 0 or index >= len(self.order_groups):
            return
        self.order_groups[index].error = error.strip()

    def _append_group_warning(self, index: int, warning: str) -> None:
        if not warning or index < 0 or index >= len(self.order_groups):
            return
        group = self.order_groups[index]
        parts = [group.warning, warning] if group.warning else [warning]
        group.warning = "\n".join(part for part in parts if part).strip()

    def _append_ctp_warning_from_result(self, result: dict | None) -> None:
        if not result:
            return
        warning = str(result.get("warning") or "").strip()
        if warning:
            self._append_group_warning(self.cur_index, warning)

    # def _looks_like_instruction(self, text: str) -> bool:
    #     return bool(SIDE_PATTERN.search(text))

    def _get_current_text(self) -> str:
        if self.cur_index < 0 or self.cur_index >= len(self.temp_instruction_list):
            return ""
        return self.temp_instruction_list[self.cur_index]

    def _set_current_text(self, text: str) -> None:
        if 0 <= self.cur_index < len(self.temp_instruction_list):
            self.temp_instruction_list[self.cur_index] = safe_strip(re.sub(r"\s+", " ", text))

    def _get_current_orders(self) -> list[Order]:
        if self.cur_index < 0 or self.cur_index >= len(self.order_groups):
            return []
        return self.order_groups[self.cur_index].orders

    def _get_leg_segments(self) -> list[dict]:
        text = self._get_current_text()
        matches = list(SIDE_PATTERN.finditer(text))
        if not matches:
            return []
        segments: list[dict] = []
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            raw_segment = text[start:end]
            stripped_segment = raw_segment.lstrip()
            leading_padding = len(raw_segment) - len(stripped_segment)
            segments.append(
                {
                    "text": safe_strip(raw_segment),
                    "offset": start + leading_padding,
                }
            )
        return segments

    def _parse_contract_from_segment(self, segment: str, fallback_product: str | None) -> dict:
        for standard_match in STANDARD_CONTRACT_PATTERN.finditer(segment):
            product = standard_match.group("product").upper()
            year = int(standard_match.group("year"))
            month = int(standard_match.group("month"))
            contract = f"{product}{year:02d}{month:02d}"
            verify_result = self._resolve_contract_match(contract)
            if verify_result is None:
                continue
            return {
                "success": True,
                "raw_token": standard_match.group(0),
                "normalized_contract": contract,
                "product": product,
                "match_span": standard_match.span(),
                "symbol": str(verify_result.get("symbol") or contract).upper(),
                "exchange": str(verify_result.get("exchange") or ""),
            }

        aliases = [*self.static_info.contract_alias_map.keys()]
        alias_pattern = "|".join(re.escape(alias) for alias in sorted(set(aliases), key=len, reverse=True))
        product_pattern = rf"(?:{alias_pattern}|[A-Z]{{1,2}})" if alias_pattern else r"[A-Z]{1,2}"
        candidate_prefix_pattern = r"(?:(?<=^)|(?<=[^A-Za-z0-9])|(?<=\d[xX]))"
        explicit_pattern = re.compile(
            rf"{candidate_prefix_pattern}(?P<alias>{product_pattern})(?:\s+)?(?P<month>[A-Za-z]+)(?P<year>\d{{0,2}})\b",
            re.IGNORECASE,
        )
        fallback_result: dict | None = None
        saw_fallback_candidate = False
        if fallback_product:
            fallback_pattern = re.compile(
                rf"{candidate_prefix_pattern}(?P<month>[A-Za-z]+)(?P<year>\d{{0,2}})\b",
                re.IGNORECASE,
            )
            for fallback_match in fallback_pattern.finditer(segment):
                saw_fallback_candidate = True
                month_info = self._parse_month_token(fallback_match.group("month"), fallback_match.group("year"))
                if month_info is None:
                    continue
                normalized_contract = self._build_contract_code(fallback_product, month_info["month"], month_info["year"])
                verify_result = self._resolve_contract_match(normalized_contract)
                if verify_result is None:
                    continue
                fallback_result = {
                    "success": True,
                    "raw_token": fallback_match.group(0),
                    "normalized_contract": normalized_contract,
                    "product": fallback_product,
                    "match_span": fallback_match.span(),
                    "symbol": str(verify_result.get("symbol") or normalized_contract).upper(),
                    "exchange": str(verify_result.get("exchange") or ""),
                }
                break

        saw_explicit_candidate = False
        for explicit_match in explicit_pattern.finditer(segment):
            if fallback_result is not None and fallback_result["match_span"][0] <= explicit_match.start():
                return fallback_result

            saw_explicit_candidate = True
            alias = explicit_match.group("alias")
            upper_alias = alias.upper()
            product = self.static_info.contract_alias_map.get(upper_alias, upper_alias)
            month_info = self._parse_month_token(explicit_match.group("month"), explicit_match.group("year"))
            if month_info is None:
                continue
            normalized_contract = self._build_contract_code(product, month_info["month"], month_info["year"])
            verify_result = self._resolve_contract_match(normalized_contract)
            if verify_result is None:
                continue
            return {
                "success": True,
                "raw_token": explicit_match.group(0),
                "normalized_contract": normalized_contract,
                "product": product,
                "match_span": explicit_match.span(),
                "symbol": str(verify_result.get("symbol") or normalized_contract).upper(),
                "exchange": str(verify_result.get("exchange") or ""),
            }

        if fallback_result is not None:
            return fallback_result
        if saw_fallback_candidate:
            return self._failure("合约月份无法识别")

        if saw_explicit_candidate:
            return self._failure("合约月份无法识别")

        return self._failure("未提取到合约")

    def _resolve_contract_match(self, normalized_contract: str) -> dict | None:
        contract = normalized_contract.strip().upper()
        if not contract:
            return None
        if not CTP_ENABLE:
            return {
                "exists": True,
                "exchange": "CFFEX",
                "symbol": contract,
            }

        verify_result = get_contract_info(contract)
        self._append_ctp_warning_from_result(verify_result)
        if verify_result.get("exists"):
            return verify_result

        fallback_exchange = "CFFEX" if contract.startswith(("IF", "IC", "IH", "IM")) else None
        if not fallback_exchange:
            return None
        return {
            "exists": True,
            "exchange": fallback_exchange,
            "symbol": contract,
        }
    def _parse_month_token(self, token: str, year_text: str) -> dict | None:
        cleaned = token.strip().lower()
        if cleaned in MONTH_NAME_MAP:
            month = MONTH_NAME_MAP[cleaned]
            return {"month": month, "year": self._resolve_contract_year(year_text, month)}

        upper = token.strip().upper()
        if len(upper) == 1:
            try:
                month = int(self.static_info.get_month_code(upper))
            except KeyError:
                return None
            return {"month": month, "year": self._resolve_contract_year(year_text, month)}
        return None

    def _resolve_contract_year(self, year_text: str, month: int) -> int:
        now = datetime.now()
        if year_text:
            year_value = int(year_text)
            if len(year_text) == 1:
                return 20 + year_value
            if len(year_text) == 2:
                return 2000 + year_value
            return year_value

        year = now.year
        if month < now.month:
            year += 1
        return year

    def _build_contract_code(self, product: str, month: int, year: int) -> str:
        return f"{product.upper()}{year % 100:02d}{month:02d}"

    def _reorder_spread_orders_by_contract_month(self, orders: list[Order]) -> None:
        if len(orders) != 2:
            return

        first_key = self._contract_sort_key(orders[0].contract)
        second_key = self._contract_sort_key(orders[1].contract)
        if first_key is None or second_key is None:
            return

        if first_key == second_key:
            self._append_group_warning(self.cur_index, "两腿合约为同月, 未调整远近月顺序")
            return

        if first_key < second_key:
            orders[0], orders[1] = orders[1], orders[0]

        orders[0].leg_index = LegIndex.FIRST
        orders[1].leg_index = LegIndex.SECOND

    def _contract_sort_key(self, contract: str | None) -> tuple[int, int] | None:
        if not contract:
            return None

        match = STANDARD_CONTRACT_PATTERN.fullmatch(contract.strip().upper())
        if not match:
            return None

        year = int(match.group("year"))
        month = int(match.group("month"))
        return year, month

    def _warn_if_instruction_product_mismatch(self, instruction_name: str, orders: list[Order]) -> None:
        if ")" not in instruction_name:
            return

        expected_product = self.static_info.get_instruction_name_product(instruction_name)
        if not expected_product:
            return

        actual_product = self._extract_contract_product(orders[0].contract if orders else None)
        if not actual_product:
            self._append_group_warning(self.cur_index, "订单名包含标的提示, 但无法根据实际订单判断标的品种")
            return

        if actual_product != expected_product:
            self._append_group_warning(
                self.cur_index,
                f"订单名提示标的为 {expected_product}, 实际订单标的为 {actual_product}",
            )

    def _warn_if_instruction_spread_side_mismatch(self, instruction_name: str, orders: list[Order]) -> None:
        match = re.search(r"(?<![A-Za-z])(lr|sr)(?![A-Za-z])", instruction_name, re.IGNORECASE)
        if not match:
            return

        expected_direction = match.group(1).lower()
        actual_direction = self._resolve_spread_direction(orders)
        if actual_direction is None:
            reason = self._resolve_spread_direction_unknown_reason(orders)
            self._append_group_warning(
                self.cur_index,
                f"订单名中为 {expected_direction}, 但实际订单{reason}",
            )
            return

        if actual_direction != expected_direction:
            self._append_group_warning(
                self.cur_index,
                f"订单名中为 {expected_direction}, 但实际订单为 {actual_direction}",
            )

    def _resolve_spread_direction(self, orders: list[Order]) -> str | None:
        if len(orders) != 2:
            return None

        first_key = self._contract_sort_key(orders[0].contract)
        second_key = self._contract_sort_key(orders[1].contract)
        if first_key is None or second_key is None or first_key == second_key:
            return None

        first_side = orders[0].side
        second_side = orders[1].side
        if first_side == Side.BUY and second_side == Side.SELL:
            return "lr"
        if first_side == Side.SELL and second_side == Side.BUY:
            return "sr"
        return None

    def _resolve_spread_direction_unknown_reason(self, orders: list[Order]) -> str:
        if len(orders) != 2:
            return "为单腿"

        first_key = self._contract_sort_key(orders[0].contract)
        second_key = self._contract_sort_key(orders[1].contract)
        if first_key is None or second_key is None:
            return "月序无法判断"
        if first_key == second_key:
            return "为同月"
        return "月序无法判断"

    def _extract_contract_product(self, contract: str | None) -> str | None:
        if not contract:
            return None

        match = STANDARD_CONTRACT_PATTERN.fullmatch(contract.strip().upper())
        if not match:
            return None
        return match.group("product").upper()

    def _build_instruction_name_from_orders(self, orders: list[Order]) -> str | None:
        if len(orders) == 1:
            return self._build_single_leg_instruction_name(orders[0])
        if len(orders) == 2 and orders[0].is_spread :
            return self._build_spread_instruction_name(orders)
        else:
            return self._build_single_leg_instruction_name(orders[0])+self._build_single_leg_instruction_name(orders[1])

    def _build_single_leg_instruction_name(self, order: Order) -> str | None:
        if not order.contract or not order.side or not order.lots:
            return None
        return f"{order.contract}-{order.side.value.lower()} {order.lots}x"

    def _build_spread_instruction_name(self, orders: list[Order]) -> str | None:
        direction = self._resolve_spread_direction(orders)
        if not direction:
            return None

        product = self._extract_contract_product(orders[0].contract)
        lots = orders[0].lots
        if not product or not lots:
            return None

        roll_side = "long" if direction == "lr" else "short"
        return f"{product} {roll_side} roll-{lots}x"

    def _set_instruction_name(self, instruction_name: str, orders: list[Order]) -> None:
        if 0 <= self.cur_index < len(self.instruction_name_list):
            self.instruction_name_list[self.cur_index] = instruction_name
        for order in orders:
            order.instruction_name = instruction_name

    def _apply_auto_offset(self, orders: list[Order]) -> None:
        resolved_orders: list[Order] = []
        for order in orders:
            if order.offset is not None:
                resolved_orders.append(order)
                continue

            if not order.contract or not order.side or not order.lots: 
                resolved_orders.append(order)
                continue


            position = query_position(order.contract, account=order.account)
            logger.info(
                "auto offset position lookup: account=%s contract=%s result=%s",
                order.account,
                order.contract,
                position,
            )
            self._append_ctp_warning_from_result(position)
            reverse_total, reverse_today = self._resolve_reverse_position(order, position)
            resolved_orders.extend(self._build_auto_offset_orders(order, reverse_total, reverse_today))

        orders[:] = self._rebalance_spread_auto_offset_orders(orders, resolved_orders)

    def _rebalance_spread_auto_offset_orders(
        self,
        original_orders: list[Order],
        resolved_orders: list[Order],
    ) -> list[Order]:
        logger.info("_rebalance_spread_auto_offset_orders1")
        if not (len(original_orders) == 2 and original_orders[0].is_spread):
            return resolved_orders
        if len(resolved_orders) == 2:
            return resolved_orders

        logger.info("_rebalance_spread_auto_offset_orders2")
        for order in resolved_orders:
            if order.side is None:
                self._append_group_warning(
                    self.cur_index,
                    "spread自动开平配对失败: 存在未识别买卖方向的订单, 已保留原拆单顺序",
                )
                return resolved_orders

        for order in resolved_orders:
            if order.lots is None or order.lots <= 0:
                self._append_group_warning(
                    self.cur_index,
                    "spread自动开平配对失败: 存在手数不合法的订单, 已保留原拆单顺序",
                )
                return resolved_orders

        buy_orders: list[Order] = []
        sell_orders: list[Order] = []
        for order in resolved_orders:
            if order.side == Side.BUY:
                buy_orders.append(order)
            elif order.side == Side.SELL:
                sell_orders.append(order)
            else:
                self._append_group_warning(
                    self.cur_index,
                    "spread自动开平配对失败: 买卖方向异常, 已保留原拆单顺序",
                )
                return resolved_orders

        if not buy_orders or not sell_orders:
            self._append_group_warning(
                self.cur_index,
                "spread自动开平配对失败: 买卖两组订单不完整, 已保留原拆单顺序",
            )
            return resolved_orders

        buy_total = sum(order.lots for order in buy_orders if order.lots is not None)
        sell_total = sum(order.lots for order in sell_orders if order.lots is not None)
        if buy_total != sell_total:
            self._append_group_warning(
                self.cur_index,
                "spread自动开平配对失败: 买卖总手数不一致, 已保留原拆单顺序",
            )
            return resolved_orders

        paired_orders: list[Order] = []
        buy_remaining_lots = [order.lots or 0 for order in buy_orders]
        sell_remaining_lots = [order.lots or 0 for order in sell_orders]
        buy_index = 0
        sell_index = 0

        while buy_index < len(buy_orders) and sell_index < len(sell_orders):
            buy_order = buy_orders[buy_index]
            sell_order = sell_orders[sell_index]
            buy_remaining = buy_remaining_lots[buy_index]
            sell_remaining = sell_remaining_lots[sell_index]
            matched_lots = min(buy_remaining, sell_remaining)

            if matched_lots <= 0:
                self._append_group_warning(
                    self.cur_index,
                    "spread自动开平配对失败: 存在手数不合法的订单, 已保留原拆单顺序",
                )
                return resolved_orders

            if matched_lots == buy_order.lots and matched_lots == buy_remaining:
                paired_orders.append(buy_order)
            else:
                paired_orders.append(self._clone_order_with_offset(buy_order, buy_order.offset, matched_lots))

            if matched_lots == sell_order.lots and matched_lots == sell_remaining:
                paired_orders.append(sell_order)
            else:
                paired_orders.append(self._clone_order_with_offset(sell_order, sell_order.offset, matched_lots))

            buy_remaining_lots[buy_index] -= matched_lots
            sell_remaining_lots[sell_index] -= matched_lots

            if buy_remaining_lots[buy_index] == 0:
                buy_index += 1
            if sell_remaining_lots[sell_index] == 0:
                sell_index += 1

        if not paired_orders:
            self._append_group_warning(
                self.cur_index,
                "spread自动开平配对失败: 未生成有效配对订单, 已保留原拆单顺序",
            )
            return resolved_orders
        if len(paired_orders) % 2 != 0:
            self._append_group_warning(
                self.cur_index,
                "spread自动开平配对失败: 配对结果数量异常, 已保留原拆单顺序",
            )
            return resolved_orders

        for index in range(0, len(paired_orders), 2):
            first = paired_orders[index]
            second = paired_orders[index + 1]
            if first.lots != second.lots:
                self._append_group_warning(
                    self.cur_index,
                    "spread自动开平配对失败: 配对结果手数不一致, 已保留原拆单顺序",
                )
                return resolved_orders
        self._append_group_warning(self.cur_index,"spread自动开平拆单后手数配对完成")
        return paired_orders

    def _resolve_reverse_position(self, order: Order, position: dict) -> tuple[int, int]:
        if order.side == Side.BUY:
            return int(position.get("short_total") or 0), int(position.get("short_today") or 0)
        if order.side == Side.SELL:
            return int(position.get("long_total") or 0), int(position.get("long_today") or 0)
        return 0, 0

    def _build_auto_offset_orders(self, order: Order, reverse_total: int, reverse_today: int) -> list[Order]:
        lots = order.lots
        threshold = self.static_info.get_auto_offset_threshold(order.account)

        if reverse_total <= 0 or lots <= 0:
            self._append_auto_offset_warning(
                order,
                Offset.OPEN,
                lots,
                reverse_total,
                reverse_today,
                split_applied=False,
            )
            return [self._clone_order_with_offset(order, Offset.OPEN, lots)]

        if(lots>threshold):
            closable_lots = reverse_total# max(reverse_total - reverse_today, 0)
        else:
            closable_lots = 0 if reverse_today == 0 else reverse_total

        if closable_lots >= lots:
            self._append_auto_offset_warning(
                            order,
                            Offset.CLOSE,
                            lots,
                            reverse_total,
                            reverse_today,
                            split_applied=False,
                        )
            return [self._clone_order_with_offset(order, Offset.CLOSE, lots)]
        resolved_orders: list[Order] = []
        split_applied=False
        if closable_lots > 0:
            resolved_orders.append(self._clone_order_with_offset(order, Offset.CLOSE, closable_lots))
            split_applied=True
            self._append_auto_offset_warning(
                                        order,
                                        Offset.CLOSE,
                                        closable_lots,
                                        reverse_total,
                                        reverse_today,
                                        split_applied,
                                    )
        remaining_open_lots = lots - closable_lots
        resolved_orders.append(self._clone_order_with_offset(order, Offset.OPEN, remaining_open_lots))
        self._append_auto_offset_warning(
                                                order,
                                                Offset.OPEN,
                                                remaining_open_lots,
                                                reverse_total,
                                                reverse_today,
                                                split_applied
                                            )
        warning = self._adjust_auto_offset_time(resolved_orders)
        if warning:
            self._append_group_warning(self.cur_index, warning)
        return resolved_orders



    def _adjust_auto_offset_time(self,orders: list[Order]) -> str:

        # logger.info("_adjust_auto_offset_time")
        if len(orders) != 2:
            return "拆单后调整时间: 订单数不为2, 未调整"

        first, second = orders
        if first.lots is None or second.lots is None:
            return "拆单后调整时间: 手数不合法, 未调整"

        has_start = first.start_immediately or first.start_time is not None
        if not has_start or first.end_time is None:
            return "拆单后调整时间: 未指定起始或结束时间, 未调整"

        end_dt = self._parse_order_time(first.end_time)
        if end_dt is None:
            return "拆单后调整时间: 结束时间格式不正确, 未调整"
        # current time+1 min
        now = (datetime.now() + timedelta(minutes=1)).replace(microsecond=0)
        if first.start_immediately:
            start_dt = now
        else:
            start_dt = self._parse_order_time(first.start_time)
            if start_dt is None:
                return "拆单后调整时间: 起始时间格式不正确, 未调整"
        if start_dt >end_dt:
            return "拆单后调整时间: 起始时间晚于结束时间, 未调整"

        total_lots = first.lots + second.lots

        total_seconds = int((end_dt - start_dt).total_seconds())
        first_seconds = round(total_seconds * (first.lots-1) / (total_lots-1))
        first_end_dt = start_dt + timedelta(seconds=first_seconds)
        second_start_dt = first_end_dt + timedelta(seconds=math.ceil(first_seconds / (first.lots-1)))
        if second_start_dt > end_dt:
            second_start_dt = end_dt

        first.start_immediately = False
        first.start_time = self._format_dt(start_dt)
        first.end_time = self._format_dt(first_end_dt)
        second.start_time = self._format_dt(second_start_dt)
        second.end_time = self._format_dt(end_dt)
        second.start_immediately = False
        return f"拆单后调整时间: 1腿范围 {first.start_time} -> {first.end_time}, 2腿范围 {second.start_time} -> {second.end_time}"
    def _parse_order_time(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.000+0800")
        except ValueError:
            return None


    def _clone_order_with_offset(self, order: Order, offset: Offset, lots: int) -> Order:
        order_data = order.model_dump() if hasattr(order, "model_dump") else order.dict()
        order_data["offset"] = offset
        order_data["lots"] = lots
        return Order(**order_data)

    def _append_auto_offset_warning(
        self,
        order: Order,
        offset: Offset,
        lots: int,
        reverse_total: int,
        reverse_today: int,
        split_applied: bool,
    ) -> None:

        action_text = "平仓" if offset == Offset.CLOSE else "开仓"
        account_text = order.account or "未知账户"
        warning_parts = [
            f"自动判断开平: 账户 {account_text}, 合约 {order.contract or ''}, 自动判定为{action_text} {lots} 手",
            f"查询结果: 反向持仓 {max(reverse_total, 0)} 手, 反向今仓 {max(reverse_today, 0)} 手",
        ]
        if split_applied:
            warning_parts.append("可平仓不足, 已按可平与开仓拆分订单")
        if offset == Offset.CLOSE and reverse_today > 0:
            warning_parts.append(f"存在平今风险, 今仓 {reverse_today} 手,触发平今手数阈值为{self.static_info.get_auto_offset_threshold(order.account)}")
        warning_text = ", ".join(part for part in warning_parts if part)
        # debug
        # logger.info(
        #     "auto offset warning generated: account=%s contract=%s offset=%s lots=%s warning=%s",
        #     order.account,
        #     order.contract,
        #     offset.value,
        #     lots,
        #     warning_text,
        # )
        #
        self._append_group_warning(self.cur_index, warning_text)

    def _parse_time_text(self, text: str) -> dict:
        lowered = text.lower()
        now = datetime.now()

        if "at close" in lowered:
            start = now.replace(hour=14, minute=59, second=51, microsecond=0)
            end = now.replace(hour=14, minute=59, second=54, microsecond=0)
            span = lowered.index("at close"), lowered.index("at close") + len("at close")
            return self._time_success(start, end, False, span, "已识别 at close, 时间区间调整为 14:59:51-14:59:54")

        if "now till close" in lowered or "now to close" in lowered:
            raw = "now till close" if "now till close" in lowered else "now to close"
            start = now
            end = now.replace(hour=14, minute=59, second=50, microsecond=0)
            span = lowered.index(raw), lowered.index(raw) + len(raw)
            return self._time_success(start, end, True, span, "")

        if "last 1 min" in lowered:
            start = now.replace(hour=14, minute=59, second=0, microsecond=0)
            end = now.replace(hour=14, minute=59, second=50, microsecond=0)
            span = lowered.index("last 1 min"), lowered.index("last 1 min") + len("last 1 min")
            return self._time_success(start, end, False, span, "已识别 last 1 min, 时间区间调整为 14:59:00-14:59:50")

        range_patterns = [
            re.compile(r"(?P<start>[A-Za-z0-9:]+)\s*-\s*(?P<end>[A-Za-z0-9:]+)", re.IGNORECASE),
            re.compile(r"(?P<start>[A-Za-z0-9:]+)\s+to\s+(?P<end>[A-Za-z0-9:]+)", re.IGNORECASE),
            re.compile(r"(?P<start>[A-Za-z0-9:]+)\s+till\s+(?P<end>[A-Za-z0-9:]+)", re.IGNORECASE),
            re.compile(r"(?P<start>[A-Za-z0-9:]+)\s+until\s+(?P<end>[A-Za-z0-9:]+)", re.IGNORECASE),
        ]
        for pattern in range_patterns:
            match = pattern.search(text)
            if not match:
                continue
            start_result = self._parse_start_time_point(match.group("start"), now)
            end_result = self._parse_end_time_point(match.group("end"), now)
            if start_result is None or end_result is None:
                return self._failure("时间格式不合法")
            start_dt, immediate = start_result
            end_dt = end_result
            return self._time_success(start_dt, end_dt, immediate, match.span(), "")

        duration_match = re.search(r"(?P<duration>\d+(?:\.\d+)?)\s*(?P<unit>m|min|h|hour)\b", text, re.IGNORECASE)
        if duration_match:
            value = float(duration_match.group("duration"))
            unit = duration_match.group("unit").lower()
            start_dt = now + timedelta(seconds=30)
            if unit in {"m", "min"}:
                end_dt = start_dt + timedelta(minutes=value)
            else:
                end_dt = start_dt + timedelta(hours=value)
            return self._time_success(start_dt, end_dt, False, duration_match.span(), "")

        return self._failure("未提取到时间")

    def _parse_start_time_point(self, token: str, base: datetime) -> tuple[datetime, bool] | None:
        lowered = token.lower()
        if lowered == "now":
            return base, True
        if lowered == "close":
            return base.replace(hour=14, minute=59, second=50, microsecond=0), False

        for fmt in ("%H:%M", "%H%M"):
            try:
                parsed = datetime.strptime(token, fmt)
                dt = base.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
                return self._adjust_start_time(dt, base)
            except ValueError:
                continue

        ampm_match = re.fullmatch(r"(?P<hour>\d{1,2})(?P<ampm>am|pm)", lowered)
        if ampm_match:
            hour = int(ampm_match.group("hour")) % 12
            if ampm_match.group("ampm") == "pm":
                hour += 12
            dt = base.replace(hour=hour, minute=0, second=0, microsecond=0)
            return self._adjust_start_time(dt, base)

        return None

    def _parse_end_time_point(self, token: str, base: datetime) -> datetime | None:
        lowered = token.lower()
        if lowered == "close":
            return base.replace(hour=14, minute=59, second=50, microsecond=0)
        if lowered == "now":
            return base

        for fmt in ("%H:%M", "%H%M"):
            try:
                parsed = datetime.strptime(token, fmt)
                return base.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            except ValueError:
                continue

        ampm_match = re.fullmatch(r"(?P<hour>\d{1,2})(?P<ampm>am|pm)", lowered)
        if ampm_match:
            hour = int(ampm_match.group("hour")) % 12
            if ampm_match.group("ampm") == "pm":
                hour += 12
            return base.replace(hour=hour, minute=0, second=0, microsecond=0)

        return None

    def _adjust_start_time(self, value: datetime, base: datetime) -> tuple[datetime, bool]:
        threshold = base + timedelta(seconds=30)
        if value <= threshold:
            return threshold, True
        return value, False

    def _time_success(
        self,
        start_dt: datetime,
        end_dt: datetime,
        immediate: bool,
        span: tuple[int, int],
        message: str,
    ) -> dict:
        # if end_dt <= start_dt:
        #     return self._failure("开始时间必须早于结束时间")

        notes: list[str] = []
        if immediate:
            notes.append("开始时间早于当前时间+30秒, 已按立即开始处理")

        start_dt, start_note = self._normalize_start_time(start_dt)
        if start_note:
            notes.append(start_note)

        end_dt, end_note = self._normalize_end_time(end_dt)
        if end_note:
            notes.append(end_note)
        if message:
            notes.append(message)

        return {
            "success": True,
            "message": "\n".join(notes).strip(),
            "start_time": self._format_dt(start_dt),
            "end_time": self._format_dt(end_dt),
            "start_immediately": immediate,
            "span": span,
        }

    def _normalize_start_time(self, value: datetime) -> tuple[datetime, str]:
        if value.hour == 9 and value.minute == 30 and value.second == 0:
            return value.replace(hour=9, minute=30, second=5), "开始时间 9:30 已修正为 9:30:05"
        if value.hour == 13 and value.minute == 0 and value.second == 0:
            return value.replace(hour=13, minute=0, second=5), "开始时间 13:00 已修正为 13:00:05"
        return value, ""

    def _normalize_end_time(self, value: datetime) -> tuple[datetime, str]:
        if value.hour == 11 and value.minute == 30 and value.second == 0:
            return value.replace(hour=11, minute=29, second=50), "结束时间 11:30 已修正为 11:29:50"
        if value.hour == 15 and value.minute == 0 and value.second == 0:
            return value.replace(hour=14, minute=59, second=50), "结束时间 15:00 已修正为 14:59:50"
        return value, ""

    def _format_dt(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S.000+0800")

    def _apply_price_band(self, order: Order) -> str:
        ratio = self.static_info.get_price_limit_ratio()
        if ratio < 0 or order.limit_price is None or not order.contract:
            return ""

        if not CTP_ENABLE:
            return "限价触发价格保护提醒"

        market_result = query_last_price(order.contract)
        self._append_ctp_warning_from_result(market_result)
        last_price = market_result.get("last_price")
        if last_price is None:
            return ""

        try:
            upper_bound = float(last_price) * (1 + ratio)
            lower_bound = float(last_price) * (1 - ratio)
        except (TypeError, ValueError):
            return ""

        if order.limit_price > upper_bound or order.limit_price < lower_bound:
            return "限价触发价格保护提醒"
        return ""

    def _apply_spread_price_band(self, orders: list[Order]) -> str:
        ratio = self.static_info.get_spread_price_limit_ratio()
        if ratio <= 0 or len(orders) != 2:
            return ""

        left_order = orders[0]
        right_order = orders[1]
        extracted_spread = left_order.limit_price
        if extracted_spread is None or not left_order.contract or not right_order.contract:
            return ""

        if not CTP_ENABLE:
            return ""

        left_market_result = query_last_price(left_order.contract)
        right_market_result = query_last_price(right_order.contract)
        self._append_ctp_warning_from_result(left_market_result)
        self._append_ctp_warning_from_result(right_market_result)
        left_last_price = left_market_result.get("last_price")
        right_last_price = right_market_result.get("last_price")
        if left_last_price is None or right_last_price is None:
            return ""

        try:
            actual_spread = float(left_last_price) - float(right_last_price)
            spread_diff = abs(float(extracted_spread) - actual_spread)
            threshold = abs(actual_spread) * ratio
        except (TypeError, ValueError):
            return ""

        if spread_diff <= threshold:
            return "未超过价格保护带"

        return (
            f"多腿价差保护提醒: 提取价差 {self._format_price_value(extracted_spread)}，"
            f"实际价差 {self._format_price_value(actual_spread)}，"
            f"差值 {self._format_price_value(spread_diff)}，"
            f"超过价格保护率 {self._format_percent_value(ratio)}"
        )

    def _build_principle_warning(self, order: Order, estimated_principle_million: float) -> str:
        if estimated_principle_million <= 0:
            return ""

        if not CTP_ENABLE:
            return "检测到预估名义本金, 当前未启用CTP, 无法校验实际名义本金"

        if not order.contract or not order.lots:
            return "检测到预估名义本金, 但缺少合约或手数, 无法校验实际名义本金"

        exchange_rate = self.static_info.exchange_rate
        if exchange_rate is None:
            return "检测到预估名义本金, 但缺少汇率配置, 无法校验实际名义本金"

        market_result = query_last_price(order.contract)
        self._append_ctp_warning_from_result(market_result)
        last_price = market_result.get("last_price")
        if last_price is None:
            return (
                f"检测到预估名义本金, 但未查到合约 {order.contract} 最新价, 无法校验实际名义本金"
                f"{self._format_ctp_last_error_detail()}"
            )

        try:
            normalized_last_price = float(last_price)
        except (TypeError, ValueError):
            return f"检测到预估名义本金, 但合约 {order.contract} 最新价为无效值, 无法校验实际名义本金"
        # CTP often uses DBL_MAX-like sentinel values to mean "invalid market data".
        if (
            not math.isfinite(normalized_last_price)
            or normalized_last_price <= 0
            or normalized_last_price >= (sys.float_info.max * 0.9)
        ):
            return f"检测到预估名义本金, 但合约 {order.contract} 最新价为无效值, 无法校验实际名义本金"

        contract_info = get_contract_info(order.contract)
        self._append_ctp_warning_from_result(contract_info)
        volume_multiple = contract_info.get("volume_multiple")
        logger.info(
            "principle check: contract=%s last_price=%r volume_multiple=%r exchange_rate=%r lots=%r",
            order.contract,
            normalized_last_price,
            volume_multiple,
            exchange_rate,
            order.lots,
        )
        if volume_multiple is None:
            return (
                f"检测到预估名义本金, 但未查到合约 {order.contract} 合约乘数, 无法校验实际名义本金"
                f"{self._format_ctp_last_error_detail()}"
            )

        try:
            actual_principle = (
                normalized_last_price * int(order.lots) * float(volume_multiple) / float(exchange_rate)
            )
        except (TypeError, ValueError):
            return f"检测到预估名义本金, 但合约 {order.contract} 实际名义本金计算失败"

        if actual_principle <= 0:
            return f"检测到预估名义本金, 但合约 {order.contract} 实际名义本金为0, 无法校验"

        actual_principle_million = actual_principle / 1_000_000
        ratio = estimated_principle_million / actual_principle_million
        ratio_min, ratio_max = self.static_info.get_principle_ratio_range()
        if ratio_min is None or ratio_max is None:
            return ""
        if ratio_min <= ratio <= ratio_max:
            return ""

        return (
            f"预估名义本金约 {estimated_principle_million:.2f}m, 实际名义本金约 {actual_principle_million:.2f}m, "
            f"比值 {ratio:.4f} 不在配置范围 [{ratio_min}, {ratio_max}] 内"
        )

    def _format_ctp_last_error_detail(self) -> str:
        last_error = get_last_error()
        if not last_error:
            return ""
        return f"; CTP最近错误: {last_error}"

    def _apply_spread_price_shift(self, orders: list[Order]) -> str:
        if not orders:
            return ""
        first = orders[0]
        original_price = first.limit_price
        if original_price is None:
            return ""
        symbol = first.contract or ""
        product = self._extract_contract_product(symbol) or symbol.upper()
        start_time = first.start_time
        shift_ticks = self.static_info.get_price_shift_ticks(symbol, start_time)
        if shift_ticks in (None, 0, 0.0) and product:
            shift_ticks = self.static_info.get_price_shift_ticks(product, start_time)
        if shift_ticks in (None, 0, 0.0):
            return ""

        try:
            shift_tick_count = abs(float(shift_ticks))
        except (TypeError, ValueError):
            return ""

        if shift_tick_count == 0:
            return ""

        price_tick: float | None = None
        raw_price_tick = None
        if CTP_ENABLE and symbol:
            contract_info = get_contract_info(symbol)
            self._append_ctp_warning_from_result(contract_info)
            # logger.info("spread price shift contract lookup: symbol=%s contract_info=%s", symbol, contract_info)
            raw_price_tick = contract_info.get("price_tick")
            if raw_price_tick is not None:
                try:
                    price_tick = abs(float(raw_price_tick))
                except (TypeError, ValueError):
                    price_tick = None

        if price_tick in (None, 0, 0.0):
            return (
                f"多腿价差原限价 {self._format_price_value(original_price)}，"
                f"按 {(product or '价差')} 让档 {self._format_price_value(shift_tick_count)} tick，"
                f"查到的 price_tick={raw_price_tick}，未执行价格调整"
            )

        if first.side == Side.BUY:
            adjusted_price = original_price - (shift_tick_count * price_tick)
        else:
            adjusted_price = original_price + (shift_tick_count * price_tick)

        for order in orders:
            order.limit_price = adjusted_price

        return (
            f"多腿价差原限价 {self._format_price_value(original_price)}，"
            f"按 {(product or '价差')} 让档 {self._format_price_value(shift_tick_count)} tick"
            f"（price_tick={self._format_price_value(price_tick)}），"
            f"调整后为 {self._format_price_value(adjusted_price)}"
        )

    def _format_price_value(self, value: float | int) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _format_percent_value(self, value: float) -> str:
        percent_value = value * 100
        if float(percent_value).is_integer():
            return f"{int(percent_value)}%"
        return f"{percent_value:g}%"

    def _find_price_match_from_end(self, text: str) -> re.Match[str] | None:
        matches: list[re.Match[str]] = []
        for pattern in (AT_PRICE_PATTERN, AT_SYMBOL_PRICE_PATTERN, NEGATIVE_PRICE_PATTERN):
            matches.extend(pattern.finditer(text))

        if not matches:
            return None

        return max(matches, key=lambda match: (match.end(), match.start()))

    def _remove_match(self, text: str, match: re.Match[str]) -> str:
        return self._remove_span(text, match.span())

    def _remove_span(self, text: str, span: tuple[int, int]) -> str:
        start, end = span
        return safe_strip(f"{text[:start]} {text[end:]}")

    def _remove_spans(self, text: str, spans: list[tuple[int, int]]) -> str:
        result = text
        for start, end in sorted(spans, reverse=True):
            result = self._remove_span(result, (start, end))
        return result

    def _success(self, message: str) -> dict:
        return {"success": True, "message": message}

    def _failure(self, message: str) -> dict:
        return {"success": False, "message": message}
