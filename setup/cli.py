"""`apply` and `status` for a locally set-up host. Plain terminal I/O only; the TUI replaces `ask` and the printing."""

from __future__ import annotations

import argparse
import signal
import shutil
import sys
from collections.abc import Sequence
from getpass import getpass
from pathlib import Path

from scripts.release.modules import load_site

from .engine.checkpoints import Checkpoints
from .engine.contract import (
    Answer,
    Choice,
    Confirm,
    Context,
    OpenUrl,
    Prompt,
    Secret,
    Status,
)
from .engine.layout import HostClaimed, Layout, SetupError
from .engine.local import Release, build_steps, defaults
from .engine.ops import LocalOps
from .engine.runner import Event, run, status
from .engine.secrets import SecretStore
from .hostprep import apply_local_host_config, ensure_service_user, install_pinned_packages, preflight

DEFAULT_ROOT = "/srv/homelab"


def make_ops() -> LocalOps:
    return LocalOps()


def phases():
    return defaults()


def open_site(args: argparse.Namespace, layout: Layout, network_spec) -> tuple[Release, Path]:
    """Validate and render the site (read-only), and find where its secrets live."""
    plugins = dict(args.plugin)
    env = load_site(args.site, args.platform, plugins).env
    return Release.render(layout, args.platform, args.site, plugins, network_spec), Path(env["HOMELAB_SECRET_DIR"])


def ask(prompt: Prompt) -> Answer:
    if isinstance(prompt, Secret):
        return getpass(f"{prompt.label} {('(' + prompt.hint + ')') if prompt.hint else ''}: ")
    if isinstance(prompt, OpenUrl):
        input(f"{prompt.label}\n  {prompt.url}\nPress Enter when done. ")
        return True
    if isinstance(prompt, Confirm):
        return input(f"{prompt.label} [y/N] ").strip().lower() in ("y", "yes")
    if isinstance(prompt, Choice):
        return input(f"{prompt.label} ({'/'.join(prompt.options)}): ").strip()
    return input(f"{prompt.label}: ").strip()


def _context(args: argparse.Namespace, layout: Layout, secrets: Path) -> Context:
    return Context(
        layout,
        SecretStore(secrets),
        Checkpoints(layout.setup_dir),
        args.platform,
        args.site,
        dict(args.plugin),
    )


def _steps(ctx: Context, release: Release, ops, network_spec):
    chosen, generated = phases()
    return build_steps(ctx, release, ops, phases=chosen, generated=generated, network_spec=network_spec)


def _guard(layout: Layout, ops) -> None:
    """Refuse, before any write, a host that the owner's pipeline has claimed or has containers running on."""
    layout.refuse_owner_host()
    if shutil.which("docker") is None:
        return
    if foreign := ops.foreign_containers(layout.releases):
        raise HostClaimed(
            f"containers in the owner's Compose projects that setup did not create: {', '.join(sorted(foreign))}. "
            "Local setup does not adopt or recreate them; use a host without them."
        )


def _show(event: Event) -> None:
    if event.kind != "applying":
        print(f"{event.step}: {event.kind}{(' - ' + event.message) if event.message else ''}")


def _apply(args: argparse.Namespace, layout: Layout) -> int:
    preflight(acknowledge_public_ip=args.ack_public_ip)
    ops = make_ops()
    _guard(layout, ops)
    install_pinned_packages()
    ensure_service_user()
    apply_local_host_config()
    network_spec = ops.network_spec(layout.setup_dir)
    release, secrets = open_site(args, layout, network_spec)
    layout.claim()
    ctx = _context(args, layout, secrets)
    outcome = run(
        _steps(ctx, release, ops, network_spec),
        ctx,
        ask=ask if sys.stdin.isatty() else None,
        on_event=_show,
    )
    if not outcome.ok:
        print(
            f"stopped at {outcome.stopped_at}: {outcome.reason}\nFix that, then run apply again; it resumes.",
            file=sys.stderr,
        )
    return 0 if outcome.ok else 1


def _status(args: argparse.Namespace, layout: Layout) -> int:
    ops = make_ops()
    _guard(layout, ops)
    if layout.mode() is None:
        print("not set up on this host yet; run apply")
        return 0
    network_spec = ops.network_spec(layout.setup_dir)
    release, secrets = open_site(args, layout, network_spec)
    ctx = _context(args, layout, secrets)
    report = status(_steps(ctx, release, ops, network_spec), ctx)
    for step, state in report.items():
        print(f"{step}: {state.status.value}{(' - ' + state.reason) if state.reason else ''}")
    for step in ctx.checkpoints.interrupted():
        print(f"{step}: interrupted last run; apply will redo it")
    return 0 if all(s.status is Status.DONE for s in report.values()) else 1


def _plugin(value: str) -> tuple[str, Path]:
    name, _, folder = value.partition("=")
    if not (name and folder):
        raise argparse.ArgumentTypeError("--plugin must be NAME=DIR")
    return name, Path(folder)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m setup.cli", description="Set up this host locally.")
    parser.add_argument("--root", type=Path, default=Path(DEFAULT_ROOT))
    parser.add_argument("--site", type=Path, required=True, help="your local site directory")
    parser.add_argument(
        "--platform",
        type=Path,
        default=Path.cwd(),
        help="the homelab checkout or release",
    )
    parser.add_argument("--plugin", type=_plugin, action="append", default=[], help="NAME=DIR")
    parser.add_argument(
        "--ack-public-ip",
        action="store_true",
        help="acknowledge that this host has a globally routable address",
    )
    parser.add_argument("command", choices=("apply", "status"))
    args = parser.parse_args(argv)
    for name in (
        "SIGHUP",
        "SIGTERM",
    ):  # a dropped SSH session ends the run cleanly; apply resumes from checks
        signal.signal(getattr(signal, name), _interrupt)
    try:
        return (_apply if args.command == "apply" else _status)(args, Layout(args.root))
    except SetupError as error:
        print(
            f"{'refused' if isinstance(error, HostClaimed) else 'cannot continue'}: {error}",
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, RuntimeError) as error:
        print(f"cannot continue: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; run apply again to resume where it stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
