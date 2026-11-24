"""Persist Codex turn limits and scrubbed usage across runtime restarts."""

from contextlib import closing
import json
import re
import sqlite3

from src.architect.providers import ModelBudgetExhausted


DEFAULT_MAX_TURNS = 100
DEFAULT_MAX_TOKENS = 3_000_000
BUDGET_TASK_FILE = "codex-task.json"

# Account for a full attempted turn until SDK usage arrives. Unreported turns
# keep their reservation, including an interrupted runtime or connection.
TOKEN_RESERVATION = 24_000


def task_budget_log(directory):
    try:
        selected = json.loads((directory / BUDGET_TASK_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return directory / "codex-turns.sqlite3"
    task = selected.get("task") if isinstance(selected, dict) else None
    if not isinstance(task, str) or not re.fullmatch(r"[0-9a-f]{32}", task):
        raise ValueError("无效的 Codex 任务预算记录。")
    return directory / "codex-task-budgets" / f"{task}.sqlite3"


class CodexBudget:
    def __init__(self, path, max_turns, max_tokens):
        if max_tokens < TOKEN_RESERVATION:
            raise ValueError(f"Codex token budget must be at least {TOKEN_RESERVATION}.")
        self.path = path
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS turns (
                sequence INTEGER PRIMARY KEY, charged_tokens INTEGER NOT NULL, record TEXT NOT NULL
            )""")

    def reserve(self, record):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            count, charged = db.execute("SELECT COUNT(*), COALESCE(SUM(charged_tokens), 0) FROM turns").fetchone()
            if count >= self.max_turns or charged + TOKEN_RESERVATION > self.max_tokens:
                raise ModelBudgetExhausted("Codex turn or token budget exhausted")
            return db.execute("INSERT INTO turns(charged_tokens, record) VALUES (?, ?)",
                              (TOKEN_RESERVATION, json.dumps(record, ensure_ascii=False))).lastrowid

    def finish(self, sequence, record):
        charged = (record.get("tokens") or {}).get("total_tokens", TOKEN_RESERVATION)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE turns SET charged_tokens = ?, record = ? WHERE sequence = ?",
                       (charged, json.dumps(record, ensure_ascii=False), sequence))
            total = db.execute("SELECT SUM(charged_tokens) FROM turns").fetchone()[0]
            return total <= self.max_tokens

    def records(self):
        with closing(sqlite3.connect(self.path)) as db:
            return [{"sequence": seq, "charged_tokens": charged, **json.loads(record)}
                    for seq, charged, record in db.execute(
                        "SELECT sequence, charged_tokens, record FROM turns ORDER BY sequence")]
