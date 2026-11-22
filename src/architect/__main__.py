"""Run the standalone Minecraft Architect service."""

from argparse import ArgumentParser
from pathlib import Path
import sys

from src.architect.codex_provider import DEFAULT_EFFORT, DEFAULT_MODEL, REASONING_EFFORTS, CodexProvider
from src.architect.providers import FixtureProvider, HttpProvider
from src.architect.runtime import ArchitectClient, Runtime, SessionStore

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Scoped Architect loopback configuration")
    parser.add_argument("--instance", type=Path, required=True, help="PCL instance for local Minecraft assets")
    parser.add_argument("--blender", type=Path, default=ROOT / "tools/blender/blender.exe")
    parser.add_argument("--stop-file", type=Path, help="Exit gracefully when this local launcher marker appears")
    parser.add_argument("--provider", choices=("http", "fixture", "codex"), default="http")
    parser.add_argument("--timeout", type=float, default=15.0, help="Model connection and response timeout")
    parser.add_argument("--codex-model", default=DEFAULT_MODEL, help="Model for the Codex provider")
    parser.add_argument("--codex-effort", choices=REASONING_EFFORTS, default=DEFAULT_EFFORT)
    parser.add_argument("--codex-budget-log", type=Path, help="Shared local SQLite ledger for Codex turn and token limits")
    parser.add_argument("--codex-max-tokens", type=int, default=150_000, help="Token limit for the Codex budget ledger")
    parser.add_argument("--codex-max-turns", type=int, default=12, help="Maximum Codex turns per ledger, or per process without a ledger")
    parser.add_argument("--share-observations", action="store_true",
                        help="Permit explicit NPC surveys and supervised builds to send their local images to Codex")
    parser.add_argument("--planning", action="store_true",
                        help="Allow a bounded Codex strategy for the unstarted roof of each approved cottage")
    options = parser.parse_args()
    if (options.share_observations or options.planning) and options.provider != "codex":
        parser.error("--share-observations and --planning require the Codex provider")
    if (options.share_observations or options.planning) and options.codex_budget_log is None:
        options.codex_budget_log = ROOT / ".cache" / "m5-launcher" / "codex-turns.sqlite3"
    try:
        config = options.config.resolve(strict=True)
        client = ArchitectClient(config)
        if options.provider == "http":
            provider = HttpProvider.from_environment(options.timeout)
        elif options.provider == "codex":
            provider = CodexProvider(model=options.codex_model, effort=options.codex_effort,
                                     timeout=options.timeout, max_turns=options.codex_max_turns,
                                     budget_log=options.codex_budget_log, max_tokens=options.codex_max_tokens)
        else:
            provider = FixtureProvider()
        storage = SessionStore(config.parent.parent / "architect-previews")
        runtime = Runtime(client, provider, storage, options.instance.resolve(strict=True),
                          options.blender.resolve(strict=True), share_observations=options.share_observations,
                          parallel_planning=options.planning)
        print(f"Architect runtime ready ({options.provider})", flush=True)
        runtime.run(options.stop_file.is_file if options.stop_file else lambda: False)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"Architect startup: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
