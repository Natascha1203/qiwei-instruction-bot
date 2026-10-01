import logging
import signal
import threading

from .ctp import CTPClient, CTP_IMPORT_ERROR, api
from .logging_config import setup_logging
from .settings import get_settings
from .wecom import start_wecom_connector, stop_wecom_connector
from .xgo import XGO_IMPORT_ERROR, get_xgo_client


_shutdown_event = threading.Event()
logger = logging.getLogger(__name__)


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
    logger.info("twap api enabled=%s", settings.twap_api_enabled)
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
                # logger.info("ctp loaded contracts: %s", contract_symbols)
            else:
                logger.error("ctp load_contracts failed: %s", client.last_error())
        else:
            logger.error("ctp connect failed: %s", client.last_error())
    if settings.twap_api_enabled:
        logger.info(
            "xgo config:"
            f" ip={settings.xgo_ip}"
            f" port={settings.xgo_port}"
            f" account_code_set={bool(settings.xgo_account_code)}"
            f" login_key_set={bool(settings.xgo_login_key)}"
            f" sdk_loaded={XGO_IMPORT_ERROR is None}"
        )
        if XGO_IMPORT_ERROR is not None:
            logger.error("xgo import error: %r", XGO_IMPORT_ERROR)
        client = get_xgo_client()
        if client.is_configured():
            logger.info("xgo connect and login start")
            if client.connect_and_login():
                logger.info("xgo connect and login ok")
            else:
                logger.error("xgo connect and login failed: %s", client.last_error())
        else:
            logger.error("xgo client not configured, twap orders will fall back to csv")
    logger.info("startup completed")


def shutdown_event() -> None:
    logger.info("shutdown begin")
    stop_wecom_connector()
    settings = get_settings()
    if settings.ctp_enabled:
        CTPClient.get_instance().release()
    if settings.twap_api_enabled:
        get_xgo_client().release()
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
