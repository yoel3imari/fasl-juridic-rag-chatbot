# Next.js 16 frontend (task 11). Standalone build would need next.config
# changes; plain build + start keeps the scaffold config untouched.
FROM node:22-slim

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . ./
RUN npm run build

EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD node -e "fetch('http://localhost:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["npm", "run", "start", "--", "-p", "3000"]
