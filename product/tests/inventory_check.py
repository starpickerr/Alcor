"""Synthetic offline copies only; no import of the legacy runtime."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/product/inventory-legacy.py'
spec = importlib.util.spec_from_file_location('inventory_legacy', SCRIPT)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class InventoryCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='stella-offline-inventory-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def run_inventory(self, selections=None):
        return inventory.inventory(self.root, selections or [('user', '.')], True)

    def database(self):
        path = self.root / 'todos/todos.db'
        path.parent.mkdir()
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE todos(id TEXT,title TEXT,completed_at TEXT); CREATE TABLE reminders(status TEXT COLLATE NOCASE, bucket TEXT);')
            db.executemany('INSERT INTO todos VALUES (?,?,?)', [('private-id', 'private-body', None), ('private-id-2', 'private-body', '2026-11-04')])
            db.executemany('INSERT INTO reminders VALUES (?,?)', [('pending', 'a'), ('pending', 'b'), ('unknown', 'c'), ('private-secret-state', 'd'), (None, 'e'), ('PENDING', 'f')])
            db.execute('CREATE TABLE "private-table-name"(secret TEXT DEFAULT "private-schema-secret")')
        return path

    def test_counts_redaction_and_source_preservation(self):
        path = self.database()
        before = path.read_bytes()
        (self.root / '.env').write_text('PRIVATE_SECRET=must-not-read')
        memory = self.root / 'plugins/memory'
        memory.mkdir(parents=True)
        (memory / 'private-person-name.md').write_text('private-memory-body')
        result = self.run_inventory([('user', '.'), ('shared', '.')])
        assets = result['scopes'][0]['assets']
        tables = {item['table_sha256']: item for item in assets['todos/todos.db']['tables']}
        self.assertEqual(tables[hashlib.sha256(b'todos').hexdigest()]['completion_counts'], {'open': 1, 'completed': 1})
        self.assertEqual(tables[hashlib.sha256(b'reminders').hexdigest()]['status_counts'], {'pending': 2, 'unknown': 1, '<other>': 2, '<null>': 1})
        self.assertEqual(assets['plugins/memory']['files'], 1)
        self.assertEqual(result['scopes'][1]['assets']['.env'], {'present': True})
        self.assertFalse(result['migration_ready'])
        self.assertNotIn('private-', json.dumps(result))
        self.assertNotIn('must-not-read', json.dumps(result))
        command = subprocess.run([sys.executable, str(SCRIPT), '--snapshot', str(self.root), '--user-root', '.', '--shared-root', '.', '--confirm-offline-copy'], capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertEqual(json.loads(command.stdout), result)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_missing_assets_do_not_create_files(self):
        self.assertFalse(self.run_inventory()['scopes'][0]['assets']['todos/todos.db']['present'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_offline_confirmation_and_explicit_roots(self):
        for selections, confirmed in [([('user', '.')], False), ([('user', '../elsewhere')], True), ([('user', str(self.root))], True), ([], True), ([('user', '.'), ('user', '.')], True)]:
            with self.assertRaises(inventory.InventoryError):
                inventory.inventory(self.root, selections, confirmed)

    def test_links_and_sidecars_are_rejected(self):
        path = self.database()
        sidecar = Path(str(path) + '-wal')
        sidecar.write_bytes(b'')
        with self.assertRaisesRegex(inventory.InventoryError, 'sidecars'):
            self.run_inventory()
        sidecar.unlink()
        (self.root / 'plugins').mkdir()
        (self.root / 'plugins/memory').symlink_to(path.parent, target_is_directory=True)
        with self.assertRaisesRegex(inventory.InventoryError, 'symbolic link'):
            self.run_inventory()
        (self.root / 'plugins/memory').unlink()
        os.link(path, self.root / 'linked.db')
        with self.assertRaisesRegex(inventory.InventoryError, 'hard link'):
            self.run_inventory()

    def test_cli_failure_never_prints_partial_report_or_private_path(self):
        path = self.database()
        path.write_bytes(b'private-corrupt-database-content')
        result = subprocess.run([sys.executable, str(SCRIPT), '--snapshot', str(self.root), '--user-root', '.', '--confirm-offline-copy'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertNotIn(str(self.root), result.stderr)
        self.assertNotIn('private-', result.stderr)

    def test_virtual_tables_and_views_are_not_queried(self):
        path = self.database()
        with sqlite3.connect(path) as db:
            db.executescript('CREATE VIRTUAL TABLE search USING fts5(body); CREATE VIEW private_view AS SELECT * FROM todos;')
            db.execute('INSERT INTO search VALUES (?)', ('private-index-body',))
        summary = self.run_inventory()['scopes'][0]['assets']['todos/todos.db']
        self.assertGreater(summary['excluded_virtual_or_shadow_tables'], 0)
        self.assertEqual(len(summary['tables']), 3)


if __name__ == '__main__':
    unittest.main()
