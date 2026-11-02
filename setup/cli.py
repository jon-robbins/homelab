"""`start`, `prepare`, `apply` and `status` for a locally set-up host. Plain terminal I/O only; the TUI replaces `ask` and the printing."""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
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
    Text,
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
    default = getattr(prompt, "default", None)
    return input(f"{prompt.label}{f' [{default}]' if default else ''}: ").strip() or default or ""


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


def _prepare_host(args: argparse.Namespace, layout: Layout):
    """Refuse an unsuitable or claimed host, then install and configure it. Safe to repeat; a rerun changes nothing."""
    preflight(check_ports=layout.mode() is None)
    ops = make_ops()
    _guard(layout, ops)
    # Claim before the first package or host-file mutation. claim() rechecks owner markers.
    layout.claim()
    install_pinned_packages()
    ensure_service_user()
    apply_local_host_config()
    return ops


def _prepare(args: argparse.Namespace, layout: Layout) -> int:
    _prepare_host(args, layout)
    print("host is ready")
    return 0


def _start(args: argparse.Namespace, layout: Layout) -> int:
    """What the one-line bootstrap runs. WS-09 SEAM: the wizard's first screen replaces the plain prompt below."""
    _prepare(args, layout)
    if not sys.stdin.isatty():
        print("Run `python3 -m setup.cli start` in a terminal to continue.")
        return 0
    folder = ask(Text("site", "Folder for your local site", default=str(layout.root / "site")))
    print(f"The guided screens will build your site in {folder}.")
    return 0


def _apply(args: argparse.Namespace, layout: Layout) -> int:
    ops = _prepare_host(args, layout)
    network_spec = ops.plan_network(layout.setup_dir)
    release, secrets = open_site(args, layout, network_spec)
    release.require_loopback_published_ports()
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


COMMANDS = {"start": _start, "prepare": _prepare, "apply": _apply, "status": _status}


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
    parser.add_argument("--site", type=Path, help="your local site directory (apply and status)")
    parser.add_argument(
        "--platform",
        type=Path,
        default=Path.cwd(),
        help="the homelab checkout or release",
    )
    parser.add_argument("--plugin", type=_plugin, action="append", default=[], help="NAME=DIR")
    parser.add_argument("command", choices=sorted(COMMANDS), help="start: prepare this host, then begin guided setup")
    args = parser.parse_args(argv)
    if args.command in ("apply", "status") and args.site is None:
        parser.error("--site is required for apply and status")
    for name in (
        "SIGHUP",
        "SIGTERM",
    ):  # a dropped SSH session ends the run cleanly; apply resumes from checks
        signal.signal(getattr(signal, name), _interrupt)
    try:
        return COMMANDS[args.command](args, Layout(args.root))
    except SetupError as error:
        print(
            f"{'refused' if isinstance(error, HostClaimed) else 'cannot continue'}: {error}",
            file=sys.stderr,
        )
        return 2
    except subprocess.CalledProcessError as error:
        print(f"cannot continue: {' '.join(map(str, error.cmd))} exited {error.returncode}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as error:
        print(f"cannot continue: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; run apply again to resume where it stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
