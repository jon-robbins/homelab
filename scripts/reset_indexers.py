#!/usr/bin/env python3
import subprocess, json, time
API_KEY = subprocess.check_output(["grep", "-oP", "(?<=<ApiKey>)[^<]+", "/home/jon/homelab/data/prowlarr/config.xml"]).decode().strip()
BASE = "http://localhost:9696/prowlarr/api/v1"
def pcurl(method, path, data=None):
    cmd = ["docker", "exec", "-i", "homelab-prowlarr-1", "curl", "-s", "-X", method, f"{BASE}/{path}", "-H", f"X-Api-Key: {API_KEY}", "-H", "Content-Type: application/json"]
    if data:
        cmd += ["-d", json.dumps(data)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.stdout.strip():
        try:
            return json.loads(r.stdout)
        except:
            return r.stdout.strip()
    return None
print("=== Current backoff ===", flush=True)
statuses = pcurl("GET", "indexerstatus")
if statuses and isinstance(statuses, list):
    for s in statuses:
        print(f"  ID:{s['indexerId']} till:{s.get('disabledTill','?')}", flush=True)
else:
    print("  None", flush=True)
indexers = pcurl("GET", "indexer")
print(f"\n=== {len(indexers)} indexers ===", flush=True)
backoff_ids = {s['indexerId'] for s in (statuses if isinstance(statuses, list) else [])}
for ix in indexers:
    iid, name, en = ix['id'], ix['name'], ix['enable']
    bo = iid in backoff_ids
    print(f"\n{iid}: {name} en={en} backoff={bo}", flush=True)
    if bo and en:
        off = dict(ix); off['enable'] = False
        pcurl("PUT", f"indexer/{iid}", off)
        print("  disabled", flush=True); time.sleep(1)
        on = dict(ix); on['enable'] = True
        pcurl("PUT", f"indexer/{iid}", on)
        print("  re-enabled", flush=True); time.sleep(0.5)
print("\n=== After reset ===", flush=True)
s2 = pcurl("GET", "indexerstatus")
if s2 and isinstance(s2, list) and len(s2) > 0:
    for s in s2:
        print(f"  ID:{s['indexerId']} till:{s.get('disabledTill','?')}", flush=True)
else:
    print("  All clear!", flush=True)
print("\n=== Testing ===", flush=True)
fresh = pcurl("GET", "indexer")
for ix in (fresh if isinstance(fresh, list) else indexers):
    if ix['enable']:
        r = pcurl("POST", "indexer/test", ix)
        if r is None or (isinstance(r, dict) and len(r) == 0):
            st = "PASS"
        elif isinstance(r, list) and len(r) > 0:
            st = f"FAIL: {r[0].get('errorMessage','?')[:60]}"
        else:
            st = f"?: {str(r)[:60]}"
        print(f"  {ix['id']}: {ix['name']} -> {st}", flush=True)
