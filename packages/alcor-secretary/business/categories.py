"""Required category storage shared by the calendar and task domains."""
import uuid
import json


def default_category(db, table, name, preferred=None):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_bindings'").fetchone():
        for row in db.execute("SELECT local_id,document FROM apple_bindings WHERE entity='category'"):
            binding = json.loads(row[1])
            if binding.get('default') and binding.get('can_modify') and db.execute(f'SELECT 1 FROM {table} WHERE id=?',(row[0],)).fetchone():
                return row[0]
    if preferred:
        row = db.execute(f'SELECT id FROM {table} WHERE id=?', (preferred,)).fetchone()
        if row:
            return row[0]
    db.execute(f"INSERT OR IGNORE INTO {table} VALUES (?,?,?,datetime('now'))",
               (str(uuid.uuid4()), name, '#6B8AF7'))
    return db.execute(f'SELECT id FROM {table} WHERE name=?', (name,)).fetchone()[0]


def require_category(identity):
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError('请选择有效分类或清单')
    return identity


def ensure_required_categories(db, table, records, name, preferred=None):
    missing = f'category_id IS NULL OR category_id NOT IN (SELECT id FROM {table})'
    if db.execute(f'SELECT 1 FROM {records} WHERE {missing} LIMIT 1').fetchone():
        identity = default_category(db, table, name, preferred)
        db.execute(f'UPDATE {records} SET category_id=? WHERE {missing}', (identity,))
    for action in ('INSERT', 'UPDATE OF category_id'):
        trigger = f'{records}_required_category_' + action.split()[0].lower()
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {action} ON {records}
            WHEN NEW.category_id IS NULL OR NEW.category_id NOT IN (SELECT id FROM {table})
            BEGIN SELECT RAISE(ABORT, 'category is required'); END""")
    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_require_empty_delete BEFORE DELETE ON {table}
        WHEN EXISTS (SELECT 1 FROM {records} WHERE category_id=OLD.id)
        BEGIN SELECT RAISE(ABORT, 'delete category contents through service'); END""")
