#!/usr/bin/env bash
# Static-only structural checks for qwen-serve profile gating + secret isolation.
# Uses `docker compose config` outputs. Never runs compose up.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

# shellcheck source=/dev/null
source scripts/lib/managed-secrets.sh
export_ci_secret_placeholders
# Also export the one non-managed required env for QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS
export QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="100001"

if ! command -v docker >/dev/null 2>&1; then
  echo "SKIP: docker not available"
  exit 0
fi

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

Q1="qwen-serve"
Q2="qwen-code-telegram"
Q3="homelab-mcp-telegram"
Q4="qwen-code-telegram-owner"
Q5="homelab-mcp-telegram-owner"

# ---- Run compose config WITHOUT profile (unscoped) with UNSET QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS ----
(
  unset QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS
  CFG_UNSET=$(docker compose -f docker-compose.yml config --format json)
  SERVICES_UNSET=$(python3 -c "import json,sys;d=json.load(sys.stdin);print(' '.join(sorted(d.get('services',{}).k""eys())))" <<<"$CFG_UNSET")
  for svc in $Q1 $Q2 $Q3 $Q4 $Q5; do
    case " $SERVICES_UNSET " in
      *" $svc "*) fail "unscoped compose with unset chat IDs MUST NOT include $svc (profile leak)";;
    esac
  done
)
pass "unscoped compose succeeds with unset QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS and includes NONE of the 3 qwen-serve services"

# ---- Run compose config WITH profile qwen-serve (with fake chat ID) ----
CFG_WITH=$(docker compose -f docker-compose.yml --profile qwen-serve config --format json)
SERVICES_WITH=$(python3 -c "import json,sys;d=json.load(sys.stdin);print(' '.join(sorted(d.get('services',{}).k""eys())))" <<<"$CFG_WITH")

# 1. With profile: all three present
for svc in $Q1 $Q2 $Q3 $Q4 $Q5; do
  case " $SERVICES_WITH " in
    *" $svc "*) ;;
    *) fail "with --profile qwen-serve: missing service $svc";;
  esac
done
pass "profile qwen-serve activates all 5 services ($Q1 $Q2 $Q3 $Q4 $Q5)"

# ---- Secret isolation checks (WITH profile rendering) ----
# Extract secrets arrays per service.
read_svc_secrets() {
  local svc="$1" cfg="$2"
  python3 -c '
import json, sys
svc_name = sys.argv[1]
d = json.load(sys.stdin)
svc = d.get("services", {}).get(svc_name, {})
secrets = svc.get("secrets") or []
out = []
if isinstance(secrets, list):
    for s in secrets:
        if isinstance(s, dict):
            out.append(s.get("source") or "")
        else:
            out.append(str(s))
print(" ".join(sorted(out)))
' "$svc" <<<"$cfg"
}

SECRETS_Q2=$(read_svc_secrets "$Q2" "$CFG_WITH")   # qwen-code-telegram
SECRETS_Q3=$(read_svc_secrets "$Q3" "$CFG_WITH")   # homelab-mcp-telegram
SECRETS_Q4=$(read_svc_secrets "$Q4" "$CFG_WITH")
SECRETS_Q5=$(read_svc_secrets "$Q5" "$CFG_WITH")

# 3. qwen-code-telegram must NOT mount primary MCP_API_TOKEN secret (dedicated isolation)
case " $SECRETS_Q2 " in
  *" MCP_API_TOKEN "*) fail "qwen-code-telegram must NOT mount primary MCP_API_TOKEN; it must use TELEGRAM_MCP_API_TOKEN only";;
esac
pass "qwen-code-telegram never mounts primary MCP_API_TOKEN"

# 4. qwen-code-telegram MUST mount TELEGRAM_BOT_TOKEN, VLLM_API_KEY, TELEGRAM_MCP_API_TOKEN (dedicated MCP client secret)
for need in TELEGRAM_BOT_TOKEN VLLM_API_KEY TELEGRAM_MCP_API_TOKEN; do
  case " $SECRETS_Q2 " in
    *" $need "*) ;;
    *) fail "qwen-code-telegram missing required secret source: $need";;
  esac
done
pass "qwen-code-telegram mounts exactly the 3 allowed secrets (bot, vllm, dedicated-mcp-client)"
[[ "$SECRETS_Q4" == "TELEGRAM_OWNER_BOT_TOKEN TELEGRAM_OWNER_MCP_API_TOKEN VLLM_API_KEY" ]] \
  || fail "owner qwen service must mount only owner bot, owner MCP, and VLLM secrets; got: $SECRETS_Q4"
pass "owner qwen service mounts isolated owner credentials"

# 5. homelab-mcp-telegram must NOT mount primary MCP_API_TOKEN
case " $SECRETS_Q3 " in
  *" MCP_API_TOKEN "*) fail "homelab-mcp-telegram must NOT mount primary MCP_API_TOKEN";;
esac
pass "homelab-mcp-telegram never mounts primary MCP_API_TOKEN"

# 6. homelab-mcp-telegram MUST mount exactly these 9 secrets (sorted):
#    TELEGRAM_MCP_API_TOKEN MEDIA_AGENT_TOKEN SONARR_API_KEY RADARR_API_KEY PROWLARR_API_KEY
#    OVERSEERR_API_KEY QBITTORRENT_USERNAME QBITTORRENT_PASSWORD PLEX_TOKEN
EXPECTED_Q3_SECRETS_SORTED=$(printf '%s\n' MEDIA_AGENT_TOKEN OVERSEERR_API_KEY PLEX_TOKEN PROWLARR_API_KEY QBITTORRENT_PASSWORD QBITTORRENT_USERNAME RADARR_API_KEY SONARR_API_KEY TELEGRAM_MCP_API_TOKEN | sort | tr '\n' ' ')
EXPECTED_Q3_SECRETS_SORTED="${EXPECTED_Q3_SECRETS_SORTED% }"
[[ "$SECRETS_Q3" == "$EXPECTED_Q3_SECRETS_SORTED" ]] \
  || fail "homelab-mcp-telegram must mount exactly 9 secrets sorted [$EXPECTED_Q3_SECRETS_SORTED]; got: [$SECRETS_Q3]"
case " $SECRETS_Q3 " in
  *" MCP_API_TOKEN "*) fail "homelab-mcp-telegram must NOT include primary MCP_API_TOKEN in 9-secret mount set";;
esac
pass "homelab-mcp-telegram mounts exactly 9 secrets + NEVER mounts primary MCP_API_TOKEN"
[[ "$SECRETS_Q5" == "OVERSEERR_API_KEY PLEX_TOKEN PROWLARR_API_KEY QBITTORRENT_PASSWORD QBITTORRENT_USERNAME RADARR_API_KEY SONARR_API_KEY TELEGRAM_OWNER_MCP_API_TOKEN" ]] \
  || fail "owner MCP service must mount exactly its dedicated 8-secret set; got: $SECRETS_Q5"
case " $SECRETS_Q5 " in *" TELEGRAM_MCP_API_TOKEN "*) fail "owner MCP must not mount trusted token";; esac
pass "owner MCP service mounts isolated owner token and backend credentials"

# ---- Env isolation: role-specific MCP profiles and mutation gates ----
read_svc_env() {
  local svc="$1" cfg="$2"
  python3 -c '
import json, sys
svc_name = sys.argv[1]
d = json.load(sys.stdin)
env = d.get("services", {}).get(svc_name, {}).get("environment") or {}
if isinstance(env, dict):
    print(json.dumps(env, sort_keys=True))
elif isinstance(env, list):
    out = {}
    for item in env:
        if "=" in item:
            k,v = item.split("=", 1)
            out[k] = v
    print(json.dumps(out, sort_keys=True))
else:
    print("{}")
' "$svc" <<<"$cfg"
}

ENV_Q3=$(read_svc_env "$Q3" "$CFG_WITH")
MUTATIONS=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('HOMELAB_MCP_ENABLE_MUTATIONS',''))" "$ENV_Q3")
[[ "$MUTATIONS" == "1" ]] || fail "trusted MCP must set HOMELAB_MCP_ENABLE_MUTATIONS=1; got: $MUTATIONS"
PROFILE=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('HOMELAB_MCP_TOOL_PROFILE',''))" "$ENV_Q3")
[[ "$PROFILE" == "trusted" ]] || fail "trusted MCP must set profile=trusted; got: $PROFILE"
ENV_Q5=$(read_svc_env "$Q5" "$CFG_WITH")
OWNER_MUTATIONS=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('HOMELAB_MCP_ENABLE_MUTATIONS',''))" "$ENV_Q5")
OWNER_PROFILE=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('HOMELAB_MCP_TOOL_PROFILE',''))" "$ENV_Q5")
[[ "$OWNER_MUTATIONS" == "1" && "$OWNER_PROFILE" == "full" ]] || fail "owner MCP must set mutations=1 and profile=full"
pass "trusted/owner MCP services enable mutations only behind trusted/full profiles"

for svc_env in "$ENV_Q3" "$ENV_Q5"; do
  ALLOWLIST=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('HOMELAB_MCP_ALLOWED_TOOLS',''))" "$svc_env")
  [[ -z "$ALLOWLIST" ]] || fail "MCP role service must not duplicate the canonical role artifact in HOMELAB_MCP_ALLOWED_TOOLS"
done
pass "trusted and owner MCP services have no duplicate HOMELAB_MCP_ALLOWED_TOOLS inventory"

# ---- Profiles key present on all three services ----
check_service_has_profile_qwen_serve() {
  local svc="$1" cfg="$2"
  local profiles
  profiles=$(python3 -c '
import json, sys
svc_name = sys.argv[1]
d = json.load(sys.stdin)
profiles = d.get("services", {}).get(svc_name, {}).get("profiles") or []
if isinstance(profiles, list):
    print(" ".join(sorted(profiles)))
else:
    print("")
' "$svc" <<<"$cfg")
  case " $profiles " in
    *" qwen-serve "*) ;;
    *) fail "service $svc missing profiles: [qwen-serve] (profiles=$profiles)";;
  esac
}

check_service_has_profile_qwen_serve "$Q1" "$CFG_WITH"
check_service_has_profile_qwen_serve "$Q2" "$CFG_WITH"
check_service_has_profile_qwen_serve "$Q3" "$CFG_WITH"
check_service_has_profile_qwen_serve "$Q4" "$CFG_WITH"
check_service_has_profile_qwen_serve "$Q5" "$CFG_WITH"
pass "all 5 services have profiles: [qwen-serve]"

# ---- qwen-code-telegram environment: role + no-old-QWEN_CODE_* envs ----
ENV_Q2=$(read_svc_env "$Q2" "$CFG_WITH")
ROLE=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('QWEN_CODE_TELEGRAM_ROLE',''))" "$ENV_Q2")
[[ "$ROLE" == "trusted" ]] || fail "qwen-code-telegram must resolve role=trusted; got: $ROLE"
pass "qwen-code-telegram role wiring resolves to trusted"
# QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS must still be present (non-secret whitelist)
ALLOWED_ENV_PRESENT=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print('yes' if 'QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS' in d else 'no')" "$ENV_Q2")
[[ "$ALLOWED_ENV_PRESENT" == "yes" ]] || fail "qwen-code-telegram missing QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS env"
# OLD secret/setup envs MUST be absent (new entrypoint hydrates secrets from files, no env pass-through)
OLD_ENVS_CHECK=$(python3 -c "
import json,sys
d=json.loads(sys.argv[1])
bad = [k for k in ['QWEN_CODE_TELEGRAM_BOT_TOKEN_FILE','QWEN_CODE_MODEL_BASE_URL','QWEN_CODE_MODEL','QWEN_CODE_API_KEY_FILE','QWEN_CODE_MCP_SERVERS_JSON'] if k in d]
print('none' if not bad else ','.join(bad))
" "$ENV_Q2")
[[ "$OLD_ENVS_CHECK" == "none" ]] || fail "qwen-code-telegram must NOT include old QWEN_CODE_* secret envs: $OLD_ENVS_CHECK"
pass "qwen-code-telegram environment block: role and allowlist pins; old file/token/env vars removed"

# ---- qwen-code-telegram: no published ports (no inbound ingress) ----
Q2_PORTS=$(python3 -c '
import json, sys
svc_name = sys.argv[1]
d = json.load(sys.stdin)
ports = d.get("services", {}).get(svc_name, {}).get("ports") or []
print(len(ports))
' "$Q2" <<<"$CFG_WITH")
[[ "$Q2_PORTS" == "0" ]] || fail "qwen-code-telegram must have ZERO published ports; got $Q2_PORTS port bindings"
pass "qwen-code-telegram publishes zero ports (no inbound ingress)"

Q3_PORTS=$(python3 -c '
import json, sys
svc_name = sys.argv[1]
d = json.load(sys.stdin)
ports = d.get("services", {}).get(svc_name, {}).get("ports") or []
print(len(ports))
' "$Q3" <<<"$CFG_WITH")
[[ "$Q3_PORTS" == "0" ]] || fail "homelab-mcp-telegram must have ZERO published ports; got $Q3_PORTS port bindings"
pass "homelab-mcp-telegram publishes zero ports (no ingress from outside homelab_net)"

# ---- qwen-code-telegram: data volume + QWEN_HOME / QWEN_RUNTIME_DIR envs ----
Q2_VOLS=$(python3 -c '
import json, sys
svc_name = sys.argv[1]
d = json.load(sys.stdin)
vols = d.get("services", {}).get(svc_name, {}).get("volumes") or []
targets = [v.get("target") for v in vols if isinstance(v, dict)]
print(" ".join(sorted(targets)))
' "$Q2" <<<"$CFG_WITH")
case " $Q2_VOLS " in
  *"/data"*) ;;
  *) fail "qwen-code-telegram missing persistent /data volume mount (volumes: $Q2_VOLS)";;
esac
pass "qwen-code-telegram mounts persistent /data volume"

QWEN_HOME_VAL=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('QWEN_HOME',''))" "$ENV_Q2")
[[ "$QWEN_HOME_VAL" == "/data/home" ]] || fail "qwen-code-telegram QWEN_HOME must be '/data/home'; got: $QWEN_HOME_VAL"
QWEN_RNT_VAL=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('QWEN_RUNTIME_DIR',''))" "$ENV_Q2")
[[ "$QWEN_RNT_VAL" == "/data/runtime" ]] || fail "qwen-code-telegram QWEN_RUNTIME_DIR must be '/data/runtime'; got: $QWEN_RNT_VAL"
pass "qwen-code-telegram environment sets QWEN_HOME=/data/home and QWEN_RUNTIME_DIR=/data/runtime"

echo "ALL test_qwen_telegram_structure.sh PASS"
