import os

# env names
HOST = "HOST"
PORT = "PORT"
LOG_LEVEL = "LOG_LEVEL"

PANEL_URL = "PANEL_URL"
SUBSCRIPTION_URL = "SUBSCRIPTION_URL"
RW_BEARER = "RW_BEARER"

DEFAULT_OUTBOUNT_TAG = "DEFAULT_OUTBOUNT_TAG"
BASE_ENTRY_PROXY_TAG = "BASE_ENTRY_PROXY_TAG"
SHREDDER_ADMIN_CONFIG_NEXT_URL = "SHREDDER_ADMIN_CONFIG_NEXT_URL"
SHREDDER_ADMIN_TOKEN = "SHREDDER_ADMIN_TOKEN"
SHREDDER_ADMIN_REQUEST_TIMEOUT = "SHREDDER_ADMIN_REQUEST_TIMEOUT"
LAGOM_USER_AGENT = "LAGOM_USER_AGENT"
LAGOM_REQUEST_TIMEOUT = "LAGOM_REQUEST_TIMEOUT"
LAGOM_CACHE_TTL_SECONDS = "LAGOM_CACHE_TTL_SECONDS"
LAGOM_DIALER_PROXY = "LAGOM_DIALER_PROXY"


class Config:
    def __init__(self):
        self.host = os.getenv(HOST, "127.0.0.1")
        self.port = self.__read_int_env(PORT, 8443)
        self.log_level = os.getenv(LOG_LEVEL, "info")
        self.panel_url = self.__read_required_str_env(PANEL_URL)
        self.subscription_url = self.__read_required_str_env(SUBSCRIPTION_URL)
        self.bearer = self.__read_required_str_env(RW_BEARER)
        self.default_outbound_tag = os.getenv(DEFAULT_OUTBOUNT_TAG, "proxy")
        self.base_entry_proxy_tag = self.__read_required_str_env(BASE_ENTRY_PROXY_TAG)
        self.admin_config_next_url = os.getenv(SHREDDER_ADMIN_CONFIG_NEXT_URL)
        self.admin_token = os.getenv(SHREDDER_ADMIN_TOKEN)
        self.admin_request_timeout = self.__read_float_env(
            SHREDDER_ADMIN_REQUEST_TIMEOUT,
            5.0,
        )
        self.lagom_user_agent = os.getenv(
            LAGOM_USER_AGENT,
            "Happ/4.12.0/ios/2606121423535",
        )
        self.lagom_request_timeout = self.__read_float_env(
            LAGOM_REQUEST_TIMEOUT,
            30.0,
        )
        self.lagom_cache_ttl_seconds = self.__read_int_env(
            LAGOM_CACHE_TTL_SECONDS,
            300,
        )
        self.lagom_dialer_proxy = os.getenv(LAGOM_DIALER_PROXY, "ROUTING-IN")

    def __read_int_env(self, name: str, default) -> int:
        value = os.getenv(name, default)

        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{name} must be an integer, got {value!r}")

    def __read_float_env(self, name: str, default) -> float:
        value = os.getenv(name, default)

        try:
            return float(value)
        except ValueError:
            raise ValueError(f"{name} must be a float, got {value!r}")

    def __read_required_int_env(self, name: str) -> int:
        value = os.getenv(name)

        if value is None:
            raise ValueError(f"{name} environment variable is not set.")

        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{name} must be an integer, got {value!r}")

    def __read_required_str_env(self, name: str) -> str:
        value = os.getenv(name)

        if value is None:
            raise ValueError(f"{name} environment variable is not set.")

        return value
