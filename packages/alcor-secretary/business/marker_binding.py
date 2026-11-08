"""Conditional metadata/binding commit for an existing local marker target only."""
import json
from business.security_receipts import read_receipt,save_receipt,validate_operation_id


def commit(owner,operation_id,expected,category,native,issues):
    validate_operation_id(operation_id)
    reminder=owner.APPLE_ITEMS=='todos';group='list_id' if reminder else 'calendar_id';native_id='reminder_id' if reminder else 'event_id'
    if (not isinstance(expected,dict) or expected.get('entity')!='item' or not isinstance(category,dict)
            or not isinstance(native,dict) or native.get('alcor_id')!=expected.get('local_id')
            or any(not isinstance(native.get(key),str) or not native[key] for key in (group,native_id,'source_id'))
            or not isinstance(issues,list)):
        raise ValueError('MARKER_CHANGED: invalid marker identity')
    identity=expected['local_id'];args={'expected':expected,'category':category,'native':native,'issues':issues}
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if read_receipt(db,operation_id,'bind_existing_marker',args):
            raise ValueError('marker binding already committed; inspect receipt before reconciliation')
        if owner._apple_unbound_snapshot(db,'item',identity,require_unbound=False)!=expected:
            raise ValueError('MARKER_CHANGED: local content, incarnation or binding changed')
        if owner._apple_category_absence_snapshot(db,category['local_id'])!=category:
            raise ValueError('MARKER_CHANGED: marker category changed')
        container=category['local'].get('apple') or {}
        if (expected['local'].get('category_id')!=category['local_id'] or expected['local'].get('apple_pending')
                or category['local'].get('apple_pending') or container.get('apple_id')!=native[group]
                or container.get('source_id')!=native['source_id']):
            raise ValueError('MARKER_CHANGED: marker source or local location is unverified')
        previous=expected['local'].get('apple') or {}
        if not previous:
            raise ValueError('MARKER_CHANGED: 历史 marker 缺少持久来源绑定依据，内容已保留待核对，暂不自动重新关联')
        if previous and any(previous.get(key)!=value for key,value in {'apple_id':native[native_id],'group_id':native[group],'source_id':native['source_id']}.items()):
            raise ValueError('MARKER_CHANGED: existing binding belongs to another native identity')
        fields={'url':native.get('url') or '', 'alarms':native.get('alarms',[]),'floating':bool(native.get('floating')),
                'attendees':native.get('attendees',[]),'native_snapshot':native,'native_recurrence':native.get('recurrence',{}),
                'native_series_key':native.get('series_key') or native.get('external_id')}
        if reminder:fields['native_managed']=True
        owner._apple_fields(db,identity,fields,True)
        binding={'apple_id':native[native_id],'group_id':native[group],'source_id':native['source_id'],
                 'can_delete':bool(native.get('writable')),'can_modify':bool(native.get('writable')) and not issues,
                 'issues':issues,'native':native,'series_key':native.get('external_id'),'floating':native.get('floating',False)}
        db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)',('item',identity,json.dumps(binding,ensure_ascii=False)))
        save_receipt(db,operation_id,'bind_existing_marker',args,identity)
        result=owner._apple_decorate(db,'item',db.execute(f'SELECT * FROM {owner.APPLE_ITEMS} WHERE id=?',(identity,)).fetchone())
    if reminder:
        owner._backfill_scheduler_links();owner._sync_scheduler_links()
    return result
