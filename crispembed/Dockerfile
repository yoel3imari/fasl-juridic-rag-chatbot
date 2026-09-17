# CrispEmbed server built from source (task 11).
# Builder clones with submodules and compiles via cmake; runtime ships only
# the server binary. The GGUF model auto-downloads by name (-m bge-m3) into
# the /models volume on first boot, so restarts are fast.
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates cmake g++ make libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --recursive --depth 1 https://github.com/CrispStrobe/CrispEmbed .
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j"$(nproc)" --target crispembed-server

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libcurl4 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY --from=builder /src/build/crispembed-server ./crispembed-server
COPY --from=builder /src/build/ggml/src/*.so* /usr/local/lib/
RUN ldconfig
# Model auto-downloads land in the XDG cache (/root/.cache/crispembed);
# /models is the request sandbox root (see --image-root/--model-root).
VOLUME /models
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
    CMD curl -sf http://localhost:8080/health || exit 1
# Server defaults to 127.0.0.1 (healthy yet unreachable); bind all interfaces.
CMD ["./crispembed-server", "-m", "bge-m3", "--port", "8080", \
     "--host", "0.0.0.0", "--image-root", "/models", "--model-root", "/models"]
