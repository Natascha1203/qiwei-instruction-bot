from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ..instruction_parser import TwapRow
from ..settings import Settings, get_settings
from .order_mapper import TwapOrderSpec, build_twap_order_spec

try:
    from xgo_trader import ErrorInfo, xGoTraderApi
    from xgo_trader.xgo_constant import (
        AlgorithmStartTimingKind,
        AlgorithmStopTimingKind,
        OffsetTagKind,
        OrderSideKind,
        TradingPurposeKind,
    )
    from xgo_trader.xgo_types import (
        InsertTwapOrderReq,
        OpenUserSessionReq,
        OrderRole,
        OrderSide,
        OrderTarget,
        SubmitUserSystemInfoReq,
        VerifyBrokerAppTokenReq,
    )

    XGO_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - Windows 开发机无 SDK 时走此分支
    ErrorInfo = None  # type: ignore[assignment]
    xGoTraderApi = None  # type: ignore[assignment]
    AlgorithmStartTimingKind = None  # type: ignore[assignment]
    AlgorithmStopTimingKind = None  # type: ignore[assignment]
    OffsetTagKind = None  # type: ignore[assignment]
    OrderSideKind = None  # type: ignore[assignment]
    TradingPurposeKind = None  # type: ignore[assignment]
    InsertTwapOrderReq = None  # type: ignore[assignment]
    OpenUserSessionReq = None  # type: ignore[assignment]
    OrderRole = None  # type: ignore[assignment]
    OrderSide = None  # type: ignore[assignment]
    OrderTarget = None  # type: ignore[assignment]
    SubmitUserSystemInfoReq = None  # type: ignore[assignment]
    VerifyBrokerAppTokenReq = None  # type: ignore[assignment]
    XGO_IMPORT_ERROR = exc

from .spi import ResponseAggregator, Spi  # noqa: E402  spi 自带 import 容错

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10
RELOGIN_MIN_INTERVAL_SECONDS = 30.0


@dataclass
class XgoSendResult:
    ok: bool
    algo_entry_code: str = ""
    error: str = ""


class XGoClient:
    _instance: "XGoClient | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.aggregator = ResponseAggregator()
        self._api: Any = None
        self._spi: Spi | None = None
        self._ready = False
        self._last_error: str = ""
        self._request_id = 0
        self._request_id_lock = threading.Lock()
        self._login_lock = threading.Lock()
        self._verified_accounts: set[str] = set()
        self._connected_future: Any = None
        self._last_relogin_attempt = 0.0

    @classmethod
    def get_instance(cls) -> "XGoClient":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def is_configured(self) -> bool:
        if XGO_IMPORT_ERROR is not None:
            return False
        settings = self.settings
        return bool(
            settings.xgo_ip.strip()
            and settings.xgo_port
            and settings.xgo_account_code.strip()
            and settings.xgo_login_key.strip()
        )

    def is_ready(self) -> bool:
        return self._ready and self._api is not None

    def last_error(self) -> str:
        return self._last_error

    def connect_and_login(self) -> bool:
        with self._login_lock:
            self._last_relogin_attempt = time.monotonic()
            try:
                self._connect_and_login_locked()
                self._ready = True
                self._last_error = ""
                logger.info("xgo login ok")
                return True
            except Exception as exc:
                self._ready = False
                self._last_error = str(exc)
                self.aggregator.fail_all(RuntimeError(f"xGo连接/登录失败: {exc}"))
                logger.error("xgo login failed: %s", exc)
                return False

    def _connect_and_login_locked(self) -> None:
        settings = self.settings
        self._release_api()

        self._connected_future = Future()
        self._spi = Spi(
            self.aggregator,
            on_connected=lambda: self._connected_future.set_result(True),
            on_disconnected=self._handle_disconnected,
        )
        config = {
            "ip": settings.xgo_ip.strip(),
            "port": settings.xgo_port,
            "time_out": settings.xgo_query_timeout,
            "log_level": settings.xgo_log_level,
            "auto_reconnect": settings.xgo_auto_reconnect,
        }
        self._api = xGoTraderApi(config, self._spi)
        self._connected_future.result(timeout=CONNECT_TIMEOUT_SECONDS)

        ret = self._future_request(
            lambda request_id: self._api.open_user_session(
                request_id=request_id,
                request=OpenUserSessionReq(
                    account_code=settings.xgo_account_code.strip(),
                    login_key=settings.xgo_login_key.strip(),
                ),
            )
        )
        self._check_error(ret, "open_user_session")

        subscribe_ret = self._api.subscribe(self._next_request_id())
        if subscribe_ret is not None and subscribe_ret.error_id != 0:
            raise RuntimeError(f"subscribe失败: {subscribe_ret.error_info}")

        ret = self._future_request(
            lambda request_id: self._api.submit_user_system_info(
                request_id=request_id,
                request=SubmitUserSystemInfoReq(
                    app_code=settings.xgo_app_code.strip(),
                    auth_code=settings.xgo_auth_code.strip(),
                ),
            )
        )
        self._check_error(ret, "submit_user_system_info")

    def _handle_disconnected(self, info: Any) -> None:
        self._ready = False
        self.aggregator.fail_all(RuntimeError("xGo连接已断开"))

    def send_twap_order(self, row: TwapRow) -> XgoSendResult:
        try:
            spec = build_twap_order_spec(
                row,
                default_investor_code=self.settings.xgo_investor_code.strip(),
            )
        except ValueError as exc:
            return XgoSendResult(ok=False, error=str(exc))

        try:
            if not self.is_ready() and not self._try_relogin():
                return XgoSendResult(ok=False, error=f"xGo未就绪: {self._last_error or '连接不可用'}")

            if spec.investor_code not in self._verified_accounts:
                self._verify_account(spec.investor_code, spec)

            request = self._build_twap_request(spec)
            logger.info(
                "xgo insert_twap_order request: contract=%s side=%s offset=%s target_unit=%s slice_count=%s"
                " investor=%s broker=%s counter=%s/%s style=%s formal_name=%s",
                spec.contract_code, spec.side, spec.offset, spec.target_unit, spec.slice_count,
                spec.investor_code, spec.broker_code, spec.counter_code, spec.counter_account,
                spec.trading_style_code, spec.formal_name,
            )
            ret = self._future_request(
                lambda request_id: self._api.insert_twap_order(
                    request_id=request_id,
                    request=request,
                )
            )
            self._check_error(ret, "insert_twap_order")
            algo_entry_code = str(getattr(ret["payload"], "algo_entry_code", "") or "")
            return XgoSendResult(ok=True, algo_entry_code=algo_entry_code)
        except Exception as exc:
            self._last_error = str(exc)
            logger.error("xgo send_twap_order failed: %s", exc)
            return XgoSendResult(ok=False, error=str(exc))

    def release(self) -> None:
        with self._login_lock:
            if self._api is None:
                return
            try:
                if self._ready:
                    self._future_request(
                        lambda request_id: self._api.close_user_session(request_id=request_id),
                        timeout=5,
                    )
            except Exception as exc:
                logger.warning("xgo close_user_session failed: %s", exc)
            self._release_api()
            self._ready = False

    def _release_api(self) -> None:
        if self._api is None:
            return
        try:
            self._api.release()
            self._api.join()
        except Exception as exc:
            logger.warning("xgo api release failed: %s", exc)
        finally:
            self._api = None
            self._spi = None

    def _try_relogin(self) -> bool:
        now = time.monotonic()
        if now - self._last_relogin_attempt < RELOGIN_MIN_INTERVAL_SECONDS:
            return False
        return self.connect_and_login()

    def _verify_account(self, investor_code: str, spec: TwapOrderSpec) -> None:
        settings = self.settings
        logger.info(
            "xgo verify_broker_app_token request: investor=%s broker=%s counter=%s/%s app_code=%s",
            investor_code, spec.broker_code, spec.counter_code, spec.counter_account,
            settings.xgo_app_code.strip(),
        )
        ret = self._future_request(
            lambda request_id: self._api.verify_broker_app_token(
                request_id=request_id,
                request=VerifyBrokerAppTokenReq(
                    counter_code=spec.counter_code,
                    counter_account=spec.counter_account,
                    broker_code=spec.broker_code,
                    investor_code=investor_code,
                    app_code=settings.xgo_app_code.strip(),
                    auth_code=settings.xgo_auth_code.strip(),
                ),
            )
        )
        self._check_error(ret, "verify_broker_app_token")
        self._verified_accounts.add(investor_code)

    def _build_twap_request(self, spec: TwapOrderSpec):
        return InsertTwapOrderReq(
            formal_name=spec.formal_name,
            caption=spec.caption,
            start_timing=AlgorithmStartTimingKind[spec.start_timing],
            start_time=spec.start_dt,
            stop_timing=AlgorithmStopTimingKind[spec.stop_timing],
            stop_time=spec.stop_dt,
            duration=spec.duration,
            target_unit=spec.target_unit,
            slice_count=spec.slice_count,
            order_role=OrderRole(
                counter_code=spec.counter_code,
                counter_account=spec.counter_account,
                broker_code=spec.broker_code,
                investor_code=spec.investor_code,
                hedge_flag=TradingPurposeKind[spec.hedge_flag],
            ),
            order_target=OrderTarget(
                contract_code=spec.contract_code,
                order_ratio=spec.order_ratio,
            ),
            order_side=OrderSide(
                order_side=OrderSideKind[spec.side],
                offset_tag=OffsetTagKind[spec.offset],
            ),
            trading_style_code=spec.trading_style_code,
        )

    def _next_request_id(self) -> int:
        with self._request_id_lock:
            self._request_id += 1
            return self._request_id

    def _future_request(self, call: Callable[[int], Any], timeout: float | None = None) -> dict:
        request_id = self._next_request_id()
        future = self.aggregator.register(request_id)
        ret = call(request_id)
        if ret is not None and ret.error_id != 0:
            self.aggregator.cancel(request_id)
            raise RuntimeError(f"{ret.error_info}")
        timeout = timeout if timeout is not None else self.settings.xgo_request_timeout
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            self.aggregator.cancel(request_id)
            raise RuntimeError(f"请求超时({timeout}s), request_id={request_id}") from exc

    @staticmethod
    def _check_error(ret: dict, step: str) -> None:
        info = ret.get("info") if isinstance(ret, dict) else None
        if info is not None and info.error_id != 0:
            raise RuntimeError(f"{step}失败: {info.error_info}")


def get_xgo_client() -> XGoClient:
    return XGoClient.get_instance()


def xgo_available() -> bool:
    return XGO_IMPORT_ERROR is None
