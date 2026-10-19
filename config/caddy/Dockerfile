FROM caddy:2.11.7-builder AS builder
RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare@v0.2.4 \
    && setcap -r /usr/bin/caddy

FROM busybox:musl AS busybox

FROM caddy:2.11.7
COPY --from=busybox /bin/wget /bin/wget
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
