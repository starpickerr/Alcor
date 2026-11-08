"""Tasks schema: base DDL lives in schema.sql; legacy columns are added in place."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from business.categories import ensure_required_categories
from business.object_generations import ensure_object_generations

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Columns added after the first release; existing databases receive them via ALTER.
_LATE_COLUMNS = (
    ("repeat_interval", "INTEGER NOT NULL DEFAULT 1"),
    ("planned_on", "TEXT"), ("list_name", "TEXT NOT NULL DEFAULT ''"),
    ("tags", "TEXT NOT NULL DEFAULT '[]'"), ("flagged", "INTEGER NOT NULL DEFAULT 0"),
    ("checklist", "TEXT NOT NULL DEFAULT '[]'"),
    ("status", "TEXT NOT NULL DEFAULT 'todo'"), ("parent_id", "TEXT"),
    ("sort_order", "INTEGER NOT NULL DEFAULT 0"), ("remind_at", "TEXT"),
    ("reminder_delivery", "TEXT"), ("category_id", "TEXT"),
)

REQUIRED_TABLES = frozenset({
    "todos", "todo_completions", "todo_checklist_completions", "todo_dependencies",
    "todo_notifications", "reminder_occurrences", "reminder_deliveries",
    "reminder_route_deliveries", "todo_migrations", "briefing_applied",
    "source_tombstones", "todo_scheduler_links", "todo_categories",
})


def load_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def ensure_schema(db: sqlite3.Connection) -> None:
    """Idempotently create or upgrade the Tasks schema on an open connection."""
    db.executescript(load_schema_sql())
    db.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in db.execute("PRAGMA table_info(todos)")}
    for name, definition in _LATE_COLUMNS:
        if name not in columns:
            db.execute(f"ALTER TABLE todos ADD COLUMN {name} {definition}")
    missing = 'category_id IS NULL OR category_id NOT IN (SELECT id FROM todo_categories)'
    for (name,) in db.execute(f'SELECT DISTINCT list_name FROM todos WHERE ({missing}) AND length(list_name) BETWEEN 1 AND 200').fetchall():
        db.execute("INSERT OR IGNORE INTO todo_categories VALUES (?,?,?,datetime('now'))",(str(uuid.uuid4()),name,'#6B8AF7'))
    db.execute(f"""UPDATE todos SET category_id=(SELECT id FROM todo_categories WHERE name=todos.list_name)
        WHERE ({missing}) AND length(list_name) BETWEEN 1 AND 200""")
    db.execute("INSERT OR IGNORE INTO todo_migrations VALUES ('stable-task-categories')")
    ensure_required_categories(db, 'todo_categories', 'todos', '提醒事项')
    db.execute("""UPDATE todos SET list_name=(SELECT name FROM todo_categories WHERE id=todos.category_id)
        WHERE list_name!=(SELECT name FROM todo_categories WHERE id=todos.category_id)""")
    db.execute("CREATE INDEX IF NOT EXISTS todos_parent_order ON todos(parent_id,sort_order,created_at)")
    ensure_object_generations(db, 'todos', 'todo_categories')
    if "status" not in columns:
        db.execute("UPDATE todos SET status='completed' WHERE completed_at IS NOT NULL")


def missing_tables(db: sqlite3.Connection) -> set[str]:
    present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return set(REQUIRED_TABLES) - present
