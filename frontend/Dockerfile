# Next.js 16 frontend, development image (bun). Source arrives via compose
# bind mounts; `bun run dev` serves with hot reload.
# Production lives in Dockerfile.prod (standalone build, non-root runner).
FROM oven/bun:1@sha256:9114c058aeae42162ee16dd5084b95fe9473970bb6bcb5b232ab1630f0546895

WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY . .
EXPOSE 3000
CMD ["bun", "run", "dev"]
