"""Export a Building and control the local Forge construction bridge."""

import argparse
import json
from pathlib import Path
import sys

from src.core.building import Building, write_json
from src.construction.client import BridgeClient, BridgeError
from src.construction.plan import export_demo, make_plan


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "mc-test" / "forge-client" / "config" / "mcaibuilder.json"


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = cli.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="write the wall model and its construction plan")
    demo.add_argument("--directory", type=Path, default=ROOT / "projects" / "m1_wall")
    demo.add_argument("--origin", type=int, nargs=3, default=(6, -60, 6))
    demo.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), default=0)
    export = commands.add_parser("export", help="convert an existing building.json")
    export.add_argument("building", type=Path)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--building-id", required=True)
    export.add_argument("--version", type=int, default=1)
    export.add_argument("--origin", type=int, nargs=3, required=True)
    export.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), default=0)
    commands.add_parser("status")
    progress = commands.add_parser("progress")
    progress.add_argument("--output", type=Path)
    submit = commands.add_parser("submit")
    submit.add_argument("plan", type=Path)
    for operation in ("pause", "resume", "cancel"):
        commands.add_parser(operation).add_argument("job_id")

    args = cli.parse_args()
    try:
        if args.command == "demo":
            plan = export_demo(args.directory, tuple(args.origin), args.rotation)
            print(f"{len(plan['actions'])} blocks -> {args.directory / 'construction-plan.json'}")
            return 0
        if args.command == "export":
            plan = make_plan(Building.read(args.building), building_id=args.building_id,
                             version=args.version, origin=tuple(args.origin), rotation=args.rotation)
            write_json(args.output, plan)
            print(f"{len(plan['actions'])} blocks -> {args.output}")
            return 0
        client = BridgeClient(args.config)
        if args.command == "status":
            result = client.status()
        elif args.command == "progress":
            result = client.progress()
            if args.output:
                write_json(args.output, result)
        elif args.command == "submit":
            result = client.submit(json.loads(args.plan.read_text(encoding="utf-8")))
        else:
            result = client.control(args.command, args.job_id)
        print(json.dumps(result, indent=2, ensure_ascii=True))
    except (OSError, ValueError, KeyError, TypeError, BridgeError) as error:
        print(f"Construction: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
