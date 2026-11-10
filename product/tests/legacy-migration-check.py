"""Synthetic migration regression: retained deletions/current edits, rollback and bill totals."""
from pathlib import Path
from contextlib import closing
import importlib.util
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('migration', ROOT / 'scripts/product/migrate-legacy.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

with tempfile.TemporaryDirectory() as temporary:
    source, target = [Path(temporary)/name for name in ('old.db','native.db')]
    schema = (ROOT/'packages/alcor-secretary/business/calendar/domain/schema.sql').read_text()
    for path in (source,target):
        with closing(sqlite3.connect(path)) as db, db: db.executescript(schema)
    def event(db, identifier, title, source_type=None, source_key=None):
        db.execute('INSERT INTO calendar_events(id,title,start_at,end_at,source_type,source_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',(identifier,title,'2026-01-01T01:00:00Z','2026-01-01T02:00:00Z',source_type,source_key,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z'))
    with closing(sqlite3.connect(source)) as db, db:
        event(db,'same','Old edit');event(db,'new','New old event')
        event(db,'apple','Deleted holiday','apple_calendar','apple-key')
        event(db,'deleted','Deleted by user','briefing','deleted-key')
    with closing(sqlite3.connect(target)) as db, db:
        event(db,'same','Current edit')
        db.execute("INSERT INTO source_tombstones VALUES ('deleted-key','2026-01-01')")
    result=migration.merge_database(source,target,'calendar')
    assert result['calendar_events:inserted']==1 and result['calendar_events:current_apple_owner']==1 and result['calendar_events:kept_deletion']==1
    with closing(sqlite3.connect(target)) as db, db:
        assert db.execute('select count(*) from calendar_events').fetchone()[0]==2
        assert db.execute("select title from calendar_events where id='same'").fetchone()[0]=='Current edit'
    assert migration.merge_database(source,target,'calendar').get('calendar_events:inserted',0)==0
    # An unexpected unique collision aborts the complete transaction.
    with closing(sqlite3.connect(source)) as db, db:
        event(db,'first','Must roll back')
        db.execute('PRAGMA ignore_check_constraints=ON')
        db.execute("INSERT INTO agenda_handoffs(source_type,source_key,destination,title,briefing_id,briefing_item_id,updated_at) VALUES ('briefing','bad','invalid','Test','b','i','2026-01-01')")
    try:migration.merge_database(source,target,'calendar')
    except sqlite3.IntegrityError:pass
    else:raise AssertionError('Collision must fail')
    with closing(sqlite3.connect(target)) as db, db:assert not db.execute("select 1 from calendar_events where id='first'").fetchone()
print('PASS: synthetic migration preserves current edits and deletions, excludes old Apple records, is idempotent and rolls back collisions')

with tempfile.TemporaryDirectory() as temporary:
    source,target=[Path(temporary)/name for name in ('old.db','native.db')]
    schema=(ROOT/'packages/alcor-secretary/business/bills/domain/schema.sql').read_text()
    for path in (source,target):
        with closing(sqlite3.connect(path)) as db, db:db.executescript(schema)
    for path,identifiers in [(source,['old-a','old-b']),(target,['current'])]:
        with closing(sqlite3.connect(path)) as db, db:
            for identifier in identifiers:
                db.execute("INSERT INTO financial_events(id,event_type,amount_minor,name,created_at,updated_at) VALUES (?,'ledger_entry',105,'Same purchase','2026-01-01','2026-01-01')",(identifier,))
                db.execute("INSERT INTO ledger_entries(event_id,entry_type,occurred_at) VALUES (?,'expense','2026-01-01T00:00:00Z')",(identifier,))
    migration.merge_database(source,target,'bills')
    migration.refresh_statements(target)
    with closing(sqlite3.connect(target)) as db, db:
        assert db.execute('select sum(amount_minor) from financial_events').fetchone()[0]==315
        assert db.execute('select expense_minor from period_statements where period_type=\'monthly\'').fetchone()[0]==315
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert migration.merge_database(source,target,'bills').get('financial_events:inserted',0)==0
print('PASS: bill migration keeps independent identical purchases and exact totals/relationships')
