"""Inspect a paired NPC observation or explicitly share one selected frame with Codex."""

from argparse import ArgumentParser
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys

from src.architect.codex_provider import CodexProvider
from src.architect.observations import observation_image
from src.architect.providers import ModelError
from src.architect.runtime import ArchitectClient, scrub
from src.architect.scene import decode_scene, verify_scene, world_context
from src.construction.client import BridgeError
from src.core.building import write_json


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Observation:
    metadata: dict
    context: dict
    png: bytes

    @property
    def id(self):
        return self.metadata["observation_id"]


def read_observation(client, directory: Path, *, expected_id=None):
    metadata = client.observe_world()
    if expected_id is not None and metadata.get("observation_id") != expected_id:
        raise ValueError("The current observation is different from the selected one.")
    return Observation(metadata, world_context(metadata), observation_image(directory, metadata))


def analyze(observation, provider, *, allow_upload=False):
    authoritative = world_context(observation.metadata)
    report, metrics = provider.analyze_scene_with_metrics(
        observation.context, observation.png, observation_id=observation.id, allow_upload=allow_upload,
    )
    report = decode_scene(scrub(json.dumps(report, ensure_ascii=False)))
    return {
        "observation_id": observation.id, "source_image_sha256": observation.metadata["image_sha256"],
        "image_sha256": sha256(observation.png).hexdigest(), "scene": report,
        "forge_checks": verify_scene(report, authoritative), "model": metrics,
    }


def _observation_directory(config):
    instance = config.parent.parent
    test = ROOT / "mc-test"
    if (config.name != "mcaibuilder-architect.json" or config.parent.name != "config"
            or instance.parent not in (test, test / "world-copies")
            or not ((instance / ".mcaibuilder-test").is_file() or
                    (instance / ".mcaibuilder-world-copy.json").is_file())):
        raise ValueError("Select the Architect configuration of an isolated Minecraft instance.")
    return ROOT / ".cache" / "observations" / instance.name


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Scoped Architect configuration in an isolated instance")
    parser.add_argument("--share-image", metavar="OBSERVATION_ID",
                        help="Explicitly send this reviewed RGB observation and its relative context to Codex")
    options = parser.parse_args()
    try:
        config = options.config.resolve(strict=True)
        directory = _observation_directory(config)
        observation = read_observation(ArchitectClient(config), directory, expected_id=options.share_image)
        if options.share_image:
            provider = CodexProvider(timeout=90, max_turns=10, max_tokens=150_000,
                                     budget_log=ROOT / ".cache" / "m5-launcher" / "codex-turns.sqlite3")
            result = analyze(observation, provider, allow_upload=True)
            write_json(directory / "analysis-latest.json", result)
        else:
            result = {"observation_id": observation.id, "image_sha256": sha256(observation.png).hexdigest(),
                      "image_size": [observation.metadata["width"], observation.metadata["height"]],
                      "local_context": observation.context}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, BridgeError, ValueError, ModelError) as error:
        reason = str(error) if isinstance(error, (ValueError, BridgeError, ModelError)) else type(error).__name__
        print("Architect vision: " + scrub(reason), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
