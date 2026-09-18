# Qdrant with a real HTTP healthcheck (task 11).
# Upstream qdrant/qdrant ships no wget/curl/nc/python, so service_healthy
# was impossible to evaluate honestly — add curl (cached layer, ~5MB).
# Pinned to immutable digest so rebuilds are reproducible (task 12).
FROM qdrant/qdrant:latest@sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -sf http://localhost:6333/ || exit 1
