"""Additive metadata baselines keep old content hashes and local identities compatible."""
import hashlib
import json
from apple_sync.timezones import normalize_timezone


def conflict_summaries(mappings, get_record, identity):
    result = []
    for row in mappings:
        if not row.get('conflict'):
            continue
        try:
            local = get_record(row[identity])
        except KeyError:
            local = {}
        result.append({identity:row[identity], 'reason':row['conflict'],
                       'title':local.get('title'), 'category_id':local.get('category_id')})
    return result


def metadata(row, remote=False):
    return {'timezone':normalize_timezone(row.get('timezone') if remote else row.get('repeat_timezone',row.get('timezone','UTC'))),
        'floating':bool(row.get('floating')), 'url':row.get('url') or '', 'alarms':row.get('alarms',[]),
        'native_recurrence':row.get('recurrence',{}) if remote else row.get('native_recurrence',{}),
        'simple_recurrence':[row.get('repeat_frequency','none'), row.get('repeat_days','[]'),row.get('repeat_until'),row.get('repeat_interval',1)] if not remote else None}


def snapshot_fields(remote):
    return {'url':remote.get('url') or '', 'alarms':remote.get('alarms',[]), 'floating':bool(remote.get('floating')),
        'attendees':remote.get('attendees',[]),'native_snapshot':remote,
        'native_recurrence':remote.get('recurrence',{}),'native_series_key':remote.get('series_key') or remote.get('external_id')}


def record_baseline(sync, local, remote):
    sync._set('item-metadata:'+local['id'],[metadata(local),metadata(remote,True)])


def changed(sync, local, remote):
    old = sync._get('item-metadata:'+local['id'])
    if not old:
        fields = {key:value for key,value in snapshot_fields(remote).items() if key not in local}
        owner = getattr(sync,'calendar',None) or sync.todos
        if fields:
            owner.apple_apply_metadata(local['id'],fields)
            local.update(fields)
        record_baseline(sync,local,remote)
        # The old hash omitted timezone; disagreement has no trustworthy winner.
        mismatch = metadata(local)['timezone']!=metadata(remote,True)['timezone'] or bool(local.get('floating'))!=bool(remote.get('floating'))
        return mismatch,mismatch
    return old[0]!=metadata(local),old[1]!=metadata(remote,True)


def write_fields(sync, local, mapping=None):
    old = sync._get('item-metadata:'+local['id'])
    fields = {}
    for key in ('url','alarms','floating'):
        if key in local and (not mapping or not old or old[0].get(key)!=local[key]):
            fields[key] = local[key]
    if mapping and old and metadata(local)['simple_recurrence']!=old[0].get('simple_recurrence'):
        fields['set_recurrence'] = True
    if 'edit_scope' in local:
        fields['series'] = local['edit_scope']=='future'
    if 'native_recurrence' in local and local['native_recurrence'] and (not mapping or not old or old[0].get('native_recurrence')!=local['native_recurrence']):
        fields['native_recurrence'] = local['native_recurrence']
    # A durable native receipt replaces the user's URL as the operation identity.
    identity = json.dumps([local['id'],mapping is None,fields,local.get('updated_at')],sort_keys=True).encode()
    receipt = sync.db_path.parent/'native-receipts'/ (hashlib.sha256(identity).hexdigest()+'.json')
    receipt.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fields['receipt_file'] = str(receipt)
    return fields
