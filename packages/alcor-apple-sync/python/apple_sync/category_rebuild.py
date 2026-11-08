"""Explicit empty-container rebuild. Existing members stay local, never uploaded here."""
from copy import deepcopy
import json
from apple_sync.contracts import category_binding
from apple_sync.group_scan_claims import WorkClaim,digest
from apple_sync.category_create import create_native_category,_check,_update


def capture(sync,owner,kind,apple_id,source_id):
    reminder=kind=='reminders';group='list_id' if reminder else 'calendar_id'
    link=next((row for row in sync._stored_links() if row['apple_id']==apple_id),None)
    if not link:raise ValueError('原分类关联已变化，请刷新后重新预览')
    expected=owner.apple_capture_category_absence(link['alcor_id'])
    old=(expected['local'].get('apple') or {})
    if old.get('apple_id')!=apple_id or not old.get('source_id') or expected['local'].get('apple_pending'):
        raise ValueError('原分类来源或待处理状态尚未确认，请先核对')
    inventory=sync.bridge.call('reminder_lists' if reminder else 'calendars')
    if any(row['id']==apple_id for row in inventory['lists' if reminder else 'calendars']):
        raise ValueError('原 Apple 分类仍存在，不能作为缺失分类重建')
    sources=[row for row in inventory.get('sources',[]) if row.get('id')==source_id]
    if len(sources)!=1 or source_id not in inventory.get('source_ids',[]):
        raise ValueError('请选择当前已确认的 Apple 账户')
    members=[row for row in (owner.list_todos() if reminder else owner.list_events()) if row.get('category_id')==link['alcor_id']]
    members.sort(key=lambda row:row['id'])
    if owner.apple_capture_category_absence(link['alcor_id'])!=expected:
        raise ValueError('分类或成员已变化，请重新预览')
    mappings=[row for row in sync._maps() if row[group]==apple_id]
    if any(row['todo_id' if reminder else 'alcor_id'] not in {item['id'] for item in members} for row in mappings):
        raise ValueError('旧映射含其他分类或缺失成员，已保留待核对')
    document={'version':1,'kind':kind,'rebuild_link':link,'expected':expected,
              'source':sources[0],'members':members,'mappings':mappings,'mode':'empty_container_only'}
    return document,inventory


def preview(sync,owner,kind,apple_id,source_id):
    document,_=capture(sync,owner,kind,apple_id,source_id)
    return {'kind':kind,'apple_id':apple_id,'source_id':source_id,'source':document['source'],
            'title':document['expected']['local']['name'],'color':document['expected']['local']['color'],
            'local_id':document['rebuild_link']['alcor_id'],'snapshot':digest(document),
            'retained_members':[{'id':row['id'],'title':row['title']} for row in document['members']],
            'mode':'empty_container_only'}


def rebuild(sync,owner,kind,apple_id,source_id,snapshot):
    """Private owner-page dispatch; a snapshot is a condition, not authorization."""
    document,inventory=capture(sync,owner,kind,apple_id,source_id)
    if snapshot!=digest(document):raise ValueError('预览内容已变化，请重新预览并确认')
    link=document['rebuild_link'];expected=document['expected'];local=expected['local']
    old_source=local['apple']['source_id'];group='list_id' if kind=='reminders' else 'calendar_id'
    ids=[row['id'] for row in document['members']]
    with WorkClaim(sync,{apple_id:old_source},ids,document,category_ids=[local['id']],category_kind=kind) as old_claim:
        fresh,_=capture(sync,owner,kind,apple_id,source_id)
        if fresh!=document:raise ValueError('分类状态已变化，请重新预览')
        key,intent,saved=create_native_category(sync,owner,kind,local,inventory,source_id=source_id,prior_claim=old_claim,expected=expected)
        with WorkClaim(sync,{saved['id']:source_id},[],{'kind':'category_rebuild_result','intent':key[-1],'saved':saved}) as new_claim:
            new_claim.started('native_committed')
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');old_claim._check(db);new_claim._check(db);_check(db,key,'native_saved',intent)
                current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(apple_id,)).fetchone()
                mappings=[dict(row) for row in db.execute(f'SELECT * FROM mappings WHERE {group}=?',(apple_id,))]
                if not current or dict(current)!=link or mappings!=document['mappings']:
                    raise ValueError('分类映射已变化，Apple 创建结果已保留待核对')
            old_claim.started('owner_committing');new_claim.started('owner_claiming')
            _update(sync,key,'native_saved',intent,'owner_claiming',intent)
            owner.apple_connect_category(digest({'snapshot':snapshot,'intent':key[-1]}),local['id'],expected,{},category_binding(saved))
            old_claim.started('owner_committed');new_claim.started('owner_committed')
            _update(sync,key,'owner_claiming',intent,'owner_committed',intent)
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');old_claim._check(db);new_claim._check(db);_check(db,key,'owner_committed',intent)
                current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(apple_id,)).fetchone()
                mappings=[dict(row) for row in db.execute(f'SELECT * FROM mappings WHERE {group}=?',(apple_id,))]
                if (not current or dict(current)!=link or mappings!=document['mappings']
                        or db.execute('SELECT 1 FROM category_links WHERE apple_id=?',(saved['id'],)).fetchone()):
                    raise ValueError('分类映射已变化，本地提交与 Apple 回执已保留待核对')
                db.execute('UPDATE category_links SET apple_id=?,apple_name=?,name_conflict=?,updated_at=? WHERE apple_id=?',
                           (saved['id'],saved['title'],'',sync.clock(),apple_id))
                # Old mappings and bindings remain evidence; baseline suppresses only this frozen set.
                db.executemany('INSERT OR IGNORE INTO baseline_local VALUES (?)',[(identity,) for identity in ids])
                retained={'kind':kind,'old_group':apple_id,'new_group':saved['id'],'local_id':local['id'],
                          'source_id':source_id,'snapshot':snapshot,'evidence':document,'native_receipt':intent['proof'],
                          'state':'members_retained','member_ids':ids}
                values={'category-retained:'+apple_id:retained,'category-snapshot:'+saved['id']:saved,
                        'category-source:'+saved['id']:source_id}
                selected='list_ids' if kind=='reminders' else 'calendar_ids'
                values[selected]=[row[0] for row in db.execute('SELECT apple_id FROM category_links WHERE enabled=1 ORDER BY apple_id')]
                row=db.execute("SELECT value FROM settings WHERE key='category_conflicts'").fetchone()
                values['category_conflicts']=[entry for entry in json.loads(row[0]) if entry['apple_id']!=apple_id] if row else []
                db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',[(name,json.dumps(value,ensure_ascii=False)) for name,value in values.items()])
                db.execute('DELETE FROM category_create_intents WHERE kind=? AND local_id=? AND incarnation=? AND source_id=? AND token=?',key)
    return sync.status()


def retained(sync):
    with sync._connect() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT value FROM settings WHERE key LIKE 'category-retained:%' ORDER BY key")]


def status(sync):
    return [{'old_group':row['old_group'],'new_group':row['new_group'],'local_id':row['local_id'],
             'title':row['evidence']['expected']['local']['name'],'source_id':row['source_id'],
             'member_count':len(row['member_ids']),'state':row['state'],
             'reason':'仅创建了空 Apple 容器；原成员及旧关联证据保留在本地，等待重新关联，未上传成员'} for row in retained(sync)]


def retained_ids(sync):
    """Independent persistent hold, never cleared by baseline/reset/direction changes.

    No release path is provided here. A future release requires separately verified
    concrete member approval; a generic start-sync or marker cannot provide it.
    """
    return {identity for row in retained(sync) for identity in row['member_ids']}


def guard_retained(sync, identity):
    if identity in retained_ids(sync):
        raise ValueError('重建容器的原成员仍保留本地待重新关联，需先具体批准成员重新关联，普通同步或冲突选择不能上传或更改旧关联')
