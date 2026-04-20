#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Replacements:
    media_hdd: str
    media_nvme: str
    plex_srv: str
    jellyfin_published_url: str
    enable_gpu: bool


def _norm_root(p: str) -> str:
    p = p.rstrip("/")
    if not p.startswith("/"):
        raise ValueError(f"path must be absolute: {p}")
    return p


def _replace_mount_prefixes(text: str, repl: Replacements) -> str:
    # Replace only whole-path prefixes to avoid accidental substitutions.
    # We preserve any subpath, and allow /mnt/media-hdd itself.
    def sub(prefix_old: str, prefix_new: str, s: str) -> str:
        pattern = re.compile(rf"(?<![A-Za-z0-9_./-]){re.escape(prefix_old)}(?=/|\\b)")
        return pattern.sub(prefix_new, s)

    out = text
    out = sub("/mnt/media-hdd", repl.media_hdd, out)
    out = sub("/mnt/media-nvme", repl.media_nvme, out)
    out = sub("/srv/plex", repl.plex_srv, out)
    return out


def _toggle_gpu_lines(text: str, enable: bool) -> str:
    # This is intentionally conservative: it removes only the specific GPU lines
    # we know exist in this repo, and only when they appear as standalone YAML lines.
    if enable:
        return text

    lines = text.splitlines(True)
    out: list[str] = []
    skip_next_env_value = False

    for line in lines:
        # Remove `gpus: all` lines.
        if re.match(r"^\s*gpus:\s*all\s*$", line):
            continue

        # Remove environment entries like `- NVIDIA_VISIBLE_DEVICES=all`.
        if re.match(r"^\s*-\s*NVIDIA_VISIBLE_DEVICES=.*\s*$", line):
            continue

        # Remove environment YAML keys like:
        # environment:
        #   - NVIDIA_VISIBLE_DEVICES=all
        # (handled above) or `NVIDIA_VISIBLE_DEVICES: ...` (not used here).
        if re.match(r"^\s*NVIDIA_VISIBLE_DEVICES\s*:\s*.*\s*$", line):
            continue

        out.append(line)

    return "".join(out)


def _update_jellyfin_url(text: str, jellyfin_url: str) -> str:
    # Replace the entire value, regardless of current domain.
    return re.sub(
        r"(?m)^(\s*-\s*JELLYFIN_PublishedServerUrl=).*$",
        rf"\1{jellyfin_url}",
        text,
    )


def rewrite_file(path: Path, repl: Replacements) -> None:
    original = path.read_text(encoding="utf-8")
    updated = original
    updated = _replace_mount_prefixes(updated, repl)
    updated = _toggle_gpu_lines(updated, enable=repl.enable_gpu)
    if path.name == "docker-compose.media.yml":
        updated = _update_jellyfin_url(updated, repl.jellyfin_published_url)

    if updated != original:
        path.write_text(updated, encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Rewrite compose YAML with user-specific paths and GPU settings.")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--media-hdd", required=True)
    ap.add_argument("--media-nvme", required=True)
    ap.add_argument("--plex-srv", required=True)
    ap.add_argument("--jellyfin-url", required=True)
    ap.add_argument("--enable-gpu", action="store_true")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    media_hdd = _norm_root(args.media_hdd)
    media_nvme = _norm_root(args.media_nvme)
    plex_srv = _norm_root(args.plex_srv)
    jellyfin_url = args.jellyfin_url.strip()
    if not (jellyfin_url.startswith("http://") or jellyfin_url.startswith("https://")):
        raise ValueError("jellyfin url must start with http:// or https://")

    repl = Replacements(
        media_hdd=media_hdd,
        media_nvme=media_nvme,
        plex_srv=plex_srv,
        jellyfin_published_url=jellyfin_url,
        enable_gpu=bool(args.enable_gpu),
    )

    targets = [
        repo_root / "docker-compose.media.yml",
        repo_root / "docker-compose.llm.yml",
    ]

    for p in targets:
        if not p.exists():
            raise FileNotFoundError(str(p))
        rewrite_file(p, repl)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as e:
        print(f"rewrite_compose.py: error: {e}", file=sys.stderr)
        raise SystemExit(2)
