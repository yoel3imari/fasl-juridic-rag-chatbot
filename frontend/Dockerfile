# Next.js 16 frontend (App Router, npm): multi-stage dev/prod targets.
#   docker build --target dev  -t fasl-frontend:dev  ./frontend
#   docker build --target prod -t fasl-frontend:prod ./frontend   (default)
# Standalone output is enabled via next.config.standalone.mjs, which wraps
# next.config.js — local `npm run build` / `npm run start` keep using
# next.config.js untouched (the wrapper is only swapped in inside `builder`).
# NOTE: no `public/` dir exists yet; the runner omits the public COPY until one
# is added (standalone server serves ./.next/static only).
ARG NODE_BASE=node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5

# ---- dev: bind-mount-friendly local development (root is fine, no build) ----
FROM ${NODE_BASE} AS dev
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "-p", "3000"]

# ---- prod deps ----
FROM ${NODE_BASE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- prod builder ----
FROM ${NODE_BASE} AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* vars are baked at build time; prod compose passes this as a build arg.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
# Swap in the standalone wrapper (Docker-only; next.config.js stays the local config).
RUN mv next.config.js next.config.base.js \
    && cp next.config.standalone.mjs next.config.mjs \
    && npm run build

# ---- prod runner: minimal non-root standalone server ----
FROM ${NODE_BASE} AS runner
WORKDIR /app
RUN groupadd -r nextjs && useradd -r -g nextjs nextjs
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD node -e "fetch('http://localhost:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "server.js"]

FROM runner AS prod
