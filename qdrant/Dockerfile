# Qdrant with a real HTTP healthcheck (task 11).
# Upstream qdrant/qdrant ships no wget/curl/nc/python, so service_healthy
# was impossible to evaluate honestly — add curl (cached layer, ~5MB).
FROM qdrant/qdrant:latest

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -sf http://localhost:6333/ || exit 1
