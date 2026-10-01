"""CLI entry point: python -m jobradar [--config config.toml] [--dry-run] [--no-notify]"""
import argparse
import os
import sys
import tomllib

from .pipeline import run, write_report
from .state import State


def load_env(path=".env"):
    env = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k in ("ANTHROPIC_API_KEY", "NTFY_TOPIC")})
    return env


def main(argv=None):
    p = argparse.ArgumentParser(prog="jobradar", description=__doc__)
    p.add_argument("--config", default="config.toml")
    p.add_argument("--dry-run", action="store_true", help="no notifications, no state or report written")
    p.add_argument("--no-notify", action="store_true", help="run normally but skip notifications")
    args = p.parse_args(argv)

    if not os.path.exists(args.config):
        sys.exit(f"Config not found: {args.config}. Copy config.example.toml to config.toml first.")
    with open(args.config, "rb") as f:
        config = tomllib.load(f)
    with open(config["profile"]["resume_path"], encoding="utf-8") as f:
        resume = f.read()

    env = load_env()
    state = State(config.get("state_path", "state/state.json"))

    report = run(config, env, state, resume, notify=not (args.dry_run or args.no_notify))

    print(f"fetched={report.fetched} relevant={report.relevant} new={report.new} "
          f"passed={len(report.passed)} blocked={len(report.blocked)} "
          f"alerts={len(report.alerts)} notified={report.notified} scoring={report.engine}")
    if report.suppressed:
        print("[info] first run: alerts suppressed so you are not flooded with the existing backlog")
    if not args.dry_run:
        state.save()
        print(f"report: {write_report(report)}")


if __name__ == "__main__":
    main()
