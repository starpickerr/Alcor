"""Per-object incarnation markers, in the same transaction as business rows."""


def ensure_object_generations(db, items, categories):
    # Table names are fixed by the owner, never supplied by a request.
    if (items, categories) not in (('todos', 'todo_categories'), ('calendar_events', 'calendar_categories')):
        raise ValueError('invalid generation owner')
    db.execute('''CREATE TABLE IF NOT EXISTS alcor_object_generations (
        entity TEXT NOT NULL, object_id TEXT NOT NULL, generation TEXT NOT NULL,
        PRIMARY KEY(entity, object_id))''')
    for entity, table in (('item', items), ('category', categories)):
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS alcor_generation_{table}_insert
            AFTER INSERT ON {table} BEGIN
            INSERT OR REPLACE INTO alcor_object_generations(entity, object_id, generation)
            VALUES ('{entity}', NEW.id, lower(hex(randomblob(16)))); END''')
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS alcor_generation_{table}_delete
            AFTER DELETE ON {table} BEGIN
            DELETE FROM alcor_object_generations WHERE entity='{entity}' AND object_id=OLD.id; END''')
        db.execute(f'''INSERT OR IGNORE INTO alcor_object_generations(entity, object_id, generation)
            SELECT '{entity}', id, lower(hex(randomblob(16))) FROM {table}''')


def generations_for(db, entity, identities):
    result = []
    for identity in sorted(set(identities)):
        row = db.execute('SELECT generation FROM alcor_object_generations WHERE entity=? AND object_id=?',
                         (entity, identity)).fetchone()
        if not row or not isinstance(row[0], str) or len(row[0]) != 32 or any(c not in '0123456789abcdef' for c in row[0]):
            raise ValueError('object generation missing; review again')
        result.append([identity, row[0]])
    return result
