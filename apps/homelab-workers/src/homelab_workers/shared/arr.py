from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


def read_api_key_from_config_xml(path: str | Path) -> str:
    cfg = Path(path)
    if not cfg.exists():
        return ""
    text = cfg.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
    return match.group(1).strip() if match else ""


def _indexer_is_enabled(row: dict[str, Any]) -> bool:
    """Radarr/Sonarr may omit `enable` for Prowlarr-synced indexers."""
    enable = row.get("enable")
    if enable is True:
        return True
    if enable is False:
        return False
    return bool(
        row.get("enableRss")
        or row.get("enableAutomaticSearch")
        or row.get("enableInteractiveSearch")
    )


def indexer_row_is_enabled(row: dict[str, Any]) -> bool:
    return _indexer_is_enabled(row)


class ArrClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-Api-Key": api_key} if api_key else {},
            timeout=float(timeout_seconds),
        )

    def _api_path(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        if normalized.startswith("/api/v3/"):
            return normalized
        return f"/api/v3{normalized}"

    def _params(self, query: dict[str, object] | None = None) -> dict[str, object]:
        params = dict(query or {})
        if self.api_key:
            params.setdefault("apikey", self.api_key)
        return params

    def get(self, path: str, query: dict[str, object] | None = None) -> Any:
        resp = self._client.get(self._api_path(path), params=self._params(query))
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict[str, object]) -> Any:
        resp = self._client.post(self._api_path(path), params=self._params(), json=payload)
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, payload: dict[str, object]) -> Any:
        resp = self._client.put(self._api_path(path), params=self._params(), json=payload)
        resp.raise_for_status()
        if not resp.content:
            return None
        return resp.json()

    def get_response(self, path: str, query: dict[str, object] | None = None) -> httpx.Response:
        return self._client.get(self._api_path(path), params=self._params(query))

    def post_response(self, path: str, payload: dict[str, object]) -> httpx.Response:
        return self._client.post(self._api_path(path), params=self._params(), json=payload)

    def indexers_enabled_count(self) -> int:
        payload = self.get("/indexer")
        if not isinstance(payload, list):
            return 0
        return sum(1 for row in payload if isinstance(row, dict) and _indexer_is_enabled(row))

    def delete(self, path: str, query: dict[str, object] | None = None) -> None:
        resp = self._client.delete(self._api_path(path), params=self._params(query))
        resp.raise_for_status()

    def queue(self, *, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        payload = self.get(
            "/queue",
            query={"page": page, "pageSize": page_size, "includeUnknownMovieItems": True},
        )
        return payload if isinstance(payload, dict) else {"records": [], "totalRecords": 0}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ArrClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


# --- E2E queue helpers (Radarr & Sonarr share v3 queue shape) ---

_ARR_QUEUE_DL_ID_KEYS = ("downloadId", "trackedDownloadId", "downloadClientId")
_ACTIVE_ARR_QUEUE_STATUSES = frozenset(
    {"downloading", "queued", "paused", "warned", "importpending", "delay"}
)


@dataclass(frozen=True)
class ArrService:
    name: str
    id_field: str
    history_ids_param: str
    default_port: int


RADARR = ArrService("radarr", "movieId", "movieId", 7878)
SONARR = ArrService("sonarr", "seriesId", "seriesId", 8989)


def _is_hex_info_hash(s: str) -> bool:
    t = s.strip().lower()
    return len(t) >= 32 and all(c in "0123456789abcdef" for c in t)


def _arr_id_eq(value: object, want: int) -> bool:
    if value is None:
        return False
    try:
        return int(value) == want
    except (TypeError, ValueError):
        return False


def _queue_record_download_ids(rec: dict[str, Any]) -> list[str]:
    """Candidate qBT info-hash strings from a Radarr/Sonarr queue row."""
    out: list[str] = []
    seen: set[str] = set()
    for key in _ARR_QUEUE_DL_ID_KEYS:
        raw = rec.get(key)
        if not isinstance(raw, str):
            continue
        did = raw.strip().lower()
        if did and did not in seen:
            seen.add(did)
            out.append(did)
    return out


def _queue_record_hex_download_ids(rec: dict[str, Any]) -> list[str]:
    return [did for did in _queue_record_download_ids(rec) if _is_hex_info_hash(did)]


def _arr_queue_record_download_terminal(rec: dict[str, Any]) -> bool:
    st = rec.get("status")
    st_l = st.strip().lower() if isinstance(st, str) else ""
    if st_l == "completed":
        return True
    tdst = rec.get("trackedDownloadState")
    if isinstance(tdst, str) and tdst.strip().lower() == "imported":
        return True
    return False


def _arr_queue_record_sort_key(rec: dict[str, Any]) -> tuple[int, int]:
    st = rec.get("status")
    st_l = st.strip().lower() if isinstance(st, str) else ""
    active = 0 if st_l in _ACTIVE_ARR_QUEUE_STATUSES else 1
    qid = rec.get("id")
    try:
        qid_i = int(qid) if qid is not None else 0
    except (TypeError, ValueError):
        qid_i = 0
    return (active, -qid_i)


def _arr_queue_records_for(
    queue: dict[str, Any], arr_id: int, id_field: str
) -> list[dict[str, Any]]:
    rows = [
        rec
        for rec in queue.get("records") or []
        if isinstance(rec, dict) and _arr_id_eq(rec.get(id_field), arr_id)
    ]
    return sorted(rows, key=_arr_queue_record_sort_key)


def _arr_queue_record_active(rec: dict[str, Any]) -> bool:
    if _arr_queue_record_download_terminal(rec):
        return False
    st = rec.get("status")
    st_l = st.strip().lower() if isinstance(st, str) else ""
    if not st_l:
        return True
    return st_l in _ACTIVE_ARR_QUEUE_STATUSES


def _arr_queue_client_reports_ok(rec: dict[str, Any]) -> bool:
    tds = rec.get("trackedDownloadStatus")
    return isinstance(tds, str) and tds.strip().lower() == "ok"


def _queue_row_diagnostic_line(rec: dict[str, Any]) -> str:
    qid = rec.get("id")
    st = rec.get("status", "")
    tds = rec.get("trackedDownloadStatus", "")
    ids = _queue_record_download_ids(rec)
    did = ids[0] if ids else ""
    if len(did) > 16:
        did = did[:16] + "…"
    return f"id={qid} status={st} trackedDownloadStatus={tds} downloadId={did or '(none)'}"


def arr_download_terminal_for_hash(
    queue: dict[str, Any], target_id: int, id_field: str, dl_hash: str
) -> bool:
    want = dl_hash.lower()
    for rec in _arr_queue_records_for(queue, target_id, id_field):
        if want not in _queue_record_download_ids(rec):
            continue
        return _arr_queue_record_download_terminal(rec)
    return False


def radarr_download_terminal_for_hash(
    queue: dict[str, Any], movie_id: int, dl_hash: str
) -> bool:
    return arr_download_terminal_for_hash(queue, movie_id, "movieId", dl_hash)


def sonarr_download_terminal_for_hash(
    queue: dict[str, Any], series_id: int, dl_hash: str
) -> bool:
    return arr_download_terminal_for_hash(queue, series_id, "seriesId", dl_hash)
