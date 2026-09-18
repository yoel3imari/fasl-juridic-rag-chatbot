# CrispEmbed server built from source (task 11).
# Builder clones with submodules and compiles via cmake; runtime ships only
# the server binary. The GGUF model auto-downloads by name (-m bge-m3) into
# the /models volume on first boot, so restarts are fast.
# Bases pinned to immutable debian digest; source pinned to v0.9.0
# (commit ad9a90cfa9615cd9709c554f907e8d9c0ceaa54f) for reproducibility.
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates cmake g++ make libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone https://github.com/CrispStrobe/CrispEmbed . && git checkout ad9a90cfa9615cd9709c554f907e8d9c0ceaa54f && git submodule update --init --recursive
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j"$(nproc)" --target crispembed-server

FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget ca-certificates libcurl4 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -m -s /usr/sbin/nologin crispembed \
    && mkdir -p /home/crispembed/.cache/crispembed /srv /models/cache /models/crispembed-cache \
    && chown -R crispembed:crispembed /home/crispembed /srv /models

COPY --chown=crispembed:crispembed --from=builder /src/build/crispembed-server /srv/crispembed-server
COPY --chown=crispembed:crispembed --from=builder /src/build/ggml/src/*.so* /usr/local/lib/
RUN ldconfig
# Cache lands under the writable /models volume via XDG_CACHE_HOME
# (/models/cache) because /root/.cache is not writable as non-root.
# Compose task: mount the models volume at /models so the GGUF persists;
# /models is the request sandbox root (see --image-root/--model-root).
# /models/crispembed-cache is pre-created for the alternate cache path.
ENV XDG_CACHE_HOME=/models/cache
USER crispembed
WORKDIR /srv
VOLUME /models
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
    CMD curl -sf http://localhost:8080/health || exit 1
# Server defaults to 127.0.0.1 (healthy yet unreachable); bind all interfaces.
CMD ["./crispembed-server", "-m", "bge-m3", "--port", "8080", \
     "--host", "0.0.0.0", "--image-root", "/models", "--model-root", "/models"]
