#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo ./scripts/setup-yacdn-origin.sh ORIGIN_DOMAIN

Example:
  sudo CDN_DOMAIN=cdn1.orpheous.ru ./scripts/setup-yacdn-origin.sh ru5.orpheous.ru

What it does:
  - installs docker, nginx, curl, openssl if needed
  - installs TLS cert from /tmp/fullchain.pem + /tmp/privatekey.pem when present
  - configures nginx HTTPS origin for Yandex CDN xHTTP
  - proxies XHTTP path to local Xray/Remnanode inbound
  - configures 127.0.0.1:9000 HTTPS fallback for Reality

Environment:
  CDN_DOMAIN              Public CDN domain. Default: ORIGIN_DOMAIN
  CERT_NAME               Cert directory name. Default: ORIGIN_DOMAIN
  XHTTP_PATH              XHTTP path. Default: /api/uploadFile/
  XHTTP_PORT              Local Xray inbound port. Default: 10086
  FALLBACK_PORT           Local Reality fallback port. Default: 9000
  ENABLE_REALITY_FALLBACK 1/0. Default: 1

Certificate lookup order:
  1. /tmp/fullchain.pem and /tmp/privatekey.pem
  2. /etc/shredder/ssl/$CERT_NAME/fullchain.pem and privatekey.pem
  3. /etc/letsencrypt/live/$CERT_NAME/fullchain.pem and privkey.pem
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$(id -u)" != "0" ]]; then
  echo "ERROR: run as root, for example: sudo $0 ORIGIN_DOMAIN" >&2
  exit 1
fi

ORIGIN_DOMAIN="${1:-${ORIGIN_DOMAIN:-}}"
if [[ -z "$ORIGIN_DOMAIN" ]]; then
  usage >&2
  exit 1
fi

CDN_DOMAIN="${CDN_DOMAIN:-$ORIGIN_DOMAIN}"
CERT_NAME="${CERT_NAME:-$ORIGIN_DOMAIN}"
XHTTP_PATH="${XHTTP_PATH:-/api/uploadFile/}"
XHTTP_PORT="${XHTTP_PORT:-10086}"
FALLBACK_PORT="${FALLBACK_PORT:-9000}"
ENABLE_REALITY_FALLBACK="${ENABLE_REALITY_FALLBACK:-1}"

if [[ "$XHTTP_PATH" != /* ]]; then
  XHTTP_PATH="/$XHTTP_PATH"
fi
if [[ "$XHTTP_PATH" != */ ]]; then
  XHTTP_PATH="$XHTTP_PATH/"
fi

SERVER_NAMES="$ORIGIN_DOMAIN"
if [[ "$CDN_DOMAIN" != "$ORIGIN_DOMAIN" ]]; then
  SERVER_NAMES="$SERVER_NAMES $CDN_DOMAIN"
fi

echo "== Yandex CDN origin setup =="
echo "Origin domain: $ORIGIN_DOMAIN"
echo "CDN domain:    $CDN_DOMAIN"
echo "Server names:  $SERVER_NAMES"
echo "Cert name:     $CERT_NAME"
echo "XHTTP path:    $XHTTP_PATH"
echo "XHTTP port:    $XHTTP_PORT"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  docker.io \
  nginx \
  openssl

systemctl enable --now docker
systemctl enable nginx

CERT_DIR="/etc/shredder/ssl/$CERT_NAME"
mkdir -p "$CERT_DIR"

if [[ -s /tmp/fullchain.pem && -s /tmp/privatekey.pem ]]; then
  echo "Installing certificate from /tmp"
  install -o root -g root -m 0644 /tmp/fullchain.pem "$CERT_DIR/fullchain.pem"
  install -o root -g root -m 0600 /tmp/privatekey.pem "$CERT_DIR/privatekey.pem"
  rm -f /tmp/fullchain.pem /tmp/privatekey.pem
elif [[ -s "$CERT_DIR/fullchain.pem" && -s "$CERT_DIR/privatekey.pem" ]]; then
  echo "Using existing certificate in $CERT_DIR"
elif [[ -s "/etc/letsencrypt/live/$CERT_NAME/fullchain.pem" && -s "/etc/letsencrypt/live/$CERT_NAME/privkey.pem" ]]; then
  echo "Using Let's Encrypt certificate for $CERT_NAME"
  ln -sfn "/etc/letsencrypt/live/$CERT_NAME/fullchain.pem" "$CERT_DIR/fullchain.pem"
  ln -sfn "/etc/letsencrypt/live/$CERT_NAME/privkey.pem" "$CERT_DIR/privatekey.pem"
else
  echo "ERROR: certificate not found for $CERT_NAME" >&2
  echo "Put fullchain.pem and privatekey.pem into /tmp or issue certbot cert first." >&2
  exit 1
fi

openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -subject -dates

mkdir -p /var/www/yacdn-origin
cat > /var/www/yacdn-origin/index.html <<'EOF'
ok
EOF

rm -f /etc/nginx/conf.d/default.conf
rm -f /etc/nginx/sites-enabled/default

NGINX_CONF="/etc/nginx/conf.d/yacdn-origin.conf"

cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    server_name $SERVER_NAMES;

    location = ${XHTTP_PATH}_health {
        add_header Cache-Control "no-store" always;
        add_header CDN-Cache-Control "no-store" always;
        add_header X-Origin-Node "$ORIGIN_DOMAIN" always;
        add_header X-CDN-Test "yandex-https-xhttp-origin-ready" always;
        return 204;
    }

    location = ${XHTTP_PATH}_echo {
        default_type text/plain;
        add_header Cache-Control "no-store" always;
        add_header CDN-Cache-Control "no-store" always;
        add_header X-Origin-Node "$ORIGIN_DOMAIN" always;
        return 200 "$ORIGIN_DOMAIN yandex cdn origin ok\n";
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    http2 on;
    server_name $SERVER_NAMES;

    ssl_certificate $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privatekey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    root /var/www/yacdn-origin;
    index index.html;

    location = ${XHTTP_PATH}_health {
        add_header Cache-Control "no-store" always;
        add_header CDN-Cache-Control "no-store" always;
        add_header X-Origin-Node "$ORIGIN_DOMAIN" always;
        add_header X-CDN-Test "yandex-https-xhttp-origin-ready" always;
        return 204;
    }

    location = ${XHTTP_PATH}_echo {
        default_type text/plain;
        add_header Cache-Control "no-store" always;
        add_header CDN-Cache-Control "no-store" always;
        add_header X-Origin-Node "$ORIGIN_DOMAIN" always;
        return 200 "$ORIGIN_DOMAIN yandex cdn origin ok\n";
    }

    location ${XHTTP_PATH} {
        proxy_pass http://127.0.0.1:${XHTTP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_cache off;
        proxy_connect_timeout 60s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        client_max_body_size 0;

        add_header Cache-Control "no-store, no-cache, must-revalidate, proxy-revalidate" always;
        add_header CDN-Cache-Control "no-store" always;
        add_header Pragma "no-cache" always;
        add_header Expires "0" always;
    }

    location / {
        try_files \$uri \$uri/ =401;
        add_header Cache-Control "public, max-age=86400" always;
    }
}
EOF

if [[ "$ENABLE_REALITY_FALLBACK" == "1" ]]; then
  cat > /etc/nginx/conf.d/reality-fallback.conf <<EOF
server {
    listen 127.0.0.1:${FALLBACK_PORT} ssl proxy_protocol;
    http2 on;
    server_name $SERVER_NAMES;

    ssl_certificate $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privatekey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    real_ip_header proxy_protocol;
    set_real_ip_from 127.0.0.1;

    location / {
        return 401;
    }
}
EOF
fi

nginx -t
systemctl restart nginx

echo "== Local checks =="
curl -skI "https://127.0.0.1${XHTTP_PATH}_health" \
  --resolve "$ORIGIN_DOMAIN:443:127.0.0.1" \
  -H "Host: $ORIGIN_DOMAIN" || true

ss -lntp | grep -E ":(80|443|${XHTTP_PORT}|${FALLBACK_PORT})\\b" || true

cat <<EOF

Done.

Remnawave inbound should match this origin:
  tag: vless-xhttp-cdn-cur
  listen: 127.0.0.1
  port: ${XHTTP_PORT}
  network: xhttp
  path: ${XHTTP_PATH}
  mode: packet-up
  sessionIDKey: X-Upload-Token
  sessionIDPlacement: header
  seqKey: chunk_id
  seqPlacement: query

CDN health check:
  curl -vkI https://${CDN_DOMAIN}${XHTTP_PATH}_health
EOF
