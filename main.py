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


@app.get("/sub/{short_uuid}/custom-json")
async def generate_custom_config(short_uuid: str):
    """
    Генерирует кастомную VPN конфигурацию.
    При возникновении ошибки, делает запрос к оригинальному сервису remnawave-subscription-page и запрашивает json.
    """
    logging.info(f"generating custom config for: {short_uuid}")

    try:
        client = app.state.http_client

        raw_subscription_json = await get_user_subscription_raw(
            client=client, short_uuid=short_uuid
        )

        vless_uuid = raw_subscription_json["response"]["user"]["vlessUuid"]
        subscription_url = raw_subscription_json["response"]["user"]["subscriptionUrl"]
        username = raw_subscription_json["response"]["user"]["username"]
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

        client_config = []
        for outbound, remarks in outbounds:
            rendered_host = TEMPLATE.render(
                VLESS_USER=vless_uuid,
                REMARKS=remarks,
                ENTRY_NAME=config.base_entry_proxy_tag
            )

            host_json = orjson.loads(rendered_host)

            if should_remove_youtube_route(outbound):
                host_json = remove_youtube_route(host_json)

            outbound["streamSettings"]["sockopt"] = {
                "dialerProxy": "ROUTING-IN"
            }
            host_json["outbounds"].insert(0, outbound)
            client_config.append(host_json)

        now = datetime.now()
        future_date = now + timedelta(days=days_left)
        seconds = future_date.timestamp()

        if days_left >= 3650:
            seconds = 0  # never expires

        traffic_raw = converted_user_info["trafficUsed"]
        traffic_bytes = parse_traffic_to_bytes(traffic_raw)

        announce = "Белые списки на Российских серверах"

        return Response(
            content=orjson.dumps(client_config, option=orjson.OPT_INDENT_2).decode('utf-8'),
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename={username}.json",
                "Support-URL": "https://t.me/monkeyislandsupportbot",
                "announce": f"base64:{base64.b64encode(announce.encode("utf-8")).decode("utf-8")}",
                "Profile-Title": f"base64:{base64.b64encode('ShredderVPN'.encode('utf-8')).decode('utf-8')}",
                "Profile-Update-Interval": "1",
                "profile-web-page-url": f"{subscription_url}",
                "Subscription-Userinfo": f"upload=0; download={traffic_bytes}; total=0; expire={seconds}",
                "Cache-Control": "public, immutable",
                "X-Request-ID": str(uuid.uuid4()),
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Unexpected error for {short_uuid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


if __name__ == "__main__":
    logging.info(f"starting VPN Config Server on {config.host}:{config.port}")

    uvicorn.run(
       "main:app", host=config.host, port=config.port, reload=True, log_level="info"
    )
