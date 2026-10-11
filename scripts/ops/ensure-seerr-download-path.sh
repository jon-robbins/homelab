#!/usr/bin/env bash
# Ensure qBittorrent Incoming is writable for Arr/Seerr downloads and category dirs exist.
#
# Infisical/qBT API work runs as the invoking user. Only filesystem chown/mkdir
# that require root are elevated via sudo — so bootstrap ownership stays valid.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "${ROOT}/scripts/lib/managed-secrets.sh"

python3 "${ROOT}/scripts/lib/infisical_fetch.py" require \
  --paths /qbt \
  --keys QBITTORRENT_USERNAME,QBITTORRENT_PASSWORD \
  --reexec "$0" "$@"

source_nonsecret_env "${ROOT}/.env"

INCOMING="${MEDIA_NVME_PATH:-/mnt/media-nvme}/Incoming"
OWNER="${SUDO_USER:-${USER:?USER must be set}}"
GROUP="plex"

run_as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

run_as_root "${ROOT}/scripts/ops/fix-media-permissions.sh" \
  --owner "${OWNER}" --group "${GROUP}" --path "${INCOMING}"

run_as_root mkdir -p "${INCOMING}"/{tv,movies,prowlarr,radarr,tv-sonarr,incomplete}
run_as_root chown -R "${OWNER}:${GROUP}" "${INCOMING}"/{tv,movies,prowlarr,radarr,tv-sonarr,incomplete}
run_as_root chmod 2775 "${INCOMING}" "${INCOMING}"/{tv,movies,prowlarr,radarr,tv-sonarr,incomplete}

# Enable Auto TMM so Sonarr category tv-sonarr lands in /downloads/tv (not unwritable-or-messy root)
# Runs as the invoking user with Infisical-resolved qBT credentials in the environment.
python3 - <<PY
import json, os, subprocess, pathlib, textwrap
script = textwrap.dedent('''
import json, os, urllib.parse, urllib.request, http.cookiejar
cj=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
data=urllib.parse.urlencode({"username":os.environ["USER"],"password":os.environ["PASS"]}).encode()
opener.open("http://127.0.0.1:8080/api/v2/auth/login", data=data).read()
def post(path, payload):
    body=urllib.parse.urlencode(payload).encode()
    return opener.open(f"http://127.0.0.1:8080/api/v2/{path}", data=body).read().decode()
post("app/setPreferences", {"json": json.dumps({
  "auto_tmm_enabled": True,
  "torrent_changed_tmm_enabled": False,
  "save_path_changed_tmm_enabled": False,
  "category_changed_tmm_enabled": False,
})})
desired={"tv-sonarr":"tv","movies-radarr":"movies","prowlarr":"prowlarr","radarr":"radarr","sonarr":"tv"}
cats=json.load(opener.open("http://127.0.0.1:8080/api/v2/torrents/categories"))
for name, path in desired.items():
    if name not in cats:
        post("torrents/createCategory", {"category": name, "savePath": path})
    elif (cats[name] or {}).get("savePath") != path:
        post("torrents/editCategory", {"category": name, "savePath": path})
print("auto_tmm + categories ok")
''')
path = pathlib.Path("/tmp/ensure_qbt_tmm.py")
path.write_text(script)
env = os.environ.copy()
env["USER"] = os.environ["QBITTORRENT_USERNAME"]
env["PASS"] = os.environ["QBITTORRENT_PASSWORD"]
subprocess.check_call([
  "docker","run","--rm","--network","container:homelab-qbittorrent-1",
  "-e", "USER",
  "-e", "PASS",
  "-v", f"{path}:/tmp/ensure_qbt_tmm.py:ro",
  "python:3.12-slim","python","/tmp/ensure_qbt_tmm.py",
], env=env)
PY

docker exec -u abc homelab-qbittorrent-1 sh -c \
  'touch /downloads/.ok /downloads/tv/.ok /downloads/prowlarr/.ok && rm -f /downloads/.ok /downloads/tv/.ok /downloads/prowlarr/.ok && echo qbit_write_ok'

echo "Done. Re-run: bash tests/runtime/test_seerr_download_path_health.sh"
