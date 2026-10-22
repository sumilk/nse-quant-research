"""Command line: uv run lab <command> [--strategy ID]."""

from __future__ import annotations

import argparse

from strategylab.config import list_strategies


def main() -> None:
    ap = argparse.ArgumentParser(prog="lab", description="Strategy research lab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list strategies")
    f = sub.add_parser("fetch", help="download data needed by one or all strategies")
    f.add_argument("--strategy", action="append", help="repeatable; default: all strategies")
    f.add_argument("--refresh", action="store_true", help="re-download files already cached")
    for name, help_ in [("research", "development-period gates and report"),
                        ("current", "ranking on the latest close"),
                        ("holdout", "spend the holdout (once)")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--strategy", required=True, choices=list_strategies())
        if name == "holdout":
            p.add_argument("--i-have-not-changed-anything", action="store_true", dest="confirm")
    args = ap.parse_args()

    if args.cmd == "list":
        print("\n".join(list_strategies()))
    elif args.cmd == "fetch":
        from strategylab.data.fetch import fetch
        fetch(args.strategy, refresh=args.refresh)
    elif args.cmd == "research":
        from strategylab.evaluation.research import main as research
        research(args.strategy)
    elif args.cmd == "current":
        from strategylab.evaluation.current import main as current
        current(args.strategy)
    elif args.cmd == "holdout":
        from strategylab.evaluation.holdout import main as holdout
        holdout(args.strategy, args.confirm)


if __name__ == "__main__":
    main()
