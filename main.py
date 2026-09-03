import uuid
import copy
import httpx
import orjson
import uvicorn
import logging
import base64

from dataclasses import dataclass
from jinja2 import Template
from datetime import datetime
from datetime import timedelta
from pathlib import Path as PLPath
from fastapi import Path
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import Config
from lagom import BRIDGE_TAG
from lagom import LagomSettings
from lagom import build_lagom_outbounds
from lagom import fetch_lagom_subscription

config = Config()

LOCAL_TEMPLATE_CONTENT = PLPath("template.json").read_text(encoding="utf-8")
LOCAL_TEMPLATE = Template(LOCAL_TEMPLATE_CONTENT)
YANDEX_HTTPS_TEMPLATE_CONTENT = PLPath("template_yandex_https.json").read_text(
    encoding="utf-8"
)
YANDEX_HTTPS_TEMPLATE = Template(YANDEX_HTTPS_TEMPLATE_CONTENT)
STRICT_CDN_OUTBOUND_TAG = "WL-03-YCDN-RU6"
YANDEX_HTTPS_CDN_OUTBOUND_TAG = "WL-03-YCDN-HTTPS-RU5"
TIMEWEB_CDN_OUTBOUND_TAG = "WL-03-TWCDN-RU5"
AUTOSELECT_CDN_DOMAIN = "cdn2.orpheous.ru"
AUTOSELECT_CDN_REMARKS_MARKERS = (
    "автовыбор",
    "авто-выбор",
    "авто выбор",
    "auto-select",
    "autoselect",
    "auto-selection",
)
AUTOSELECT_CDN_ADDRESSES = {AUTOSELECT_CDN_DOMAIN, "201.51.9.71"}
YOUTUBE_DIRECT_ENTRY_ADDRESSES = {"yt.orpheous.ru"}
CDN_OUTBOUND_TAGS = {
    STRICT_CDN_OUTBOUND_TAG,
    YANDEX_HTTPS_CDN_OUTBOUND_TAG,
    TIMEWEB_CDN_OUTBOUND_TAG,
}


@dataclass(frozen=True)
class RuntimeTemplate:
    template: Template
    lagom: LagomSettings | None = None


LOCAL_RUNTIME_TEMPLATE = RuntimeTemplate(template=LOCAL_TEMPLATE)
YANDEX_HTTPS_RUNTIME_TEMPLATE = RuntimeTemplate(template=YANDEX_HTTPS_TEMPLATE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=15.0,
            read=30.0,
            write=30.0,
            pool=10.0
        ),
        limits=httpx.Limits(
            max_keepalive_connections=100,
            max_connections=200,
            keepalive_expiry=60.0
        ),
        http2=True,
        follow_redirects=True,
        transport=httpx.AsyncHTTPTransport(retries=3)
    )
    app.state.lagom_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=15.0,
            read=max(config.lagom_request_timeout, 15.0),
            write=30.0,
            pool=10.0,
        ),
        limits=httpx.Limits(
            max_keepalive_connections=20,
            max_connections=50,
            keepalive_expiry=30.0,
        ),
        http2=False,
        follow_redirects=True,
        transport=httpx.AsyncHTTPTransport(retries=2),
    )
    logging.info("HTTP client initialized")

    yield

    # Shutdown
    await app.state.lagom_http_client.aclose()
    await app.state.http_client.aclose()
    logging.info("HTTP client closed")


# Создаем FastAPI приложение
app = FastAPI(
    title="Monkey Island VPN Custom JSON Config Generator",
    description="Генератор кастомных VPN конфигураций",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def parse_traffic_to_bytes(traffic_str):
    # Словарь множителей для двоичных единиц (IEC стандарт)
    units = {
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "TiB": 1024**4,
        "PiB": 1024**5
    }
    
    parts = traffic_str.split()
    if len(parts) != 2:
        return 0
    
    value = float(parts[0])
    unit = parts[1]
    
    return int(value * units.get(unit, 1))

async def get_user_subscription_raw(client: httpx.AsyncClient, short_uuid: str):
    headers = {
        "Authorization": f"Bearer {config.bearer}",
    }

    response = await client.get(
        f"{config.panel_url}/api/subscriptions/by-short-uuid/{short_uuid}/raw",
        headers=headers,
    )
    response.raise_for_status()
    return parse_remnawave_response(response)


def parse_remnawave_response(response: httpx.Response):
    if response.status_code in (202, 204) or not response.content:
        return None
    return response.json()


async def get_client_json_config(client: httpx.AsyncClient, short_uuid: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.bearer}",
    }

    response = await client.get(
        f"{config.subscription_url}/{short_uuid}/json", headers=headers
    )
    response.raise_for_status()
    payload = parse_remnawave_response(response)
    if payload is None:
        raise HTTPException(status_code=502, detail="empty client config response")
    return payload


async def get_runtime_template(
    client: httpx.AsyncClient,
    user_identity: dict | None = None,
) -> RuntimeTemplate:
    if not config.admin_config_next_url:
        return LOCAL_RUNTIME_TEMPLATE

    headers = {}
    if config.admin_token:
        headers["X-Admin-Token"] = config.admin_token
    if user_identity:
        if user_identity.get("user_key"):
            headers["X-User-Key"] = str(user_identity["user_key"])
        if user_identity.get("user_id"):
            headers["X-User-Id"] = str(user_identity["user_id"])
        if user_identity.get("telegram_id"):
            headers["X-Telegram-Id"] = str(user_identity["telegram_id"])
        if user_identity.get("username"):
            headers["X-Username"] = str(user_identity["username"])
        if user_identity.get("remnawave_user_id"):
            headers["X-Remnawave-User-Id"] = str(user_identity["remnawave_user_id"])
        elif user_identity.get("remnawave_user_uuid"):
            headers["X-Remnawave-User-Uuid"] = str(user_identity["remnawave_user_uuid"])
        if user_identity.get("short_uuid"):
            headers["X-Short-Uuid"] = str(user_identity["short_uuid"])

    try:
        response = await client.get(
            config.admin_config_next_url,
            headers=headers,
            timeout=config.admin_request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("content")
        if isinstance(content, str):
            template_source = content
        elif isinstance(content, (dict, list)):
            template_source = orjson.dumps(content).decode("utf-8")
        else:
            raise ValueError("admin response does not contain JSON content")
        lagom_url = payload.get("lagom_subscription_url")
        lagom_country = payload.get("lagom_country")
        lagom_settings = None
        if isinstance(lagom_url, str) and lagom_url.strip():
            lagom_settings = LagomSettings(
                subscription_url=lagom_url.strip(),
                country=lagom_country.strip()
                if isinstance(lagom_country, str) and lagom_country.strip()
                else None,
            )
        logging.info(
            "using admin config template id=%s name=%s index=%s/%s assignment=%s user_id=%s lagom=%s",
            payload.get("id"),
            payload.get("name"),
            payload.get("index"),
            payload.get("total_active"),
            payload.get("assignment_status"),
            payload.get("user_id"),
            bool(lagom_settings),
        )
        return RuntimeTemplate(
            template=Template(template_source),
            lagom=lagom_settings,
        )
    except Exception as exc:
        logging.warning(
            "failed to fetch admin config template, using local template.json: %s",
            exc,
        )
        return LOCAL_RUNTIME_TEMPLATE


def render_host_json(
    runtime_template: RuntimeTemplate,
    vless_uuid: str,
    remarks: str,
    client_name: str,
) -> dict:
    try:
        rendered_host = runtime_template.template.render(
            VLESS_USER=vless_uuid,
            REMARKS=remarks,
            ENTRY_NAME=config.base_entry_proxy_tag,
            CLIENT=client_name,
        )
        return orjson.loads(rendered_host)
    except Exception:
        if runtime_template.template is LOCAL_TEMPLATE:
            raise

        logging.exception("admin template render failed, using local template.json")
        rendered_host = LOCAL_TEMPLATE.render(
            VLESS_USER=vless_uuid,
            REMARKS=remarks,
            ENTRY_NAME=config.base_entry_proxy_tag,
            CLIENT=client_name,
        )
        return orjson.loads(rendered_host)


def detect_client_name(request: Request | None) -> str:
    if request is None:
        return "happ"

    query_client = (request.query_params.get("client") or "").strip().lower()
    if query_client in {"incy", "happ"}:
        return query_client

    user_agent = (request.headers.get("user-agent") or "").lower()
    if "incy" in user_agent:
        return "incy"
    return "happ"


async def get_all_outbounds(
    client_config_json: str, searching_outbound_tag: str
) -> list[tuple[dict, str]]:
    outbounds = []
    for object in client_config_json:
        for outbound in object["outbounds"]:
            if outbound["tag"] == searching_outbound_tag:
                outbound["tag"] = config.base_entry_proxy_tag
                outbounds.append((outbound, object["remarks"]))

    return outbounds


def should_remove_youtube_route(outbound) -> bool:
    if "streamSettings" in outbound and "realitySettings" in outbound["streamSettings"]:
        if "serverName" in outbound["streamSettings"]["realitySettings"]:
            return outbound["streamSettings"]["realitySettings"]["serverName"] == "ru.monkeyisland.xyz"
    
    return False

def remove_youtube_route(host_json):
    if "burstObservatory" in host_json and "subjectSelector" in host_json["burstObservatory"]:
        host_json["burstObservatory"]["subjectSelector"].remove("YOUTUBE")

    if "routing" in host_json and "balancers" in host_json["routing"]:
        index_to_remove = None

        for i, balancer in enumerate(host_json["routing"]["balancers"]):
            if "tag" in balancer and balancer["tag"] == "YOUTUBE-BALANCER":
                index_to_remove = i
                break
        
        if index_to_remove is not None:
            host_json["routing"]["balancers"].pop(index_to_remove)

    if "routing" in host_json and "rules" in host_json["routing"]:
        index_to_remove = None

        for i, balancer in enumerate(host_json["routing"]["rules"]):
            if "balancerTag" in balancer and balancer["balancerTag"] == "YOUTUBE-BALANCER":
                index_to_remove = i
                break

        if index_to_remove is not None:
            host_json["routing"]["rules"].pop(index_to_remove)

    if "outbounds" in host_json:
        index_to_remove = None

        for i, outbound in enumerate(host_json["outbounds"]):
            if "tag" in outbound and outbound["tag"] == "YOUTUBE":
                index_to_remove = i
                break

        if index_to_remove is not None:
            host_json["outbounds"].pop(index_to_remove)

    return host_json


def force_strict_cdn_routing(host_json, cdn_outbound_tag=STRICT_CDN_OUTBOUND_TAG):
    host_json["outbounds"] = [
        outbound
        for outbound in host_json.get("outbounds", [])
        if outbound.get("tag") not in CDN_OUTBOUND_TAGS
        or outbound.get("tag") == cdn_outbound_tag
    ]

    if "burstObservatory" in host_json:
        host_json["burstObservatory"]["subjectSelector"] = [cdn_outbound_tag]

    routing = host_json.get("routing", {})

    for balancer in routing.get("balancers", []):
        if balancer.get("tag") in {
            "WL-BALANCER",
            "01-FALLBACK",
            "02-FALLBACK",
            "03-FALLBACK",
        }:
            balancer["selector"] = [cdn_outbound_tag]
            balancer["fallbackTag"] = cdn_outbound_tag

    for rule in routing.get("rules", []):
        if rule.get("inboundTag"):
            continue

        if rule.get("balancerTag") == "WL-BALANCER":
            rule.pop("balancerTag", None)
            rule["outboundTag"] = config.base_entry_proxy_tag
            continue

        if rule.get("outboundTag") in {
            "RU-WL-DIRECT",
            STRICT_CDN_OUTBOUND_TAG,
            YANDEX_HTTPS_CDN_OUTBOUND_TAG,
            TIMEWEB_CDN_OUTBOUND_TAG,
        }:
            rule["outboundTag"] = config.base_entry_proxy_tag
            continue

        if rule.get("outboundTag") == "DIRECT" and rule.get("ip") == ["77.88.8.8"]:
            rule["outboundTag"] = config.base_entry_proxy_tag

    return host_json


def remove_experimental_cdn_outbounds(host_json):
    host_json["outbounds"] = [
        outbound
        for outbound in host_json.get("outbounds", [])
        if outbound.get("tag") != TIMEWEB_CDN_OUTBOUND_TAG
    ]

    return host_json


def remove_template_lagom_outbounds(host_json):
    host_json["outbounds"] = [
        outbound
        for outbound in host_json.get("outbounds", [])
        if not str(outbound.get("tag", "")).startswith("WL-0")
        and outbound.get("tag") != BRIDGE_TAG
    ]

    return host_json


def is_autoselect_outbound(outbound: dict, remarks: str) -> bool:
    normalized_remarks = remarks.lower()
    if any(marker in normalized_remarks for marker in AUTOSELECT_CDN_REMARKS_MARKERS):
        return True

    vnext = outbound.get("settings", {}).get("vnext", [])
    for server in vnext:
        address = server.get("address") if isinstance(server, dict) else None
        if address in AUTOSELECT_CDN_ADDRESSES:
            return True

    return False


def is_youtube_direct_entry_outbound(outbound: dict) -> bool:
    vnext = outbound.get("settings", {}).get("vnext", [])
    for server in vnext:
        address = server.get("address") if isinstance(server, dict) else None
        if address in YOUTUBE_DIRECT_ENTRY_ADDRESSES:
            return True

    return False


def build_autoselect_cdn_outbound(outbound: dict) -> dict:
    autoselect_outbound = copy.deepcopy(outbound)
    autoselect_outbound["tag"] = config.base_entry_proxy_tag
    autoselect_outbound["protocol"] = "vless"

    vnext = autoselect_outbound.setdefault("settings", {}).setdefault("vnext", [{}])
    if not vnext:
        vnext.append({})
    vnext[0]["address"] = AUTOSELECT_CDN_DOMAIN
    vnext[0]["port"] = 443

    stream_settings = autoselect_outbound.setdefault("streamSettings", {})
    stream_settings["network"] = "xhttp"
    stream_settings["security"] = "tls"
    stream_settings.pop("sockopt", None)
    stream_settings["xhttpSettings"] = {
        "host": AUTOSELECT_CDN_DOMAIN,
        "path": "/api/uploadFile/",
        "mode": "packet-up",
        "uplinkHTTPMethod": "GET",
        "sessionKey": "X-Upload-Token",
        "sessionPlacement": "header",
        "sessionIDKey": "X-Upload-Token",
        "sessionIDPlacement": "header",
        "seqKey": "chunk_id",
        "seqPlacement": "query",
        "xPaddingHeader": "X-Client-Version",
        "xPaddingMethod": "tokenish",
        "xPaddingKey": "hash",
        "xPaddingObfsMode": True,
        "xPaddingPlacement": "queryInHeader",
        "xmux": {
            "hMaxReusableSecs": "1800-3000",
            "cMaxReuseTimes": 1000,
            "maxConcurrency": "16-32",
            "maxConnections": 0,
            "hKeepAlivePeriod": 20000,
            "hMaxRequestTimes": "600-900",
        },
    }
    stream_settings["tlsSettings"] = {
        "serverName": AUTOSELECT_CDN_DOMAIN,
        "allowInsecure": False,
        "alpn": ["h2", "http/1.1"],
        "fingerprint": "chrome",
    }

    return autoselect_outbound


def force_autoselect_cdn_routing(host_json: dict) -> dict:
    if "burstObservatory" in host_json:
        host_json["burstObservatory"]["subjectSelector"] = [config.base_entry_proxy_tag]

    routing = host_json.get("routing", {})
    for balancer in routing.get("balancers", []):
        if balancer.get("tag") in {
            "WL-BALANCER",
            "01-FALLBACK",
            "02-FALLBACK",
            "03-FALLBACK",
            "YOUTUBE-BALANCER",
        }:
            balancer["selector"] = [config.base_entry_proxy_tag]
            balancer["fallbackTag"] = config.base_entry_proxy_tag

    for rule in routing.get("rules", []):
        if rule.get("balancerTag") in {
            "WL-BALANCER",
            "01-FALLBACK",
            "02-FALLBACK",
            "03-FALLBACK",
            "YOUTUBE-BALANCER",
        }:
            rule.pop("balancerTag", None)
            rule["outboundTag"] = config.base_entry_proxy_tag
            continue

        if rule.get("outboundTag") in {
            "RU-WL-DIRECT",
            "DIRECT",
            STRICT_CDN_OUTBOUND_TAG,
            YANDEX_HTTPS_CDN_OUTBOUND_TAG,
            TIMEWEB_CDN_OUTBOUND_TAG,
        }:
            rule["outboundTag"] = config.base_entry_proxy_tag

    return host_json


def force_entry_proxy_over_cdn(outbound, dialer_proxy="ROUTING-IN"):
    stream_settings = outbound.setdefault("streamSettings", {})
    sockopt = stream_settings.setdefault("sockopt", {})
    sockopt["dialerProxy"] = dialer_proxy
    return outbound


def force_cdn_only_config(host_json, cdn_outbound_tag=STRICT_CDN_OUTBOUND_TAG):
    host_json["outbounds"] = [
        outbound
        for outbound in host_json.get("outbounds", [])
        if outbound.get("tag") in {
            config.base_entry_proxy_tag,
            cdn_outbound_tag,
            "DIRECT",
            "RU-WL-DIRECT",
            "BLOCK",
            "ROUTING-IN",
            "LOOP-WL",
            "LOOP-01",
            "LOOP-02",
        }
        or outbound.get("tag") == BRIDGE_TAG
        or str(outbound.get("tag", "")).startswith("WL-0")
    ]

    if "burstObservatory" in host_json:
        host_json["burstObservatory"]["subjectSelector"] = [cdn_outbound_tag]

    return host_json


async def build_custom_config_response(
    short_uuid: str,
    strict_cdn: bool = False,
    cdn_only: bool = False,
    cdn_outbound_tag: str = STRICT_CDN_OUTBOUND_TAG,
    template_override: RuntimeTemplate | None = None,
    client_name: str = "happ",
):
    """
    Генерирует кастомную VPN конфигурацию.
    При возникновении ошибки, делает запрос к оригинальному сервису remnawave-subscription-page и запрашивает json.
    """
    logging.info(
        f"generating custom config for: {short_uuid}, "
        f"strict_cdn={strict_cdn}, cdn_only={cdn_only}, "
        f"cdn_outbound_tag={cdn_outbound_tag}"
    )

    try:
        client = app.state.http_client

        raw_subscription_json = await get_user_subscription_raw(
            client=client, short_uuid=short_uuid
        )

        user = raw_subscription_json["response"]["user"]
        vless_uuid = user["vlessUuid"]
        subscription_url = user["subscriptionUrl"]
        username = user["username"]
        days_left = raw_subscription_json["response"]["convertedUserInfo"]["daysLeft"]
        converted_user_info = raw_subscription_json["response"]["convertedUserInfo"]
        telegram_id = user.get("telegramId") or user.get("telegram_id")
        if telegram_id is not None:
            telegram_id = int(telegram_id)

        user_identity = {
            "user_key": f"sub:{short_uuid}",
            "user_id": user.get("id"),
            "username": username,
            "telegram_id": telegram_id,
            "remnawave_user_id": user.get("id"),
            "remnawave_user_uuid": user.get("uuid"),
            "short_uuid": short_uuid,
        }

        client_config_json = await get_client_json_config(
            client=client, short_uuid=short_uuid
        )

        outbounds = await get_all_outbounds(
            client_config_json=client_config_json,
            searching_outbound_tag="proxy",
        )

        if not outbounds:
            return JSONResponse(
                content=client_config_json,
                media_type="application/json",
            )

        if cdn_only:
            outbounds = outbounds[:1]

        runtime_template = template_override
        if runtime_template is None:
            runtime_template = await get_runtime_template(
                client,
                user_identity=user_identity,
            )
        lagom_subscription = None
        if runtime_template.lagom is not None:
            try:
                lagom_subscription = await fetch_lagom_subscription(
                    app.state.lagom_http_client,
                    runtime_template.lagom.subscription_url,
                    user_agent=config.lagom_user_agent,
                    timeout=config.lagom_request_timeout,
                    ttl_seconds=config.lagom_cache_ttl_seconds,
                )
            except Exception:
                logging.exception(
                    "Lagom subscription is unavailable, using template without Lagom: %s",
                    runtime_template.lagom.subscription_url,
                )
        client_config = []
        for outbound_index, (outbound, remarks) in enumerate(outbounds):
            host_json = render_host_json(runtime_template, vless_uuid, remarks, client_name)
            entry_dialer_proxy = "ROUTING-IN"

            if runtime_template.lagom is not None and lagom_subscription is not None:
                host_json = remove_template_lagom_outbounds(host_json)
                lagom_outbounds = build_lagom_outbounds(
                    lagom_subscription,
                    wanted_country=runtime_template.lagom.country,
                    fallback_remarks=remarks,
                    dialer_proxy=config.lagom_dialer_proxy,
                    position=outbound_index,
                )
                host_json.setdefault("outbounds", [])[0:0] = lagom_outbounds
                entry_dialer_proxy = BRIDGE_TAG

            if should_remove_youtube_route(outbound):
                host_json = remove_youtube_route(host_json)

            if strict_cdn:
                host_json = force_strict_cdn_routing(
                    host_json,
                    cdn_outbound_tag=cdn_outbound_tag,
                )
            else:
                host_json = remove_experimental_cdn_outbounds(host_json)

            autoselect_outbound = is_autoselect_outbound(outbound, remarks)
            if autoselect_outbound:
                outbound = build_autoselect_cdn_outbound(outbound)
                host_json = force_autoselect_cdn_routing(host_json)
            elif is_youtube_direct_entry_outbound(outbound):
                logging.info("entry proxy uses direct dial for youtube host: %s", remarks)
            else:
                outbound = force_entry_proxy_over_cdn(
                    outbound,
                    dialer_proxy=entry_dialer_proxy,
                )
            host_json["outbounds"].insert(0, outbound)

            if cdn_only:
                host_json["remarks"] = f"{host_json.get('remarks', remarks)} CDN ONLY"
                host_json = force_cdn_only_config(
                    host_json,
                    cdn_outbound_tag=cdn_outbound_tag,
                )

            client_config.append(host_json)

        now = datetime.now()
        future_date = now + timedelta(days=days_left)
        seconds = future_date.timestamp()

        if days_left >= 3650:
            seconds = 0  # never expires

        traffic_raw = converted_user_info["trafficUsed"]
        traffic_bytes = parse_traffic_to_bytes(traffic_raw)

        announce = "❗ ВСЕ ЛОКАЦИИ В БЕЛОМ СПИСКЕ ❗\nЕсли что-то не работает, обновите подписку"
        announce_base64 = base64.b64encode(announce.encode("utf-8")).decode("utf-8")
        profile_title_base64 = base64.b64encode(
            "ShredderVPN".encode("utf-8")
        ).decode("utf-8")

        return Response(
            content=orjson.dumps(client_config, option=orjson.OPT_INDENT_2).decode('utf-8'),
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename={username}.json",
                "Support-URL": "https://t.me/Shredder_vps_bot",
                "announce": f"base64:{announce_base64}",
                "Profile-Title": f"base64:{profile_title_base64}",
                "Profile-Update-Interval": "1",
                "profile-web-page-url": f"{subscription_url}",
                "Subscription-Userinfo": f"upload=0; download={traffic_bytes}; total=0; expire={seconds}",
                "Cache-Control": "private, no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Request-ID": str(uuid.uuid4()),
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Unexpected error for {short_uuid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/sub/{short_uuid}/custom-json")
async def generate_custom_config(request: Request, short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        client_name=detect_client_name(request),
    )


@app.head("/sub/{short_uuid}/custom-json")
async def check_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/custom-json-cdn")
async def generate_strict_cdn_custom_config(request: Request, short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        client_name=detect_client_name(request),
    )


@app.head("/sub/{short_uuid}/custom-json-cdn")
async def check_strict_cdn_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/cdn/custom-json")
async def generate_strict_cdn_custom_config_compatible_path(request: Request, short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        client_name=detect_client_name(request),
    )


@app.head("/sub/{short_uuid}/cdn/custom-json")
async def check_strict_cdn_custom_config_compatible_path(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/cdn/custom-json-only")
async def generate_cdn_only_custom_config(request: Request, short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        cdn_only=True,
        client_name=detect_client_name(request),
    )


@app.head("/sub/{short_uuid}/cdn/custom-json-only")
async def check_cdn_only_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/timeweb/custom-json")
async def generate_timeweb_strict_cdn_custom_config(request: Request, short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        cdn_outbound_tag=TIMEWEB_CDN_OUTBOUND_TAG,
        client_name=detect_client_name(request),
    )


@app.head("/sub/{short_uuid}/timeweb/custom-json")
async def check_timeweb_strict_cdn_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/timeweb/custom-json-only")
async def generate_timeweb_cdn_only_custom_config(request: Request, short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        cdn_only=True,
        cdn_outbound_tag=TIMEWEB_CDN_OUTBOUND_TAG,
        client_name=detect_client_name(request),
    )


@app.head("/sub/{short_uuid}/timeweb/custom-json-only")
async def check_timeweb_cdn_only_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/yandex-https/custom-json")
async def generate_yandex_https_strict_cdn_custom_config(request: Request, short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        cdn_outbound_tag=YANDEX_HTTPS_CDN_OUTBOUND_TAG,
        template_override=YANDEX_HTTPS_RUNTIME_TEMPLATE,
        client_name=detect_client_name(request),
    )


@app.head("/sub/{short_uuid}/yandex-https/custom-json")
async def check_yandex_https_strict_cdn_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/yandex-https/custom-json-only")
async def generate_yandex_https_cdn_only_custom_config(request: Request, short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        cdn_only=True,
        cdn_outbound_tag=YANDEX_HTTPS_CDN_OUTBOUND_TAG,
        template_override=YANDEX_HTTPS_RUNTIME_TEMPLATE,
        client_name=detect_client_name(request),
    )


@app.head("/sub/{short_uuid}/yandex-https/custom-json-only")
async def check_yandex_https_cdn_only_custom_config(short_uuid: str):
    return Response(status_code=200)


if __name__ == "__main__":
    logging.info(f"starting VPN Config Server on {config.host}:{config.port}")

    uvicorn.run(
       "main:app", host=config.host, port=config.port, reload=True, log_level="info"
    )
