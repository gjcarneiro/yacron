FROM ubuntu:xenial

ENV PATH=$PATH:/root/.cargo/bin
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libc-dev \
        curl \
        ca-certificates && \
    curl https://sh.rustup.rs -sSf | \
        sh -s -- -y && \
    apt-get remove -y curl ca-certificates && \
    apt-get autoremove -y && \
    apt-get clean -y && \
    rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y software-properties-common
RUN add-apt-repository ppa:deadsnakes/ppa && \
 	apt-get update && \
 	DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
 	python3.7 virtualenv \
 	libssl-dev pkg-config


RUN cargo install pyoxidizer

RUN mkdir /code
COPY build/yacron*.whl /code/
RUN virtualenv -p /usr/bin/python3.7 /yacron && \
	/yacron/bin/pip install /code/yacron*.whl

COPY yacron /code/yacron
RUN cd /code/yacron && pyoxidizer build --release
