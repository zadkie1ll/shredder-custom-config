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

    def __read_int_env(self, name: str, default) -> int:
        value = os.getenv(name, default)

        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{name} must be an integer, got {value!r}")

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
