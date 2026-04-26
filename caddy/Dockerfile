FROM busybox:musl AS busybox
FROM lucaslorentz/caddy-docker-proxy:latest
COPY --from=busybox /bin/wget /bin/wget
COPY caddy /bin/caddy
