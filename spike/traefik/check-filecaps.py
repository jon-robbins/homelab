#!/usr/bin/env python3
"""Report file capabilities (security.capability xattr) on every file of an image saved with `docker save`.

Usage: docker save IMAGE | check-filecaps.py   (reads the tar stream on stdin)
Why: the pinned Caddy binary carries cap_net_bind_service+ep; with `cap_drop: [ALL]` the kernel then refuses to exec
it as non-root. This proves whether the Traefik image has the same trap.
"""
import io
import sys
import tarfile

found, scanned = [], 0
outer = tarfile.open(fileobj=sys.stdin.buffer, mode="r|*")
for member in outer:
    if not member.isfile() or member.size < 512:
        continue
    data = outer.extractfile(member).read()
    try:
        layer = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError:
        continue
    scanned += 1
    for entry in layer:
        caps = {k: v for k, v in entry.pax_headers.items() if "capability" in k}
        if caps:
            found.append((entry.name, sorted(caps)))
print(f"layers scanned: {scanned}; files with security.capability xattr: {len(found)}")
for name, keys in found:
    print("  FILE CAPABILITY:", name, keys)
sys.exit(1 if found else 0)
