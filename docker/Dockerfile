ARG PYTHON_VERSION=3.12
FROM python:$PYTHON_VERSION-slim as builder

ENV container="docker"
ENV DEBIAN_FRONTEND=noninteractive

RUN apt update && apt upgrade -y && \
    apt install -y --no-install-recommends \
        git \
        make \
        libssl-dev \
        pkg-config \
        build-essential \
        python3-dev \
        libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory.
WORKDIR /app

# copy local scripts
# copy local scripts
COPY *.py ./
COPY *.pyi ./
COPY template.json ./
COPY requirements.txt ./

RUN python3 -m pip install --upgrade pip setuptools && \
    pip install --no-cache-dir -r requirements.txt

FROM python:$PYTHON_VERSION-slim

ENV PYTHON_LIB_PATH=/usr/local/lib/python${PYTHON_VERSION%.*}/site-packages
WORKDIR /app

COPY --from=builder $PYTHON_LIB_PATH $PYTHON_LIB_PATH
COPY --from=builder /app /app

# install ca-certificates to allow curl verify ssl certificates of a remote hosts
RUN apt update && apt install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

CMD ["python3", "/app/main.py"]