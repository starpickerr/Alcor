"""Normal reminder moves use both existing container claims and owner CAS."""
from apple_sync.group_scan_claims import ScanHeld, WorkClaim


def move(sync,service,mapping,original,target):
    from apple_sync.reminders import _payload,_hash
    from apple_sync.metadata import metadata
    before=dict(mapping);local=sync._local(mapping['todo_id']);binding=(local or {}).get('apple') or {}
    inventory=sync.bridge.call('reminder_lists');groups={row['id']:row for row in inventory['lists']}
    origin=groups.get(original['apple_id']);destination=groups.get(target['apple_id'])
    if (not origin or not destination or not origin.get('source_id') or not destination.get('source_id') or
            not destination.get('writable') or binding.get('source_id')!=origin['source_id']):
        raise ValueError('reminder move sources cannot be verified')
    expected=service.apple_capture_reminder_sync(local['id'],target['alcor_id'])
    if expected['local'].get('category_id')!=target['alcor_id']:
        raise ScanHeld('reminder move target changed before capture')
    baseline=sync._reminder_baselines(local['id'])
    evidence={'kind':'reminder_move','mapping':before,'expected':expected,'original':original,'target':target}
    with WorkClaim(sync,{origin['id']:origin['source_id'],destination['id']:destination['source_id']},[local['id']],evidence) as claim:
        try:
            if [row for row in sync._maps() if row['todo_id']==local['id']]!=[before]:
                raise ValueError('reminder move mapping changed')
            current_inventory=sync.bridge.call('reminder_lists')
            if current_inventory!=inventory:raise ValueError('reminder move inventory changed')
            remote=sync.bridge.call('reminder_lookup',list_id=origin['id'],reminder_id=mapping['reminder_id'])['reminder']
            if (not remote or remote.get('reminder_id')!=mapping['reminder_id'] or remote.get('list_id')!=origin['id'] or
                    remote.get('source_id')!=origin['source_id'] or remote!=binding.get('native') or
                    _hash(remote,remote=True)!=mapping['remote_hash'] or
                    baseline['item-metadata:'] and baseline['item-metadata:'][1]!=metadata(remote,True)):
                raise ValueError('reminder move original content or identity changed')
            try:
                service.apple_verify_reminder_sync(expected)
            except ValueError as exc:
                if str(exc).startswith('INBOUND_CHANGED:'):
                    raise ScanHeld('reminder move intent changed before send') from exc
                raise
            payload={**sync._write_payload(expected['local'],mapping,list_id=destination['id']),
                     'lookup_list_id':origin['id'],'expected_source_id':destination['source_id'],
                     'expected_snapshot':remote,'expected_destination_snapshot':destination}
            claim.started()
            saved=sync.bridge.call('reminder_upsert',**payload)['reminder']
            sync._set('reminder-move-receipt:'+local['id'],{'evidence':evidence,'saved':saved})
            applied=service.apple_apply_reminder_move(expected,_payload(saved),saved,remote)
            sync._commit_reminder_snapshot(mapping,before,baseline,applied,saved,expected['generation'],
                move_from={'generation':expected['generation'],'source_id':origin['source_id'],'group_id':origin['id']})
            return 1
        except ScanHeld:
            raise
        except ValueError as exc:
            sync._reminder_conflict(mapping,before,baseline,expected,{},str(exc),claim=claim)
            raise


def reconcile_inbound_moves(sync,service,links,inventory,observations):
    """Restore only exact-ID, same-source native moves with unchanged local intent."""
    from apple_sync.reminders import _hash,_payload,_supported
    from apple_sync.metadata import metadata
    from apple_sync.group_scan_claims import ScanHeld
    by_group={row['apple_id']:row for row in links if row['enabled']}
    groups={row['id']:row for row in inventory.get('lists',[])}
    selected=[key for key in by_group if key in groups]
    moved=0;deferred=set();errors=[]
    if not selected:return moved,deferred,errors
    rows=sync.bridge.call('reminders',list_ids=selected)
    successful=set(rows.get('successful_list_ids',[]))
    for mapping in sync._maps():
        if mapping['state']!='active' or mapping['list_id'] not in by_group:continue
        found=[row for row in rows.get('reminders',[]) if row.get('reminder_id')==mapping['reminder_id']]
        if len(found)!=1 or found[0].get('list_id')==mapping['list_id']:continue
        remote=found[0];origin=by_group[mapping['list_id']];target=by_group.get(remote.get('list_id'))
        observed=observations.get(mapping['todo_id']) or {};initial=observed.get('capture')
        if not initial or not target or origin['direction'] not in ('import','bidirectional') or target['direction'] not in ('import','bidirectional'):continue
        local=initial['local'];binding=local.get('apple') or {};native=binding.get('native') or {}
        source=binding.get('source_id');before=dict(mapping);baseline=sync._reminder_baselines(local['id'])
        if (not source or source not in inventory.get('source_ids',[]) or source!=remote.get('source_id') or source!=native.get('source_id') or
                any(key not in successful or groups.get(key,{}).get('source_id')!=source for key in (origin['apple_id'],target['apple_id'])) or
                binding.get('apple_id')!=mapping['reminder_id'] or binding.get('group_id')!=mapping['list_id'] or
                native.get('reminder_id')!=mapping['reminder_id'] or native.get('list_id')!=mapping['list_id'] or
                local.get('category_id')!=origin['alcor_id'] or local.get('apple_pending') or
                _hash(local)!=mapping['local_hash'] or _hash(native,remote=True)!=mapping['remote_hash'] or not baseline['item-metadata:'] or
                baseline['item-metadata:']!=[metadata(local),metadata(native,True)] or not _supported(remote) or not _supported(native)):
            continue
        evidence={'kind':'reminder_inbound_move','mapping':before,'observed':initial,'remote':remote,'origin':origin,'target':target}
        try:
            with WorkClaim(sync,{origin['apple_id']:source,target['apple_id']:source},[local['id']],evidence) as claim:
                try:
                    if [row for row in sync._maps() if row['todo_id']==local['id']]!=[before]:
                        raise ValueError('inbound move mapping changed')
                    if sync.bridge.call('reminder_lists')!=inventory:
                        raise ValueError('inbound move source inventory changed')
                    current=sync.bridge.call('reminder_lookup',list_id=remote['list_id'],reminder_id=remote['reminder_id'])['reminder']
                    if current!=remote:raise ValueError('inbound move native identity or content changed')
                    expected=service.apple_capture_reminder_sync(local['id'],target['alcor_id'])
                    if any(expected[key]!=initial[key] for key in ('generation','row','local','related_snapshot')):
                        raise ValueError('INBOUND_CHANGED: local intent changed after native move observation')
                    sync._set('reminder-inbound-move:'+local['id'],{'evidence':evidence,'expected':expected})
                    claim.started('owner_claiming')
                    try:
                        applied=service.apple_apply_reminder_move(expected,_payload(remote),remote,native)
                    except ValueError as exc:
                        if str(exc).startswith('INBOUND_CHANGED:'):
                            claim.owner_rejected()
                        raise
                    claim.started('owner_committed')
                    sync._commit_reminder_snapshot(mapping,before,baseline,applied,remote,expected['generation'],
                        move_from={'generation':expected['generation'],'source_id':source,'group_id':origin['apple_id']})
                    moved+=1
                except ValueError as exc:
                    sync._reminder_conflict(mapping,before,baseline,initial,remote,str(exc),claim=claim)
                    raise
        except ScanHeld as exc:
            deferred.add(local['id']);errors.append(str(exc))
        except Exception as exc:
            deferred.add(local['id']);errors.append(str(exc))
    return moved,deferred,errors
