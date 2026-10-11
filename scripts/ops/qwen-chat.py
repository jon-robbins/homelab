#!/usr/bin/env python3
"""Minimal terminal chat with the local Qwen vLLM server (stdlib only).

Talks to the OpenAI-compatible /chat/completions endpoint of the host-side
vLLM deployment (~/qwen, 127.0.0.1:18020). After the Stage B cutover the
target can be changed via --base-url.

Auth: $VLLM_API_KEY from the environment. If not set, auto-fetches from
Infisical /llm via the built-in stdlib REST client.

In-chat commands: /reset clears history, /quit or /exit (or Ctrl-D) leaves.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

DEFAULT_BASE_URL = "http://127.0.0.1:18020/v1"
DEFAULT_MODEL = "qwen3.8-27b"


def resolve_api_key() -> str:
    key = os.environ.get("VLLM_API_KEY", "").strip()
    if key:
        return key
    try:
        from infisical_fetch import ensure_secrets
        ensure_secrets("/llm", "VLLM_API_KEY")
    except SystemExit as exc:
        sys.exit(f"qwen-chat: {exc}")
    key = os.environ.get("VLLM_API_KEY", "").strip()
    if key:
        return key
    sys.exit("qwen-chat: VLLM_API_KEY not set — failed to fetch from Infisical")


def chat(base_url: str, api_key: str, model: str, messages: list[dict]) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "messages": messages}).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Minimal terminal chat with the local Qwen vLLM server."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--system", default=None, help="optional system prompt")
    parser.add_argument(
        "--show-reasoning",
        action="store_true",
        help="also print the model's reasoning_content (qwen3 thinking)",
    )
    args = parser.parse_args()

    api_key = resolve_api_key()
    history: list[dict] = (
        [{"role": "system", "content": args.system}] if args.system else []
    )
    print(f"qwen-chat — {args.model} at {args.base_url}")
    print("/reset clears history, /quit exits\n")

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return 0
        if line == "/reset":
            history = history[:1] if args.system else []
            print("(history cleared)")
            continue

        history.append({"role": "user", "content": line})
        try:
            response = chat(args.base_url, api_key, args.model, history)
        except KeyboardInterrupt:
            print("\n(interrupted — request abandoned)")
            history.pop()
            continue
        except urllib.error.HTTPError as error:
            body = error.read().decode(errors="replace")[:300]
            print(f"[http {error.code}] {body}")
            history.pop()
            continue
        except OSError as error:
            print(f"[error] {error}")
            history.pop()
            continue

        message = response["choices"][0]["message"]
        if args.show_reasoning:
            reasoning = (message.get("reasoning_content") or "").strip()
            if reasoning:
                print(f"\n[thinking]\n{reasoning}\n")
        content = (message.get("content") or "").strip()
        print(f"\nqwen> {content if content else '(empty reply — try --show-reasoning)'}\n")
        history.append({"role": "assistant", "content": content})


if __name__ == "__main__":
    sys.exit(main())
