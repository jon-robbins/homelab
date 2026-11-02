#!/usr/bin/env bash
# Reproduces every claim in FINDINGS.md on the local Docker daemon. Project name `traefikspike`; nothing else is touched.
#   ./run.sh            run everything, print a verdict table, tear down
#   KEEP=1 ./run.sh     leave the stack running afterwards (tear down with ./run.sh clean)
set -uo pipefail
cd "$(dirname "$0")"
P=traefikspike
BASE=example.homelab.local
IMG='traefik:v3.7.17@sha256:30ff62491aacdb862ceb8c9edcf2627da638d7deafdd4b6dab32fe34f8aa3e90'
REPO_ROOT="$(git rev-parse --show-toplevel)"
export PUID="$(id -u)" PGID="$(id -g)"
dc() { docker compose -p "$P" "$@"; }
strip() { sed 's/\x1b\[[0-9;]*m//g'; }

clean() {
  docker rm -f traefikspike-single traefikspike-bad traefikspike-hr traefikspike-min >/dev/null 2>&1
  dc --profile '*' down -v --remove-orphans >/dev/null 2>&1
  docker volume rm traefikspike_hrvol >/dev/null 2>&1
  rm -rf .work /tmp/traefikspike-*
}
[ "${1:-}" = clean ] && { clean; echo cleaned; exit 0; }
[ -z "${KEEP:-}" ] && trap clean EXIT
clean

PASS=0; FAIL=0; declare -a VERDICTS
h()   { printf '\n\n############ %s\n' "$*"; }
say() { printf '\n--> %s\n' "$*"; }
show() { printf '$ %s\n' "$*"; }
ok()  { # ok "desc" "expected" "actual"
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); printf '  PASS  %s (= %s)\n' "$1" "$2"
  else FAIL=$((FAIL+1)); printf '  FAIL  %s (expected %s, got %s)\n' "$1" "$2" "$3"; fi; }
verdict() { VERDICTS+=("$1|$2|$3"); }

# ---------------------------------------------------------------- setup
h "SETUP"
mkdir -p .work/{dynamic,dynamic-acme,acme,acme-nl,acme-none,secrets}
cp traefik/dynamic/routes.yml .work/dynamic/
cat > .work/dynamic-acme/min.yml <<'Y'
http:
  routers:
    r: {rule: "Host(`spike.example.com`)", entryPoints: [websecure], service: noop@internal}
Y
TOKEN="$(LC_ALL=C tr -dc '[:alnum:]_-' </dev/urandom | head -c 40)"   # bogus: random, shaped like a Cloudflare token
printf '%s' "$TOKEN" > .work/secrets/CLOUDFLARE_TOKEN
printf '%s\n' "$TOKEN" > .work/secrets/CLOUDFLARE_TOKEN_NL
chmod 600 .work/secrets/*
PID="$(docker create ghcr.io/letsencrypt/pebble:latest)"; docker cp "$PID:/test/certs/pebble.minica.pem" .work/pebble.minica.pem >/dev/null; docker rm "$PID" >/dev/null   # public test CA, copied from the image
show "docker compose up (traefik + 4 whoami backends + auth stub)"
dc up -d --wait 2>&1 | tail -3
T=traefikspike-traefik-1
R=(); for hname in "" gate. explicit. ungated. regex. rule. guard.; do R+=(--resolve "$hname$BASE:18443:127.0.0.1"); done
c() { curl -sk "${R[@]}" "$@"; }
hdrs() { python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({k:v[0] for k,v in sorted(d['headers'].items()) if 'authentik' in k.lower() or 'remote' in k.lower()}))"; }
SPOOF=(-H 'X-Authentik-Username: akadmin' -H 'X-Authentik-Groups: admins' -H 'X-Authentik-Email: forged@example.invalid'
       -H 'Remote-User: akadmin' -H 'X-Authentik-Future-Thing: forged' -H 'X_Authentik_Username: alias-forged')

# ---------------------------------------------------------------- item 1
h "ITEM 1  hardening: digest pin, non-root, cap_drop ALL, no cap_add, read_only, no-new-privileges"
say "container security settings"
docker inspect "$T" --format 'image={{.Config.Image}}
user={{.Config.User}} readonlyRootfs={{.HostConfig.ReadonlyRootfs}} capDrop={{.HostConfig.CapDrop}} capAdd={{.HostConfig.CapAdd}} securityOpt={{.HostConfig.SecurityOpt}} privileged={{.HostConfig.Privileged}}'
ok "read-only rootfs" true "$(docker inspect "$T" --format '{{.HostConfig.ReadonlyRootfs}}')"
ok "cap_add empty" "[]" "$(docker inspect "$T" --format '{{.HostConfig.CapAdd}}')"
say "kernel view inside the container (CapEff=0 means no capability at all; uid is not root)"
show "docker exec traefik sh -c 'id; grep -E \"^(CapPrm|CapEff|CapBnd|NoNewPrivs)\" /proc/1/status'"
docker exec "$T" sh -c 'id; grep -E "^(CapPrm|CapEff|CapBnd|NoNewPrivs)" /proc/1/status'
ok "effective caps" "0000000000000000" "$(docker exec "$T" sh -c 'grep ^CapEff /proc/1/status' | awk '{print $2}')"
say "read-only rootfs: writes fail outside tmpfs/volumes"
docker exec "$T" sh -c 'touch /etc/x 2>&1; touch /tmp/x && echo "/tmp (tmpfs) writable"'
say "file capabilities on the binary (the Caddy trap): scan every layer's security.capability xattr"
show "docker save \$IMG | python3 -I check-filecaps.py        # positive control: official caddy image"
docker save "$IMG" | python3 -I check-filecaps.py; ok "traefik image has no file capability" 0 "${PIPESTATUS[1]}"
docker save caddy:2.11.7 2>/dev/null | python3 -I check-filecaps.py >/dev/null 2>&1; ok "control: official caddy image DOES carry one (checker works)" 1 "${PIPESTATUS[1]}"
say "binds its HTTPS entrypoint (non-root, no caps): listening sockets and a TLS handshake"
docker exec "$T" netstat -tln | grep -E "4443|8000|8082|8083"
c -o /dev/null -w 'https via published port: http=%{http_code} tls=%{ssl_verify_result}(self-signed default cert)\n' "https://$BASE:18443/nothing"
curl -skv --resolve "$BASE:18443:127.0.0.1" "https://$BASE:18443/" 2>&1 | grep -E "subject:|SSL connection|ALPN: server"
say "even port 443 inside the container needs no NET_BIND_SERVICE (Docker sets net.ipv4.ip_unprivileged_port_start=0)"
dc --profile p443 up -d --wait traefik-443 2>&1 | tail -1
docker exec traefikspike-traefik-443-1 sh -c 'echo "ip_unprivileged_port_start=$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start)"; grep ^CapEff /proc/1/status; netstat -tln | grep ":443 "'
ok "TLS on :443 as non-root, cap_drop ALL" 404 "$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:18444/)"
say "the repo's real compose policy (scripts/release/compose_policy.py) on the proposed module"
python3 policy-check.py "$REPO_ROOT"; echo "(exit=$?  -> only the image namespace is rejected)"
python3 policy-check.py "$REPO_ROOT" --allow-traefik-namespace; ok "policy passes once docker.io/library/traefik is allowlisted" 0 "$?"
verdict 1 PROVEN "digest-pinned v3.7.17, uid ${PUID}, CapEff=0, no cap_add, ro rootfs, no file caps, binds 4443 (and even 443); policy passes except image namespace needs an allowlist entry"

# ---------------------------------------------------------------- item 2
h "ITEM 2  no Docker socket; file provider only; hot reload"
say "no socket in the module, the container, or the static config"
ok "docker.sock mounts" 0 "$(docker inspect "$T" --format '{{json .Mounts}}' | grep -c docker.sock)"
ok "docker.sock in rendered compose" 0 "$(dc config | grep -c docker.sock)"
echo "providers started (log):"; docker logs "$T" 2>&1 | strip | grep "Starting provider" | grep -v aggregator | sed 's/^[^ ]* //'
echo "static providers block:"; sed -n '/^providers:/,$p' traefik/static.yml
B="$BASE"; code() { c -o /dev/null -w '%{http_code}' "https://$B:18443/hot"; }
HOT='.work/dynamic/hot.yml'
mkhot() { printf 'http:\n  routers:\n    hot: {rule: "Host(`%s`) && Path(`/hot`)", entryPoints: [websecure], service: s1}\n  services:\n    s1: {loadBalancer: {servers: [{url: "http://%s"}]}}\n' "$B" "$1" > "$HOT"; }
say "hot reload, directory provider on a bind mount (Docker Desktop virtiofs)"
ok "/hot before the file exists" 404 "$(code)"
mkhot sonarr:8989; sleep 4
ok "new file picked up" 200 "$(code)"; ok "  served by sonarr" "Name: sonarr" "$(c "https://$B:18443/hot" | grep ^Name)"
mkhot overseerr:5055; sleep 4
ok "in-place edit picked up" "Name: overseerr" "$(c "https://$B:18443/hot" | grep ^Name)"
printf 'http: [this is: not valid\n' > "$HOT"; sleep 4
ok "invalid edit: previous config keeps serving" 200 "$(code)"
ok "invalid edit: other routes unaffected" 200 "$(c -o /dev/null -w '%{http_code}' "https://$B:18443/sonarr/x")"
ok "invalid edit: container still healthy" healthy "$(docker inspect "$T" --format '{{.State.Health.Status}}')"
docker logs "$T" 2>&1 | strip | grep "ERR" | tail -1 | cut -c1-200
rm -f "$HOT"; sleep 4; touch .work/dynamic/routes.yml; sleep 4
ok "after delete (+touch: virtiofs bind mounts do not deliver unlink events; named volumes do)" 404 "$(code)"
say "single-file bind mount (what Compose 'configs:' produces)"
mkdir -p /tmp/traefikspike-single; printf 'http:\n  routers:\n    one: {rule: "Path(`/one`)", entryPoints: [web], service: s}\n  services:\n    s: {loadBalancer: {servers: [{url: "http://sonarr:8989"}]}}\n' > /tmp/traefikspike-single/dyn.yml
docker run -d --name traefikspike-single --network ${P}_default -v /tmp/traefikspike-single/dyn.yml:/etc/traefik/dyn.yml -p 127.0.0.1:18091:8000 "$IMG" \
  --entrypoints.web.address=:8000 --providers.file.filename=/etc/traefik/dyn.yml --providers.file.watch=true --global.sendanonymoususage=false >/dev/null; sleep 3
ok "single file: initial" Name:\ sonarr "$(curl -s localhost:18091/one | grep ^Name)"
sed 's/sonarr:8989/overseerr:5055/' /tmp/traefikspike-single/dyn.yml > /tmp/traefikspike-single/new && cat /tmp/traefikspike-single/new > /tmp/traefikspike-single/dyn.yml; sleep 4
ok "single file: in-place rewrite (cat >) reloads" Name:\ overseerr "$(curl -s localhost:18091/one | grep ^Name)"
sed -i.bak 's/overseerr:5055/bazarr:6767/' /tmp/traefikspike-single/dyn.yml; sleep 4
echo "single file: replace-by-rename (sed -i, new inode) -> $(curl -s localhost:18091/one | grep ^Name)   (stale: the mount pins the old inode; with 'configs:' the edge updater recreates the container instead)"
docker rm -f traefikspike-single >/dev/null
verdict 2 PROVEN "no socket anywhere, providers=file only; watch reloads create/edit in 1-3s, invalid edit keeps old config; delete needs a named volume on Docker Desktop; single-file mounts reload only on in-place writes"

# ---------------------------------------------------------------- item 3
h "ITEM 3  Cloudflare DNS-01 token from a file (lego via CF_DNS_API_TOKEN_FILE), acme.json under read-only rootfs"
say "Pebble (local ACME test CA) + three Traefik instances: token file, token file ending in newline, no credential"
dc --profile acme up -d pebble 2>&1 | tail -1; sleep 2
dc --profile acme up -d acme-file acme-newline acme-nofile 2>&1 | tail -1
for _ in $(seq 1 40); do
  n=0; for s in acme-file acme-newline acme-nofile; do [ "$(docker logs traefikspike-$s-1 2>&1 | grep -c "Unable to obtain ACME certificate")" -gt 0 ] && n=$((n+1)); done
  [ $n -eq 3 ] && break; sleep 3
done
for s in acme-file acme-newline acme-nofile; do
  printf '\n[%s] ' "$s"; docker logs traefikspike-$s-1 2>&1 | strip | grep "Unable to obtain ACME certificate" | head -1 | sed -E 's/.*error="([^"]*)".*/\1/' | cut -c1-420
done
f="$(docker logs traefikspike-acme-file-1 2>&1 | strip | grep -c 'cloudflare: failed to find zone')"
nl="$(docker logs traefikspike-acme-newline-1 2>&1 | strip | grep -c 'cloudflare: failed to find zone')"
nf="$(docker logs traefikspike-acme-nofile-1 2>&1 | strip | grep -c 'some credentials information are missing')"
ok "token file: lego reached Cloudflare and was rejected there (file was read)" yes "$([ "$f" -gt 0 ] && echo yes || echo no)"
ok "token file with trailing newline: same Cloudflare rejection (newline trimmed, no local 'invalid header' error)" yes "$([ "$nl" -gt 0 ] && echo yes || echo no)"
ok "no credential: 'credentials information are missing' (so the file is what made the difference)" yes "$([ "$nf" -gt 0 ] && echo yes || echo no)"
say "acme.json: created 0600 by the non-root user on the bind-mounted volume while the rootfs is read-only"
ls -l .work/acme/acme.json
ok "acme.json mode" 600 "$(python3 -c "import os;print(oct(os.stat('.work/acme/acme.json').st_mode & 0o777)[2:])")"
ok "rootfs read-only" true "$(docker inspect traefikspike-acme-file-1 --format '{{.HostConfig.ReadonlyRootfs}}')"
say "too-open acme.json is refused"
chmod 644 .work/acme/acme.json; dc --profile acme up -d --force-recreate acme-file 2>&1 | tail -1; sleep 6
docker logs traefikspike-acme-file-1 2>&1 | strip | grep -i "too open" | head -1 | cut -c1-260
ok "644 acme.json rejected" 1 "$(docker logs traefikspike-acme-file-1 2>&1 | grep -ci 'too open' | sed 's/^[1-9][0-9]*$/1/')"
dc --profile acme rm -sf acme-file acme-newline acme-nofile pebble >/dev/null 2>&1
verdict 3 PROVEN "lego v5.6.0 in Traefik 3.7.17 reads CF_DNS_API_TOKEN_FILE (Cloudflare 6111/auth rejection with file, 'credentials missing' without); newline trimmed; acme.json 0600 works on ro rootfs. Cannot prove a real cert issues (no real token)"

# ---------------------------------------------------------------- item 4
h "ITEM 4  forward auth equal to gate.caddy; spoofed X-Authentik-* never reaches the backend"
say "anonymous (with forged identity headers): sent to login, Set-Cookie preserved"
c -o /dev/null -D - "${SPOOF[@]}" "https://gate.$BASE:18443/.homelab/whoami" | grep -iE "^HTTP|^location|^set-cookie"
ok "anonymous gated path" 302 "$(c -o /dev/null -w '%{http_code}' "${SPOOF[@]}" "https://gate.$BASE:18443/.homelab/whoami")"
ok "anonymous random path (catch-all is gated too)" 302 "$(c -o /dev/null -w '%{http_code}' "https://gate.$BASE:18443/$(openssl rand -hex 6)")"
say "auth succeeds (alice, family), client sends forged identity + Remote-User + an unknown X-Authentik-* + an X_Authentik_ alias"
c "${SPOOF[@]}" -H 'Cookie: stub_session=alice' "https://gate.$BASE:18443/.homelab/whoami" | hdrs
ok "backend sees only the auth server's identity" '{"X-Authentik-Email": "alice@example.invalid", "X-Authentik-Groups": "family", "X-Authentik-Name": "Alice", "X-Authentik-Uid": "u-alice", "X-Authentik-Username": "alice"}' "$(c "${SPOOF[@]}" -H 'Cookie: stub_session=alice' "https://gate.$BASE:18443/.homelab/whoami" | hdrs)"
say "auth succeeds but the auth response has NO Email header (carol): the forged Email must not survive"
c "${SPOOF[@]}" -H 'Cookie: stub_session=carol' "https://gate.$BASE:18443/.homelab/whoami" | hdrs
ok "forged Email absent" 0 "$(c "${SPOOF[@]}" -H 'Cookie: stub_session=carol' "https://gate.$BASE:18443/.homelab/whoami" | grep -c 'X-Authentik-Email')"
say "auth skipped: route with only the strip middleware (and the outpost route)"
c "${SPOOF[@]}" "https://ungated.$BASE:18443/api" | hdrs
ok "ungated: Remote-User/known X-Authentik-* stripped" '{"X-Authentik-Future-Thing": "forged"}' "$(c "${SPOOF[@]}" "https://ungated.$BASE:18443/api" | hdrs)"
c "${SPOOF[@]}" "https://gate.$BASE:18443/outpost.goauthentik.io/echo" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({k:v for k,v in sorted(d.items()) if 'authentik' in k.lower() or 'remote' in k.lower()}))"
say "variants: explicit authResponseHeaders only (A) vs regex only (B) vs recommended = strip-identity + regex"
echo "A explicit : $(c "${SPOOF[@]}" -H 'Cookie: stub_session=carol' "https://explicit.$BASE:18443/api" | hdrs)"
echo "B regex    : $(c "${SPOOF[@]}" -H 'Cookie: stub_session=carol' "https://regex.$BASE:18443/api" | hdrs)"
ok "A leaves unknown X-Authentik-Future-Thing" 1 "$(c "${SPOOF[@]}" -H 'Cookie: stub_session=carol' "https://explicit.$BASE:18443/api" | grep -c Future-Thing)"
ok "B leaves Remote-User" 1 "$(c "${SPOOF[@]}" -H 'Cookie: stub_session=carol' "https://regex.$BASE:18443/api" | grep -c Remote-User)"
ok "recommended chain leaves neither" 0 "$(c "${SPOOF[@]}" -H 'Cookie: stub_session=carol' "https://gate.$BASE:18443/.homelab/whoami" | grep -ciE 'Future-Thing|Remote-User|alias-forged')"
say "what the auth server itself received (forwarded request headers, forged identity already stripped)"
docker exec traefikspike-authstub-1 python -I -c "import urllib.request,json;d=json.load(urllib.request.urlopen('http://127.0.0.1:9000/_last'));print(json.dumps({k:v for k,v in d.items() if k.startswith('X-')},indent=1))"
say "admins_only: second forwardAuth into a loopback-only entrypoint whose routers match HeaderRegexp(X-Authentik-Groups)"
ok "alice (family) + forged Groups: admins -> /.homelab/admin" 403 "$(c -o /dev/null -w '%{http_code}' "${SPOOF[@]}" -H 'Cookie: stub_session=alice' "https://gate.$BASE:18443/.homelab/admin")"
ok "bob (admins) -> /.homelab/admin" 200 "$(c -o /dev/null -w '%{http_code}' -H 'Cookie: stub_session=bob' "https://gate.$BASE:18443/.homelab/admin")"
ok "alice -> /sonarr (admins_only route)" 403 "$(c -o /dev/null -w '%{http_code}' -H 'Cookie: stub_session=alice' "https://gate.$BASE:18443/sonarr/x")"
ok "bob -> /sonarr" 200 "$(c -o /dev/null -w '%{http_code}' -H 'Cookie: stub_session=bob' "https://gate.$BASE:18443/sonarr/x")"
ok "bob -> unknown path (handle { respond 404 })" 404 "$(c -o /dev/null -w '%{http_code}' -H 'Cookie: stub_session=bob' "https://gate.$BASE:18443/nope")"
ok "helper entrypoint not reachable from outside the container" 000 "$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8083/ || true)"
say "anti-pattern: a router rule on a header that forwardAuth sets later cannot work (routing happens first)"
ok "real admin (bob) does NOT match a HeaderRegexp router rule" 404 "$(c -o /dev/null -w '%{http_code}' -H 'Cookie: stub_session=bob' "https://rule.$BASE:18443/api")"
ok "forged header DOES match the rule (client-controlled at routing time)" 302 "$(c -o /dev/null -w '%{http_code}' -H 'X-Authentik-Groups: admins' "https://rule.$BASE:18443/api")"
say "fail closed: auth server down"
docker stop traefikspike-authstub-1 >/dev/null
for p in /.homelab/whoami /.homelab/admin /sonarr/x /zzz; do printf '  %-20s -> %s\n' "$p" "$(c -o /dev/null -w '%{http_code}' "${SPOOF[@]}" "https://gate.$BASE:18443$p")"; done
ok "gate.py FAIL_CLOSED=(502,503,504) would NOT accept this status" 500 "$(c -o /dev/null -w '%{http_code}' "https://gate.$BASE:18443/sonarr/x")"
docker start traefikspike-authstub-1 >/dev/null; sleep 3
verdict 4 PROVEN "headers middleware (empty value = delete) before forwardAuth(authResponseHeadersRegex) + aliasHeadersStrategy=delete; gap: unknown X-Authentik-* names on routes with no forwardAuth; fail-closed status is 500 not 50[234]; admins_only needs a loopback helper entrypoint"

# ---------------------------------------------------------------- item 5
h "ITEM 5  GATE-1 /.homelab/whoami probe without a static-response handler"
say "traefik/whoami (allowlisted namespace 'traefik/', scratch image, uid ${PUID}, cap_drop ALL, read_only) behind replacePath /api"
docker inspect traefikspike-probe-1 --format 'user={{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} capDrop={{.HostConfig.CapDrop}} image={{.Config.Image}}'
for u in alice bob; do
  c "${SPOOF[@]}" -H "Cookie: stub_session=$u" "https://gate.$BASE:18443/.homelab/whoami" | python3 -c "
import json,sys
h=json.load(sys.stdin)['headers']; g=lambda k: h.get(k,[''])[0]
print('$u ->', '|'.join([g('X-Authentik-Username'), g('X-Authentik-Groups'), g('Remote-User')]))"
done
ok "pipe-joined body gate.py expects, family member" "alice|family|" "$(c "${SPOOF[@]}" -H 'Cookie: stub_session=alice' "https://gate.$BASE:18443/.homelab/whoami" | python3 -c "
import json,sys
h=json.load(sys.stdin)['headers']; g=lambda k: h.get(k,[''])[0]
print('|'.join([g('X-Authentik-Username'), g('X-Authentik-Groups'), g('Remote-User')]))")"
echo "gate.py today: user, groups, remote = reply.body.decode().split('|')   -> needs 3 lines to read whoami JSON instead"
echo "no CMD healthcheck possible in a scratch image -> use label homelab.probe=http://:8080/health (accepted by compose_policy._health; run policy-check above)"
verdict 5 PROVEN "whoami + replacePath=/api returns JSON of request headers; gate.py must parse the JSON (3 lines) instead of splitting on a pipe; behaviour (identity, forged headers dropped, Remote-User empty, 403 admin) matches"

# ---------------------------------------------------------------- item 6
h "ITEM 6  ipAllowList = Caddy remote_ip, and what source IP Traefik sees behind Docker NAT"
echo "lan-only middleware: $(grep -A2 'lan-only:' traefik/dynamic/routes.yml | tr -d '\n')"
cl() { local ip="$1"; shift; docker run --rm --network ${P}_default --ip "$ip" curlimages/curl:latest -sk --connect-to "guard.$BASE:4443:traefik:4443" "$@"; }
say "client container INSIDE the allowed range (no NAT, direct bridge): Traefik sees its real address"
ok "allowed source 198.51.100.50" 200 "$(cl 198.51.100.50 -o /dev/null -w '%{http_code}' "https://guard.$BASE:4443/")"
cl 198.51.100.50 "https://guard.$BASE:4443/api" | python3 -c "import json,sys; h=json.load(sys.stdin)['headers']; print('  backend saw X-Forwarded-For =', h['X-Forwarded-For'], ' X-Real-Ip =', h['X-Real-Ip'])"
say "client outside the range, with forged X-Forwarded-For/X-Real-Ip claiming an allowed address or loopback"
ok "denied source 198.51.100.100" 403 "$(cl 198.51.100.100 -o /dev/null -w '%{http_code}' "https://guard.$BASE:4443/")"
ok "  + X-Forwarded-For: allowed address" 403 "$(cl 198.51.100.100 -H 'X-Forwarded-For: 198.51.100.50' -o /dev/null -w '%{http_code}' "https://guard.$BASE:4443/")"
ok "  + X-Forwarded-For: 127.0.0.1" 403 "$(cl 198.51.100.100 -H 'X-Forwarded-For: 127.0.0.1' -o /dev/null -w '%{http_code}' "https://guard.$BASE:4443/")"
ok "  + X-Real-Ip: allowed address" 403 "$(cl 198.51.100.100 -H 'X-Real-Ip: 198.51.100.50' -o /dev/null -w '%{http_code}' "https://guard.$BASE:4443/")"
say "from the Mac through the published port (Docker Desktop NAT): the client appears as the bridge gateway"
c "${SPOOF[@]}" -H 'X-Forwarded-For: 203.0.113.9' "https://ungated.$BASE:18443/api" | python3 -c "import json,sys; h=json.load(sys.stdin)['headers']; print('  forged XFF 203.0.113.9 sent; backend saw X-Forwarded-For =', h['X-Forwarded-For'])"
ok "host via published port is NOT in the allow list" 403 "$(c -o /dev/null -w '%{http_code}' "https://guard.$BASE:18443/")"
ok "autobrr route (lan-only) from NAT'd host" 403 "$(c -o /dev/null -w '%{http_code}' "https://$BASE:18443/autobrr/x")"
ok "autobrr route from allowed container, prefix stripped" "GET /x HTTP/1.1" "$(docker run --rm --network ${P}_default --ip 198.51.100.51 curlimages/curl:latest -sk --connect-to "$BASE:4443:traefik:4443" "https://$BASE:4443/autobrr/x" | tr -d '\r' | grep '^GET')"
verdict 6 PARTIAL "ipAllowList works and ignores forged X-Forwarded-For/X-Real-Ip (entrypoint trustedIPs=[]); behind Docker Desktop NAT the client is the bridge gateway. Native-Linux external-client (Tailscale) source preservation cannot be proven on this Mac; Caddy remote_ip has the identical constraint"

# ---------------------------------------------------------------- item 7
h "ITEM 7  healthcheck: traefik healthcheck --ping (no extra binaries)"
docker inspect "$T" --format 'healthcheck={{json .Config.Healthcheck.Test}} status={{.State.Health.Status}}'
show "docker exec traefik traefik healthcheck --ping"; docker exec "$T" traefik healthcheck --ping; ok "healthcheck exit" 0 "$?"
echo "ping answers on the loopback-only entrypoint, not on the published ports:"
ok "published port has no /ping" 404 "$(c -o /dev/null -w '%{http_code}' "https://$BASE:18443/ping")"
say "caveat: ping stays green when the dynamic config is INVALID at startup (Caddy would fail to start)"
mkdir -p /tmp/traefikspike-bad; printf 'http: [this is: not valid\n' > /tmp/traefikspike-bad/bad.yml
docker run -d --name traefikspike-bad --user "${PUID}:${PGID}" --read-only --cap-drop ALL -v "$PWD/traefik/static.yml:/etc/traefik/traefik.yml:ro" -v /tmp/traefikspike-bad:/etc/traefik/dynamic:ro "$IMG" >/dev/null; sleep 4
docker logs traefikspike-bad 2>&1 | strip | grep "Error while building configuration" | head -1 | cut -c1-200
docker exec traefikspike-bad traefik healthcheck --ping; ok "ping with broken dynamic config" 0 "$?"
docker exec traefikspike-bad wget -q -O /dev/null http://127.0.0.1:8082/healthz 2>&1; ok "route-based /healthz probe (config-aware, busybox wget already in the image)" 1 "$?"
ok "same /healthz probe on the good config" 0 "$(docker exec "$T" wget -q -O /dev/null http://127.0.0.1:8082/healthz; echo $?)"
docker rm -f traefikspike-bad >/dev/null
verdict 7 PROVEN "compose healthcheck works with CMD traefik healthcheck --ping; BUT it cannot detect an invalid dynamic file; /healthz router (ping@internal) + busybox wget (already in image) is config-aware"

# ---------------------------------------------------------------- item 8
h "ITEM 8  routing: prefix kept vs stripped, host routing, redirect, compress, 404, line counts"
req() { c "$@" ; }
echo "$(req "https://$BASE:18443/sonarr/api/v3?x=1" | grep -E '^(Name|GET)' | tr '\n' ' ')   <- /sonarr keeps prefix (Caddy: handle)"
echo "$(req "https://$BASE:18443/overseerr/foo" | grep -E '^(Name|GET)' | tr '\n' ' ')   <- /overseerr stripped (Caddy: handle_path)"
ok "prefix kept" "GET /sonarr/api/v3?x=1 HTTP/1.1" "$(req "https://$BASE:18443/sonarr/api/v3?x=1" | tr -d '\r' | grep '^GET')"
ok "prefix stripped" "GET /foo HTTP/1.1" "$(req "https://$BASE:18443/overseerr/foo" | tr -d '\r' | tr -d '\r' | grep '^GET')"
ok "host routing: wrong Host" 404 "$(c --resolve "other.$BASE:18443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://other.$BASE:18443/sonarr/x")"
ok "redir /bazarr -> /bazarr/" "301 https://$BASE:18443/bazarr/" "$(req -o /dev/null -w '%{http_code} %{redirect_url}' "https://$BASE:18443/bazarr")"
ok "unmatched path: built-in 404 (no catch-all needed)" 404 "$(req -o /dev/null -w '%{http_code}' "https://$BASE:18443/zzz")"
ok "compress zstd" zstd "$(req -H 'Accept-Encoding: zstd' -D - -o /dev/null "https://$BASE:18443/sonarr/$(head -c 2000 /dev/zero | tr '\0' a)" | tr -d '\r' | awk -F': ' 'tolower($1)=="content-encoding"{print $2}')"
ok "plain HTTP listener (Caddy ':80' block)" "GET /sonarr/x HTTP/1.1" "$(curl -s localhost:18000/sonarr/x | tr -d '\r' | grep '^GET')"
echo; echo "priority gotcha: Traefik default priority = rule length, so PathPrefix(/bazarr) beat Path(/bazarr) until priority: 100 was set on the redirect router"
say "line counts for the two representative routes (/sonarr kept, /overseerr stripped); full YAML is two-routes.example.yml"
CAD="$(awk '/handle \/sonarr\*/ && !a {a=1;f=3} /handle_path \/overseerr\*/ && !b {b=1;f=3} f>0{n++;f--} END{print n}' "$REPO_ROOT/config/caddy/Caddyfile")"
echo "Caddy  : $CAD lines (3 per route, no skeleton)"
echo "Traefik: $(wc -l < two-routes.example.yml | tr -d ' ') lines in block style incl. skeleton (routers/middlewares/services)"
cat two-routes.example.yml
docker run -d --name traefikspike-min --network ${P}_default --user "${PUID}:${PGID}" --read-only --cap-drop ALL -e BASE_DOMAIN="$BASE" -v "$PWD/two-routes.example.yml:/etc/traefik/dynamic/r.yml:ro" "$IMG" \
  --entrypoints.web.address=:8000 --providers.file.directory=/etc/traefik/dynamic --global.sendanonymoususage=false >/dev/null; sleep 3
ok "two-routes.example.yml works as-is (keep)" "GET /sonarr/q HTTP/1.1" "$(docker exec traefikspike-min wget -q -O- --header "Host: $BASE" http://127.0.0.1:8000/sonarr/q | tr -d '\r' | grep '^GET')"
ok "two-routes.example.yml works as-is (strip)" "GET /q HTTP/1.1" "$(docker exec traefikspike-min wget -q -O- --header "Host: $BASE" http://127.0.0.1:8000/overseerr/q | tr -d '\r' | grep '^GET')"
docker rm -f traefikspike-min >/dev/null
echo "Whole files: Caddyfile $(wc -l < "$REPO_ROOT/config/caddy/Caddyfile") lines + gate.caddy $(git -C "$REPO_ROOT" show origin/feat/ws-01-authentik:setup/config/authentik/gate.caddy 2>/dev/null | wc -l | tr -d " ") lines | routes.yml $(wc -l < traefik/dynamic/routes.yml) lines ($(grep -vcE '^\s*(#|$)' traefik/dynamic/routes.yml) without comments/blank) covering the same routes plus the gate"
verdict 8 PROVEN "keep/strip/host/redirect/compress/404 all behave like Caddy; ~3x more lines (2 routes: 6 Caddy vs 17 YAML in block style); priority must be explicit where rules overlap"

# ---------------------------------------------------------------- item 9
h "ITEM 9  Dependabot docker-compose + multi-arch"
show "docker buildx imagetools inspect traefik:v3.7.17 (index, platforms)"
docker buildx imagetools inspect traefik:v3.7.17 2>&1 | grep -E "^Digest|Platform:" | grep -v "unknown/unknown"
ok "pinned digest is the multi-arch index" "application/vnd.oci.image.index.v1+json" "$(docker buildx imagetools inspect "$IMG" 2>&1 | awk '/^MediaType:/{print $2; exit}')"
show "Docker Hub tags (what Dependabot sees)"
curl -s 'https://hub.docker.com/v2/repositories/library/traefik/tags?page_size=100&name=v3.7' | python3 -c "
import json,sys
t=[x['name'] for x in json.load(sys.stdin)['results']]
print('plain vX.Y.Z tags:', [n for n in t if n.count('.')==2 and '-' not in n][:6]); print('suffixed variants (windows) are filtered by Dependabot because the current tag has no suffix:', [n for n in t if '-' in n][:2])"
echo "repo precedent (git log): docker-compose ecosystem already bumps docker.io official + v-prefixed tags:"
git -C "$REPO_ROOT" log --oneline --author=dependabot -n 12 | grep -E "postgres|autobrr|cloudflared"
echo "dependabot.yml covers: $(grep -A1 'package-ecosystem: docker-compose' "$REPO_ROOT/.github/dependabot.yml" | grep directories)   (traefik module would live in /modules/edge)"
verdict 9 PARTIAL "tag/digest/multi-arch facts proven; Dependabot itself cannot run locally. Strong precedent: cloudflared, library/postgres, v-prefixed autobrr bumps already merged. Automerge covers /modules/edge patch+minor"

# ---------------------------------------------------------------- item 10
h "ITEM 10  what Caddy does that Traefik cannot (see FINDINGS.md section 10)"
echo "static responses: no 'respond'; closest = noop@internal (418) + errors{statusRewrites}, body is whatever the error service returns:"
c -i "https://gate.$BASE:18443/nope" -H 'Cookie: stub_session=bob' | tr -d '\r' | sed -n '1p;/^content-type/Ip;/^$/,$p'
echo "env in static config: Go templates work only in the dynamic file (used above); static config needs flags/TRAEFIK_* env"
docker exec "$T" env | grep -c BASE_DOMAIN | sed 's/^/BASE_DOMAIN visible to dynamic template (count): /'
verdict 10 PROVEN "list in FINDINGS.md"

# ---------------------------------------------------------------- summary
h "SUMMARY"
printf '%s\n' "${VERDICTS[@]}" | awk -F'|' '{printf "item %-2s %-8s %s\n",$1,$2,$3}'
printf '\nassertions: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
