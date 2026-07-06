import uuid
import httpx
import orjson
import uvicorn
import logging
import base64

from jinja2 import Template
from datetime import datetime
from datetime import timedelta
from pathlib import Path as PLPath
from fastapi import Path
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import Config

config = Config()

TEMPLATE = Template(PLPath("template.json").read_text(encoding="utf-8"))
STRICT_CDN_OUTBOUND_TAG = "WL-01-YCDN-RU6"
TIMEWEB_CDN_OUTBOUND_TAG = "WL-01-TWCDN-RU5"
CDN_OUTBOUND_TAGS = {
    STRICT_CDN_OUTBOUND_TAG,
    TIMEWEB_CDN_OUTBOUND_TAG,
}


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
    logging.info("HTTP client initialized")

    yield

    # Shutdown
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
    return response.json()




def build_admin_identity_headers(user_identity):
    if not user_identity:
        return {}

    header_map = {
        "user_key": "X-User-Key",
        "user_id": "X-User-Id",
        "telegram_id": "X-Telegram-Id",
        "username": "X-Username",
        "remnawave_user_uuid": "X-Remnawave-User-Uuid",
        "short_uuid": "X-Short-Uuid",
    }

    headers = {}
    for key, header_name in header_map.items():
        value = user_identity.get(key)
        if value is not None and value != "":
            headers[header_name] = str(value)

    return headers


async def get_runtime_template(client, user_identity=None):
    if not config.admin_config_next_url:
        return TEMPLATE

    headers = {
        "Accept": "application/json",
        **build_admin_identity_headers(user_identity),
    }
    if config.admin_token:
        headers["X-Admin-Token"] = config.admin_token

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

        logging.info(
            "using admin config template id=%s name=%s assignment=%s user_id=%s",
            payload.get("id"),
            payload.get("name"),
            payload.get("assignment_key"),
            payload.get("user_id"),
        )
        return Template(template_source)
    except Exception as exc:
        logging.error(
            "failed to load admin config template, falling back to local template.json: %s",
            exc,
            exc_info=True,
        )
        return TEMPLATE


async def get_client_json_config(client: httpx.AsyncClient, short_uuid: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.bearer}",
    }

    response = await client.get(
        f"{config.subscription_url}/{short_uuid}/json", headers=headers
    )
    response.raise_for_status()
    return response.json()


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


def force_entry_proxy_over_cdn(outbound):
    stream_settings = outbound.setdefault("streamSettings", {})
    sockopt = stream_settings.setdefault("sockopt", {})
    sockopt["dialerProxy"] = "ROUTING-IN"
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
    ]

    if "burstObservatory" in host_json:
        host_json["burstObservatory"]["subjectSelector"] = [cdn_outbound_tag]

    return host_json


async def build_custom_config_response(
    short_uuid: str,
    strict_cdn: bool = False,
    cdn_only: bool = False,
    cdn_outbound_tag: str = STRICT_CDN_OUTBOUND_TAG,
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

        user_data = raw_subscription_json["response"]["user"]
        vless_uuid = user_data["vlessUuid"]
        subscription_url = user_data["subscriptionUrl"]
        username = user_data["username"]
        user_identity = {
            "user_key": f"sub:{short_uuid}",
            "username": username,
            "telegram_id": user_data.get("telegramId") or user_data.get("telegram_id"),
            "remnawave_user_uuid": user_data.get("uuid") or user_data.get("id"),
            "short_uuid": short_uuid,
        }
        runtime_template = await get_runtime_template(
            client=client,
            user_identity=user_identity,
        )
        days_left = raw_subscription_json["response"]["convertedUserInfo"]["daysLeft"]
        converted_user_info = raw_subscription_json["response"]["convertedUserInfo"]

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

        client_config = []
        for outbound, remarks in outbounds:
            rendered_host = runtime_template.render(
                VLESS_USER=vless_uuid,
                REMARKS=remarks,
                ENTRY_NAME=config.base_entry_proxy_tag
            )

            host_json = orjson.loads(rendered_host)

            if should_remove_youtube_route(outbound):
                host_json = remove_youtube_route(host_json)

            if strict_cdn:
                host_json = force_strict_cdn_routing(
                    host_json,
                    cdn_outbound_tag=cdn_outbound_tag,
                )
            else:
                host_json = remove_experimental_cdn_outbounds(host_json)

            outbound = force_entry_proxy_over_cdn(outbound)
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

        announce = "🔐В белых списках доступ ко всем сайтам. Все локации в белом списке!"
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
async def generate_custom_config(short_uuid: str):
    return await build_custom_config_response(short_uuid=short_uuid)


@app.head("/sub/{short_uuid}/custom-json")
async def check_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/custom-json-cdn")
async def generate_strict_cdn_custom_config(short_uuid: str):
    return await build_custom_config_response(short_uuid=short_uuid, strict_cdn=True)


@app.head("/sub/{short_uuid}/custom-json-cdn")
async def check_strict_cdn_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/cdn/custom-json")
async def generate_strict_cdn_custom_config_compatible_path(short_uuid: str):
    return await build_custom_config_response(short_uuid=short_uuid, strict_cdn=True)


@app.head("/sub/{short_uuid}/cdn/custom-json")
async def check_strict_cdn_custom_config_compatible_path(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/cdn/custom-json-only")
async def generate_cdn_only_custom_config(short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        cdn_only=True,
    )


@app.head("/sub/{short_uuid}/cdn/custom-json-only")
async def check_cdn_only_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/timeweb/custom-json")
async def generate_timeweb_strict_cdn_custom_config(short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        cdn_outbound_tag=TIMEWEB_CDN_OUTBOUND_TAG,
    )


@app.head("/sub/{short_uuid}/timeweb/custom-json")
async def check_timeweb_strict_cdn_custom_config(short_uuid: str):
    return Response(status_code=200)


@app.get("/sub/{short_uuid}/timeweb/custom-json-only")
async def generate_timeweb_cdn_only_custom_config(short_uuid: str):
    return await build_custom_config_response(
        short_uuid=short_uuid,
        strict_cdn=True,
        cdn_only=True,
        cdn_outbound_tag=TIMEWEB_CDN_OUTBOUND_TAG,
    )


@app.head("/sub/{short_uuid}/timeweb/custom-json-only")
async def check_timeweb_cdn_only_custom_config(short_uuid: str):
    return Response(status_code=200)


if __name__ == "__main__":
    logging.info(f"starting VPN Config Server on {config.host}:{config.port}")

    uvicorn.run(
       "main:app", host=config.host, port=config.port, reload=True, log_level="info"
    )
