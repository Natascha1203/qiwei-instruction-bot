import json
import re
from copy import deepcopy
from datetime import datetime, time
from pathlib import Path
from typing import Any


class StaticInfo:
    """Container for configurable static lookup tables used by parsing."""

    def __init__(
        self,
        account_map: dict[str, Any] | None = None,
        contract_alias_map: dict[str, str] | None = None,
        instruction_name_product_map: dict[str, str] | None = None,
        auto_offset_threshold_map: dict[str, Any] | None = None,
        auto_offset_default_threshold: int = 0,
        month_code_map: dict[str, str] | None = None,
        price_shift_rules: dict[str, Any] | None = None,
        price_limit_ratio: float = 0.0,
        spread_price_limit_ratio: float = 0.0,
        exchange_rate: float | None = None,
        principle_ratio_min: float | None = None,
        principle_ratio_max: float | None = None,
    ) -> None:
        self.account_map = account_map or {}
        self.contract_alias_map = contract_alias_map or {
            "IFB": "IF",
            "SNSZ300": "IF",
            "FFD": "IC",
            "CSI500": "IC",
            "FFB": "IH",
            "SSE50": "IH",
            "IFD": "IM",
            "CSI1000": "IM",
            "SH000852": "IM",
            "000852.SH": "IM",
            "SZ399852": "IM",
            "339852.SZ": "IM",
        }
        self.instruction_name_product_map = {
            key.strip().lower(): value.strip().upper()
            for key, value in (
                instruction_name_product_map
                or {
                    "c5": "IC",
                    "c10": "IM",
                    "s5": "IH",
                    "c3": "IF",
                }
            ).items()
            if key and value
        }
        self.auto_offset_threshold_map = self._normalize_auto_offset_threshold_map(auto_offset_threshold_map or {})
        self.auto_offset_default_threshold = int(auto_offset_default_threshold)
        self.month_code_map = month_code_map or {
            "F": "01",
            "G": "02",
            "H": "03",
            "J": "04",
            "K": "05",
            "M": "06",
            "N": "07",
            "Q": "08",
            "U": "09",
            "V": "10",
            "X": "11",
            "Z": "12",
        }
        self.price_shift_rules = price_shift_rules or {}
        self.price_limit_ratio = price_limit_ratio
        self.spread_price_limit_ratio = spread_price_limit_ratio
        self.exchange_rate = exchange_rate
        self.principle_ratio_min = float(principle_ratio_min) if principle_ratio_min is not None else None
        self.principle_ratio_max = float(principle_ratio_max) if principle_ratio_max is not None else None

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> "StaticInfo":
        return cls(
            account_map=data.get("account_map") or {},
            contract_alias_map=data.get("contract_alias_map") or {},
            instruction_name_product_map=data.get("instruction_name_product_map") or {},
            auto_offset_threshold_map=data.get("auto_offset_threshold_map") or {},
            auto_offset_default_threshold=int(data.get("auto_offset_default_threshold") or 0),
            month_code_map=data.get("month_code_map") or {},
            price_shift_rules=data.get("price_shift_rules") or {},
            price_limit_ratio=float(data.get("price_limit_ratio") or 0.0),
            spread_price_limit_ratio=float(data.get("spread_price_limit_ratio") or 0.0),
            exchange_rate=data.get("exchange_rate"),
            principle_ratio_min=data.get("principle_ratio_min"),
            principle_ratio_max=data.get("principle_ratio_max"),
        )

    @classmethod
    def load_from_json(cls, path: str | Path) -> "StaticInfo":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_json_data(data)

    def to_json_data(self) -> dict[str, Any]:
        return {
            "account_map": deepcopy(self.account_map),
            "contract_alias_map": deepcopy(self.contract_alias_map),
            "instruction_name_product_map": deepcopy(self.instruction_name_product_map),
            "auto_offset_threshold_map": deepcopy(self.auto_offset_threshold_map),
            "auto_offset_default_threshold": self.auto_offset_default_threshold,
            "month_code_map": deepcopy(self.month_code_map),
            "price_shift_rules": deepcopy(self.price_shift_rules),
            "price_limit_ratio": self.price_limit_ratio,
            "spread_price_limit_ratio": self.spread_price_limit_ratio,
            "exchange_rate": self.exchange_rate,
            "principle_ratio_min": self.principle_ratio_min,
            "principle_ratio_max": self.principle_ratio_max,
        }

    def save_to_json(self, path: str | Path) -> None:
        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(self.to_json_data(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clone(self) -> "StaticInfo":
        return StaticInfo.from_json_data(self.to_json_data())

    def replace_with(self, other: "StaticInfo") -> None:
        self.account_map = deepcopy(other.account_map)
        self.contract_alias_map = deepcopy(other.contract_alias_map)
        self.instruction_name_product_map = deepcopy(other.instruction_name_product_map)
        self.auto_offset_threshold_map = deepcopy(other.auto_offset_threshold_map)
        self.auto_offset_default_threshold = other.auto_offset_default_threshold
        self.month_code_map = deepcopy(other.month_code_map)
        self.price_shift_rules = deepcopy(other.price_shift_rules)
        self.price_limit_ratio = other.price_limit_ratio
        self.spread_price_limit_ratio = other.spread_price_limit_ratio
        self.exchange_rate = other.exchange_rate
        self.principle_ratio_min = other.principle_ratio_min
        self.principle_ratio_max = other.principle_ratio_max

    def get_default_account(self) -> str | None:
        value = self.account_map.get("default")
        if isinstance(value, dict):
            return None
        normalized = str(value or "").strip()
        return normalized or None

    def get_default_account_for_user_id(self, user_id: str) -> str | None:
        default_value = self.account_map.get("default")
        normalized_user_id = user_id.strip()
        if isinstance(default_value, dict):
            if not normalized_user_id:
                return None
            mapped_value = str(default_value.get(normalized_user_id) or "").strip()
            return mapped_value or None
        return self.get_default_account()

    def format_supported_settings(self) -> list[str]:
        lines: list[str] = []
        if self.exchange_rate is not None:
            lines.append(f"汇率={self._format_number(self.exchange_rate)}")

        default_account = self.get_default_account()
        if default_account:
            lines.append(f"默认资金账号={default_account}")

        lines.append(f"单腿价格保护带={self._format_percent(self.price_limit_ratio)}")

        ratio_line = self._format_principle_ratio_line()
        if ratio_line:
            lines.append(ratio_line)
        return lines

    def apply_chat_setting_lines(self, lines: list[str], user_id: str = "") -> tuple[list[str], list[str]]:
        updated_lines: list[str] = []
        errors: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if "=" not in line:
                errors.append(f"{line}: 配置格式必须为 名称=值")
                continue
            name, raw_value = line.split("=", 1)
            name = name.strip()
            raw_value = raw_value.strip()
            if not name:
                errors.append(f"{line}: 配置名称不能为空")
                continue
            try:
                updated_lines.append(self.apply_chat_setting(name, raw_value, user_id=user_id))
            except ValueError as exc:
                errors.append(f"{name}={raw_value}: {exc}")
        return updated_lines, errors

    def apply_chat_setting(self, name: str, raw_value: str, user_id: str = "") -> str:
        if name == "汇率":
            self.exchange_rate = self._parse_float(raw_value, "汇率")
            return f"汇率={self._format_number(self.exchange_rate)}"

        if name == "默认资金账号":
            account = raw_value.strip()
            if not account:
                raise ValueError("默认资金账号不能为空")
            normalized_user_id = user_id.strip()
            if not normalized_user_id:
                raise ValueError("缺少userid，无法设置默认资金账号")
            default_value = self.account_map.get("default")
            if isinstance(default_value, dict):
                default_account_map = deepcopy(default_value)
            else:
                default_account_map = {}
            default_account_map[normalized_user_id] = account
            self.account_map["default"] = default_account_map
            return f"默认资金账号={account}"

        if name == "允许平今手数阈值":
            threshold = self._parse_non_negative_int(raw_value, "允许平今手数阈值")
            normalized_user_id = user_id.strip()
            if not normalized_user_id:
                raise ValueError("缺少userid，无法设置允许平今手数阈值")
            default_value = self.auto_offset_threshold_map.get("default")
            if isinstance(default_value, dict):
                default_threshold_map = deepcopy(default_value)
            else:
                default_threshold_map = {}
            default_threshold_map[normalized_user_id] = threshold
            self.auto_offset_threshold_map["default"] = default_threshold_map
            return f"允许平今手数阈值={threshold}"

        if name == "单腿价格保护带":
            self.price_limit_ratio = self._parse_percent(raw_value, "单腿价格保护带")
            return f"单腿价格保护带={self._format_percent(self.price_limit_ratio)}"

        if name == "预估名义本金比例":
            ratio_min, ratio_max = self._parse_percent_range(raw_value, "预估名义本金比例")
            self.principle_ratio_min = ratio_min
            self.principle_ratio_max = ratio_max
            return self._format_principle_ratio_line() or ""

        raise ValueError("不支持的设置项")

    # def get_account(self, full_or_short_account: str) -> str:
    #     key = full_or_short_account.strip()
    #     return self.account_map.get(key, key)

    def normalize_contract_alias(self, text: str) -> str:
        key = text.strip().upper()
        return self.contract_alias_map.get(key, text.strip())

    def get_instruction_name_product(self, text: str) -> str | None:
        lowered = text.strip().lower()
        if not lowered:
            return None
        for key in sorted(self.instruction_name_product_map.keys(), key=len, reverse=True):
            if key in lowered:
                return self.instruction_name_product_map[key]
        return None

    def get_auto_offset_threshold(self, account: str | None, user_id: str = "") -> int:
        if account:
            account_key = account.strip()
            if account_key in self.auto_offset_threshold_map:
                return self.auto_offset_threshold_map[account_key]
        default_value = self.auto_offset_threshold_map.get("default")
        normalized_user_id = user_id.strip()
        if isinstance(default_value, dict):
            if normalized_user_id and normalized_user_id in default_value:
                return int(default_value[normalized_user_id])
        elif default_value is not None:
            return int(default_value)
        return self.auto_offset_default_threshold

    @staticmethod
    def _normalize_auto_offset_threshold_map(raw_map: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in raw_map.items():
            if not key:
                continue
            normalized_key = key.strip()
            if normalized_key == "default" and isinstance(value, dict):
                normalized[normalized_key] = {
                    str(user_id).strip(): int(threshold)
                    for user_id, threshold in value.items()
                    if str(user_id).strip()
                }
            else:
                normalized[normalized_key] = int(value)
        return normalized

    def get_month_code(self, text: str) -> str:
        key = text.strip().upper()
        return self.month_code_map[key]

    def get_price_shift_ticks(self, symbol: str, start_time: str | None) -> Any:
        if not self.price_shift_rules:
            return None

        target_time = self._parse_time_from_string(start_time) if start_time else datetime.now().time()
        product = self._extract_contract_product(symbol)

        for time_range, product_rules in self.price_shift_rules.items():
            if not isinstance(product_rules, dict):
                continue
            if self._time_in_range(target_time, time_range):
                if product and product in product_rules:
                    return product_rules[product]
                if symbol in product_rules:
                    return product_rules[symbol]
                return product_rules.get("default", 0)

        return 0

    @staticmethod
    def _time_in_range(target: time, time_range: str) -> bool:
        parts = time_range.split("-")
        if len(parts) != 2:
            return False
        try:
            start = datetime.strptime(parts[0].strip(), "%H:%M:%S").time()
            end = datetime.strptime(parts[1].strip(), "%H:%M:%S").time()
        except ValueError:
            return False
        return start <= target <= end

    @staticmethod
    def _parse_time_from_string(dt_string: str) -> time:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(dt_string, fmt).time()
            except ValueError:
                continue
        return datetime.now().time()

    @staticmethod
    def _extract_contract_product(symbol: str | None) -> str | None:
        if not symbol:
            return None
        match = re.fullmatch(r"(?P<product>[A-Z]{1,2})\d{4}", symbol.strip().upper())
        if not match:
            return None
        return match.group("product")

    def get_price_limit_ratio(self) -> float:
        return self.price_limit_ratio

    def get_spread_price_limit_ratio(self) -> float:
        return self.spread_price_limit_ratio

    def get_principle_ratio_range(self) -> tuple[float | None, float | None]:
        return self.principle_ratio_min, self.principle_ratio_max

    def _format_principle_ratio_line(self) -> str | None:
        if self.principle_ratio_min is None or self.principle_ratio_max is None:
            return None
        return (
            f"预估名义本金比例="
            f"{self._format_percent(self.principle_ratio_min)}-"
            f"{self._format_percent(self.principle_ratio_max)}"
        )

    def _format_number(self, value: float | int | None) -> str:
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _format_percent(self, value: float | None) -> str:
        if value is None:
            return ""
        percent_value = value * 100
        if float(percent_value).is_integer():
            return f"{int(percent_value)}%"
        return f"{percent_value:g}%"

    def _parse_float(self, raw_value: str, field_name: str) -> float:
        try:
            return float(raw_value.strip())
        except ValueError as exc:
            raise ValueError(f"{field_name}必须是数字") from exc

    def _parse_non_negative_int(self, raw_value: str, field_name: str) -> int:
        try:
            value = int(raw_value.strip())
        except ValueError as exc:
            raise ValueError(f"{field_name}必须是整数") from exc
        if value < 0:
            raise ValueError(f"{field_name}不能小于0")
        return value

    def _parse_percent(self, raw_value: str, field_name: str) -> float:
        value = raw_value.strip()
        if not value.endswith("%"):
            raise ValueError(f"{field_name}必须使用百分比格式")
        number_text = value[:-1].strip()
        try:
            return float(number_text) / 100
        except ValueError as exc:
            raise ValueError(f"{field_name}百分比格式不合法") from exc

    def _parse_percent_range(self, raw_value: str, field_name: str) -> tuple[float, float]:
        value = raw_value.strip()
        if "-" not in value:
            raise ValueError(f"{field_name}必须使用 95%-105% 格式")
        left, right = value.split("-", 1)
        ratio_min = self._parse_percent(left.strip(), field_name)
        ratio_max = self._parse_percent(right.strip(), field_name)
        if ratio_min > ratio_max:
            raise ValueError(f"{field_name}下限不能大于上限")
        return ratio_min, ratio_max
