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
```

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