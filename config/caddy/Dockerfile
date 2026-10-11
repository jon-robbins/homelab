FROM caddy:2.11.6-builder AS builder
RUN xcaddy build v2.11.2 \
    --with github.com/caddy-dns/cloudflare@v0.2.4 \
    --with github.com/lucaslorentz/caddy-docker-proxy/v2@v2.12.0

FROM busybox:musl AS busybox

FROM caddy:2.11.7
COPY --from=busybox /bin/wget /bin/wget
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
