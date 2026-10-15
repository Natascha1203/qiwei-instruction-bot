import logging
import signal
import threading
from pathlib import Path

from .ctp import CTPClient, CTP_IMPORT_ERROR, api
from .logging_config import setup_logging
from .settings import get_settings
from .wecom import start_wecom_connector, stop_wecom_connector


_shutdown_event = threading.Event()
logger = logging.getLogger(__name__)


def _write_contracts_log(client: CTPClient) -> None:
    log_dir = Path("log")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        lines = client.contract_lines()
        with (log_dir / "contract.log").open("w", encoding="utf-8") as log_file:
            log_file.write(f"# 启动加载合约总数: {len(lines)}\n")
            log_file.write("\n".join(lines))
            log_file.write("\n")
        logger.info("contracts written to log/contract.log: count=%s", len(lines))
    except OSError as exc:
        logger.error("write contract.log failed: %s", exc)


def startup_event() -> None:
    settings = get_settings()
    logger.info("startup begin")
    logger.info(
        "wecom config:"
        f" enabled={settings.wecom_ws_enabled}"
        f" bot_id_set={bool(settings.wecom_bot_id)}"
        f" bot_secret_set={bool(settings.wecom_bot_secret)}"
        f" single_file_enabled={settings.wecom_single_file_enabled}"
    )
    logger.info("ctp enabled=%s", settings.ctp_enabled)
    start_wecom_connector()
    if settings.ctp_enabled:
        front_addr = f"tcp://{settings.ctp_host}:{settings.ctp_port}"
        logger.info(
            "ctp config:"
            f" front={front_addr}"
            f" broker_id={settings.ctp_broker_id}"
            f" user_id_set={bool(settings.ctp_user_id)}"
            f" password_set={bool(settings.ctp_password)}"
            f" app_id_set={bool(settings.ctp_app_id)}"
            f" auth_code_set={bool(settings.ctp_auth_code)}"
            f" api_loaded={api is not None}"
        )
        if CTP_IMPORT_ERROR is not None:
            logger.error("ctp import error: %r", CTP_IMPORT_ERROR)
        logger.info("ctp connect start front=%s", front_addr)
        client = CTPClient.get_instance()
        if client.connect_with_retry():
            logger.info("ctp connect ok, loading contracts")
            if client.load_contracts():
                contract_symbols = client.contract_symbols()
                logger.info("ctp contracts loaded: count=%s", len(contract_symbols))
                _write_contracts_log(client)
                # logger.info("ctp loaded contracts: %s", contract_symbols)
            else:
                logger.error("ctp load_contracts failed: %s", client.last_error())
        else:
            logger.error("ctp connect failed: %s", client.last_error())
    logger.info("startup completed")


def shutdown_event() -> None:
    logger.info("shutdown begin")
    stop_wecom_connector()
    if get_settings().ctp_enabled:
        CTPClient.get_instance().release()
    logger.info("shutdown completed")


def _handle_signal(signum, _frame) -> None:
    logger.info("received signal: %s", signum)
    _shutdown_event.set()


def register_signal_handlers() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except ValueError:
            # signal handlers can only be registered from the main thread
            pass


def wait_forever() -> None:
    while not _shutdown_event.wait(timeout=1):
        continue


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    register_signal_handlers()
    startup_event()
    try:
        wait_forever()
    finally:
        shutdown_event()


if __name__ == "__main__":
    main()
