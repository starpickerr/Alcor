"""Atomic local-only repair of a missing legacy category and its exact retained members."""
from datetime import datetime,timezone
from business.object_generations import generations_for
from business.security_receipts import read_receipt,save_receipt,validate_operation_id


def capture_in_db(owner,db,identity,member_ids):
    if db.execute(f'SELECT 1 FROM {owner.APPLE_CATEGORIES} WHERE id=?',(identity,)).fetchone():
        raise ValueError('LEGACY_CHANGED: category now exists')
    if not member_ids or len(set(member_ids))!=len(member_ids):
        raise ValueError('LEGACY_CHANGED: distinct retained members required')
    rows=[]
    for member in sorted(member_ids):
        row=db.execute(f'SELECT * FROM {owner.APPLE_ITEMS} WHERE id=?',(member,)).fetchone()
        if row is None:raise ValueError('LEGACY_CHANGED: retained member disappeared')
        local=owner._apple_decorate(db,'item',row)
        if local.get('apple_pending'):
            raise ValueError('LEGACY_CHANGED: retained member has a pending operation')
        if row['category_id']!=identity and row['user_modified']:
            raise ValueError('LEGACY_CHANGED: retained member has a local edit; keep its current category for review')
        rows.append({'row':dict(row),'local':local,'generation':generations_for(db,'item',[member])[0][1]})
    binding=owner._apple_binding(db,'category',identity)
    return {'version':1,'identity':identity,'category_binding':binding,'members':rows}


def capture(owner,identity,member_ids):
    if not isinstance(identity,str) or not identity or not isinstance(member_ids,list) or any(not isinstance(i,str) or not i for i in member_ids):
        raise ValueError('LEGACY_CHANGED: invalid legacy category target')
    with owner._connect() as db:
        db.execute('BEGIN')
        return capture_in_db(owner,db,identity,member_ids)


def commit(owner,operation_id,expected,document):
    validate_operation_id(operation_id)
    if not isinstance(expected,dict) or expected.get('version')!=1 or not isinstance(document,dict):
        raise ValueError('LEGACY_CHANGED: legacy category snapshot required')
    identity=expected['identity'];ids=[entry['row']['id'] for entry in expected['members']]
    args={'expected':expected,'document':document}
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if read_receipt(db,operation_id,'restore_legacy_category',args):
            # Receipt proves a prior commit, not permission to apply the old members again.
            raise ValueError('legacy category restoration already committed; inspect retained receipt')
        if capture_in_db(owner,db,identity,ids)!=expected:
            raise ValueError('LEGACY_CHANGED: category or retained members changed')
        binding=expected['category_binding'] or {}
        if not binding.get('apple_id') or not binding.get('source_id'):
            raise ValueError('LEGACY_CHANGED: category source identity unavailable')
        for entry in expected['members']:
            item=entry['local'].get('apple') or {}
            if item.get('group_id')!=binding['apple_id'] or item.get('source_id')!=binding['source_id']:
                raise ValueError('LEGACY_CHANGED: member source identity cannot establish legacy ownership')
        title=str(document.get('title') or 'Apple')[:180];name=title;index=2
        while db.execute(f'SELECT 1 FROM {owner.APPLE_CATEGORIES} WHERE name=?',(name,)).fetchone():
            name=title+f' ({index})';index+=1
        db.execute(f'INSERT INTO {owner.APPLE_CATEGORIES} VALUES (?,?,?,?)',(identity,name,document.get('color') or '#6B8AF7',datetime.now(timezone.utc).isoformat()))
        for member in ids:
            if owner.APPLE_ITEMS=='todos':db.execute('UPDATE todos SET category_id=?,list_name=? WHERE id=?',(identity,name,member))
            else:db.execute('UPDATE calendar_events SET category_id=? WHERE id=?',(identity,member))
        save_receipt(db,operation_id,'restore_legacy_category',args,identity)
        result=owner._apple_decorate(db,'category',db.execute(f'SELECT * FROM {owner.APPLE_CATEGORIES} WHERE id=?',(identity,)).fetchone())
    return result
