"""Run the bounded Codex construction case on a prepared synthetic BMC4 world copy."""

from argparse import ArgumentParser
from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import Event, Thread

from src.architect.codex_provider import CodexProvider
from src.architect.design import DesignError
from src.architect.providers import FixtureProvider, ModelBudgetExhausted
from src.architect.runtime import ArchitectClient, Runtime, SessionStore
from src.construction.client import BridgeError
from src.core.building import write_json
from tests.m1_smoke import require, wait_for
from tests.m5_b_smoke import fixture
from tests.m6_c_smoke import CopyGame, synthetic_copy
from tests.m6_d_smoke import agent
from tests.m7_a_smoke import HOME as M7A, checkpoint_after_reopen, loaded
from tests.m7_p_bench import HOME, PORT, game_for, run_case
from tools.mcai_launcher import SETUP, find_setup, read_object
from tools.world_copy import file_manifest


PRIMARY = ("/architect survey 请根据当前真实视角和已经批准的现场标记设计一栋7格宽9格深的单层住宅。"
           "保持原点不偏移，保持已批准的0度朝向。用云杉木板、石砖、蓝色染色玻璃，"
           "完整平屋顶，门洞在正面偏左，成对窗组，两件室内构件是长凳和桌台。"
           "选已核对且在原点的地基，当前画面足够，直接出图。")
HINT = "建议先从屋顶背侧铺起，入口附近仍需要采光和通行。请结合当前画面与地形，评估这两个可用工作组。"
REUSE = "/architect ask 请重用上一次真实勘察生成的住宅设计，保持原点、朝向和所有方块，再给我审批。"
RETRY_HINT = ("请优先安排屋顶背侧，留前侧给玩家通行。保留背侧天然一级高差。"
              "请用画面与已核对的 Forge 作业站位确认；有明确阻碍才保留前侧优先。")


class ScopedProvider(CodexProvider):
    """Apply the test's consent limits before any image or prompt leaves this process."""

    def __init__(self, *, prior_design=None, **options):
        self.prior_design = prior_design
        super().__init__(**options)

    def survey_with_metrics(self, payload, image, **flags):
        if payload["request"] != PRIMARY.split(" ", 2)[2]:
            raise DesignError("Only the approved synthetic cottage survey may be sent.")
        pads = [pad for pad in payload["cottage_foundations"]
                if pad["offset_from_mark"] == [0, 0, 0] and pad["rotation"] == 0
                and any(fit["width"] == 7 and fit["depth"] == 9 and "left" in fit["entries"] for fit in pad["sizes"])]
        if not pads:
            raise DesignError("The saved benchmark mark has no compatible foundation.")
        # Preserve the Bridge-issued option IDs; all design parameters still come from the model.
        payload = {**payload, "cottage_foundations": pads, "remaining_observations": 0, "movement_options": []}
        return super().survey_with_metrics(payload, image, **flags)

    def plan_with_metrics(self, payload, image=None, **flags):
        hint = RETRY_HINT if self.prior_design else HINT
        if payload.get("player_suggestion") != hint or image is None:
            raise DesignError("The real comparison accepts only the approved hint with a paired route frame.")
        if self.prior_design:
            payload = {**payload, "can_observe_again": False}
        return super().plan_with_metrics(payload, image, **flags)

    def _vision_decision(self, *args, **kwargs):
        if sum("image_sha256" in row for row in self.budget.records()) >= 4:
            raise ModelBudgetExhausted("The four-image M7-P authorization is exhausted.")
        return super()._vision_decision(*args, **kwargs)

    def decide(self, context):
        if self.prior_design and context["message"] == REUSE.split(" ", 2)[2]:
            return {"action": "create_design", "kind": self.prior_design["kind"],
                    "changes": self.prior_design["changes"], "origin": [0, 0, 0], "rotation": 0}
        raise DesignError("No unrelated test conversation may be sent to Codex.")


class LiveCopy(CopyGame):
    def runtime(self, provider):
        settings = self.settings
        return Runtime(self.client, provider, SessionStore(self.world / "architect-previews"),
                       settings["instance"], settings["blender"], share_observations=True, parallel_planning=True)


def check_source(game):
    record = synthetic_copy(game)
    manifest = read_object(game / "copy-integrity.json")
    require(file_manifest(Path(record["source_world"]), world=True) == manifest["world"]
            and file_manifest(Path(record["source_instance"]) / "mods") == manifest["mods"],
            "The synthetic source world and pack remained byte-identical")


def reject_strategy(run, job, decision, expected):
    plan = job["planning"]
    try:
        run.client.planning("resolve", job_id=job["job_id"], lease=plan["lease"],
                            group=decision, reason="验收请求")
    except BridgeError as error:
        require(error.status == 409 and error.body["error"] == expected, "The Bridge refused the untrusted strategy")
        return expected
    raise AssertionError("The Bridge accepted an invalid or expired strategy")


def run_live(game, site, label):
    require(not (HOME / f"{label}.json").exists(), "Use a virgin copy and keep prior acceptance records")
    prior = read_object(HOME / "C.json") if label != "C" else None
    if prior:
        require(prior["passed"] and prior["planning"]["state"] == "kept",
                "The replay uses the exact completed survey design from the first Codex comparison")
        design = prior["survey"]["decisions"][-1]
    else:
        design = None
    hint, prompt = (RETRY_HINT, REUSE) if design else (HINT, PRIMARY)
    provider = ScopedProvider(prior_design=design, model="gpt-6.2-sol", effort="medium", timeout=40, max_turns=5,
                              max_tokens=80_000, budget_log=HOME / "codex-turns.sqlite3")
    prior_calls = len(provider.budget.records())
    if sum("image_sha256" in row for row in provider.budget.records()) > (3 if design else 2):
        raise ValueError("The comparison needs its model images within the total four-image authorization.")
    denials = {}

    def inspect(job):
        if job["state"] != "running":
            return
        state = job.get("planning", {}).get("state")
        if state == "thinking" and "invalid_group" not in denials:
            denials["invalid_group"] = reject_strategy(run, job, "out_of_scope", "invalid_planning_group")
        if state == "applied" and "duplicate_result" not in denials:
            denials["duplicate_result"] = reject_strategy(run, job, "roof_rear", "stale_planning")

    with LiveCopy(game, PORT) as run:
        trace = run_case(run, label, site, provider, prompt, inspect=inspect, on_start=lambda active: active.chat.send(
            active.child, "/architect plan " + hint, "施工顺序建议已记录"))
        baseline, optimized = (read_object(HOME / f"{label}.json") for label in ("A", "B"))
        require(all(trace[key] == baseline[key] == optimized[key]
                    for key in ("model_sha256", "site_block_digest", "stock")), "A, B and C share the exact input conditions")
        plan = trace["planning"]
        require(plan["state"] == "applied" and plan["group"] == "roof_rear",
                "A real Codex strategy changed the authorized roof order")
        require(plan["observation_id"], "The asynchronous turn used a paired on-route NPC view")
        roof = plan["first_step"]
        first = trace["actions"][roof]["local"]
        require(first == plan["first_local"] and first != optimized["actions"][roof]["local"],
                "The native action order changed compared with the deterministic build")
        overlap = [step for step in trace["actions"] if step["status"] == "placed"
                   and plan["model_start_ms"] < step["profile_ms"] <= plan["resolved_ms"]]
        require(len(overlap) == plan["eligible_actions_during_thinking"] and len(overlap) >= 2,
                "Native placement actions continued while Codex was thinking")
        require(len(denials) == 2, "The native Bridge rejected invalid and duplicate planning results")
        trace["planner_overlap"] = {
            "interval_ms": plan["resolved_ms"] - plan["model_start_ms"],
            "observed_action_span_ms": overlap[-1]["profile_ms"] - overlap[0]["profile_ms"],
            "successful_placements": len(overlap), "first_step": overlap[0]["step"], "last_step": overlap[-1]["step"],
            "deterministic_first_roof": optimized["actions"][roof]["local"], "model_first_roof": first,
        }
        trace["planner_denials"] = denials
        trace["native_model_usage"] = provider.budget.records()
        trace["turns_in_this_copy"] = len(trace["native_model_usage"]) - prior_calls
        trace["reused_survey_design"] = bool(design)
        require(sum("image_sha256" in row for row in trace["native_model_usage"]) <= 4,
                "Real use stays in the four-image consent range")
        require(len(trace["native_model_usage"]) <= 5
                and sum(row["charged_tokens"] for row in trace["native_model_usage"]) <= 80_000,
                "The real run stays in the Codex budget")
        terminal = run.bridge.progress()
        write_json(HOME / f"{label}-checkpoint.json", {
            "snapshot": run.snapshot(site), "job": terminal["job"], "npc": terminal["npc"],
            "materials": terminal["materials"],
        })
    check_source(game)
    trace.update(native_save=True, source_unchanged=True)
    write_json(HOME / f"{label}.json", trace)


@contextmanager
def runtime_during_reopen(game):
    settings = find_setup(read_object(SETUP))
    client = ArchitectClient(game / "config" / "mcaibuilder-architect.json", timeout=4)
    store = SessionStore(game / "architect-previews")
    runtime = Runtime(client, FixtureProvider(), store, settings["instance"], settings["blender"])
    stop = Event()
    thread = Thread(target=runtime.run, args=(stop.is_set,), daemon=True)
    thread.start()
    try:
        yield runtime.trace
    finally:
        stop.set()
        thread.join(timeout=20)
        require(not thread.is_alive(), "The observer Runtime released its worker after reconnect")


def reopen(game, site, label):
    saved = read_object(HOME / f"{label}-checkpoint.json")
    trace = read_object(HOME / f"{label}.json")
    require(trace.get("native_save") and saved.get("job"), "Finish the real C build and native save before reopening")
    with runtime_during_reopen(game) as observer, CopyGame(game, PORT) as run:
        wait_for(run.child, lambda: run.bridge.health()["architect_runtime_online"],
                 "the same Runtime to reconnect to the actual restarted server", seconds=30)
        require(loaded(run, site) == saved["snapshot"], "All monitored block states survived native reopen")
        trace["recovery"] = checkpoint_after_reopen(run)
        progress = run.bridge.progress()
        require(all(progress["job"][key] == saved["job"][key] for key in
                    ("job_id", "building_version", "state", "processed", "actions", "supply", "verification", "planning")),
                "The final journal, materials, and applied strategy survived native reopen")
        require(progress["npc"]["npc_id"] == saved["npc"]["npc_id"]
                and progress["materials"] == saved["materials"], "The same NPC and exact stock survived native reopen")
        trace["planner_denials"]["after_reopen"] = reject_strategy(run, progress["job"], "roof_rear", "stale_planning")
        run.chat.submit(run.child, "/gamemode spectator")
        run.chat.submit(run.child, "/execute in minecraft:the_nether run tp @s 0 110 0")
        wait_for(run.child, lambda: run.bridge.progress().get("recovery", {}).get("waiting") == "site_not_loaded",
                 "the synthetic Overworld site to unload", seconds=75)
        require(run.bridge.progress()["job"]["actions"] == saved["job"]["actions"],
                "An unloaded site executed no construction")
        trace["planner_denials"]["unloaded"] = reject_strategy(run, progress["job"], "roof_rear", "stale_planning")
        fixture(run.child, "field", position=site["player"])
        checkpoint_after_reopen(run)
        require(run.snapshot(site) == saved["snapshot"], "Returning from the unloaded dimension preserved the site")
        trace["chunk_unload_recovered"] = True
    logs = [json.loads(line) for line in observer.path.read_text(encoding="utf-8").splitlines()]
    signals = [row.get("code", row["event"]) for row in logs if row["run"] == observer.run_id]
    require("bridge_unavailable" in signals and "bridge_restored" in signals
            and signals.index("bridge_unavailable") < signals.index("bridge_restored"),
            "A real Bridge disconnect and reconnection were recorded by the same Runtime")
    check_source(game)
    trace["bridge_reconnected"] = True
    trace["native_reopen"] = True
    write_json(HOME / f"{label}.json", trace)
    print("Native reopen retained the same complete house, NPC, chest, cargo, journal and applied strategy.", flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("build", "reopen"))
    parser.add_argument("--label", choices=("C", "C2", "C3"), default="C")
    parser.add_argument("--site", type=Path, default=M7A / "selected-site.json")
    argv = parser.parse_args()
    os.environ["MC_AI_BUILDER_PROFILE"] = "1"
    os.environ["MC_AI_BUILDER_LEGACY_EXECUTOR"] = "0"
    source = Path(read_object(HOME / "prepared-source.json")["source"]).resolve(strict=True)
    game = game_for(argv.label, source)
    (run_live if argv.phase == "build" else reopen)(game, read_object(argv.site), argv.label)


if __name__ == "__main__":
    main()
