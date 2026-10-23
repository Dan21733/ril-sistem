# Halaman kasir (hasil build Vue) + reverse proxy, menggantikan `vite preview`
# di Start POS.bat. Dibangun dari folder POS (lihat compose.yaml).
#
# Nama/alamat toko tercetak di nota dan dibaca Vite saat build, jadi diubah lewat
# .env server lalu `docker compose build web`.

FROM node:22-bookworm-slim AS bangun
WORKDIR /app
COPY pos-frontend/package.json pos-frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY pos-frontend/ ./
ARG VITE_STORE_NAME=""
ARG VITE_STORE_ADDRESS=""
ARG VITE_STORE_PHONE=""
ARG VITE_STORE_CODE=""
ENV VITE_STORE_NAME=$VITE_STORE_NAME \
    VITE_STORE_ADDRESS=$VITE_STORE_ADDRESS \
    VITE_STORE_PHONE=$VITE_STORE_PHONE \
    VITE_STORE_CODE=$VITE_STORE_CODE
RUN npm run build

FROM caddy:2-alpine
COPY pos-backend/master/server/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=bangun /app/dist /srv
