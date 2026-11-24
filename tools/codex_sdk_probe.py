"""Check the official SDK and perform a one-turn synthetic intent smoke test."""

from argparse import ArgumentParser
from importlib.metadata import version
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from urllib.parse import urlsplit
import webbrowser

from src.architect.codex_sdk import (
    WORK_ROOT, auth_summary, inspect_config, rate_limits_summary, require_safe_config, safe_failure,
    safety_options, sdk_config, usage_summary,
)

PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["window_wall"]},
        "width": {"type": "integer"},
        "wood": {"type": "string"},
    },
    "required": ["kind", "width", "wood"],
    "additionalProperties": False,
}
PROMPT = ("Interpret this synthetic Minecraft design as JSON. Return kind, width, and the Minecraft "
          "wood block ID only. Build an eight-block-wide window wall using spruce planks.")


def write_report(path: Path, report: dict):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)



def provider_probe(report):
    from dataclasses import asdict

    from src.architect.codex_provider import CodexProvider
    from src.architect.design import Wall, placement
    from src.construction.plan import make_plan

    wall = Wall()
    origin = [6, -60, 6]
    prior = {"kind": "window_wall", "version": 1, "parameters": asdict(wall), "origin": origin, "rotation": 0}
    context = {
        "message": "把上次设计的窗户换成原版蓝色染色玻璃。墙的尺寸、结构和木材都保留。",
        "suggested_origin": origin, "facing": "north",
        "recent": [{"message": "建一面 8 格宽的中世纪云杉窗墙。", "action": "create_design"}],
        "design": prior,
    }

    provider = CodexProvider(timeout=45.0, max_turns=1)
    start = perf_counter()
    try:
        intent, metrics = provider.decide_with_metrics(context)
        report.update(metrics)
        report["intent"] = intent
        if intent["action"] != "revise_design" or intent["base_version"] != prior["version"]:
            raise ValueError("Codex did not reference the existing building version")
        edited = Wall.edit(intent["changes"], wall)
        model = edited.building()
        source = wall.building()
        changes = {point for point in model.blocks.keys() | source.blocks.keys()
                   if model.blocks.get(point) != source.blocks.get(point)}
        if (len(model.blocks) != 30 or changes != {(x, 1, 0) for x in (1, 2, 5, 6)}
                or any(model.blocks[point].state.block_id != "minecraft:blue_stained_glass" for point in changes)):
            raise ValueError("The candidate did not selectively change the four windows")
        model_origin, rotation = placement(intent, context, prior, locked=True)
        plan = make_plan(model, building_id="codex_probe_wall", version=2, origin=model_origin, rotation=rotation)
        model.write(WORK_ROOT / "provider-candidate-building.json")
        report["building"] = {"blocks": len(model.blocks), "changed_blocks": len(changes),
                              "model_sha256": plan["model_sha256"], "changed_positions": sorted(changes)}
        report["passed"] = True
    finally:
        report["model_turns"] = provider.turns_started
        report["total_elapsed_ms"] = round((perf_counter() - start) * 1000)

def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("status", "login", "sdk", "provider"), default="status", nargs="?")
    args = parser.parse_args()
    from openai_codex import Codex, Thread

    from src.architect.codex_provider import CodexProvider

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": args.mode,
        "sdk_version": version("openai-codex"),
        "runtime_package_version": version("openai-codex-cli-bin"),
        "model_turns": 0, "passed": False,
    }
    if args.mode == "provider":
        try:
            provider_probe(report)
        except Exception as error:
            report["error"] = safe_failure(error)
        write_report(WORK_ROOT / "provider.json", report)
        return 0 if report["passed"] else 1
    try:
        with TemporaryDirectory(prefix="isolated-", dir=WORK_ROOT) as folder:
            workspace = Path(folder)
            sdk = sdk_config(workspace)
            report["codex_home"] = sdk.env["CODEX_HOME"]
            with Codex(sdk) as codex:
                account = auth_summary(codex.account(refresh_token=False))
                report["account"] = account
                config = inspect_config(codex._client, workspace)
                report["tools"] = config
                require_safe_config(config)
                if args.mode == "login" and account["mode"] != "chatgpt":
                    login = codex.login_chatgpt()
                    endpoint = urlsplit(login.auth_url)
                    if endpoint.scheme != "https" or endpoint.hostname != "auth.openai.com":
                        login.cancel()
                        raise RuntimeError("Unexpected ChatGPT login endpoint")
                    if not webbrowser.open(login.auth_url):
                        login.cancel()
                        raise RuntimeError("The official ChatGPT sign-in page could not be opened")
                    outcome = login.wait()
                    report["login_completed"] = outcome.success
                    account = auth_summary(codex.account(refresh_token=False))
                    report["account"] = account
                if account["mode"] != "chatgpt":
                    report["error"] = {"kind": "chatgpt_login_required"}
                else:
                    provider = CodexProvider(max_turns=1)
                    thread_params = provider.thread_params(workspace, "Interpret synthetic building text. Use no tools.")
                    started = codex._client.thread_start(thread_params)
                    provider.verify_thread(started, report)
                    if args.mode in ("status", "login"):
                        report["passed"] = True
                        write_report(WORK_ROOT / f"{args.mode}.json", report)
                        return 0
                    try:
                        report["quota_before"] = rate_limits_summary(codex._client)
                    except Exception as error:
                        report["quota_unavailable"] = safe_failure(error)
                    if any(bucket.get("used_percent", 0) >= 100 for bucket in report.get("quota_before", {}).values()):
                        raise RuntimeError("The ChatGPT usage limit has already been reached")
                    options = safety_options()
                    thread = Thread(codex._client, started.thread.id)
                    report["model_turns"] = 1
                    (WORK_ROOT / "sdk.json").write_text(json.dumps(report) + "\n", encoding="utf-8")
                    started = perf_counter()
                    turn = thread.run(PROMPT, effort=provider.effort, output_schema=PROBE_SCHEMA,
                                      turn_service_tier="default", **options)
                    report["elapsed_ms"] = round((perf_counter() - started) * 1000)
                    report["sdk_duration_ms"] = turn.duration_ms
                    report["status"] = turn.status.value if hasattr(turn.status, "value") else turn.status
                    report["tokens"] = usage_summary(turn)
                    if turn.error:
                        report["error"] = safe_failure(turn.error)
                    elif turn.final_response:
                        data = json.loads(turn.final_response)
                        expected = {"kind": "window_wall", "width": 8, "wood": "minecraft:spruce_planks"}
                        if data == expected:
                            report["intent"] = data
                            report["passed"] = report["status"] == "completed"
                        else:
                            report["error"] = {"kind": "unexpected_intent"}
                    else:
                        report["error"] = {"kind": "missing_final_response"}
    except Exception as error:
        report["error"] = safe_failure(error)
    write_report(WORK_ROOT / f"{args.mode}.json", report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
