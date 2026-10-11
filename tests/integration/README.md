# Integration test scripts

These scripts hit a live homelab stack (Docker Compose). They are not run in default PR CI; use them on a host where the stack is up.

## Media pipeline E2E — retired

The Overseerr → Arr → qBittorrent live E2E runner (`tests/integration/media-pipeline-e2e.sh`, `media-pipeline-e2e` console script, and `homelab_workers.integration.media_pipeline_e2e`) is **removed**. Do not restore it as a supported CI or nightly gate.

## Bazarr E2E

Shell checks against **live** Caddy (`:80`) and Bazarr’s HTTP API. The first blocks are read-only (config + language profile). The **subtitle** step checks whether Bazarr’s API shows a **downloaded sidecar**: any subtitle object with a non-empty `path` (what Bazarr stores after it saves a file next to the video). That is the practical signal for “user can pick subs in the player,” distinct from “Bazarr is configured.”

This does **not** open a video player; it asserts the same database field Bazarr uses for external files. Embedded-only tracks often keep `path: null`.

### Run (local homelab)

```bash
bash tests/integration/bazarr-e2e.sh
```

Requires `data/bazarr/config/config.yaml` (or `BAZARR_API_KEY` in `.e-nv`). Uses `http://127.0.0.1:80/bazarr` unless you set `BAZARR_E2E_URL`. For `BAZARR_E2E_TRIGGER_SEARCH` and `BAZARR_E2E_REQUIRE_EXTERNAL_SUB`, values are trimmed and treated as on for `1`, `1.` (common typo), `true`, or `yes` (case-insensitive).

**Realistic “new import got subs” flow:** take **Radarr movie id** or **Sonarr episode id** from the Arr UI (or API) for the item that just landed, then:

```bash
BAZARR_E2E_TRIGGER_SEARCH=1 \
BAZARR_E2E_TARGET_RADARR_MOVIE_ID=123 \
BAZARR_E2E_REQUIRE_EXTERNAL_SUB=1 \
bash tests/integration/bazarr-e2e.sh
```

With a target id set, `TRIGGER_SEARCH` runs Bazarr’s **targeted** download actions (`PATCH` movie `sync` + `search-missing`, or `PATCH` episode subtitles for the first missing language / `BAZARR_E2E_TARGET_LANGUAGE`), then polls the probe. Without targets, it posts the global scheduler tasks (full-library style) as before.

| Variable | When |
|----------|------|
| `BAZARR_E2E_SKIP=1` | Exit 0 without hitting the stack (e.g. empty runner). |
| `BAZARR_E2E_URL` | Base URL including path, default `http://127.0.0.1:80/bazarr`. |
| `BAZARR_E2E_EXPECT_LANGS` | Comma-separated `code2` list expected enabled in Bazarr (default `en,zt,zh,es`). |
| `BAZARR_E2E_SKIP_SUBTITLE_ARTIFACT=1` | Skip subtitle probe and triggers (`REQUIRE_EXTERNAL_SUB=1` then fails). |
| `BAZARR_E2E_TARGET_RADARR_MOVIE_ID` | Probe only this movie; with `TRIGGER_SEARCH=1`, Bazarr sync + search-missing for this Radarr id. |
| `BAZARR_E2E_TARGET_SONARR_EPISODE_ID` | Probe only this episode; with `TRIGGER_SEARCH=1`, episode subtitle download API for that id. |
| `BAZARR_E2E_TARGET_LANGUAGE` | When targeting an episode, language code2 if Bazarr has no `missing_subtitles` hint (default `en`). |
| `BAZARR_E2E_TRIGGER_SEARCH=1` | Run triggers (targeted if target ids set, else global Sonarr/Radarr sync + missing-subtitle tasks), then poll `bazarr_e2e_subtitle_probe.py` every `BAZARR_E2E_SUB_POLL_INTERVAL` sec for up to `BAZARR_E2E_SUB_POLL_SEC` (default `360` / `20`). Large libraries may need a longer poll window before any sidecar appears in the scanned slice. |
| `BAZARR_E2E_REQUIRE_EXTERNAL_SUB=1` | Fail if no external subtitle `path` is found (after optional poll). Default is warn-only if none found. |
| `BAZARR_E2E_SCAN_MOVIES_MAX` / `BAZARR_E2E_SCAN_SERIES_MAX` | Broad scan only: passed to `bazarr_e2e_subtitle_probe.py` (defaults `800` / `25`). |

**CI:** `ci.yml` only runs `bash -n` on `bazarr-e2e.sh`. It is **not** wired into the nightly dep-loy job yet; run it on the host where the stack is up before adding it to GitHub Actions.
