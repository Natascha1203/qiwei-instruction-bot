from __future__ import annotations

import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable

try:
    from xgo_trader import ErrorInfo, xGoTraderSpi

    XGO_SPI_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - Windows 开发机无 SDK 时走此分支
    ErrorInfo = None  # type: ignore[assignment]
    xGoTraderSpi = object  # type: ignore[assignment,misc]
    XGO_SPI_IMPORT_ERROR = exc

logger = logging.getLogger(__name__)


class ResponseAggregator:
    """按 request_id 聚合多包应答并写入 Future（移植自 demo.py future_helper，线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._futures: dict[int, Future] = {}
        self._cache: dict[int, list] = {}

    def register(self, request_id: int) -> Future:
        future: Future = Future()
        with self._lock:
            self._futures[request_id] = future
        return future

    def cancel(self, request_id: int) -> None:
        with self._lock:
            self._futures.pop(request_id, None)
            self._cache.pop(request_id, None)

    def resolve(self, request_id: int, info: Any, payload: Any, is_last: bool = True) -> None:
        with self._lock:
            if not is_last:
                self._append_cache(request_id, payload)
                return
            future = self._futures.pop(request_id, None)
            cache = self._cache.pop(request_id, None)
            if future is None or future.done():
                return
            if cache:
                if isinstance(payload, list):
                    cache.extend(payload)
                else:
                    cache.append(payload)
                future.set_result({"info": info, "payload": cache})
            else:
                future.set_result({"info": info, "payload": payload})

    def fail_all(self, error: Exception) -> None:
        with self._lock:
            futures = list(self._futures.values())
            self._futures.clear()
            self._cache.clear()
        for future in futures:
            if not future.done():
                future.set_exception(error)

    def _append_cache(self, request_id: int, payload: Any) -> None:
        if isinstance(payload, list):
            self._cache.setdefault(request_id, []).extend(payload)
        else:
            self._cache.setdefault(request_id, []).append(payload)


class Spi(xGoTraderSpi):  # type: ignore[misc]
    """SDK 回调薄壳：应答回调委托给 ResponseAggregator，连接事件委托给客户端回调。"""

    def __init__(
        self,
        aggregator: ResponseAggregator,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__()
        self.aggregator = aggregator
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

    def on_error(self, info) -> None:
        logger.error("xgo on_error: %s", info)

    def on_connected(self) -> None:
        logger.info("xgo connected")
        if self._on_connected is not None:
            self._on_connected()

    def on_disconnected(self, info) -> None:
        logger.warning("xgo disconnected: %s", info)
        if self._on_disconnected is not None:
            self._on_disconnected(info)

    def on_open_user_session(self, info: ErrorInfo, payload, request_id: int, is_last: bool = True) -> None:
        self.aggregator.resolve(request_id, info, payload, is_last)

    def on_close_user_session(self, info: ErrorInfo, payload, request_id: int, is_last: bool = True) -> None:
        self.aggregator.resolve(request_id, info, payload, is_last)

    def on_submit_user_system_info(self, info: ErrorInfo, payload, request_id: int, is_last: bool = True) -> None:
        self.aggregator.resolve(request_id, info, payload, is_last)

    def on_verify_broker_app_token(self, info: ErrorInfo, payload, request_id: int, is_last: bool = True) -> None:
        self.aggregator.resolve(request_id, info, payload, is_last)

    def on_insert_twap_order(self, info: ErrorInfo, payload, request_id: int, is_last: bool = True) -> None:
        self.aggregator.resolve(request_id, info, payload, is_last)

    def on_twap_order_rtn(self, info: ErrorInfo, payload, request_id: int, is_last: bool = True) -> None:
        logger.info("xgo twap_order_rtn: %s", getattr(payload, "algo_entry_code", payload))
