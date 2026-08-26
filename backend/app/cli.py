from __future__ import annotations

import argparse
import sys

from app.adapters.registry import adapter_registry
from app.adapters.runner import FrameworkRunError
from app.runs.repository import ArtifactIntegrityError, RunNotFoundError, RunRepository
from app.sdk.loader import StrategyLoadError
from app.sdk.registry import StrategyRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vqd", description="Visual Quant Debugger local CLI")
    groups = parser.add_subparsers(dest="group", required=True)
    strategy = groups.add_parser("strategy", help="Manage trusted local Python strategies")
    actions = strategy.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="Register a local Python strategy")
    add.add_argument("path")
    add.add_argument("--class", dest="class_name")
    add.add_argument("--framework", choices=("backtesting.py", "vectorbt"))
    add.add_argument("--entrypoint")
    actions.add_parser("list", help="List registered strategies")
    remove = actions.add_parser("remove", help="Remove a strategy registration")
    remove.add_argument("strategy_id")
    run = groups.add_parser("run", help="Inspect persistent research runs")
    run_actions = run.add_subparsers(dest="action", required=True)
    run_actions.add_parser("list", help="List research runs newest first")
    show = run_actions.add_parser("show", help="Show a run manifest and annotations")
    show.add_argument("run_id")
    delete = run_actions.add_parser("delete", help="Delete a run and its artifacts")
    delete.add_argument("run_id")
    delete.add_argument("--force", action="store_true", help="Confirm permanent deletion")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.group == "run":
            repository = RunRepository()
            if args.action == "list":
                page = repository.list_runs(limit=200)
                if not page.items:
                    print("No research runs recorded.")
                for item in page.items:
                    print(
                        f"{item.run_id}\t{item.status}\t{item.created_at.isoformat()}\t"
                        f"{item.strategy_name}\t{item.dataset_name}"
                    )
            elif args.action == "show":
                detail = repository.detail(args.run_id)
                print(detail.model_dump_json(indent=2))
            elif not args.force:
                print("Run deletion requires --force.", file=sys.stderr)
                return 2
            else:
                repository.delete(args.run_id)
                print(f"Deleted {args.run_id} and its run artifacts.")
            return 0
        registry = StrategyRegistry()
        if args.action == "add":
            registration = registry.add(
                args.path,
                args.class_name,
                framework=args.framework,
                entrypoint=args.entrypoint,
            )
            print(
                f"Registered {registration.strategy_id} ({registration.class_name})\n"
                f"source: {registration.source_path}\n"
                f"fingerprint: {registration.source_fingerprint}\n"
                f"runtime: {registration.runtime_kind}\n"
                f"framework: {registration.framework_name or '-'}"
            )
        elif args.action == "list":
            registrations = registry.list()
            if not registrations:
                print("No user strategies registered.")
            for registration in registrations:
                installed = (
                    True
                    if registration.adapter_id is None
                    else adapter_registry.installed_version(registration.adapter_id) is not None
                )
                print(
                    f"{registration.strategy_id}\t{registration.runtime_kind.upper()}\t"
                    f"{registration.framework_name or 'VQD'}\t"
                    f"{'AVAILABLE' if installed else 'UNAVAILABLE'}\t"
                    f"{registration.class_name}\t{registration.source_path}\t"
                    f"{registration.source_fingerprint}"
                )
        else:
            removed = registry.remove(args.strategy_id)
            print(f"Removed {removed.strategy_id} ({removed.source_path})")
    except StrategyLoadError as exc:
        print(str(exc), file=sys.stderr)
        if exc.traceback:
            print(exc.traceback, file=sys.stderr)
        return 2
    except FrameworkRunError as exc:
        print(str(exc), file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        if exc.traceback:
            print(exc.traceback, file=sys.stderr)
        return 2
    except (ArtifactIntegrityError, RunNotFoundError, KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
