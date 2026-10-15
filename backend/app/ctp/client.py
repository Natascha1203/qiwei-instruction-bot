from __future__ import annotations

import threading
import time
from typing import Any, Optional

try:
    import ThostFtdcApi as api
    CTP_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:
    api = None
    CTP_IMPORT_ERROR = exc

from ..settings import get_settings


MOCK_CONTRACTS = {
    "IF2604": {"exchange": "CFFEX", "price_tick": 0.2, "volume_multiple": 300},
    "IC2604": {"exchange": "CFFEX", "price_tick": 0.2, "volume_multiple": 200},
    "RB2510": {"exchange": "SHFE", "price_tick": 1.0, "volume_multiple": 10},
}
CTP_USER_PRODUCT_INFO = "test"
QUERY_KIND_LOAD_CONTRACTS = "load_contracts"
QUERY_KIND_POSITION = "query_position"
QUERY_KIND_MARKET = "query_last_price"
QUERY_MIN_INTERVAL_SECONDS = 1.0


def _mock_get_contract_info(symbol: str) -> dict:
    upper = symbol.upper()
    info = MOCK_CONTRACTS.get(upper)
    return {
        "exists": info is not None,
        "exchange": info.get("exchange") if info else None,
        "symbol": upper if info else None,
        "price_tick": info.get("price_tick") if info else None,
        "volume_multiple": info.get("volume_multiple") if info else None,
        "warning": None,
    }


def _empty_contract_result() -> dict:
    return {
        "exists": False,
        "exchange": None,
        "symbol": None,
        "price_tick": None,
        "volume_multiple": None,
        "warning": None,
    }


def _empty_position_result(symbol: str = "") -> dict:
    return {
        "symbol": symbol,
        "long_total": 0,
        "long_today": 0,
        "short_total": 0,
        "short_today": 0,
        "warning": None,
    }


def _empty_market_result(symbol: str = "") -> dict:
    return {"symbol": symbol, "last_price": None, "warning": None}


def _with_warning(result: dict, warning: str | None) -> dict:
    merged = dict(result)
    merged["warning"] = warning
    return merged


def _build_ctp_warning(last_error: str | None, default: str = "CTP未连接") -> str:
    detail = (last_error or "").strip()
    if not detail:
        return default
    return f"{default}: {detail}"


def _decode_ctp_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip() or None
    return str(value).strip() or None


def _format_rsp_error(pRspInfo) -> str | None:
    if not pRspInfo:
        return None
    error_id = getattr(pRspInfo, "ErrorID", None)
    error_msg = _decode_ctp_value(getattr(pRspInfo, "ErrorMsg", None))
    return f"ErrorID={error_id}, ErrorMsg={error_msg}"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def get_contract_info(symbol: str) -> dict:
    settings = get_settings()
    if not settings.ctp_enabled or api is None:
        return _mock_get_contract_info(symbol)

    try:
        return CTPClient.get_instance().get_contract_info(symbol)
    except Exception as exc:
        return _with_warning(_empty_contract_result(), f"CTP查询合约信息异常: {exc}")


def get_last_error() -> str | None:
    settings = get_settings()
    if not settings.ctp_enabled or api is None:
        return None

    try:
        return CTPClient.get_instance().last_error()
    except Exception:
        return None


def query_position(symbol: str, account: str = "") -> dict:
    settings = get_settings()
    if not settings.ctp_enabled or api is None:
        return _empty_position_result(symbol.strip())

    try:
        return CTPClient.get_instance().query_position(symbol, account=account)
    except Exception as exc:
        return _with_warning(_empty_position_result(symbol.strip()), f"CTP查询持仓异常: {exc}")


def query_last_price(symbol: str) -> dict:
    settings = get_settings()
    if not settings.ctp_enabled or api is None:
        return _empty_market_result(symbol.strip())

    try:
        return CTPClient.get_instance().query_last_price(symbol)
    except Exception as exc:
        return _with_warning(_empty_market_result(symbol.strip()), f"CTP查询行情异常: {exc}")


if api is not None:
    _CTP_SPI_BASE = api.CThostFtdcTraderSpi
else:
    _CTP_SPI_BASE = object


class CTPClientSpi(_CTP_SPI_BASE):
    def __init__(self, client: "CTPClient"):
        if api is not None:
            api.CThostFtdcTraderSpi.__init__(self)
        self._client = client

    def OnFrontConnected(self) -> None:
        settings = get_settings()
        req = api.CThostFtdcReqAuthenticateField()
        req.UserID = settings.ctp_user_id
        req.BrokerID = settings.ctp_broker_id
        req.AuthCode = settings.ctp_auth_code
        req.AppID = settings.ctp_app_id
        req.UserProductInfo = CTP_USER_PRODUCT_INFO

        request_id = self._client.next_request_id()
        ret = self._client._api.ReqAuthenticate(req, request_id)
        if ret < 0:
            self._client._connected = False
            self._client._last_error = f"ReqAuthenticate returned {ret}"
            self._client._event.set()

    def OnRspAuthenticate(self, pRspAuthenticateField, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID != 0:
            self._client._connected = False
            self._client._last_error = "OnRspAuthenticate failed: " + str(_format_rsp_error(pRspInfo))
            self._client._event.set()
            return

        settings = get_settings()
        login_field = api.CThostFtdcReqUserLoginField()
        login_field.BrokerID = settings.ctp_broker_id
        login_field.UserID = settings.ctp_user_id
        login_field.Password = settings.ctp_password
        login_field.UserProductInfo = CTP_USER_PRODUCT_INFO

        request_id = self._client.next_request_id()
        ret = self._client._api.ReqUserLogin(login_field, request_id)
        if ret < 0:
            self._client._connected = False
            self._client._last_error = f"ReqUserLogin returned {ret}"
            self._client._event.set()

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID != 0:
            self._client._connected = False
            self._client._last_error = "OnRspUserLogin failed: " + str(_format_rsp_error(pRspInfo))
        else:
            self._client._connected = True
            self._client._session_id = getattr(pRspUserLogin, "SessionID", 0)
            self._client._last_error = None
        self._client._event.set()

    def OnRspUserLogout(self, pUserLogout, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID != 0:
            self._client._last_error = "OnRspUserLogout failed: " + str(_format_rsp_error(pRspInfo))
        else:
            self._client._connected = False
            self._client._last_error = None
        self._client._event.set()

    def OnRspQryInstrument(self, pInstrument, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID != 0:
            self._client._contract_query_result = _empty_contract_result()
            self._client._last_error = "OnRspQryInstrument failed: " + str(_format_rsp_error(pRspInfo))
        elif pInstrument:
            symbol = _decode_ctp_value(getattr(pInstrument, "InstrumentID", None))
            exchange = _decode_ctp_value(getattr(pInstrument, "ExchangeID", None))
            price_tick = _to_float(getattr(pInstrument, "PriceTick", None))
            volume_multiple = _to_int(getattr(pInstrument, "VolumeMultiple", None))
            if symbol and exchange:
                contract_info = {
                    "exchange": exchange,
                    "price_tick": price_tick,
                    "volume_multiple": volume_multiple,
                }
                with self._client._contracts_lock:
                    self._client._contracts[symbol] = contract_info
                    self._client._contract_aliases[symbol.upper()] = symbol
                self._client._contract_query_result = {
                    "exists": True,
                    "exchange": exchange,
                    "symbol": symbol,
                    "price_tick": price_tick,
                    "volume_multiple": volume_multiple,
                }
                self._client._last_error = None
        elif bIsLast:
            self._client._contract_query_result = _empty_contract_result()

        if bIsLast:
            self._client._contracts_loaded = self._client._last_error is None
            if self._client._query_kind == QUERY_KIND_LOAD_CONTRACTS:
                self._client._query_kind = None
                self._client._query_symbol = None
            self._client._event.set()

    def OnRspQryInvestorPosition(self, pInvestorPosition, pRspInfo, nRequestID, bIsLast) -> None:
        if self._client._query_kind != QUERY_KIND_POSITION:
            if bIsLast:
                self._client._event.set()
            return

        if pRspInfo and pRspInfo.ErrorID != 0:
            self._client._position_query_result = _empty_position_result(self._client._query_symbol or "")
            self._client._last_error = "OnRspQryInvestorPosition failed: " + str(_format_rsp_error(pRspInfo))
        elif pInvestorPosition:
            symbol = _decode_ctp_value(getattr(pInvestorPosition, "InstrumentID", None))
            if symbol and symbol == self._client._query_symbol:
                direction = _decode_ctp_value(getattr(pInvestorPosition, "PosiDirection", None)) or ""
                position = _to_int(getattr(pInvestorPosition, "Position", None))
                today_position = _to_int(getattr(pInvestorPosition, "TodayPosition", None))
                result = self._client._position_query_result
                if direction == "2":
                    result["long_total"] += position
                    result["long_today"] += today_position
                elif direction == "3":
                    result["short_total"] += position
                    result["short_today"] += today_position
                self._client._last_error = None

        if bIsLast:
            self._client._query_kind = None
            self._client._query_symbol = None
            self._client._event.set()

    def OnRspQryDepthMarketData(self, pDepthMarketData, pRspInfo, nRequestID, bIsLast) -> None:
        if self._client._query_kind != QUERY_KIND_MARKET:
            if bIsLast:
                self._client._event.set()
            return

        if pRspInfo and pRspInfo.ErrorID != 0:
            self._client._market_query_result = _empty_market_result(self._client._query_symbol or "")
            self._client._last_error = "OnRspQryDepthMarketData failed: " + str(_format_rsp_error(pRspInfo))
        elif pDepthMarketData:
            symbol = _decode_ctp_value(getattr(pDepthMarketData, "InstrumentID", None))
            last_price = _to_float(getattr(pDepthMarketData, "LastPrice", None))
            if symbol and symbol == self._client._query_symbol:
                self._client._market_query_result = {
                    "symbol": symbol,
                    "last_price": last_price,
                }
                self._client._last_error = None
        elif bIsLast and not self._client._market_query_result.get("symbol"):
            self._client._market_query_result = _empty_market_result(self._client._query_symbol or "")

        if bIsLast:
            self._client._query_kind = None
            self._client._query_symbol = None
            self._client._event.set()

    def OnFrontDisconnected(self, nReason) -> None:
        self._client._connected = False
        self._client._last_error = f"OnFrontDisconnected: reason={nReason}"
        self._client._event.set()


class CTPClient:
    _instance: Optional["CTPClient"] = None

    def __init__(self):
        settings = get_settings()
        self._api = None
        self._spi: Optional["CTPClientSpi"] = None
        self._api_released = False
        self._connected = False
        self._session_id = 0
        self._max_retries = settings.ctp_max_retries
        self._event = threading.Event()
        self._request_id = 0
        self._query_lock = threading.Lock()
        self._query_rate_lock = threading.Lock()
        self._contracts: dict[str, dict[str, Any]] = {}
        self._contract_aliases: dict[str, str] = {}
        self._contracts_loaded = False
        self._contracts_lock = threading.RLock()
        self._last_error: str | None = None
        self._query_kind: str | None = None
        self._query_symbol: str | None = None
        self._last_query_request_at = 0.0
        self._contract_query_result: dict = _empty_contract_result()
        self._position_query_result: dict = _empty_position_result()
        self._market_query_result: dict = _empty_market_result()

    @classmethod
    def get_instance(cls) -> "CTPClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def connect(self) -> bool:
        if api is None:
            return False

        settings = get_settings()
        self._event.clear()
        self._last_error = None
        self._api = api.CThostFtdcTraderApi_CreateFtdcTraderApi()
        self._api_released = False
        self._spi = CTPClientSpi(self)
        self._api.RegisterSpi(self._spi)
        self._api.SubscribePublicTopic(api.THOST_TERT_QUICK)
        self._api.SubscribePrivateTopic(api.THOST_TERT_QUICK)
        front_addr = f"tcp://{settings.ctp_host}:{settings.ctp_port}"
        self._api.RegisterFront(front_addr)
        self._api.Init()

        if self._event.wait(settings.ctp_connect_timeout):
            self._event.clear()
        elif not self._connected:
            self._last_error = f"connect timed out after {settings.ctp_connect_timeout}s"
        return self._connected

    def logout(self) -> bool:
        if api is None or self._api is None or not self._connected:
            self._connected = False
            return True

        settings = get_settings()
        self._event.clear()
        self._last_error = None

        logout_field = api.CThostFtdcUserLogoutField()
        logout_field.BrokerID = settings.ctp_broker_id
        logout_field.UserID = settings.ctp_user_id

        ret = self._api.ReqUserLogout(logout_field, self.next_request_id())
        if ret < 0:
            self._last_error = f"ReqUserLogout returned {ret}"
            return False

        if self._event.wait(settings.ctp_logout_timeout):
            self._event.clear()
            return not self._connected

        self._last_error = f"logout timed out after {settings.ctp_logout_timeout}s"
        return False

    def release(self) -> None:
        if self._api is not None and not self._api_released:
            self._api.Release()
            self._api_released = True
        self._connected = False
        self._event.clear()
        self._query_kind = None
        self._query_symbol = None

    def last_error(self) -> str | None:
        return self._last_error

    def contract_count(self) -> int:
        with self._contracts_lock:
            return len(self._contracts)

    def contract_symbols(self) -> list[str]:
        with self._contracts_lock:
            return sorted(self._contracts.keys())

    def contract_lines(self) -> list[str]:
        with self._contracts_lock:
            return [
                f"{symbol}\t{info.get('exchange') or ''}"
                for symbol, info in sorted(self._contracts.items())
            ]

    def contracts_loaded(self) -> bool:
        return self._contracts_loaded

    def connect_with_retry(self) -> bool:
        if self._connected:
            return True

        for attempt in range(1, self._max_retries + 1):
            if self.connect():
                return True
            time.sleep(10.0)

        return False

    def _wait_for_query_interval(self) -> None:
        with self._query_rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_query_request_at
            if elapsed < QUERY_MIN_INTERVAL_SECONDS:
                time.sleep(QUERY_MIN_INTERVAL_SECONDS - elapsed)
            self._last_query_request_at = time.monotonic()

    def load_contracts(self) -> bool:
        if api is None:
            return False
        if not self._connected:
            return False

        with self._query_lock:
            with self._contracts_lock:
                self._contracts = {}
                self._contract_aliases = {}
                self._contracts_loaded = False

            self._query_kind = QUERY_KIND_LOAD_CONTRACTS
            self._query_symbol = None
            self._contract_query_result = _empty_contract_result()
            self._event.clear()
            self._last_error = None

            qry_field = api.CThostFtdcQryInstrumentField()
            qry_field.InstrumentID = ""

            self._wait_for_query_interval()
            request_id = self.next_request_id()
            ret = self._api.ReqQryInstrument(qry_field, request_id)
            if ret < 0:
                self._last_error = f"ReqQryInstrument returned {ret}"
                self._query_kind = None
                return False

            timeout = get_settings().ctp_load_contracts_timeout
            if self._event.wait(timeout):
                self._event.clear()
                return self._contracts_loaded
            self._last_error = f"load_contracts timed out after {timeout}s"
            self._query_kind = None
            self._query_symbol = None
            return False

    def get_contract_info(self, symbol: str) -> dict:
        if not self._connected:
            return _with_warning(_empty_contract_result(), _build_ctp_warning(self._last_error))

        raw = symbol.strip()
        standard_symbol = raw
        with self._contracts_lock:
            contract_info = self._contracts.get(raw)
            if contract_info is None:
                original = self._contract_aliases.get(raw.upper())
                if original:
                    standard_symbol = original
                    contract_info = self._contracts.get(original)
            elif raw in self._contracts:
                standard_symbol = raw

        return {
            "exists": contract_info is not None,
            "exchange": contract_info.get("exchange") if contract_info else None,
            "symbol": standard_symbol if contract_info is not None else None,
            "price_tick": contract_info.get("price_tick") if contract_info else None,
            "volume_multiple": contract_info.get("volume_multiple") if contract_info else None,
            "warning": None,
        }

    def query_position(self, symbol: str, account: str = "") -> dict:
        normalized = symbol.strip()
        if api is None or not normalized:
            return _empty_position_result(normalized)
        normalized_account = account.strip()
        if not normalized_account:
            return _with_warning(_empty_position_result(normalized), "CTP查询持仓失败: 缺少account")
        if not self._connected:
            return _with_warning(_empty_position_result(normalized), _build_ctp_warning(self._last_error))

        with self._query_lock:
            self._query_kind = QUERY_KIND_POSITION
            self._query_symbol = normalized
            self._position_query_result = _empty_position_result(normalized)
            self._event.clear()
            self._last_error = None

            settings = get_settings()
            qry_field = api.CThostFtdcQryInvestorPositionField()
            qry_field.BrokerID = settings.ctp_broker_id
            qry_field.InvestorID = normalized_account
            qry_field.InstrumentID = normalized

            self._wait_for_query_interval()
            request_id = self.next_request_id()
            ret = self._api.ReqQryInvestorPosition(qry_field, request_id)
            if ret < 0:
                self._last_error = f"ReqQryInvestorPosition returned {ret}"
                self._query_kind = None
                self._query_symbol = None
                return _with_warning(_empty_position_result(normalized), f"CTP查询持仓失败: {self._last_error}")

            timeout = settings.ctp_query_timeout
            if self._event.wait(timeout):
                self._event.clear()
                warning = None
                if self._last_error:
                    warning = f"CTP查询持仓失败: {self._last_error}"
                return _with_warning(self._position_query_result, warning)

            self._last_error = f"query_position timed out after {timeout}s"
            self._query_kind = None
            self._query_symbol = None
            return _with_warning(_empty_position_result(normalized), f"CTP查询持仓超时: {self._last_error}")

    def query_last_price(self, symbol: str) -> dict:
        normalized = symbol.strip()
        if api is None or not normalized:
            return _empty_market_result(normalized)
        if not self._connected:
            return _with_warning(_empty_market_result(normalized), _build_ctp_warning(self._last_error))

        with self._query_lock:
            self._query_kind = QUERY_KIND_MARKET
            self._query_symbol = normalized
            self._market_query_result = _empty_market_result(normalized)
            self._event.clear()
            self._last_error = None

            qry_field = api.CThostFtdcQryDepthMarketDataField()
            qry_field.InstrumentID = normalized
            contract_info = self.get_contract_info(normalized)
            exchange = contract_info.get("exchange")
            if exchange and hasattr(qry_field, "ExchangeID"):
                qry_field.ExchangeID = exchange

            self._wait_for_query_interval()
            request_id = self.next_request_id()
            ret = self._api.ReqQryDepthMarketData(qry_field, request_id)
            if ret < 0:
                self._last_error = f"ReqQryDepthMarketData returned {ret}"
                self._query_kind = None
                self._query_symbol = None
                return _with_warning(_empty_market_result(normalized), f"CTP查询行情失败: {self._last_error}")

            timeout = get_settings().ctp_query_timeout
            if self._event.wait(timeout):
                self._event.clear()
                return dict(self._market_query_result)

            self._last_error = f"query_last_price timed out after {timeout}s"
            self._query_kind = None
            self._query_symbol = None
            return _with_warning(_empty_market_result(normalized), f"CTP查询行情超时: {self._last_error}")
