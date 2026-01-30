import logging
from typing import Optional

from binance_sdk_derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    ConfigurationRestAPI,
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL as MAINNET_REST,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL as MAINNET_WS_STREAMS,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL as MAINNET_WS_API,
)
from binance_common.constants import (
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_TESTNET_URL as TESTNET_REST,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_TESTNET_URL as TESTNET_WS_STREAMS,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_TESTNET_URL as TESTNET_WS_API,
)
from binance_common.configuration import (
    ConfigurationWebSocketStreams,
    ConfigurationWebSocketAPI,
)
from .constants import settings

LOGGER = logging.getLogger(__name__)


class BinanceClient:
    def __init__(self) -> None:
        self._client: Optional[DerivativesTradingUsdsFutures] = None

    def get_client(self) -> DerivativesTradingUsdsFutures:
        """Get the Binance Futures client (lazily initialized)."""
        if self._client:
            return self._client

        rest_url, ws_stream_url, ws_api_url, api_key, api_secret = self._get_config()
        config_rest = ConfigurationRestAPI(
            api_key=api_key, api_secret=api_secret, base_path=rest_url, timeout=2000
        )
        config_ws_api = ConfigurationWebSocketAPI(
            api_key=api_key, api_secret=api_secret, stream_url=ws_api_url
        )
        config_ws = ConfigurationWebSocketStreams(stream_url=ws_stream_url)
        self._client = DerivativesTradingUsdsFutures(
            config_rest_api=config_rest,
            config_ws_api=config_ws_api,
            config_ws_streams=config_ws,
        )
        network = "Testnet" if settings.is_testnet else "Mainnet"
        LOGGER.info(f"Binance Futures client initialized ({network})")
        return self._client

    def _get_config(self) -> tuple[str, str, str, str, str]:
        if settings.is_testnet:
            if settings.api_key_test is None or settings.api_secret_test is None:
                raise ValueError("Testnet API credentials not configured")
            return (
                TESTNET_REST,
                TESTNET_WS_STREAMS,
                TESTNET_WS_API,
                settings.api_key_test,
                settings.api_secret_test,
            )
        else:
            if settings.api_key is None or settings.api_secret is None:
                raise ValueError("Mainnet API credentials not configured")
            return (
                MAINNET_REST,
                MAINNET_WS_STREAMS,
                MAINNET_WS_API,
                settings.api_key,
                settings.api_secret,
            )


_instance = BinanceClient()


def client():
    return _instance.get_client()
