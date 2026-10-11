import json

from homelab_workers.mcp_gap_closer.pack import build_inbox_pack


def test_prefers_workaround_excerpts_with_user_context() -> None:
    pack = build_inbox_pack(
        chats=[
            {
                "id": "c1",
                "project": "home-jon-homelab",
                "messages": [
                    {"role": "user", "text": "check sonarr queue health"},
                    {
                        "role": "assistant",
                        "text": "No MCP tool; used docker compose exec sonarr ...",
                    },
                ],
            }
        ],
        generated_at="2026-08-10T06:00:00Z",
        max_bytes=200_000,
    )
    excerpts = pack["chats"][0]["excerpts"]
    joined = "\n".join(excerpts)
    assert "docker compose exec" in joined
    assert "check sonarr queue health" in joined


def test_filters_sdd_implementer_noise_chats() -> None:
    pack = build_inbox_pack(
        chats=[
            {
                "id": "noise",
                "project": "home-jon-homelab",
                "messages": [
                    {
                        "role": "user",
                        "text": (
                            "You are implementing Task 7: Cloud runner\n"
                            "Read your task brief first: .superpowers/sdd/task-7-brief.md\n"
                            "Then used docker compose exec to inspect something."
                        ),
                    },
                    {"role": "assistant", "text": "docker compose exec sonarr true"},
                ],
            },
            {
                "id": "ops",
                "project": "home-jon-homelab",
                "messages": [
                    {"role": "user", "text": "sonarr queue is stuck"},
                    {
                        "role": "assistant",
                        "text": "No MCP tool for this; used docker compose exec sonarr ...",
                    },
                ],
            },
        ],
        generated_at="2026-08-10T06:00:00Z",
        max_bytes=200_000,
    )
    ids = [c["id"] for c in pack["chats"]]
    assert ids == ["ops"]


def test_enforces_max_bytes() -> None:
    huge = "x" * 50_000
    chats = [
        {
            "id": f"c{i}",
            "project": "home-jon-homelab",
            "messages": [{"role": "assistant", "text": f"docker exec {huge}"}],
        }
        for i in range(10)
    ]
    pack = build_inbox_pack(chats=chats, generated_at="2026-08-10T06:00:00Z", max_bytes=80_000)
    raw = json.dumps(pack, ensure_ascii=False).encode("utf-8")
    assert len(raw) <= 80_000
