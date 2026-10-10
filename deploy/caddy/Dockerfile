# Caddy with the rate-limit plugin (core Caddy has none). Versions checked 2026-10-10.
FROM caddy:2.11.7-builder AS builder
RUN xcaddy build --with github.com/mholt/caddy-ratelimit

FROM caddy:2.11.7
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
