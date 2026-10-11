#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterable

MANAGED_SECRET_KEY_PATH_PAIRS: tuple[tuple[str, str], ...] = (
    ("/network", "CLOUDFLARE_TOKEN"),
    ("/media", "TMDB_API_KEY"),
    ("/arr", "SONARR_API_KEY"),
    ("/arr", "RADARR_API_KEY"),
    ("/arr", "PROWLARR_API_KEY"),
    ("/arr", "OVERSEERR_API_KEY"),
    ("/arr", "AUTOBRR_API_KEY"),
    ("/qbt", "QBITTORRENT_USERNAME"),
    ("/qbt", "QBITTORRENT_PASSWORD"),
    ("/llm", "GEMINI_API_KEY"),
    ("/llm", "MCP_API_TOKEN"),
    ("/llm", "MEDIA_AGENT_TOKEN"),
    ("/llm", "HOMELAB_AGENT_API_TOKEN"),
    ("/llm", "VLLM_API_KEY"),
    ("/media", "PLEX_TOKEN"),
)


def managed_secret_keys() -> tuple[str, ...]:
    return tuple(name for _, name in MANAGED_SECRET_KEY_PATH_PAIRS)


def managed_secret_paths() -> tuple[str, ...]:
    return tuple(dict.fromkeys(path for path, _ in MANAGED_SECRET_KEY_PATH_PAIRS))


def managed_secret_path_for_key(name: str) -> str:
    for path, candidate in MANAGED_SECRET_KEY_PATH_PAIRS:
        if candidate == name:
            return path
    raise KeyError(name)


def managed_secret_keys_for_paths(paths: Iterable[str]) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(paths))
    unknown = sorted(set(requested) - set(managed_secret_paths()))
    if unknown:
        raise ValueError(f"unknown managed secret path(s): {', '.join(unknown)}")
    requested_set = set(requested)
    return tuple(
        name for path, name in MANAGED_SECRET_KEY_PATH_PAIRS if path in requested_set
    )


def _validate_catalog() -> None:
    names = managed_secret_keys()
    if len(names) != len(set(names)):
        raise RuntimeError("managed secret names must be unique across paths")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("keys", "pairs", "paths", "path-for-key"))
    parser.add_argument("name", nargs="?")
    args = parser.parse_args()
    _validate_catalog()
    if args.command == "keys":
        print(*managed_secret_keys(), sep="\n")
    elif args.command == "pairs":
        print(*(f"{path}\t{name}" for path, name in MANAGED_SECRET_KEY_PATH_PAIRS), sep="\n")
    elif args.command == "paths":
        print(*managed_secret_paths(), sep="\n")
    else:
        if not args.name:
            parser.error("path-for-key requires NAME")
        try:
            print(managed_secret_path_for_key(args.name))
        except KeyError:
            parser.error(f"unknown managed secret: {args.name}")
    return 0


_validate_catalog()

if __name__ == "__main__":
    raise SystemExit(main())
