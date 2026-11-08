"""Conditional same-source reminder container ID rebind, without native writes."""
import json
from business.object_generations import generations_for
from business.security_receipts import read_receipt,save_receipt,validate_operation_id
from business.reminder_sync import _verify


def commit(owner,operation_id,expected,new_category,native_items):
    validate_operation_id(operation_id)
    def changed(reason):raise ValueError('REBIND_CHANGED: '+reason)
    if (not isinstance(expected,dict) or not isinstance(new_category,dict) or not native_items
            or len(native_items)!=len(expected.get('items',[])) or len(native_items)!=len(expected.get('mappings',[]))):
        changed('incomplete reminder rebind evidence')
    category=expected['category'];old=(category['local'].get('apple') or {})
    source=old.get('source_id');group=new_category.get('id')
    if not source or not group or new_category.get('source_id')!=source or group==old.get('apple_id'):
        changed('category source or identity differs')
    arguments={'expected':expected,'new_category':new_category,'native_items':native_items}
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt=read_receipt(db,operation_id,'reminder_category_rebind',arguments)
        if receipt:
            category_generations=dict(expected['items'][0]['category_generations'])
            if generations_for(db,'category',[category['local_id']])[0][1]!=category_generations.get(category['local_id']):
                changed('replayed category incarnation differs')
            binding=owner._apple_binding(db,'category',category['local_id']) or {}
            if binding.get('apple_id')!=group or binding.get('source_id')!=source:changed('replayed category differs')
            for item,native in zip(expected['items'],native_items):
                binding=owner._apple_binding(db,'item',item['local_id']) or {}
                if (generations_for(db,'item',[item['local_id']])[0][1]!=item['generation'] or
                        binding.get('group_id')!=group or binding.get('apple_id')!=native.get('reminder_id')):
                    changed('replayed item differs')
            return {'operation_id':operation_id,'replayed':True}
        if owner._apple_category_absence_snapshot(db,category['local_id'])!=category:changed('category or members changed')
        if category['local'].get('apple_pending'):changed('category has a pending operation')
        seen=set()
        for item,mapping,native in zip(expected['items'],expected['mappings'],native_items):
            try:_verify(owner,db,item)
            except (ValueError,KeyError):changed('local reminder changed')
            binding=item['local'].get('apple') or {};native_id=native.get('reminder_id');old_native=binding.get('native') or {}
            if (item['local_id']!=mapping['todo_id'] or item['row']['category_id']!=category['local_id']
                    or item['local'].get('apple_pending') or binding.get('group_id')!=old['apple_id']
                    or binding.get('apple_id')!=mapping['reminder_id'] or binding.get('source_id') not in (None,'',source)
                    or old_native.get('list_id')!=old['apple_id'] or old_native.get('reminder_id')!=mapping['reminder_id']
                    or not native_id or native_id in seen or native.get('list_id')!=group or native.get('source_id')!=source
                    or not (native_id==mapping['reminder_id'] or mapping.get('external_id')
                            and old_native.get('external_id')==mapping['external_id'] and native.get('external_id')==mapping['external_id'])):
                changed('reminder identity cannot be verified')
            seen.add(native_id)
        for item,native in zip(expected['items'],native_items):
            binding=item['local']['apple'];updated={**binding,'apple_id':native['reminder_id'],'group_id':group,
                'source_id':source,'native':native,'series_key':native.get('external_id'),'floating':native.get('floating',False),
                'can_delete':bool(native.get('writable')),'can_modify':bool(native.get('writable')) and not binding.get('issues')}
            owner._apple_fields(db,item['local_id'],{'native_snapshot':native,'native_series_key':native.get('series_key') or native.get('external_id')},True)
            db.execute("UPDATE apple_bindings SET document=? WHERE entity='item' AND local_id=?",(json.dumps(updated,ensure_ascii=False),item['local_id']))
        mutable=not new_category['immutable'] if 'immutable' in new_category else bool(new_category.get('writable'))
        binding={**old,'apple_id':group,'source_id':source,'source_name':new_category.get('source',''),
            'title':new_category.get('title'),'can_modify':bool(new_category.get('writable')),
            'can_rename':mutable,'can_delete':mutable,'default':bool(new_category.get('default',False))}
        db.execute("UPDATE apple_bindings SET document=? WHERE entity='category' AND local_id=?",(json.dumps(binding,ensure_ascii=False),category['local_id']))
        save_receipt(db,operation_id,'reminder_category_rebind',arguments,category['local_id'])
        return {'operation_id':operation_id,'replayed':False}
