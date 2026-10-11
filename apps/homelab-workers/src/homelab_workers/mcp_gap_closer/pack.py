from __future__ import annotations

import json

WORKAROUND_HINTS: tuple[str, ...] = (
    "docker compose",
    "docker exec",
    "curl ",
    "httpx",
    "wget ",
    "qbittorrent-cli",
)

# Chats that look like subagent/SDD implementer traffic, not ops debugging.
SDD_NOISE_MARKERS: tuple[str, ...] = (
    "you are implementing task",
    "you are reviewing one task",
    "read your task brief first",
    "task brief first:",
    ".superpowers/sdd/",
    "required sub-skill",
    "task quality:",
    "spec compliance",
    "for agentic workers:",
)

MAX_EXCERPTS_PER_CHAT = 8


def _has_workaround_hint(text: str) -> bool:
    lower = text.lower()
    return any(h in lower for h in WORKAROUND_HINTS)


def _is_sdd_noise_chat(messages: list[dict[str, str]]) -> bool:
    blob = "\n".join(str(m.get("text") or "") for m in messages).lower()
    return any(marker in blob for marker in SDD_NOISE_MARKERS)


def _fallback_excerpts(messages: list[dict[str, str]]) -> list[str]:
    users = [m["text"] for m in messages if m.get("role") == "user"]
    assistants = [m["text"] for m in messages if m.get("role") == "assistant"]
    candidates: list[str] = []
    if users:
        candidates.append(users[0])
    if assistants:
        candidates.append(assistants[0])
    if len(users) > 1:
        candidates.append(users[-1])
    if len(assistants) > 1:
        candidates.append(assistants[-1])
    seen: set[str] = set()
    out: list[str] = []
    for text in candidates:
        if text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _select_excerpts(messages: list[dict[str, str]]) -> list[str]:
    """Prefer workaround lines, keeping neighboring user/assistant context."""
    hinted_indexes = [i for i, m in enumerate(messages) if _has_workaround_hint(m.get("text") or "")]
    if not hinted_indexes:
        return _fallback_excerpts(messages)

    chosen: list[int] = []
    for i in hinted_indexes:
        # Preceding user turn for operational framing.
        for j in range(i - 1, -1, -1):
            if messages[j].get("role") == "user":
                chosen.append(j)
                break
        chosen.append(i)
        # Following assistant turn if the hint was on a user message.
        if messages[i].get("role") == "user":
            for j in range(i + 1, len(messages)):
                if messages[j].get("role") == "assistant":
                    chosen.append(j)
                    break

    seen: set[str] = set()
    out: list[str] = []
    for idx in chosen:
        text = messages[idx].get("text") or ""
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
        if len(out) >= MAX_EXCERPTS_PER_CHAT:
            break
    return out


def _pack_size(pack: dict) -> int:
    return len(json.dumps(pack, ensure_ascii=False).encode("utf-8"))


def build_inbox_pack(
    *,
    chats: list[dict],
    generated_at: str,
    max_bytes: int = 200_000,
) -> dict:
    packed_chats = [
        {
            "id": c["id"],
            "project": c["project"],
            "excerpts": _select_excerpts(c["messages"]),
        }
        for c in chats
        if not _is_sdd_noise_chat(c.get("messages") or [])
    ]
    pack: dict = {
        "generated_at": generated_at,
        "window_hours": 24,
        "chats": packed_chats,
    }
    while packed_chats and _pack_size(pack) > max_bytes:
        packed_chats.pop()
    return pack
