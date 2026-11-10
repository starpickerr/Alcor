"""Owner-conditional first bindings used by the existing link-selection flow."""
import uuid
from apple_sync.contracts import category_binding
from apple_sync.group_scan_claims import digest


def prepare(owner,links,available,groups,previous=None,preserve_missing=False):
    categories=owner.categories();prepared=[];specs={}
    for raw in links:
        if not isinstance(raw,dict):raise ValueError('link must be an object')
        item={'apple_id':str(raw.get('apple_id') or '').strip(),'alcor_id':str(raw.get('alcor_id') or '').strip(),
              'direction':str(raw.get('direction') or '').strip(),'enabled':raw.get('enabled')}
        name=str(raw.get('create_name') or '').strip();payload={};new=False
        if not item['alcor_id'] and name:
            existing=next((row for row in categories if row['name']==name),None)
            item['alcor_id']=existing['id'] if existing else str(uuid.uuid4())
            if existing is None:
                new=True;payload={'name':name,'color':available.get(item['apple_id'],{}).get('color','#6B8AF7')}
        if item['apple_id'] in groups and item['apple_id'] in available:
            try:
                expected=None if new else owner.apple_capture_category_absence(item['alcor_id'])
            except KeyError:
                old=(previous or {}).get(item['apple_id'])
                if not preserve_missing or not old or any(item[key] != (bool(old[key]) if key=='enabled' else old[key]) for key in ('alcor_id','direction','enabled')):raise
                prepared.append(item);continue
            specs[item['apple_id']]={'identity':item['alcor_id'],'expected':expected,'payload':payload}
        prepared.append(item)
    return prepared,specs


def guard_discovery(db,kind,previous,available,groups):
    from apple_sync.category_create import pending_sources_in_db
    pending=pending_sources_in_db(db,kind)
    if any(group not in previous and available.get(group,{}).get('source_id') in pending for group in groups):
        raise ValueError('该来源有分类创建结果待核对，暂不自动关联新分类；已有分类同步继续')


def connect(owner,claim,spec,native):
    prior=claim.started_effects
    claim.started('owner_committing' if prior else 'owner_claiming')
    operation=digest({'claim':claim.tokens,'category':spec,'native':native})
    try:
        result=owner.apple_connect_category(operation,spec['identity'],spec['expected'],spec['payload'],category_binding(native))
    except ValueError as error:
        if not prior and str(error).startswith('CONNECTION_CHANGED:'):claim.owner_rejected()
        raise
    claim.started('owner_committed')
    return result
