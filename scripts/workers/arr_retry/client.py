from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


def read_api_key_from_config_xml(path: str) -> str:
    cfg = Path(path)
    if not cfg.exists():
        return ""
    text = cfg.read_text(errors="ignore")
    match = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
    return match.group(1).strip() if match else ""


class ArrClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _url(self, path: str, query: dict[str, object] | None = None) -> str:
        q = dict(query or {})
        q["apikey"] = self.api_key
        return f"{self.base_url}/api/v3{path}?{urllib.parse.urlencode(q, doseq=True)}"

    def get(self, path: str, query: dict[str, object] | None = None) -> object:
        with urllib.request.urlopen(self._url(path, query), timeout=self.timeout_seconds) as resp:
            return json.load(resp)

    def post(self, path: str, payload: dict[str, object]) -> object:
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            return json.load(resp)

    def delete(self, path: str, query: dict[str, object] | None = None) -> None:
        req = urllib.request.Request(self._url(path, query), method="DELETE")
        with urllib.request.urlopen(req, timeout=self.timeout_seconds):
            return
