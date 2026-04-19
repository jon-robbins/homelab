#!/usr/bin/env python3
"""Write tinyproxy.conf with BasicAuth; bind only to loopback on the VM."""
import os
import re
import sys

def main() -> None:
    user = os.environ.get("TINYPROXY_USER", "").strip()
    password = os.environ.get("TINYPROXY_PASSWORD", "")
    if not user or not password:
        print(
            "tinyproxy-auth: set TINYPROXY_USER and TINYPROXY_PASSWORD (e.g. on docker run -e).",
            file=sys.stderr,
        )
        sys.exit(1)
    if any(c in user for c in " \t\n\r"):
        print("tinyproxy-auth: TINYPROXY_USER must not contain whitespace.", file=sys.stderr)
        sys.exit(1)
    # tinyproxy.conf is line-based; avoid characters that break parsing or HTTP Basic.
    safe = re.compile(r"^[a-zA-Z0-9._~+-]+$")
    if not safe.match(user) or not safe.match(password):
        print(
            "tinyproxy-auth: use only [A-Za-z0-9._~+-] in user and password (no spaces or #).",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(password) < 12:
        print("tinyproxy-auth: TINYPROXY_PASSWORD should be at least 12 characters.", file=sys.stderr)
        sys.exit(1)

    os.makedirs("/etc/tinyproxy", mode=0o755, exist_ok=True)
    # Bind inside the container; restrict exposure on the VM with:
    #   docker run -p 127.0.0.1:8888:8888 ...
    # (Docker port-forwards appear from the bridge, not 127.0.0.1, so we do not Listen/Allow only localhost here.)
    conf = f"""User nobody
Group nobody
Port 8888
Timeout 600
Allow 0.0.0.0/0
BasicAuth {user} {password}
"""
    path = "/etc/tinyproxy/tinyproxy.conf"
    with open(path, "w", encoding="utf-8") as f:
        f.write(conf)
    os.chmod(path, 0o600)

    os.execvp("tinyproxy", ["tinyproxy", "-d"])


if __name__ == "__main__":
    main()
