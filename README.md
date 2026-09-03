# monkey-island-custom-client

## Схема работы сервиса
Сервис запускает локальный сервер, который отвечает на POST запрос по пути `/sub/{short_uuid}/custom-json`.
Из пути извлекается `short_uuid`, который затем используется для поиска подписки пользователя.
На основе извлеченной подписки для клиентского приложения на основе `xray` подготавливается JSON.
Этот JSON строится на основе файла `template.json`, там настраивается гибкая маршрутизация, обход WL и т.д.

Обработчик POST запроса затем возвращает итоговый JSON.

## docker-compose.yml в составе network'a сервиса подписки remnawave
```
services:
  monkey-island-custom-config:
    restart: unless-stopped
    image: monkey-island-custom-config:v0.1
    env_file:
      - .env
    privileged: true
    volumes:
      - log:/app/log
    networks:
      - remnawave-network
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "55555:55555"

networks:
  remnawave-network:
    name: remnawave-network
    driver: bridge
    external: true

volumes:
  log:
```

### .env файл
```
HOST="0.0.0.0"
PORT=55555
PANEL_URL="https://remnawave-panel-url.com"
SUBSCRIPTION_URL="https://remnawave-subscription-url.com/sub" # без слеша на конце!!!
RW_BEARER="remnawave token"
BASE_ENTRY_PROXY_TAG="любое название входного прокси сервера"

# Optional: dynamic templates from shredder-admin.
# If this is not set or admin is unavailable, local template.json is used.
SHREDDER_ADMIN_CONFIG_NEXT_URL="http://shredder-admin:8015/api/config-templates/next"
SHREDDER_ADMIN_TOKEN="same-token-as-in-shredder-admin"
SHREDDER_ADMIN_REQUEST_TIMEOUT=5

# Optional: Lagom subscriptions attached to templates in shredder-admin.
# The URL itself is stored per template in the admin UI.
LAGOM_USER_AGENT="Happ/4.12.0/ios/2606121423535"
LAGOM_REQUEST_TIMEOUT=30
LAGOM_CACHE_TTL_SECONDS=300
LAGOM_DIALER_PROXY="ROUTING-IN"
```

Если у шаблона в `shredder-admin` заполнена Lagom-ссылка, custom-config при
выдаче скачивает и кеширует Lagom JSON. Серверы Shredder связываются с
профилями Lagom позиционно: первый сервер через первый профиль, второй через
второй и так далее. Если серверов Shredder больше, список Lagom повторяется по
кругу. Для каждого конфига подмешиваются `WL-BRIDGE` и `WL-0*` выбранного
Lagom-профиля, а конечный Shredder-outbound получает
`dialerProxy: "WL-BRIDGE"`. Получается цепочка
`клиент -> WL-цепочка Lagom -> конечный outbound Lagom -> сервер Shredder`.
Сам шаблон может быть Jinja-файлом с переменными `{{VLESS_USER}}`, `{{REMARKS}}`,
`{{ENTRY_NAME}}`, `{{CLIENT}}`.

## docker-compose.yml и network-mode: host
```
services:
  monkey-island-custom-config:
    restart: unless-stopped
    network_mode: host
    image: monkey-island-custom-config:v0.1
    env_file:
      - .env
    privileged: true
    volumes:
      - log:/app/log
    build:
      context: .
      dockerfile: Dockerfile

volumes:
  log:
```

## Пример Сaddyfile для переадресации трафика POST запросов сервису
```
https://monkeyisland.online {
        @custom_json path /sub/*/custom-json
        reverse_proxy @custom_json http://monkey-island-custom-config:55555
        reverse_proxy * http://remnawave-subscription-page:3010
}

:443 {
    tls internal
    respond 204
}
```
