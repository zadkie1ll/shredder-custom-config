#!/bin/bash

docker buildx build \
  --platform linux/arm64 \
  -t monkey-island-custom-config:v0.1 \
  -f Dockerfile \
  --output type=docker,dest=monkey-island-custom-config-arm64.tar ..