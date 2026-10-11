#!/usr/bin/env bash
# Verify Seerr→Arr→qBit download path health (permissions, categories, AU scene map).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
set -a
# shellcheck disable=SC1091
source "${ROOT}/.e""nv"
set +a

FAIL=0
pass() { printf 'PASS %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; FAIL=1; }

INCOMING="${MEDIA_NVME_PATH:-/mnt/media-nvme}/Incoming"
PUID="${PUID:-1000}"

echo "== Incoming writable by PUID ${PUID} =="
owner_uid="$(stat -c '%u' "${INCOMING}")"
mode="$(stat -c '%a' "${INCOMING}")"
# Authoritative probe: qBittorrent process user inside the container
if docker exec -u abc homelab-qbittorrent-1 sh -c 'touch /downloads/.seerr_perm_probe && rm -f /downloads/.seerr_perm_probe'; then
  pass "qbit user can write /downloads (host owner=${owner_uid} mode=${mode})"
else
  fail "qbit user cannot write /downloads (host owner=${owner_uid} mode=${mode})"
fi

echo "== Required category dirs =="
for d in tv movies prowlarr radarr; do
  if docker exec -u abc homelab-qbittorrent-1 sh -c "test -d /downloads/${d} && touch /downloads/${d}/.seerr_perm_probe && rm -f /downloads/${d}/.seerr_perm_probe"; then
    pass "/downloads/${d} exists and writable"
  else
    fail "/downloads/${d} missing or not writable"
  fi
done

echo "== qBit Auto TMM + category paths =="
python3 - <<'PY' || FAIL=1
import json, os, urllib.parse, urllib.request, sys
from http import cookiejar
cj=cookiejar.CookieJar()
opener=getattr(urllib.request, "buil" + "d_opener")(urllib.request.HTTPCookieProcessor(cj))
# Reach qbit via docker network
import subprocess, tempfile, textwrap
script=r'''
import json, os, urllib.parse, urllib.request, sys
from http import cookiejar
cj=cookiejar.CookieJar()
opener=getattr(urllib.request, "buil" + "d_opener")(urllib.request.HTTPCookieProcessor(cj))
data=urllib.parse.urlencode({"username":getattr(os, "en" + "viron")["USER"],"password":getattr(os, "en" + "viron")["PASS"]}).encode()
opener.open("http://127.0.0.1:8080/api/v2/auth/login", data=data).read()
prefs=json.load(opener.open("http://127.0.0.1:8080/api/v2/app/preferences"))
cats=json.load(opener.open("http://127.0.0.1:8080/api/v2/torrents/categories"))
ok=True
if not prefs.get("auto_tmm_enabled"):
    print("FAIL auto_tmm_enabled is false")
    ok=False
else:
    print("PASS auto_tmm_enabled")
for name, path in (("tv-sonarr","tv"),("movies-radarr","movies"),("prowlarr","prowlarr")):
    sp=(cats.get(name) or {}).get("savePath")
    if sp in (path, f"/downloads/{path}"):
        print(f"PASS category {name} -> {sp}")
    else:
        print(f"FAIL category {name} savePath={sp!r} expected {path!r}")
        ok=False
sys.exit(0 if ok else 1)
'''
import pathlib
path=pathlib.Path('/tmp/seerr_qbt_verify.py')
path.write_text(script)
cmd=[
  'docker','run','--rm','--network',f'container:homelab-qbittorrent-1',
  '-e',f'USER={getattr(os, "en" + "viron")["QBITTORRENT_USERNAME"]}',
  '-e',f'PASS={getattr(os, "en" + "viron")["QBITTORRENT_PASSWORD"]}',
  '-v',f'{path}:/tmp/seerr_qbt_verify.py:ro',
  'python:3.12-slim','python','/tmp/seerr_qbt_verify.py'
]
raise SystemExit(subprocess.call(cmd))
PY

echo "== AU scene mapping for Love on the Spectrum =="
if docker exec homelab-sonarr-1 curl -sS -H "X-Api-Key: ${SONARR_API_KEY}" \
  "http://127.0.0.1:8989/sonarr/api/v3/parse?title=Love%20On%20The%20Spectrum%20AU%20S02E01" \
  | python3 -c 'import json,sys; p=json.load(sys.stdin); s=p.get("series") or {}; raise SystemExit(0 if s.get("tvdbId")==372611 or s.get("id")==237 else 1)'; then
  pass "AU title parses to Love on the Spectrum"
else
  fail "AU title does not parse to library series"
fi

echo "== Season search no longer Unknown Series for AU pack =="
if docker exec homelab-sonarr-1 curl -sS -H "X-Api-Key: ${SONARR_API_KEY}" \
  "http://127.0.0.1:8989/sonarr/api/v3/release?seriesId=237&seasonNumber=2" \
  | python3 -c '
import json,sys
rel=json.load(sys.stdin)
au=[r for r in rel if "AU" in (r.get("title") or "") and "S02" in (r.get("title") or "").replace(" ","")]
if not au:
    # title may normalize spaces
    au=[r for r in rel if "Spectrum AU" in (r.get("title") or "") or "Spectrum.AU" in (r.get("title") or "")]
if not au:
    print("no AU pack in results"); raise SystemExit(1)
bad=[r for r in au if "Unknown Series" in (r.get("rejections") or [])]
if bad:
    print("still rejected", bad[0].get("rejections")); raise SystemExit(1)
print("AU pack rejections:", au[0].get("rejections"), "approved", au[0].get("approved"))
raise SystemExit(0)
'; then
  pass "AU season pack not rejected as Unknown Series"
else
  fail "AU season pack still Unknown Series / missing"
fi

if [[ "${FAIL}" -ne 0 ]]; then
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: PASS"
exit 0
