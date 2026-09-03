#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def extract_short_uuid(value: str) -> str:
    match = re.search(r"/sub/([^/?#]+)", value)
    if match:
        return match.group(1)

    return value.strip()


def request_raw_subscription(panel_url: str, bearer: str, short_uuid: str) -> dict:
    url = f"{panel_url.rstrip('/')}/api/subscriptions/by-short-uuid/{short_uuid}/raw"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {bearer}"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status in (202, 204):
            return {}
        body = response.read()
        if not body:
            return {}
        return json.loads(body)


def parse_description(description: str | None) -> dict:
    if not description:
        return {}

    try:
        parsed = json.loads(description)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lookup a Remnawave subscription user by /sub/{short_uuid} link.",
    )
    parser.add_argument("subscription", help="Subscription URL or short UUID")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to env file with PANEL_URL and RW_BEARER (default: .env)",
    )
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))

    panel_url = os.getenv("PANEL_URL")
    bearer = os.getenv("RW_BEARER")
    if not panel_url or not bearer:
        print(
            "Missing PANEL_URL or RW_BEARER. Export them or provide --env-file.",
            file=sys.stderr,
        )
        return 2

    short_uuid = extract_short_uuid(args.subscription)

    try:
        payload = request_raw_subscription(panel_url, bearer, short_uuid)
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"Request failed: {error}", file=sys.stderr)
        return 1

    user = payload.get("response", {}).get("user", {})
    description = parse_description(user.get("description"))

    result = {
        "shortUuid": user.get("shortUuid"),
        "xrayLogEmail": str(user.get("id")) if user.get("id") is not None else None,
        "remnawaveEmail": user.get("email"),
        "remnawaveUserId": user.get("id"),
        "username": user.get("username"),
        "telegramId": user.get("telegramId"),
        "telegramUsername": description.get("username"),
        "telegramFirstName": description.get("first_name"),
        "telegramLastName": description.get("last_name"),
        "status": user.get("status"),
        "expireAt": user.get("expireAt"),
        "vlessUuid": user.get("vlessUuid"),
        "subscriptionUrl": user.get("subscriptionUrl"),
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
