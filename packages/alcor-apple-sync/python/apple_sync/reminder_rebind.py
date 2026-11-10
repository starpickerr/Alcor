"""Verify an existing reminder category ID reset under the shared claims."""
import json
from copy import deepcopy
from apple_sync.group_scan_claims import WorkClaim
from apple_sync.calendar_rebind import EmptyCategoryIdentity,UnverifiedCategoryIdentity


def _inventory(sync,category,old_group):
    inventory=sync.bridge.call('reminder_lists');rows=inventory.get('lists',[])
    expected={k:v for k,v in category.items() if k!='default'}
    if (category.get('source_id') not in inventory.get('source_ids',[])
            or any(not row.get('id') or not row.get('source_id') for row in rows)
            or len({row['id'] for row in rows})!=len(rows)
            or any(row['id']==old_group for row in rows)
            or [row for row in rows if row['id']==category['id']]!=[expected]):
        raise UnverifiedCategoryIdentity('reminder category/source inventory changed or is incomplete')
    return sorted(row['id'] for row in rows if row['source_id']==category['source_id'])


def _native(sync,category,groups,mappings):
    inventory=sync.bridge.call('reminders',list_ids=groups)
    if not set(groups)<=set(inventory.get('successful_list_ids',[])) or set(groups)&set(inventory.get('failed_list_ids',[])):
        raise UnverifiedCategoryIdentity('reminder source item inventory is incomplete')
    rows=inventory.get('reminders',[])
    if any(row.get('source_id')!=category['source_id'] or row.get('list_id') not in groups for row in rows):
        raise UnverifiedCategoryIdentity('reminder source item identity is incomplete')
    found=[];seen=set()
    for mapping in mappings:
        matches=[row for row in rows if row.get('reminder_id')==mapping['reminder_id'] or
                 mapping.get('external_id') and row.get('external_id')==mapping['external_id']]
        if (len(matches)!=1 or not matches[0].get('reminder_id') or matches[0]['list_id']!=category['id']
                or matches[0]['reminder_id'] in seen):
            raise UnverifiedCategoryIdentity('reminder item identity is absent or ambiguous; the category name is not identity')
        seen.add(matches[0]['reminder_id']);found.append(matches[0])
    return deepcopy(found)


def rebind(sync,owner,link,snapshot,category):
    category=deepcopy(category);link=deepcopy(link);old=link['apple_id'];source=snapshot.get('source_id')
    mappings=[row for row in sync._maps() if row['list_id']==old]
    if not mappings:raise EmptyCategoryIdentity('empty reminder category has no item identity evidence')
    if not source or category.get('source_id')!=source:raise UnverifiedCategoryIdentity('reminder category source is unverified')
    expected={'category':owner.apple_capture_category_absence(link['alcor_id']),
              'items':[owner.apple_capture_reminder_sync(row['todo_id']) for row in mappings],'mappings':mappings}
    before={row['todo_id']:sync._reminder_baselines(row['todo_id']) for row in mappings}
    groups=_inventory(sync,category,old)
    evidence={'kind':'reminder_category_rebind','link':link,'snapshot':snapshot,'category':category,'expected':expected,'baselines':before}
    with WorkClaim(sync,{old:source,category['id']:source},[row['todo_id'] for row in mappings],evidence) as claim:
        native=_native(sync,category,groups,mappings)
        if _inventory(sync,category,old)!=groups or _native(sync,category,groups,mappings)!=native:
            raise UnverifiedCategoryIdentity('reminder identity changed during category rebind')
        def check(db):
            claim._check(db)
            row=db.execute('SELECT * FROM category_links WHERE apple_id=?',(old,)).fetchone()
            current=[dict(row) for row in db.execute('SELECT * FROM mappings WHERE list_id=? ORDER BY todo_id',(old,))]
            if (not row or dict(row)!=link or current!=sorted(mappings,key=lambda row:row['todo_id'])
                    or any(sync._reminder_baselines(item['todo_id'],db)!=before[item['todo_id']] for item in mappings)):
                raise ValueError('reminder category manager evidence changed')
        operation=claim.tokens[0];key='reminder-category-rebind:'+operation
        journal={'evidence':evidence,'native':native,'state':'prepared'}
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');check(db)
            db.execute('INSERT INTO settings VALUES (?,?)',(key,json.dumps(journal,ensure_ascii=False,sort_keys=True)))
        claim.started('owner_claiming')
        try:receipt=owner.apple_commit_reminder_category_rebind(operation,expected,category,native)
        except ValueError as error:
            if str(error).startswith('REBIND_CHANGED:'):claim.owner_rejected()
            raise
        claim.started('owner_committed')
        if receipt.get('operation_id')!=operation:raise ValueError('reminder category owner receipt is invalid')
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');check(db)
            saved=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            if not saved or json.loads(saved[0])!=journal:raise ValueError('reminder category journal changed')
            if db.execute('SELECT 1 FROM category_links WHERE apple_id=?',(category['id'],)).fetchone():
                raise ValueError('reminder category target was linked concurrently')
            for mapping,item,remote in zip(mappings,expected['items'],native):
                if db.execute('SELECT 1 FROM mappings WHERE todo_id<>? AND list_id=? AND reminder_id=?',(mapping['todo_id'],category['id'],remote['reminder_id'])).fetchone():
                    raise ValueError('reminder target is already mapped')
                db.execute('UPDATE mappings SET list_id=?,reminder_id=?,external_id=? WHERE todo_id=?',
                           (category['id'],remote['reminder_id'],remote.get('external_id') or '',mapping['todo_id']))
                guard={'generation':item['generation'],'source_id':source,'group_id':category['id']}
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('reminder-binding-generation:'+mapping['todo_id'],json.dumps(guard)))
            db.execute('UPDATE category_links SET apple_id=? WHERE apple_id=?',(category['id'],old))
            db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps({'evidence':evidence,'native':native,'state':'done','receipt':receipt},ensure_ascii=False,sort_keys=True),key))
    return receipt
