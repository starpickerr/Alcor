"""Durable, source-scoped claims for creating a previously unbound category.

An intent never names a fabricated Apple group. Discovery must consult
pending_sources_in_db in the same manager transaction as a new link commit.
Only an exact bound receipt can establish the newly created group identity.
"""
import json
import secrets
from pathlib import Path

from apple_sync.bridge import BridgeError
from apple_sync.contracts import category_binding
from apple_sync.group_scan_claims import WorkClaim, digest, identity

KEY = '_category_create_schema'
VERSION = 'category-create-v1'
TABLE = ('CREATE TABLE category_create_intents (kind TEXT NOT NULL, local_id TEXT NOT NULL, '
         'incarnation TEXT NOT NULL, source_id TEXT NOT NULL, token TEXT NOT NULL UNIQUE, '
         'state TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(kind, local_id, incarnation))')


class CreatePending(ValueError):
    """The operation may have taken effect and requires reconciliation."""


def schema(db, *, create=False):
    marker = db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
    table = db.execute("SELECT type,sql FROM sqlite_master WHERE name='category_create_intents'").fetchone()
    if not marker:
        if table:
            raise ValueError('unrecognized category create table')
        if not create:
            return False
        db.execute(TABLE)
        db.execute('INSERT INTO settings VALUES (?,?)', (KEY, json.dumps(VERSION)))
        return True
    if (json.loads(marker[0]) != VERSION or not table or table[0] != 'table'
            or ' '.join(table[1].split()) != TABLE
            or db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='category_create_intents'").fetchone()):
        raise ValueError('incompatible category create schema')
    return True


def pending_in_db(db, kind):
    """Call under the caller's manager transaction when deciding new links."""
    if not schema(db):
        return []
    rows = db.execute('SELECT kind,local_id,incarnation,source_id,token,state,document FROM category_create_intents WHERE kind=? ORDER BY local_id', (kind,))
    result = []
    for raw in rows:
        row = dict(raw)
        document = json.loads(row['document'])
        if (not isinstance(document,dict) or any(document.get(key)!=row[key]
                for key in ('kind','local_id','incarnation','source_id','token'))
                or row['state'] not in ('claimed','sending','native_unknown','native_saved',
                                        'owner_claiming','owner_committed','legacy_unknown')):
            raise ValueError('category create journal is inconsistent; reconciliation required')
        result.append({**row,'document':document})
    return result


def pending_sources_in_db(db, kind):
    return {row['source_id'] for row in pending_in_db(db, kind)}


def guard_category_ids_in_db(db, kind, local_ids, *, except_token=None):
    """Exclude create-owned category IDs from concurrent link work.

    The caller must hold BEGIN IMMEDIATE through its own claim acquisition.
    An exact token permits the create owner to convert its intent to a group claim.
    """
    wanted = set(local_ids)
    for row in pending_in_db(db, kind):
        if row['local_id'] in wanted and (except_token is None or row['token'] != except_token):
            raise CreatePending('category is held by an unfinished Apple create')


def pending_sources(sync, kind):
    with sync._connect() as db:
        return pending_sources_in_db(db, kind)


def _check(db, claim, state, document):
    """Validate the entire prior journal entry under the manager write lock."""
    schema(db)
    kind,local_id,incarnation,source_id,token = claim
    row = db.execute('SELECT * FROM category_create_intents WHERE kind=? AND local_id=? AND incarnation=?',
                     (kind,local_id,incarnation)).fetchone()
    if (not row or row['source_id']!=source_id or row['token']!=token or row['state']!=state
            or json.loads(row['document'])!=document):
        raise ValueError('category create claim or evidence changed')
    return dict(row)


def _update(sync, claim, old_state, old_document, state, document, *, prior_claim=None):
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _check(db,claim,old_state,old_document)
        if prior_claim is not None:prior_claim._check(db)
        db.execute('UPDATE category_create_intents SET state=?,document=? WHERE kind=? AND local_id=? AND incarnation=? AND source_id=? AND token=?',
                   (state,json.dumps(document,ensure_ascii=False,sort_keys=True),*claim))


def _release(sync, claim, state, document, *, prior_claim=None):
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _check(db,claim,state,document)
        if prior_claim is not None:prior_claim._check(db)
        db.execute('DELETE FROM category_create_intents WHERE kind=? AND local_id=? AND incarnation=? AND source_id=? AND token=?', claim)


def create_native_category(sync, owner, kind, local, inventory, *, source_id, prior_claim=None, expected=None):
    """Create one native category under a durable incarnation-bound intent.

    The caller must have independently chosen source_id. No fallback account or
    name matching is performed. An existing binding requires the current rebuild claim.
    """
    if kind not in ('calendar','reminders'):
        raise ValueError('invalid category domain')
    local_id = local.get('id')
    identity(source_id, local_id)
    if source_id not in inventory.get('source_ids', []):
        raise ValueError('selected Apple source is not in the live inventory')
    expected = expected or owner.apple_capture_category_absence(local_id)
    generation = owner.apple_category_incarnation(expected)
    if (not isinstance(generation,str) or not generation or expected.get('local_id') != local_id
            or expected.get('local') != local or (local.get('apple') or {}).get('apple_id') and prior_claim is None):
        raise ValueError('unbound category and stable owner generation required')
    bound_source = (local.get('apple') or {}).get('source_id')
    if bound_source and bound_source != source_id and prior_claim is None:
        raise ValueError('category belongs to a different Apple source')
    token = secrets.token_hex(32)
    claim_key = (kind,local_id,generation,source_id,token)
    document = {'expected':expected,'incarnation':generation,'source_id':source_id,'kind':kind,'local_id':local_id,'token':token}
    binding = digest(document)
    receipt = sync.db_path.parent/'native-receipts'/('category-create-'+binding+'.json')
    document.update(binding=binding,receipt_file=str(receipt))
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE'); schema(db,create=True)
        existing = db.execute('SELECT state,source_id FROM category_create_intents WHERE kind=? AND local_id=?', (kind,local_id)).fetchone()
        if existing:
            raise CreatePending('category create is pending for source %s (%s); no retry' % (existing['source_id'],existing['state']))
        from apple_sync.group_scan_claims import schema as scan_schema
        held=db.execute('SELECT attempt_id FROM calendar_scan_items WHERE local_id=?',('category:'+local_id,)).fetchone() if scan_schema(db) else None
        if held and (prior_claim is None or held[0] not in prior_claim.tokens):
            raise CreatePending('category is held by another manager operation')
        linked=db.execute('SELECT * FROM category_links WHERE alcor_id=?',(local_id,)).fetchone()
        if prior_claim is not None:
            prior_claim._check(db)
            if not linked or dict(linked)!=prior_claim.evidence.get('rebuild_link'):
                raise ValueError('category rebuild link changed')
        elif linked:raise ValueError('category already linked')
        db.execute('INSERT INTO category_create_intents VALUES (?,?,?,?,?,?,?)',
                   (kind,local_id,generation,source_id,token,'claimed',json.dumps(document,ensure_ascii=False,sort_keys=True)))
    # A prior unbound receipt carries no incarnation evidence. It must not be
    # reused or bypassed with a fresh token; keep discovery paused for this source.
    legacy_receipt = receipt.parent/('category-'+local_id+'.json')
    if '/' in local_id or '\\' in local_id or legacy_receipt.exists():
        _update(sync,claim_key,'claimed',document,'legacy_unknown',{**document,'legacy_receipt':str(legacy_receipt)},prior_claim=prior_claim)
        raise CreatePending('older category create may have occurred; review required, no retry')
    try:
        receipt.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if receipt.exists():
            raise ValueError('unexpected category create receipt')
        listing, collection, op, singular = (('reminder_lists','lists','reminder_list_create','list') if kind=='reminders'
                                             else ('calendars','calendars','create_calendar','calendar'))
        capabilities=sync.bridge.call('reminder_status' if kind=='reminders' else 'status')
        if capabilities.get('category_create_version')!=1:
            raise ValueError('当前 Apple helper 不支持分类创建回执，请更新后重试；本地分类已保留')
        live = sync.bridge.call(listing)
        if source_id not in live.get('source_ids',[]) or not isinstance(live.get(collection),list):
            raise ValueError('selected Apple source is no longer available')
        if owner.apple_capture_category_absence(local_id) != expected:
            raise ValueError('category changed before create')
    except BaseException:
        _release(sync,claim_key,'claimed',document,prior_claim=prior_claim)
        raise
    if prior_claim is not None:prior_claim.started('sending')
    _update(sync,claim_key,'claimed',document,'sending',document,prior_claim=prior_claim)
    try:
        response = sync.bridge.call(op,category_create_version=1,title=local['name'],color=local['color'],source_id=source_id,
                                    receipt_binding=binding,receipt_file=str(receipt))
    except BridgeError as error:
        if error.native_no_side_effects:
            _release(sync,claim_key,'sending',document,prior_claim=prior_claim)
            if prior_claim is not None:prior_claim.native_rejected()
            raise
        _update(sync,claim_key,'sending',document,'native_unknown',document,prior_claim=prior_claim)
        raise CreatePending('Apple category create outcome unknown; no retry') from error
    except BaseException as error:
        _update(sync,claim_key,'sending',document,'native_unknown',document,prior_claim=prior_claim)
        raise CreatePending('Apple category create outcome unknown; no retry') from error
    try:
        proof = json.loads(Path(receipt).read_text())
        saved = response[singular]
        if (not isinstance(saved,dict) or proof != {'binding':binding,'saved':saved}
                or not saved.get('id') or saved.get('source_id') != source_id
                or saved.get('title') != local['name']
                or not isinstance(saved.get('color'),str) or not isinstance(local.get('color'),str)
                or saved['color'].upper() != local['color'].upper()):
            raise ValueError('native category create receipt does not match')
        identity(source_id,saved['id'])
        saved_document = {**document,'saved':saved,'proof':proof}
        _update(sync,claim_key,'sending',document,'native_saved',saved_document,prior_claim=prior_claim)
        document = saved_document
        return claim_key,document,saved
    except BaseException as error:
        raise CreatePending('category create requires reconciliation; no retry: %s' % error) from error


def create_category(sync,owner,kind,local,inventory,*,source_id):
    claim_key,document,saved=create_native_category(sync,owner,kind,local,inventory,source_id=source_id)
    local_id=local['id'];token=claim_key[-1];expected=document['expected']
    try:
        evidence = {'kind':'category_create','intent':token,'source_id':source_id,'saved':saved,'expected':expected}
        with WorkClaim(sync,{saved['id']:source_id},[],evidence) as claim:
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE'); claim._check(db)
                _check(db,claim_key,'native_saved',document)
                if db.execute('SELECT 1 FROM category_links WHERE apple_id=? OR alcor_id=?',(saved['id'],local_id)).fetchone():
                    raise ValueError('category already linked')
            # Owner CAS is a separate transaction. Any uncertain result holds both claims.
            claim.started('owner_claiming')
            _update(sync,claim_key,'native_saved',document,'owner_claiming',document)
            owner.apple_apply_category_if_unchanged(expected,{},category_binding(saved))
            claim.started('owner_committed')
            _update(sync,claim_key,'owner_claiming',document,'owner_committed',document)
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');claim._check(db)
                _check(db,claim_key,'owner_committed',document)
                if db.execute('SELECT 1 FROM category_links WHERE apple_id=? OR alcor_id=?',(saved['id'],local_id)).fetchone():
                    raise ValueError('category already linked')
                db.execute('INSERT INTO category_links (apple_id,alcor_id,direction,enabled,apple_name,alcor_name,name_conflict,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                           (saved['id'],local_id,'bidirectional',1,saved['title'],local['name'],'',sync.clock()))
                db.execute('DELETE FROM category_create_intents WHERE kind=? AND local_id=? AND incarnation=? AND source_id=? AND token=?',claim_key)
        return saved
    except BaseException as error:
        # Keep the source-scoped evidence even when the returned group cannot be claimed.
        raise CreatePending('category create requires reconciliation; no retry: %s' % error) from error


def status(sync,kind):
    with sync._connect() as db:rows=pending_in_db(db,kind)
    return [{'local_id':row['local_id'],'source_id':row['source_id'],'state':row['state'],
             'title':row['document']['expected']['local']['name'],
             'group_id':(row['document'].get('saved') or {}).get('id',''),
             'reason':'分类创建或本地提交结果待核对；不会凭同名关联或自动重试，已有分类同步继续'} for row in rows]
