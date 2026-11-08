"""Conditional calendar ID rebinding for the trusted Apple peer only."""
import json
from datetime import datetime
from business.object_generations import generations_for
from business.security_receipts import read_receipt, save_receipt, validate_operation_id


def _instant(value):
    if not isinstance(value,str) or not value:
        raise ValueError('calendar occurrence identity unavailable')
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('calendar occurrence timezone unavailable')
    return parsed


def prepare(service, category_id, old_group, source_id, mappings):
    if not all(isinstance(v,str) and v for v in (category_id,old_group,source_id)) or not isinstance(mappings,list) or not mappings:
        raise ValueError('calendar rebind needs a source and at least one uniquely identified item')
    with service._connect() as db:
        db.execute('BEGIN')
        category=db.execute('SELECT * FROM calendar_categories WHERE id=?',(category_id,)).fetchone()
        if not category: raise ValueError('calendar category unavailable')
        category_binding=service._apple_binding(db,'category',category_id) or {}
        if category_binding.get('apple_id')!=old_group or category_binding.get('source_id')!=source_id:
            raise ValueError('old calendar category identity cannot be verified')
        items=[]; seen=set()
        for mapping in mappings:
            identity=mapping.get('alcor_id'); external=mapping.get('external_id'); occurrence=mapping.get('occurrence_date')
            if not isinstance(identity,str) or not identity or identity in seen or not isinstance(external,str) or not external:
                raise ValueError('calendar item identity is ambiguous')
            seen.add(identity); _instant(occurrence)
            row=db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone()
            if not row or row['category_id']!=category_id: raise ValueError('calendar local target changed')
            binding=service._apple_binding(db,'item',identity) or {};old_native=binding.get('native') or {}
            if (binding.get('group_id')!=old_group or binding.get('apple_id')!=mapping.get('event_id')
                    or binding.get('source_id') not in (None,source_id)
                    or old_native.get('external_id')!=external or _instant(old_native.get('occurrence_date'))!=_instant(occurrence)):
                raise ValueError('old calendar item binding cannot be verified')
            items.append({'id':identity,'row':dict(row),'local':service._apple_decorate(db,'item',row),
                          'generation':generations_for(db,'item',[identity])[0][1], 'mapping':mapping})
        return {'version':1,'category_id':category_id,'old_group':old_group,'source_id':source_id,
                'category':dict(category),'category_local':service._apple_decorate(db,'category',category),
                'category_generation':generations_for(db,'category',[category_id])[0][1],'items':items}


def commit(service, operation_id, expected, new_category, native_items):
    validate_operation_id(operation_id)
    if (not isinstance(expected,dict) or expected.get('version')!=1 or not isinstance(new_category,dict)
            or not isinstance(native_items,list) or len(native_items)!=len(expected.get('items',[]))):
        raise ValueError('invalid calendar rebind evidence')
    new_group=new_category.get('id');source_id=expected.get('source_id')
    if not isinstance(new_group,str) or not new_group or new_group==expected.get('old_group') or new_category.get('source_id')!=source_id:
        raise ValueError('new calendar source or identity changed')
    arguments={'expected':expected,'new_category':new_category,'native_items':native_items}
    with service._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt=read_receipt(db,operation_id,'calendar_rebind',arguments)
        category=db.execute('SELECT * FROM calendar_categories WHERE id=?',(expected['category_id'],)).fetchone()
        if not category or generations_for(db,'category',[expected['category_id']])[0][1]!=expected['category_generation']:
            raise ValueError('calendar category incarnation changed')
        if receipt:
            # A completed operation is historical evidence; never apply it to recreated items.
            for old,native in zip(expected['items'],native_items):
                row=db.execute('SELECT * FROM calendar_events WHERE id=?',(old['id'],)).fetchone()
                binding=service._apple_binding(db,'item',old['id']) or {}
                if (not row or generations_for(db,'item',[old['id']])[0][1]!=old['generation']
                        or binding.get('group_id')!=new_group or binding.get('apple_id')!=native.get('event_id')):
                    raise ValueError('calendar rebind receipt conflicts with current object')
            return {'replayed':True,'operation_id':operation_id}
        if dict(category)!=expected['category'] or service._apple_decorate(db,'category',category)!=expected['category_local']:
            raise ValueError('calendar category changed during rebind')
        seen=set()
        for old,native in zip(expected['items'],native_items):
            row=db.execute('SELECT * FROM calendar_events WHERE id=?',(old['id'],)).fetchone()
            if (not row or dict(row)!=old['row'] or service._apple_decorate(db,'item',row)!=old['local']
                    or generations_for(db,'item',[old['id']])[0][1]!=old['generation']):
                raise ValueError('calendar item changed during rebind')
            mapping=old['mapping'];native_id=native.get('event_id');key=(native_id,_instant(native.get('occurrence_date')))
            if (not isinstance(native_id,str) or not native_id or key in seen or native.get('calendar_id')!=new_group
                    or native.get('source_id')!=source_id or native.get('external_id')!=mapping['external_id']
                    or _instant(native.get('occurrence_date'))!=_instant(mapping['occurrence_date'])):
                raise ValueError('new native calendar item identity cannot be verified')
            seen.add(key)
        for old,native in zip(expected['items'],native_items):
            binding=service._apple_binding(db,'item',old['id']) or {}
            updated={**binding,'apple_id':native['event_id'],'group_id':new_group,'source_id':source_id,'native':native,
                     'series_key':native.get('external_id'),'floating':native.get('floating',False)}
            service._apple_fields(db,old['id'],{'native_snapshot':native,'native_series_key':native.get('series_key') or native.get('external_id')},True)
            db.execute('UPDATE apple_bindings SET document=? WHERE entity=? AND local_id=?',(json.dumps(updated,ensure_ascii=False),'item',old['id']))
        cat_binding=service._apple_binding(db,'category',expected['category_id']) or {}
        mutable = not bool(new_category['immutable']) if 'immutable' in new_category else bool(new_category.get('writable'))
        cat_updated={**cat_binding,'apple_id':new_group,'source_id':source_id,'source_name':new_category.get('source') or '',
                     'title':new_category.get('title') or '', 'can_modify':bool(new_category.get('writable')),
                     'can_rename':mutable,'can_delete':mutable,'default':bool(new_category.get('default',False))}
        db.execute('UPDATE apple_bindings SET document=? WHERE entity=? AND local_id=?',(json.dumps(cat_updated,ensure_ascii=False),'category',expected['category_id']))
        save_receipt(db,operation_id,'calendar_rebind',arguments,expected['category_id'])
        return {'replayed':False,'operation_id':operation_id}
