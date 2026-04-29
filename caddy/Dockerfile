FROM busybox:musl AS busybox
FROM caddy:latest
COPY --from=busybox /bin/wget /bin/wget
COPY caddy /usr/bin/caddy
