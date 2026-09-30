from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any
from uuid import uuid4

from ..settings import get_settings
from .errors import BotError, BotMessageIgnored
from .models import WECOM_BOT_TYPE
from .service import handle_wecom_payload


logger = logging.getLogger(__name__)


class WeComWebSocketConnectionManager:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        self._websocket: Any = None
        self._request_waiters: dict[str, asyncio.Future] = {}
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop, websocket: Any) -> None:
        with self._lock:
            self._loop = loop
            self._loop_thread_id = threading.get_ident()
            self._websocket = websocket

    def clear(self) -> None:
        with self._lock:
            self._websocket = None
            waiters = list(self._request_waiters.values())
            self._request_waiters.clear()
        for waiter in waiters:
            if not waiter.done():
                self._set_waiter_exception(waiter, RuntimeError("wecom websocket is not connected"))

    async def send_json_async(self, payload: dict) -> None:
        with self._lock:
            websocket = self._websocket
        if websocket is None:
            raise RuntimeError("wecom websocket is not connected")
        await websocket.send(json.dumps(payload, ensure_ascii=False))

    def send_json(self, payload: dict) -> None:
        with self._lock:
            loop = self._loop
            websocket = self._websocket
            loop_thread_id = self._loop_thread_id

        if loop is None or websocket is None:
            raise RuntimeError("wecom websocket is not connected")

        if threading.get_ident() == loop_thread_id:
            loop.create_task(self.send_json_async(payload))
            return

        future = asyncio.run_coroutine_threadsafe(self.send_json_async(payload), loop)
        try:
            future.result(timeout=get_settings().wecom_ws_send_timeout)
        except FutureTimeoutError as exc:
            raise RuntimeError("wecom websocket send timeout") from exc

    async def send_json_and_wait_async(self, payload: dict, timeout: int | None = None) -> dict:
        req_id = str((payload.get("headers") or {}).get("req_id", ""))
        if not req_id:
            raise RuntimeError("wecom websocket req_id is required")
        return await self.send_json_and_wait_req_id_async(payload, req_id, timeout)

    async def send_json_and_wait_req_id_async(self, payload: dict, req_id: str, timeout: int | None = None) -> dict:
        if not req_id:
            raise RuntimeError("wecom websocket req_id is empty")

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        with self._lock:
            if self._websocket is None:
                raise RuntimeError("wecom websocket is not connected")
            self._request_waiters[req_id] = waiter

        try:
            await self.send_json_async(payload)
            return await asyncio.wait_for(waiter, timeout=timeout or get_settings().wecom_ws_send_timeout)
        except Exception:
            self._remove_request_waiter(req_id, waiter)
            raise

    async def send_ping_and_wait_async(self, req_id: str, timeout: int | None = None) -> dict:
        payload = {"cmd": "ping", "headers": {"req_id": req_id}}
        return await self.send_json_and_wait_req_id_async(payload, req_id, timeout)

    def send_json_and_wait(self, payload: dict, timeout: int | None = None) -> dict:
        with self._lock:
            loop = self._loop
            websocket = self._websocket
            loop_thread_id = self._loop_thread_id

        if loop is None or websocket is None:
            raise RuntimeError("wecom websocket is not connected")
        if threading.get_ident() == loop_thread_id:
            raise RuntimeError("send_json_and_wait cannot block on the websocket loop thread")

        future = asyncio.run_coroutine_threadsafe(self.send_json_and_wait_async(payload, timeout), loop)
        try:
            return future.result(timeout=timeout or get_settings().wecom_ws_send_timeout)
        except FutureTimeoutError as exc:
            raise RuntimeError("wecom websocket response timeout") from exc

    def handle_response(self, payload: dict) -> bool:
        return self._handle_req_id_response(payload)

    def _handle_req_id_response(self, payload: dict) -> bool:
        req_id = str((payload.get("headers") or {}).get("req_id", ""))
        if not req_id:
            return False

        waiter: asyncio.Future | None = None
        with self._lock:
            waiter = self._request_waiters.pop(req_id, None)

        if waiter:
            self._set_waiter_result(waiter, payload)
            return True
        return False

    def _set_waiter_result(self, waiter: asyncio.Future, payload: dict) -> None:
        def set_result_if_pending() -> None:
            if not waiter.done():
                waiter.set_result(payload)

        waiter.get_loop().call_soon_threadsafe(set_result_if_pending)

    def _set_waiter_exception(self, waiter: asyncio.Future, exc: Exception) -> None:
        def set_exception_if_pending() -> None:
            if not waiter.done():
                waiter.set_exception(exc)

        waiter.get_loop().call_soon_threadsafe(set_exception_if_pending)

    def _remove_request_waiter(self, req_id: str, waiter: asyncio.Future) -> None:
        with self._lock:
            if self._request_waiters.get(req_id) is waiter:
                self._request_waiters.pop(req_id, None)


class WeComWebSocketClientConnector:
    def __init__(self, connection_manager: WeComWebSocketConnectionManager):
        self._connection_manager = connection_manager
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._processed_msgids: set[str] = set()
        self._heartbeat_task: asyncio.Task | None = None

    def start(self) -> None:
        settings = get_settings()
        if not settings.wecom_ws_enabled:
            logger.info("connector disabled by WECOM_WS_ENABLED")
            return
        if not settings.wecom_bot_id or not settings.wecom_bot_secret:
            logger.info(
                "connector not started:"
                f" bot_id_set={bool(settings.wecom_bot_id)}"
                f" bot_secret_set={bool(settings.wecom_bot_secret)}"
            )
            return
        if self._thread and self._thread.is_alive():
            logger.info("connector thread already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="wecom-websocket-client", daemon=True)
        logger.info("starting connector thread url=%s", settings.wecom_ws_url)
        self._thread.start()

    def stop(self) -> None:
        logger.info("stop requested")
        self._stop_event.set()

    def _run_loop(self) -> None:
        logger.info("connector event loop starting")
        asyncio.run(self._connect_forever())

    async def _connect_forever(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package is not installed")
            return

        settings = get_settings()
        while not self._stop_event.is_set():
            try:
                logger.info("connecting to %s", settings.wecom_ws_url)
                async with websockets.connect(
                    settings.wecom_ws_url,
                    compression=None,
                    ping_interval=None,
                ) as websocket:
                    logger.info("websocket connected")
                    self._connection_manager.bind(asyncio.get_running_loop(), websocket)
                    await self._subscribe()
                    logger.info("subscribe sent")
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    receive_task = asyncio.create_task(self._receive_loop(websocket))
                    try:
                        done, pending = await asyncio.wait(
                            {receive_task, self._heartbeat_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in pending:
                            task.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        for task in done:
                            task.result()
                    finally:
                        logger.info("connection closing")
                        await self._stop_heartbeat()
                        self._connection_manager.clear()
            except Exception as exc:
                self._connection_manager.clear()
                logger.exception("connect loop failed: %s", exc)
                logger.info("reconnect scheduled after %ss", settings.wecom_ws_reconnect_interval)
                await asyncio.sleep(settings.wecom_ws_reconnect_interval)

    async def _subscribe(self) -> None:
        settings = get_settings()
        await self._connection_manager.send_json_async(
            {
                "cmd": "aibot_subscribe",
                "headers": {},
                "body": {"bot_id": settings.wecom_bot_id, "secret": settings.wecom_bot_secret},
            }
        )
        logger.info("subscribe payload sent")

    async def _heartbeat_loop(self) -> None:
        settings = get_settings()
        while not self._stop_event.is_set():
            await asyncio.sleep(settings.wecom_heartbeat_interval)
            req_id = uuid4().hex
            response = await self._connection_manager.send_ping_and_wait_async(
                req_id,
                timeout=settings.wecom_ws_send_timeout,
            )
            if response.get("errcode") != 0:
                errmsg = response.get("errmsg") or response
                raise RuntimeError(f"wecom heartbeat failed: {errmsg}")

    async def _stop_heartbeat(self) -> None:
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass
        self._heartbeat_task = None

    async def _receive_loop(self, websocket: Any) -> None:
        async for raw_message in websocket:
            if self._stop_event.is_set():
                break
            await self._handle_raw_message(raw_message)

    async def _handle_raw_message(self, raw_message: str | bytes) -> None:
        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            payload = json.loads(raw_message)
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.error("received non-json message")
            return

        if self._connection_manager.handle_response(payload):
            return

        body = payload.get("body") or {}
        logger.info("inbound payload=%s", payload)

        msgid = str(body.get("msgid", ""))
        if msgid:
            if msgid in self._processed_msgids:
                logger.info("duplicate message ignored msgid=%s", msgid)
                return
            self._processed_msgids.add(msgid)

        payload["connection_id"] = WECOM_BOT_TYPE
        asyncio.create_task(self._handle_wecom_payload_in_thread(payload))

    async def _handle_wecom_payload_in_thread(self, payload: dict) -> None:
        try:
            await asyncio.to_thread(handle_wecom_payload, payload, self._connection_manager)
        except BotMessageIgnored as exc:
            logger.info("ignored message: %s", exc)
        except (BotError, RuntimeError) as exc:
            logger.exception("failed to handle bot payload: %s", exc)


def build_default_wecom_connection_manager() -> WeComWebSocketConnectionManager:
    return WeComWebSocketConnectionManager()
