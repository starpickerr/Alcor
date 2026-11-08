"""Existing missing-local-category repair, with one owner commit and common claims."""
import json
from apple_sync.group_scan_claims import WorkClaim,digest


def restore(sync,owner,kind,link,document,expected,mappings):
    if expected is None:raise ValueError('旧分类成员的首次快照不可核验，已保留当前归属待核对')
    binding=expected.get('category_binding') or {};source=binding.get('source_id')
    if binding.get('apple_id')!=link['apple_id'] or not source:
        raise ValueError('旧分类来源不可核验，已保留当前归属待核对')
    if document.get('source_id') and document['source_id']!=source:
        raise ValueError('旧分类与当前来源不一致，已保留当前归属待核对')
    ids=[row['row']['id'] for row in expected['members']];group='list_id' if kind=='reminders' else 'calendar_id'
    evidence={'kind':'legacy_category_recovery','link':link,'expected':expected,'mappings':mappings,'document':document}
    with WorkClaim(sync,{link['apple_id']:source},ids,evidence,category_ids=[link['alcor_id']],category_kind=kind) as claim:
        def verify(db):
            claim._check(db)
            current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(link['apple_id'],)).fetchone()
            rows=[dict(row) for row in db.execute(f'SELECT * FROM mappings WHERE {group}=?',(link['apple_id'],))]
            if not current or dict(current)!=link or rows!=mappings:raise ValueError('legacy category mapping changed')
        with sync._connect() as db:db.execute('BEGIN IMMEDIATE');verify(db)
        operation=digest({'claim':claim.tokens,'evidence':evidence})
        claim.started('owner_claiming')
        try:restored=owner.apple_restore_legacy_category(operation,expected,document)
        except ValueError as error:
            if str(error).startswith('LEGACY_CHANGED:'):claim.owner_rejected()
            raise
        claim.started('owner_committed')
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');verify(db)
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('legacy-restored:'+link['apple_id'],json.dumps(True)))
    return restored
