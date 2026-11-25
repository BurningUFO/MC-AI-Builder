from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.architect.codex_budget import TOKEN_RESERVATION, CodexBudget
from src.architect.codex_provider import CodexProvider
from src.architect.codex_sdk import sdk_config
from src.architect.providers import ModelBudgetExhausted
from tools import mcai_launcher


class CodexLauncherTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = patch.multiple(mcai_launcher, HOME=self.root / "launcher", SETUP=self.root / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.launcher = mcai_launcher.Launcher("")

    def test_login_runtime_and_sdk_use_the_same_independent_profile(self):
        developer = self.root / "developer"
        developer.mkdir()
        global_instructions = developer / "AGENTS.md"
        global_instructions.write_text("Development instructions", encoding="utf-8")
        (developer / "config.toml").write_text('[mcp_servers.development]\ncommand = "dev-server"\n', encoding="utf-8")
        local = self.root / "local"
        sdk = ModuleType("openai_codex")
        sdk.CodexConfig = SimpleNamespace
        self.launcher.game_dir = self.root / "game"
        self.launcher.environ = {"instance": self.root / "forge"}
        with (patch.dict(os.environ, {"CODEX_HOME": str(developer), "LOCALAPPDATA": str(local)}),
              patch.dict(sys.modules, {"openai_codex": sdk}),
              patch("src.architect.codex_sdk.ROOT", self.root / "repository"),
              patch.object(mcai_launcher.subprocess, "CREATE_NO_WINDOW", 0, create=True),
              patch.object(mcai_launcher.subprocess, "Popen") as spawn):
            self.launcher.check_login()
            self.launcher.start_agent(0)
            config = sdk_config(self.root)

        isolated = (local / "MC-AI-Builder" / "codex-home").resolve()
        for child in spawn.call_args_list:
            self.assertEqual(Path(child.kwargs["env"]["CODEX_HOME"]).resolve(), isolated)
        self.assertEqual(Path(config.env["CODEX_HOME"]), isolated)
        self.assertEqual(global_instructions.read_text(encoding="utf-8"), "Development instructions")
        self.assertFalse((isolated / "AGENTS.md").exists())
        self.assertNotIn("mcp_servers.development.enabled=false", config.config_overrides)
        arguments = spawn.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--provider") + 1], "codex")
        self.assertEqual(arguments[arguments.index("--codex-max-turns") + 1], "200")
        self.assertEqual(arguments[arguments.index("--codex-max-tokens") + 1], "5000000")
        self.assertEqual(arguments[arguments.index("--codex-effort") + 1], "medium")

    def test_restart_retains_exhausted_ledger_and_new_task_uses_a_fresh_one(self):
        original = self.launcher.budget
        CodexBudget(original, 2, TOKEN_RESERVATION).reserve({"status": "reserved"})

        restarted = mcai_launcher.Launcher("")
        self.assertEqual(restarted.budget, original)
        with self.assertRaises(ModelBudgetExhausted):
            CodexBudget(restarted.budget, 2, TOKEN_RESERVATION).reserve({})

        restarted.new_task_budget()
        fresh = restarted.budget
        self.assertNotEqual(fresh, original)
        self.assertEqual(CodexBudget(fresh, 2, TOKEN_RESERVATION).records(), [])
        CodexBudget(fresh, 2, TOKEN_RESERVATION).reserve({"status": "reserved"})
        self.assertEqual(mcai_launcher.Launcher("").budget, fresh)
        self.assertEqual(len(CodexBudget(fresh, 2, TOKEN_RESERVATION).records()), 1)
        self.assertEqual(len(CodexBudget(original, 2, TOKEN_RESERVATION).records()), 1)

    def test_agent_keeps_old_ledger_until_a_clean_budget_switch(self):
        old = self.launcher.budget
        self.launcher.want_agent = True
        self.launcher.agent = Mock()
        self.launcher.bridge = Mock(health=Mock(return_value={"execution": {"state": "paused"}}))
        with self.assertRaises(ValueError):
            self.launcher.new_task_budget()
        self.assertFalse(self.launcher.stop_agent.exists())
        self.launcher.bridge.health.return_value = {"execution": {"state": "completed"}}
        self.launcher.new_task_budget()
        self.assertTrue(self.launcher.stop_agent.exists())
        self.assertEqual(self.launcher.budget, old)
        self.launcher.agent.poll.return_value = 0
        with patch.object(self.launcher, "update_connections"):
            self.launcher.update()
        self.assertTrue(self.launcher.want_agent)
        self.assertIsNone(self.launcher.agent)
        self.assertNotEqual(self.launcher.budget, old)

    def test_larger_defaults_continue_a_ledger_at_the_previous_turn_limit(self):
        path = self.launcher.budget
        previous = CodexBudget(path, 100, 3_000_000)
        for _ in range(100):
            previous.reserve({"status": "reserved"})
        history = previous.records()
        with self.assertRaises(ModelBudgetExhausted):
            previous.reserve({})
        with patch("src.architect.codex_provider.find_spec", return_value=object()):
            provider = CodexProvider(budget_log=mcai_launcher.Launcher("").budget)
            configured = CodexProvider(max_turns=240, max_tokens=6_000_000)
        self.assertEqual(configured.max_turns, 240)
        self.assertEqual(provider.max_turns, 200)
        self.assertEqual(provider.budget.max_tokens, 5_000_000)
        self.assertEqual(provider.budget.reserve({"status": "reserved"}), 101)
        self.assertEqual(provider.budget.records()[:100], history)
