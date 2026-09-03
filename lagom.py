from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass

import httpx


BRIDGE_TAG = "WL-BRIDGE"
DEFAULT_DIALER_PROXY = "ROUTING-IN"
DEFAULT_MUX = {
    "concurrency": 4,
    "enabled": True,
    "xudpConcurrency": 4,
    "xudpProxyUDP443": "reject",
}
SOURCE_BRIDGE_TAG = "proxy"
WL_TAG_PREFIX = "WL-0"


@dataclass
class LagomSettings:
    subscription_url: str
    country: str | None = None


@dataclass
class LagomCacheEntry:
    expires_at: float
    subscription: list


_cache: dict[str, LagomCacheEntry] = {}


async def fetch_lagom_subscription(
    client: httpx.AsyncClient,
    url: str,
    *,
    user_agent: str,
    timeout: float,
    ttl_seconds: int,
) -> list:
    now = time.monotonic()
    cached = _cache.get(url)
    if cached and cached.expires_at > now:
        return copy.deepcopy(cached.subscription)

    try:
        response = await client.get(
            url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        if cached:
            logging.exception("failed to refresh Lagom subscription, using stale cache: %s", url)
            return copy.deepcopy(cached.subscription)
        raise
    if not isinstance(payload, list):
        raise ValueError("Lagom subscription response must be a JSON array")

    _cache[url] = LagomCacheEntry(
        expires_at=now + max(ttl_seconds, 1),
        subscription=copy.deepcopy(payload),
    )
    return payload


def find_bridge_source(country_cfg: dict) -> dict | None:
    outbounds = country_cfg.get("outbounds")
    if not isinstance(outbounds, list):
        return None

    for outbound in outbounds:
        if isinstance(outbound, dict) and outbound.get("tag") == SOURCE_BRIDGE_TAG:
            return outbound
    for outbound in outbounds:
        if isinstance(outbound, dict) and outbound.get("protocol") == "vless":
            return outbound
    return None


def list_country_configs(subscription: list) -> list[dict]:
    return [
        cfg
        for cfg in subscription
        if isinstance(cfg, dict) and find_bridge_source(cfg) is not None
    ]


def choose_country_config(
    subscription: list,
    *,
    wanted_country: str | None,
    fallback_remarks: str,
    position: int | None = None,
) -> dict | None:
    countries = list_country_configs(subscription)
    if not countries:
        return None

    if position is not None:
        if position < 0:
            raise ValueError("Lagom config position must be non-negative")
        return countries[position % len(countries)]

    needles = [
        value.strip().lower()
        for value in (wanted_country, fallback_remarks)
        if isinstance(value, str) and value.strip()
    ]
    for needle in needles:
        exact = [
            cfg
            for cfg in countries
            if str(cfg.get("remarks", "")).strip().lower() == needle
        ]
        if len(exact) == 1:
            return exact[0]

        partial = [
            cfg
            for cfg in countries
            if needle in str(cfg.get("remarks", "")).strip().lower()
        ]
        if len(partial) == 1:
            return partial[0]

    return countries[0]


def build_wl_bridge(country_cfg: dict, dialer_proxy: str = DEFAULT_DIALER_PROXY) -> dict:
    source = find_bridge_source(country_cfg)
    if source is None:
        raise ValueError(f"Lagom config {country_cfg.get('remarks')} has no vless outbound")

    bridge = copy.deepcopy(source)
    bridge["tag"] = BRIDGE_TAG
    stream_settings = bridge.setdefault("streamSettings", {})
    stream_settings.setdefault("sockopt", {})["dialerProxy"] = dialer_proxy
    bridge.setdefault("mux", copy.deepcopy(DEFAULT_MUX))
    return bridge


def collect_wl_outbounds(subscription: list) -> list[dict]:
    seen: set[str] = set()
    outbounds: list[dict] = []
    for cfg in subscription:
        if not isinstance(cfg, dict):
            continue
        for outbound in cfg.get("outbounds") or []:
            if not isinstance(outbound, dict):
                continue
            tag = outbound.get("tag") or ""
            if tag.startswith(WL_TAG_PREFIX) and tag not in seen:
                seen.add(tag)
                outbounds.append(copy.deepcopy(outbound))
    return outbounds


def build_lagom_outbounds(
    subscription: list,
    *,
    wanted_country: str | None,
    fallback_remarks: str,
    dialer_proxy: str,
    position: int | None = None,
) -> list[dict]:
    country_cfg = choose_country_config(
        subscription,
        wanted_country=wanted_country,
        fallback_remarks=fallback_remarks,
        position=position,
    )
    if country_cfg is None:
        raise ValueError("Lagom subscription has no usable country configs")

    bridge = build_wl_bridge(country_cfg, dialer_proxy=dialer_proxy)
    # The bridge and WL chain must come from the same positional profile.
    wl_outbounds = collect_wl_outbounds([country_cfg])
    if not wl_outbounds:
        raise ValueError("Lagom subscription has no WL-0* outbounds")

    logging.info(
        "lagom outbounds prepared: bridge=%s wl_count=%s country=%s",
        bridge.get("settings", {}).get("vnext", [{}])[0].get("address"),
        len(wl_outbounds),
        country_cfg.get("remarks"),
    )
    return [bridge, *wl_outbounds]
