#!/usr/bin/env bash
# Ensure Sonarr User scene mappings for titles Seerr/Sonarr would otherwise
# reject as "Unknown Series". User-type rows survive SceneMappingService updates.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 "${ROOT}/scripts/lib/infisical_fetch.py" require \
  --paths /arr \
  --keys SONARR_API_KEY \
  --reexec "$0" "$@"

CONTAINER="${SONARR_CONTAINER:-homelab-sonarr-1}"
DB_HOST="/tmp/sonarr-scene-map-$$.db"

cleanup() { rm -f "${DB_HOST}"; }
trap cleanup EXIT

docker cp "${CONTAINER}:/config/sonarr.db" "${DB_HOST}"

sqlite3 "${DB_HOST}" <<'SQL'
-- Love on the Spectrum (AU) tvdb 372611: packs/files often include AU / Australia
INSERT INTO SceneMappings (TvdbId, SeasonNumber, SearchTerm, ParseTerm, Title, Type, SceneSeasonNumber, Comment)
SELECT 372611, -1, 'Love On The Spectrum AU', 'loveonthespectrumau', 'Love On The Spectrum AU', 'User', -1, 'homelab seerr download path health'
WHERE NOT EXISTS (
  SELECT 1 FROM SceneMappings
  WHERE TvdbId = 372611 AND ParseTerm = 'loveonthespectrumau' AND Type = 'User'
    AND SearchTerm = 'Love On The Spectrum AU'
);

INSERT INTO SceneMappings (TvdbId, SeasonNumber, SearchTerm, ParseTerm, Title, Type, SceneSeasonNumber, Comment)
SELECT 372611, -1, 'Love on the Spectrum Australia', 'loveonthespectrumaustralia', 'Love on the Spectrum Australia', 'User', -1, 'homelab seerr download path health'
WHERE NOT EXISTS (
  SELECT 1 FROM SceneMappings
  WHERE TvdbId = 372611 AND ParseTerm = 'loveonthespectrumaustralia' AND Type = 'User'
    AND SearchTerm = 'Love on the Spectrum Australia'
);

INSERT INTO SceneMappings (TvdbId, SeasonNumber, SearchTerm, ParseTerm, Title, Type, SceneSeasonNumber, Comment)
SELECT 372611, -1, 'Love on the Spectrum_ Australia', 'loveonthespectrumaustralia', 'Love on the Spectrum_ Australia', 'User', -1, 'homelab seerr download path health'
WHERE NOT EXISTS (
  SELECT 1 FROM SceneMappings
  WHERE TvdbId = 372611 AND SearchTerm = 'Love on the Spectrum_ Australia' AND Type = 'User'
);
SQL

echo "Scene mappings for tvdb 372611:"
sqlite3 "${DB_HOST}" "SELECT Id, SearchTerm, ParseTerm, Type FROM SceneMappings WHERE TvdbId=372611 ORDER BY Id;"

docker stop "${CONTAINER}"
docker cp "${DB_HOST}" "${CONTAINER}:/config/sonarr.db"
docker start "${CONTAINER}"

echo "Waiting for Sonarr API..."
for _ in $(seq 1 40); do
  if docker exec "${CONTAINER}" curl -sf -H "X-Api-Key: ${SONARR_API_KEY}" \
    "http://127.0.0.1:8989/sonarr/api/v3/system/status" >/dev/null; then
    break
  fi
  sleep 1
done

docker exec "${CONTAINER}" curl -sS -H "X-Api-Key: ${SONARR_API_KEY}" \
  "http://127.0.0.1:8989/sonarr/api/v3/parse?title=Love%20On%20The%20Spectrum%20AU%20S02E01" \
  | python3 -c 'import json,sys; p=json.load(sys.stdin); s=p.get("series") or {};
print("parse:", s.get("title"), "id=", s.get("id"), "tvdb=", s.get("tvdbId"))
raise SystemExit(0 if s.get("tvdbId")==372611 else 1)'

echo "Done."
