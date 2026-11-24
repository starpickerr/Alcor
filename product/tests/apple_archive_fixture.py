"""Inspect deletion evidence in synthetic owner databases."""


def deletion_snapshots(owner):
    with owner._connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_inbound_recovery'").fetchone():
            return []
        return [dict(row) for row in db.execute(
            'SELECT id, entity, target_id, status FROM apple_inbound_recovery ORDER BY rowid'
        )]
