from .connector import (
    WeComWebSocketClientConnector,
    WeComWebSocketConnectionManager,
    build_default_wecom_connection_manager,
)


_default_connection_manager = build_default_wecom_connection_manager()
_default_connector = WeComWebSocketClientConnector(_default_connection_manager)


def get_wecom_connection_manager() -> WeComWebSocketConnectionManager:
    return _default_connection_manager


def start_wecom_connector() -> None:
    _default_connector.start()


def stop_wecom_connector() -> None:
    _default_connector.stop()


__all__ = [
    "WeComWebSocketConnectionManager",
    "WeComWebSocketClientConnector",
    "build_default_wecom_connection_manager",
    "get_wecom_connection_manager",
    "start_wecom_connector",
    "stop_wecom_connector",
]
