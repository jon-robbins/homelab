#!/bin/bash
# Run inside google/cloud-sdk (see authorize-ssh-key-on-vm.sh). Do not run directly on host.
set -euo pipefail

: "${GCP_PROJECT:?}"
: "${GCP_ZONE:?}"
: "${GCP_INSTANCE_NAME:?}"
: "${GCP_SSH_USER:?}"
: "${PUB_LINE:?}"

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

OLD=$(gcloud compute instances describe "$GCP_INSTANCE_NAME" \
  --project="$GCP_PROJECT" --zone="$GCP_ZONE" --format=json \
  | python3 -c 'import json,sys; m=json.load(sys.stdin).get("metadata",{}).get("items",[]); print(next((i["value"] for i in m if i.get("key")=="ssh-keys"), ""))')

python3 - "$OLD" "$GCP_SSH_USER" "$PUB_LINE" >"$TMP" <<'PY'
import sys
old, user, pub = sys.argv[1], sys.argv[2], sys.argv[3]
pub = pub.strip()
if not pub:
    raise SystemExit("empty pubkey")
new_line = f"{user}:{pub}"
lines = [l.strip() for l in old.splitlines() if l.strip()]
parts = pub.split()
stub = parts[1] if len(parts) > 1 else pub
for line in lines:
    if ":" not in line:
        continue
    _, keypart = line.split(":", 1)
    if stub in keypart:
        print(old, end="")
        sys.exit(2)
lines.append(new_line)
print("\n".join(lines) + ("\n" if lines else ""))
PY
STATUS=$?
if [[ "$STATUS" -eq 2 ]]; then
  echo "Key already in instance metadata; nothing to do."
  exit 0
fi
if [[ "$STATUS" -ne 0 ]]; then
  exit "$STATUS"
fi

gcloud compute instances add-metadata "$GCP_INSTANCE_NAME" \
  --project="$GCP_PROJECT" \
  --zone="$GCP_ZONE" \
  --metadata-from-file "ssh-keys=$TMP"

echo "Updated ssh-keys on $GCP_INSTANCE_NAME. Wait ~30s for metadata to apply, then test SSH."
