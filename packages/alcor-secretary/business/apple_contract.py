"""Durable Apple bindings and operations. Only the trusted Apple peer can acknowledge writes."""
import json
import uuid
from datetime import datetime, timezone,timedelta
from business.apple_recovery import capture_inbound_delete, list_inbound_recoveries, restore_inbound_recovery


def _pending_delete_message(state):
    if state == 'sending':return '删除正在提交，请等待本机操作结果；暂不能取消'
    if state == 'uncertain':return '删除结果未知，内容和证据已保留；请核对，不要重复提交'
    return '删除等待本次确认，内容已保留；可取消此请求'


def setup_apple_contract(db, items):
    db.executescript('''CREATE TABLE IF NOT EXISTS apple_bindings (
        entity TEXT NOT NULL, local_id TEXT NOT NULL, document TEXT NOT NULL,
        PRIMARY KEY(entity,local_id));
        CREATE TABLE IF NOT EXISTS apple_completion_history (local_id TEXT NOT NULL,native_id TEXT NOT NULL,occurrence_on TEXT NOT NULL,document TEXT NOT NULL,PRIMARY KEY(local_id,native_id,occurrence_on));
        CREATE TABLE IF NOT EXISTS apple_operations (
        id TEXT PRIMARY KEY, entity TEXT NOT NULL, local_id TEXT NOT NULL, action TEXT NOT NULL,
        document TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL);''')
    columns = {row[1] for row in db.execute(f'PRAGMA table_info({items})')}
    if 'apple_data' not in columns:
        db.execute(f"ALTER TABLE {items} ADD COLUMN apple_data TEXT NOT NULL DEFAULT '{{}}'")



class AppleContract:
    def _apple_binding(self, db, entity, identity):
        row = db.execute('SELECT document FROM apple_bindings WHERE entity=? AND local_id=?', (entity,identity)).fetchone()
        return json.loads(row[0]) if row else None

    def _apple_guard(self, db, entity, identity, operation):
        binding = self._apple_binding(db, entity, identity)
        if entity == 'item' and db.execute("SELECT 1 FROM apple_operations WHERE entity='item' AND local_id=? AND action IN ('truncate','exclude') AND state IN ('sending','uncertain')", (identity,)).fetchone():
            raise ValueError('此重复日程的 Apple 操作已开始或结果不确定，请先核对 Apple')
        if entity == 'category' and operation == 'can_delete' and self.APPLE_ITEMS == 'calendar_events' and db.execute("SELECT 1 FROM apple_operations AS op JOIN calendar_events AS event ON op.local_id=event.id WHERE op.entity='item' AND event.category_id=? AND op.action IN ('truncate','exclude') AND op.state IN ('sending','uncertain')", (identity,)).fetchone():
            raise ValueError('该分类中有 Apple 重复日程操作正在执行或结果不确定，请先核对 Apple')
        if binding and binding.get('planned'):
            return None
        if binding and not binding.get(operation, False):
            raise PermissionError('Apple 来源不允许此操作，请先检查账户或来源权限')
        if entity=='item':
            item = db.execute(f'SELECT category_id FROM {self.APPLE_ITEMS} WHERE id=?',(identity,)).fetchone()
            queued=db.execute("SELECT state FROM apple_operations WHERE entity='category' AND local_id=? AND action='delete' AND state IN ('pending','sending','uncertain')",(item[0],)).fetchone() if item else None
            if queued:raise ValueError('所属分类：'+_pending_delete_message(queued['state']))
        queued=db.execute("SELECT state FROM apple_operations WHERE entity=? AND local_id=? AND action='delete' AND state IN ('pending','sending','uncertain')", (entity,identity)).fetchone()
        if queued:raise ValueError(_pending_delete_message(queued['state']))
        return binding

    def _apple_decorate(self, db, entity, row):
        binding = self._apple_binding(db, entity, row['id'])
        pending = db.execute("SELECT id,action,state,error,document FROM apple_operations WHERE entity=? AND local_id=? AND state IN ('pending','sending','uncertain') ORDER BY created_at", (entity,row['id'])).fetchall()
        history = db.execute('SELECT document FROM apple_completion_history WHERE local_id=? ORDER BY occurrence_on DESC LIMIT 1',(row['id'],)).fetchone() if entity=='item' else None
        return {**dict(row), **(json.loads(row.get('apple_data') or '{}') if isinstance(row,dict) else json.loads(row['apple_data'] or '{}') if 'apple_data' in row.keys() else {}), **({'apple':binding,**({'display_name':binding.get('desired_title') or binding.get('title') or row['name']} if entity=='category' else {})} if binding else {}), 'apple_pending':[dict(item) for item in pending],**({'completion_history':[json.loads(history[0])]} if history else {})}

    def apple_bind(self, entity, identity, document):
        if entity not in ('category','item') or not isinstance(document,dict):
            raise ValueError('Invalid Apple binding')
        with self._connect() as db:
            old = self._apple_binding(db,entity,identity) or {}
            if old.get('desired_title') and old['desired_title']!=document.get('title'):
                document = {**document,'desired_title':old['desired_title']}
            db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)', (entity,identity,json.dumps(document,ensure_ascii=False)))
        return True

    def _apple_unbound_snapshot(self,db,entity,identity,*,require_unbound=True):
        from business.object_generations import generations_for
        if entity not in ('category','item'):raise ValueError('invalid Apple binding entity')
        table=self.APPLE_CATEGORIES if entity=='category' else self.APPLE_ITEMS
        row=db.execute(f'SELECT * FROM {table} WHERE id=?',(identity,)).fetchone()
        if row is None or require_unbound and self._apple_binding(db,entity,identity):return None
        return {'entity':entity,'local_id':identity,'generation':generations_for(db,entity,[identity])[0][1],
                'row':dict(row),'local':self._apple_decorate(db,entity,self._with_progress(db,dict(row)) if entity=='item' and self.APPLE_ITEMS=='todos' else row)}

    def apple_verify_import_unlink(self, operation_id, category_id, snapshot):
        from business.import_category_delete import verify
        return verify(self,operation_id,category_id,snapshot)

    def apple_capture_unbound(self,entity,identity):
        """Private legacy read: no inferred source and no permission grant."""
        with self._connect() as db:
            db.execute('BEGIN')
            return self._apple_unbound_snapshot(db,entity,identity)

    def apple_prime_unbound(self,expected,document):
        """Install only a restrictive placeholder on the same still-unbound incarnation."""
        if (not isinstance(expected,dict) or expected.get('entity') not in ('category','item')
                or not isinstance(document,dict) or not document.get('apple_id')
                or document.get('awaiting_permissions') is not True
                or set(document)-{'apple_id','group_id','title','can_modify','can_rename','can_delete','awaiting_permissions'}
                or any(document.get(key,False) is not False for key in ('can_modify','can_rename','can_delete'))):
            raise ValueError('legacy placeholder cannot grant Apple permissions')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._apple_unbound_snapshot(db,expected['entity'],expected['local_id'])
            if current is None or current!=expected:return False
            db.execute('INSERT INTO apple_bindings VALUES (?,?,?)',
                       (expected['entity'],expected['local_id'],json.dumps(document,ensure_ascii=False)))
            return True

    def _apple_queue(self, db, entity, identity, action, document=None, *, operation_id=None, approved_snapshot=None, approved_native=None):
        if action in ('delete', 'truncate', 'exclude') and approved_snapshot is None:
            raise PermissionError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')
        document = dict(document or {})
        approved = operation_id is not None or approved_snapshot is not None or approved_native is not None
        if approved:
            if (not isinstance(operation_id, str) or len(operation_id) != 64 or any(c not in '0123456789abcdef' for c in operation_id)
                    or not isinstance(approved_snapshot, str) or len(approved_snapshot) != 64 or any(c not in '0123456789abcdef' for c in approved_snapshot)
                    or not isinstance(approved_native, dict)):
                raise ValueError('Apple 操作授权身份或快照无效')
            document['_approval'] = {'snapshot': approved_snapshot, 'native': approved_native}
        if action in ('delete', 'truncate', 'exclude'):
            binding = self._apple_binding(db, entity, identity) or {}
            document['_expected_target'] = {'apple_id': binding.get('apple_id'), 'group_id': binding.get('group_id'),
                                            'source_id': binding.get('source_id')}
            if action in ('truncate', 'exclude'):
                document['_expected_target']['native'] = binding.get('native')
                row = db.execute('SELECT * FROM calendar_events WHERE id=?', (identity,)).fetchone()
                if not row:
                    raise ValueError('日程已不存在，请刷新后重试')
                document['_expected_local'] = dict(row)
        if approved:
            import hashlib
            if action not in ('delete','truncate','exclude'):
                raise ValueError('此 Apple 操作不支持删除授权')
            expected_scope = 'this' if action == 'exclude' else 'future' if action == 'truncate' else document.get('scope')
            expected_group = binding.get('apple_id') if entity == 'category' else binding.get('group_id')
            remote = approved_native.get('native_snapshot')
            fingerprint = hashlib.sha256(json.dumps(remote,sort_keys=True,ensure_ascii=False,separators=(',', ':')).encode()).hexdigest()
            remote_identity = remote.get('id' if entity == 'category' else 'reminder_id' if self.APPLE_ITEMS == 'todos' else 'event_id') if isinstance(remote,dict) else None
            remote_group = remote.get('id' if entity == 'category' else 'list_id' if self.APPLE_ITEMS == 'todos' else 'calendar_id') if isinstance(remote,dict) else None
            if (approved_native.get('version') != 1 or approved_native.get('entity') != entity
                    or not binding.get('source_id') or approved_native.get('source_id') != binding.get('source_id')
                    or approved_native.get('series_apple_id') != binding.get('apple_id')
                    or approved_native.get('group_id') != expected_group or not expected_group
                    or approved_native.get('scope') != expected_scope
                    or approved_native.get('occurrence_on') != document.get('occurrence_on')
                    or not remote_identity or approved_native.get('instance_apple_id') != remote_identity
                    or remote_group != expected_group
                    or remote.get('source_id') != binding.get('source_id')
                    or approved_native.get('native_fingerprint') != fingerprint):
                raise ValueError('Apple 目标与已批准的来源、范围或原生快照不一致')
        encoded = json.dumps(document,sort_keys=True,ensure_ascii=False)
        if approved:
            old = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
            if old:
                if old['entity'] != entity or old['local_id'] != identity or old['action'] != action or old['document'] != encoded:
                    raise ValueError('同一操作标识不能用于不同 Apple 目标或授权')
                return {'operation_id': operation_id, 'state': old['state'], 'pending': old['state'] in ('pending','sending','uncertain'), 'replayed': True}
            if action in ('delete', 'truncate', 'exclude'):
                domain = 'calendar' if self.APPLE_ITEMS == 'calendar_events' else 'tasks'
                for target in document.get('native_range_ids',[identity]):
                    recovery = capture_inbound_delete(db, domain, entity, target)
                    if not recovery:
                        raise ValueError('无法保存 Apple 删除的恢复材料')
                    db.execute('UPDATE apple_inbound_recovery SET status=?,result=? WHERE id=?',
                               ('operation_approved', json.dumps({'operation_id':operation_id,'phase':'approved'}), recovery))
            db.execute('INSERT INTO apple_operations(id,entity,local_id,action,document,created_at) VALUES (?,?,?,?,?,?)',
                       (operation_id,entity,identity,action,encoded,datetime.now(timezone.utc).isoformat()))
            return {'operation_id': operation_id, 'state': 'pending', 'pending': True, 'replayed': False}
        old = db.execute("SELECT id FROM apple_operations WHERE entity=? AND local_id=? AND action=? AND document=? AND state IN ('pending','sending','uncertain')", (entity,identity,action,encoded)).fetchone()
        identity_op = old[0] if old else str(uuid.uuid4())
        if not old:
            db.execute('INSERT INTO apple_operations(id,entity,local_id,action,document,created_at) VALUES (?,?,?,?,?,?)',
                       (identity_op,entity,identity,action,encoded,datetime.now(timezone.utc).isoformat()))
        return {'pending':True,'operation_id':identity_op}

    def apple_operations(self):
        with self._connect() as db:
            return [{**dict(row),'document':json.loads(row['document']), 'binding':self._apple_binding(db,row['entity'],row['local_id'])}
                    for row in db.execute("SELECT * FROM apple_operations WHERE state IN ('pending','sending','uncertain') ORDER BY created_at,id")]

    def cancel_apple_operation(self, operation_id):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
            if not row:
                raise ValueError('待处理操作已完成或不存在')
            if row['state'] in ('sending', 'uncertain'):
                raise ValueError('Apple 操作已开始或结果不确定，请先核对 Apple')
            if row['state'] != 'pending':
                raise ValueError('待处理操作已完成或不存在')
            document = json.loads(row['document'])
            if row['action']=='exclude' and not document.get('_approval'):
                db.execute('DELETE FROM calendar_occurrence_exclusions WHERE series_id=? AND occurrence_on=?',(row['local_id'],document['occurrence_on']))
            db.execute("UPDATE apple_operations SET state='cancelled',error='' WHERE id=?", (operation_id,))
        return {'cancelled':True}

    def _assert_delete_scope(self, db, entity, identity):
        # Apple item deletion does not imply deletion of separately bound descendants.
        if self.APPLE_ITEMS == 'todos':
            anchor = 'id' if entity == 'item' else 'category_id'
            rows = db.execute(f"""WITH RECURSIVE affected(id) AS (
                SELECT id FROM todos WHERE {anchor}=? UNION
                SELECT item.id FROM todos item JOIN affected parent ON item.parent_id=parent.id)
                SELECT id FROM affected""", (identity,)).fetchall()
        else:
            rows = db.execute('SELECT id FROM calendar_events WHERE ' + ('id=?' if entity == 'item' else 'category_id=?'), (identity,)).fetchall()
        category_binding = self._apple_binding(db, 'category', identity) if entity == 'category' else None
        for row in rows:
            item_binding = self._apple_binding(db, 'item', row['id'])
            if not item_binding or item_binding.get('planned'): continue
            if entity == 'item' and row['id'] != identity:
                raise ValueError('子事务关联其他 Apple 对象，请先分别处理并重新确认')
            if entity == 'category' and (not category_binding or not category_binding.get('source_id')
                    or not item_binding.get('source_id') or not item_binding.get('group_id')):
                raise ValueError('分类成员的 Apple 来源身份不完整，请先核对来源后重新确认')
            if entity == 'category' and (item_binding.get('source_id') != category_binding.get('source_id')
                    or item_binding.get('group_id') != category_binding.get('apple_id')):
                raise ValueError('分类包含其他 Apple 来源的对象，请先分别处理并重新确认')

    def apple_begin_operation(self, operation_id, document, binding):
        return self._begin_operation(operation_id, document, binding)

    def apple_begin_approved_delete(self, operation_id, document, binding):
        return self._begin_operation(operation_id, document, binding, approved_delete=True)

    def _begin_operation(self, operation_id, document, binding, *, approved_delete=False):
        """Claim one unchanged pending operation; later cancellation cannot pretend it stopped."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
            if row and row['action'] in ('delete', 'truncate', 'exclude') and not approved_delete:
                raise PermissionError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')
            if not row or row['state'] != 'pending':
                raise ValueError('Apple 操作已取消、开始或不存在，请刷新核对')
            if approved_delete and (row['action'] not in ('delete', 'truncate', 'exclude') or not document.get('_approval')):
                raise ValueError('not an approved deletion')
            if json.loads(row['document']) != document or self._apple_binding(db, row['entity'], row['local_id']) != binding:
                raise ValueError('Apple 目标或权限已变化，请重新确认')
            if row['action'] == 'delete':
                self._assert_delete_scope(db, row['entity'], row['local_id'])
            if row['action'] in ('truncate', 'exclude'):
                current = db.execute('SELECT * FROM calendar_events WHERE id=?', (row['local_id'],)).fetchone()
                if not current or dict(current) != document.get('_expected_local'):
                    raise ValueError('本地日程已变化，请重新确认')
            if row['action'] in ('delete', 'truncate', 'exclude') and document.get('_approval'):
                from business.apple_delete_approval import revalidate_delete_snapshot
                revalidate_delete_snapshot(self, db, row)
            db.execute("UPDATE apple_operations SET state='sending',error='' WHERE id=? AND state='pending'", (operation_id,))
        return True

    def apple_reset_approved_delete(self, operation_id, document, binding):
        """Private caller must first prove the claimed native call had no side effects."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
            if (not row or row['state'] != 'sending' or row['action'] not in ('delete', 'truncate', 'exclude')
                    or not document.get('_approval') or json.loads(row['document']) != document
                    or self._apple_binding(db, row['entity'], row['local_id']) != binding):
                raise ValueError('approved deletion claim changed')
            from business.apple_delete_approval import revalidate_delete_snapshot
            revalidate_delete_snapshot(self, db, row)
            db.execute("UPDATE apple_operations SET state='pending',error='' WHERE id=? AND state='sending'", (operation_id,))
        return {'state': 'pending', 'not_sent': True}

    def apple_operation_error(self, operation_id, error):
        with self._connect() as db:
            db.execute("UPDATE apple_operations SET error=?, state=CASE WHEN state='sending' THEN 'uncertain' ELSE state END WHERE id=? AND state IN ('pending','sending','uncertain')", (str(error),operation_id))
        return True

    def apple_finish_operation(self, operation_id):
        return self._finish_operation(operation_id)

    def apple_finish_approved_delete(self, operation_id):
        return self._finish_operation(operation_id, approved_delete=True)

    def _finish_operation(self, operation_id, *, approved_delete=False):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
            if not row:
                raise KeyError('Apple operation not found')
            if row['action'] in ('delete', 'truncate', 'exclude') and not approved_delete:
                raise PermissionError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')
            if row['state'] not in ('pending', 'sending', 'uncertain'):
                return {'state':row['state']}
            document = json.loads(row['document'])
            if approved_delete and (row['action'] not in ('delete', 'truncate', 'exclude') or not document.get('_approval') or row['state'] not in ('sending', 'uncertain')):
                raise ValueError('approved deletion has not been claimed')
            if row['action'] == 'delete' or (row['action'] in ('truncate', 'exclude') and document.get('_approval')):
                try:
                    if row['action'] == 'delete': self._assert_delete_scope(db, row['entity'], row['local_id'])
                    if document.get('_approval'):
                        from business.apple_delete_approval import revalidate_delete_snapshot
                        revalidate_delete_snapshot(self, db, row)
                except (ValueError, KeyError):
                    marker = json.dumps({'operation_id':operation_id,'phase':'conflict'})
                    has_recovery = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_inbound_recovery'").fetchone()
                    exists = db.execute("SELECT 1 FROM apple_inbound_recovery WHERE status='operation_conflict' AND result=?", (marker,)).fetchone() if has_recovery else None
                    if not exists:
                        domain = 'calendar' if self.APPLE_ITEMS == 'calendar_events' else 'tasks'
                        recovery = capture_inbound_delete(db, domain, row['entity'], row['local_id'])
                        if recovery:
                            db.execute('UPDATE apple_inbound_recovery SET status=?,result=? WHERE id=?', ('operation_conflict', marker, recovery))
                    reason = 'Apple 操作已执行或结果待核对，本地内容发生变化，所有本地内容及恢复材料已保留'
                    db.execute("UPDATE apple_operations SET state='uncertain',error=? WHERE id=?", (reason, operation_id))
                    return {'state':'uncertain', 'conflict':True, 'reason':reason}
            if row['action'] == 'exclude':
                db.execute('INSERT INTO calendar_occurrence_exclusions VALUES (?,?)', (row['local_id'], document['occurrence_on']))
            if row['action']=='truncate':
                document = json.loads(row['document'])
                until = (datetime.fromisoformat(document['occurrence_on']).date()-timedelta(days=1)).isoformat()
                db.execute('UPDATE calendar_events SET repeat_until=? WHERE id=?',(until,row['local_id']))
            if row['action'] == 'delete':
                identity = row['local_id']
                if self.APPLE_ITEMS == 'calendar_events':
                    if document.get('native_range_ids'):
                        items=[db.execute('SELECT * FROM calendar_events WHERE id=?',(target,)).fetchone() for target in document['native_range_ids']]
                    else:
                        items = db.execute('SELECT * FROM calendar_events WHERE '+('category_id=?' if row['entity']=='category' else 'id=?'), (identity,)).fetchall()
                    for item in items:
                        self._delete_event(db,item)
                else:
                    items = db.execute('SELECT id FROM todos WHERE '+('category_id=?' if row['entity']=='category' else 'id=?'), (identity,)).fetchall()
                    for item in items:
                        self._delete_todo_tree(db,item['id'])
                if row['entity'] == 'category':
                    db.execute(f'DELETE FROM {self.APPLE_CATEGORIES} WHERE id=?', (identity,))
                for item in items:
                    db.execute("DELETE FROM apple_bindings WHERE entity='item' AND local_id=?",(item['id'],))
                    db.execute("UPDATE apple_operations SET state='done',error='' WHERE entity='item' AND local_id=? AND state='pending'",(item['id'],))
                    db.execute('DELETE FROM apple_completion_history WHERE local_id=?',(item['id'],))
                db.execute('DELETE FROM apple_bindings WHERE entity=? AND local_id=?', (row['entity'],identity))
            db.execute("UPDATE apple_operations SET state='done',error='' WHERE id=?", (operation_id,))
            if row['action'] in ('delete', 'truncate', 'exclude') and document.get('_approval'):
                marker = json.dumps({'operation_id':operation_id,'phase':'approved'})
                db.execute("UPDATE apple_inbound_recovery SET status='operation_completed' WHERE status='operation_approved' AND result=?", (marker,))
        if self.APPLE_ITEMS == 'todos':
            self._sync_scheduler_links()
        return {'state':'done'}

    def apple_apply_category(self, category_id, payload):
        return self.update_category(category_id,payload,_apple=True)

    def apple_connect_category(self, operation_id, identity, expected, payload, binding):
        """Private selection: create/bind or conditionally reassign one local category."""
        from business.object_generations import generations_for
        from business.security_receipts import read_receipt,save_receipt,validate_operation_id
        validate_operation_id(operation_id)
        def changed(reason):raise ValueError('CONNECTION_CHANGED: '+reason)
        if (not isinstance(identity,str) or not identity or not isinstance(payload,dict)
                or set(payload)-{'name','color'} or not isinstance(binding,dict)
                or not binding.get('apple_id') or not binding.get('source_id')):
            changed('invalid category connection')
        if expected is None:
            name=payload.get('name');color=payload.get('color','#6B8AF7')
            if (not isinstance(name,str) or not name.strip() or len(name.strip())>200
                    or not isinstance(color,str) or len(color)!=7 or color[0]!='#'
                    or any(c not in '0123456789abcdefABCDEF' for c in color[1:])):
                changed('invalid new category name/color')
        elif not isinstance(expected,dict) or expected.get('local_id')!=identity or payload:
            changed('existing category snapshot required')
        args={'identity':identity,'expected':expected,'payload':payload,'binding':binding}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt=read_receipt(db,operation_id,'connect_apple_category',args)
            row=db.execute(f'SELECT * FROM {self.APPLE_CATEGORIES} WHERE id=?',(identity,)).fetchone()
            if receipt:
                current=self._apple_binding(db,'category',identity) or {};stamp=current.get('_connection_receipt') or {}
                if (not row or stamp.get('operation_id')!=operation_id
                        or generations_for(db,'category',[identity])[0][1]!=stamp.get('generation')):
                    changed('connection receipt belongs to another incarnation')
                local=self._apple_decorate(db,'category',row)
                local['apple']={k:v for k,v in local['apple'].items() if k!='_connection_receipt'}
                if local!=stamp.get('local'):changed('category changed after connection commit')
                return self._apple_decorate(db,'category',row)
            if expected is None:
                if row or db.execute(f'SELECT 1 FROM {self.APPLE_CATEGORIES} WHERE name=?',(name.strip(),)).fetchone():
                    changed('new category identity or name already exists')
                db.execute(f'INSERT INTO {self.APPLE_CATEGORIES} VALUES (?,?,?,?)',(identity,name.strip(),color,datetime.now(timezone.utc).isoformat()))
            else:
                if self._apple_category_absence_snapshot(db,identity)!=expected:changed('category or members changed')
                if expected['local'].get('apple_pending'):
                    old=self._apple_binding(db,'category',identity) or {}
                    if any(old.get(key)!=binding.get(key) for key in ('apple_id','source_id')):
                        changed('category operation is pending')
                    # Selection-only direction changes must not replace pending-operation evidence.
                    return self._apple_decorate(db,'category',row)
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='apple_inbound_local_only'").fetchone() and db.execute("SELECT 1 FROM apple_inbound_local_only WHERE entity='category' AND local_id=?",(identity,)).fetchone():
                    changed('recovered local category requires an explicit export workflow')
            old=self._apple_binding(db,'category',identity) or {};saved=dict(binding)
            if old.get('desired_title') and old['desired_title']!=saved.get('title'):saved['desired_title']=old['desired_title']
            db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)',('category',identity,json.dumps(saved,ensure_ascii=False)))
            row=db.execute(f'SELECT * FROM {self.APPLE_CATEGORIES} WHERE id=?',(identity,)).fetchone()
            saved['_connection_receipt']={'operation_id':operation_id,'generation':generations_for(db,'category',[identity])[0][1],
                                          'local':self._apple_decorate(db,'category',row)}
            db.execute("UPDATE apple_bindings SET document=? WHERE entity='category' AND local_id=?",(json.dumps(saved,ensure_ascii=False),identity))
            save_receipt(db,operation_id,'connect_apple_category',args,identity)
            return self._apple_decorate(db,'category',row)

    def apple_commit_reminder_category_rebind(self, operation_id, expected, new_category, native_items):
        if self.APPLE_ITEMS!='todos':raise ValueError('reminder owner required')
        from business.reminder_rebind import commit
        return commit(self,operation_id,expected,new_category,native_items)

    def apple_apply_category_if_unchanged(self, expected, payload, binding=None):
        """Private peer: category fields and binding commit against one owner snapshot."""
        if (not isinstance(expected,dict) or expected.get('version')!=1 or not expected.get('local_id')
                or not isinstance(payload,dict) or set(payload)-{'name','color'}
                or binding is not None and not isinstance(binding,dict)):
            raise ValueError('invalid conditional category update')
        if 'name' in payload and (not isinstance(payload['name'],str) or not payload['name'].strip() or len(payload['name'].strip())>200):
            raise ValueError('category name must contain 1 to 200 characters')
        if 'color' in payload and (not isinstance(payload['color'],str) or len(payload['color'])!=7 or payload['color'][0]!='#' or any(c not in '0123456789abcdefABCDEF' for c in payload['color'][1:])):
            raise ValueError('color must be #RRGGBB')
        identity=expected['local_id']
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._apple_category_absence_snapshot(db,identity)!=expected:
                raise ValueError('CATEGORY_CHANGED: category or members changed after capture')
            old=self._apple_binding(db,'category',identity) or {}
            if binding is not None:
                if (not binding.get('apple_id') or not binding.get('source_id') or
                        old.get('apple_id') and old['apple_id']!=binding['apple_id'] or
                        old.get('source_id') and old['source_id']!=binding['source_id']):
                    raise ValueError('CATEGORY_CHANGED: category source or identity changed')
            if 'name' in payload:
                name=self._apple_category_name(db,identity,payload['name'].strip(),True)
                db.execute(f'UPDATE {self.APPLE_CATEGORIES} SET name=? WHERE id=?',(name,identity))
                if self.APPLE_ITEMS=='todos':
                    db.execute('UPDATE todos SET list_name=? WHERE category_id=?',(name,identity))
            if 'color' in payload:
                db.execute(f'UPDATE {self.APPLE_CATEGORIES} SET color=? WHERE id=?',(payload['color'],identity))
            if binding is not None:
                binding=dict(binding)
                if 'name' not in payload and old.get('desired_title') and old['desired_title']!=binding.get('title'):
                    binding['desired_title']=old['desired_title']
                db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)',('category',identity,json.dumps(binding,ensure_ascii=False)))
            row=db.execute(f'SELECT * FROM {self.APPLE_CATEGORIES} WHERE id=?',(identity,)).fetchone()
            return self._apple_decorate(db,'category',row)

    def _apple_fields(self, db, identity, payload, trusted=False):
        old = db.execute(f'SELECT apple_data FROM {self.APPLE_ITEMS} WHERE id=?',(identity,)).fetchone()
        data = json.loads(old[0] or '{}') if old else {}
        fields = ('url','alarms','floating','native_recurrence','native_series_key','native_managed','attendees','native_snapshot','edit_scope','completion_history')
        for key in fields:
            if key not in payload:
                continue
            value = payload[key]
            if key in ('attendees','native_snapshot','native_managed','native_series_key','completion_history') and not trusted:
                continue
            if key=='edit_scope' and value not in ('this','future'):
                raise ValueError('请选择本次或将来所有日程')
            if key=='url' and (not isinstance(value,str) or len(value)>8192 or any(ord(c)<32 for c in value) or value.lower().startswith(('javascript:','data:'))):
                raise ValueError('Invalid calendar URL')
            if key in ('alarms','attendees') and (not isinstance(value,list) or len(value)>50):
                raise ValueError('Invalid Apple metadata array')
            if key=='floating' and not isinstance(value,bool):
                raise ValueError('floating must be boolean')
            if key=='native_recurrence' and not isinstance(value,dict):
                raise ValueError('Invalid recurrence')
            data[key] = value
        encoded = json.dumps(data,ensure_ascii=False)
        if len(encoded)>262144:
            raise ValueError('Apple metadata is too large')
        db.execute(f'UPDATE {self.APPLE_ITEMS} SET apple_data=? WHERE id=?',(encoded,identity))

    def apple_apply_metadata(self, identity, payload):
        was_native = self.get_todo(identity).get('native_managed') if self.APPLE_ITEMS=='todos' and payload.get('native_managed') else True
        with self._connect() as db:
            self._apple_fields(db,identity,payload,True)
        if self.APPLE_ITEMS=='todos' and not was_native:
            self._backfill_scheduler_links()
            self._sync_scheduler_links()
        return True

    def apple_capture_item_restore(self, identity, payload, category_id, native, source_key=None):
        from business.apple_item_restore import capture
        return capture(self, identity, payload, category_id, native, source_key)

    def apple_restore_item(self, operation_id, expected):
        from business.apple_item_restore import commit
        return commit(self, operation_id, expected)

    def apple_verify_item_restore(self, operation_id, expected):
        from business.apple_item_restore import verify
        return verify(self, operation_id, expected)

    def apple_record_completion(self, identity, previous, payload):
        if self.APPLE_ITEMS != 'todos':
            raise ValueError('Completion is only available for reminders')
        with self._connect() as db:
            db.execute('INSERT OR IGNORE INTO apple_completion_history VALUES (?,?,?,?)',(identity,previous.get('reminder_id',''),previous.get('due_on') or '',json.dumps(previous,ensure_ascii=False)))
        updated = self.apple_apply_item(identity,payload)
        with self._connect() as db:
            db.execute("UPDATE todos SET status=?,completed_at=?,user_modified=0 WHERE id=?",('completed' if payload.get('completed') else 'todo',payload.get('completed_at'),identity))
        return self.get_todo(identity)

    def apple_apply_item(self, identity, payload):
        if self.APPLE_ITEMS=='calendar_events':
            return self.update_event(identity,payload,_apple=True)
        return self.update_todo(identity,payload,_apple=True)

    def apple_place_item(self, identity, category_id):
        if self.APPLE_ITEMS=='calendar_events':
            return self.set_event_category(identity,category_id,_apple=True)
        return self.set_todo_category(identity,category_id,_apple=True)

    def apple_exclude(self, identity, day):
        # Inbound detached-instance projection keeps the series and imports the replacement.
        day = datetime.fromisoformat(day).date().isoformat()
        series = self.get_event(identity)
        if series['repeat_frequency'] == 'none':
            raise ValueError('event is not a recurring series')
        with self._connect() as db:
            db.execute('INSERT OR IGNORE INTO calendar_occurrence_exclusions VALUES (?,?)', (identity, day))

    def apple_local_only_categories(self):
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_inbound_local_only'").fetchone():
                return []
            return [row[0] for row in db.execute("SELECT local_id FROM apple_inbound_local_only WHERE entity='category'")]

    def list_apple_inbound_recoveries(self):
        with self._connect() as db:
            return list_inbound_recoveries(db, 'calendar' if self.APPLE_ITEMS == 'calendar_events' else 'tasks')

    def restore_apple_inbound_recovery(self, recovery_id):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return restore_inbound_recovery(db, 'calendar' if self.APPLE_ITEMS == 'calendar_events' else 'tasks', recovery_id)

    def apple_remove_item(self,identity,preserve_children=False):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            capture_inbound_delete(db, 'calendar' if self.APPLE_ITEMS == 'calendar_events' else 'tasks', 'item', identity)
            if self.APPLE_ITEMS=='calendar_events':
                row = db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone()
                if row:
                    self._delete_event(db,row)
            else:
                self._delete_todo_tree(db,identity,preserve_children)
            db.execute("DELETE FROM apple_bindings WHERE entity='item' AND local_id=?",(identity,))
            db.execute('DELETE FROM apple_completion_history WHERE local_id=?',(identity,))
        if self.APPLE_ITEMS=='todos':
            self._sync_scheduler_links()
        return True

    def _apple_category_absence_snapshot(self, db, identity):
        from business.apple_recovery import _rows
        from business.apple_delete_approval import _digest
        plan = self._local_category_delete_snapshot(db, identity, allow_apple=True)
        row = db.execute(f'SELECT * FROM {self.APPLE_CATEGORIES} WHERE id=?', (identity,)).fetchone()
        ids = [target['id'] for target in plan['targets'] if not target.get('category')]
        items = _rows(db, self.APPLE_ITEMS, 'id', ids)
        # The deletion plan already covers recursive relations; include its recovery-only metadata too.
        extras = {'history': _rows(db, 'apple_completion_history', 'local_id', ids),
                  'tombstones': _rows(db, 'source_tombstones', 'source_key', sorted({item['source_key'] for item in items if item.get('source_key')}))}
        for rows in extras.values():
            rows.sort(key=lambda item: json.dumps(item, sort_keys=True))
        return {'version': 1, 'local_id': identity, 'local': self._apple_decorate(db, 'category', row),
                'snapshot': _digest({'plan': plan['snapshot'], **extras})}


    def apple_capture_category_absence(self, identity):
        """Private peer: capture once at the first trustworthy remote absence."""
        if not isinstance(identity, str) or not identity:
            raise ValueError('invalid category absence target')
        with self._connect() as db:
            db.execute('BEGIN')
            return self._apple_category_absence_snapshot(db, identity)

    def apple_category_incarnation(self, expected):
        """Read a stable generation only while it belongs to the captured category."""
        from business.object_generations import generations_for
        if not isinstance(expected,dict) or not isinstance(expected.get('local_id'),str):
            raise ValueError('category snapshot required')
        with self._connect() as db:
            db.execute('BEGIN')
            if self._apple_category_absence_snapshot(db,expected['local_id'])!=expected:
                raise ValueError('CATEGORY_CHANGED: category changed before incarnation read')
            return generations_for(db,'category',[expected['local_id']])[0][1]


    def apple_remove_category_if_unchanged(self, expected):
        """Private peer: compare the first absence snapshot and archive/delete under one lock."""
        if (not isinstance(expected, dict) or set(expected) != {'version', 'local_id', 'local', 'snapshot'}
                or expected['version'] != 1 or not isinstance(expected['local_id'], str)
                or not expected['local_id'] or not isinstance(expected['local'], dict)
                or not isinstance(expected['snapshot'], str) or len(expected['snapshot']) != 64):
            raise ValueError('first category absence snapshot required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._apple_category_absence_snapshot(db, expected['local_id']) != expected:
                raise ValueError('CATEGORY_CHANGED: category changed since first remote absence; review required')
            recovery = self._apple_remove_category(db, expected['local_id'])
        if self.APPLE_ITEMS == 'todos':
            self._sync_scheduler_links()
        return {'deleted': True, 'id': expected['local_id'], 'recovery_id': recovery}


    def apple_category_absence_targets(self, expected):
        """Read the exact recursive deletion targets only for the unchanged first snapshot."""
        if not isinstance(expected,dict) or not isinstance(expected.get('local_id'),str):
            raise ValueError('first category absence snapshot required')
        with self._connect() as db:
            db.execute('BEGIN')
            if self._apple_category_absence_snapshot(db,expected['local_id'])!=expected:
                raise ValueError('CATEGORY_CHANGED: category changed since first remote absence; review required')
            plan=self._local_category_delete_snapshot(db,expected['local_id'],allow_apple=True)
            rows=[]
            for target in plan['targets']:
                if target.get('category'):continue
                binding=db.execute("SELECT document FROM apple_bindings WHERE entity='item' AND local_id=?",(target['id'],)).fetchone()
                item=db.execute(f'SELECT category_id,source_type FROM {self.APPLE_ITEMS} WHERE id=?',(target['id'],)).fetchone()
                rows.append({'id':target['id'],'category_id':item['category_id'],'source_type':item['source_type'],
                             'binding':json.loads(binding[0]) if binding else None})
            return sorted(rows,key=lambda row:row['id'])


    def _apple_remove_category(self, db, identity):
        recovery = capture_inbound_delete(db, 'calendar' if self.APPLE_ITEMS == 'calendar_events' else 'tasks', 'category', identity)
        if not recovery:
            raise ValueError('category deletion recovery unavailable')
        items = db.execute(f'SELECT * FROM {self.APPLE_ITEMS} WHERE category_id=?', (identity,)).fetchall()
        if self.APPLE_ITEMS == 'todos':
            ids = [row[0] for row in db.execute("""WITH RECURSIVE affected(id) AS (
                SELECT id FROM todos WHERE category_id=? UNION
                SELECT item.id FROM todos item JOIN affected parent ON item.parent_id=parent.id)
                SELECT id FROM affected""", (identity,))]
        else:
            ids = [item['id'] for item in items]
        for item in items:
            if self.APPLE_ITEMS == 'calendar_events':
                self._delete_event(db, item)
            else:
                self._delete_todo_tree(db, item['id'])
        for item_id in ids:
            db.execute("DELETE FROM apple_bindings WHERE entity='item' AND local_id=?", (item_id,))
            db.execute("UPDATE apple_operations SET state='done',error='' WHERE entity='item' AND local_id=? AND state='pending'", (item_id,))
            db.execute('DELETE FROM apple_completion_history WHERE local_id=?', (item_id,))
        db.execute(f'DELETE FROM {self.APPLE_CATEGORIES} WHERE id=?', (identity,))
        db.execute("DELETE FROM apple_bindings WHERE entity='category' AND local_id=?", (identity,))
        return recovery


    def apple_remove_category(self, identity):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute(f'SELECT 1 FROM {self.APPLE_CATEGORIES} WHERE id=?', (identity,)).fetchone():
                return True
            self._apple_remove_category(db, identity)
        if self.APPLE_ITEMS == 'todos':
            self._sync_scheduler_links()
        return True


    def _apple_category_name(self, db, identity, name, trusted):
        binding = self._apple_binding(db,'category',identity)
        if not binding or binding.get('planned'):
            return name
        old = db.execute(f'SELECT name FROM {self.APPLE_CATEGORIES} WHERE id=?',(identity,)).fetchone()
        if not trusted and old and old[0]!=name:
            binding['desired_title'] = name
            db.execute("UPDATE apple_bindings SET document=? WHERE entity='category' AND local_id=?",(json.dumps(binding,ensure_ascii=False),identity))
        suffix = 2
        unique = name
        while db.execute(f'SELECT 1 FROM {self.APPLE_CATEGORIES} WHERE name=? AND id!=?',(unique,identity)).fetchone():
            unique = name[:180]+f" ({binding.get('source_name') or suffix})"
            if suffix>2:
                unique += f' {suffix}'
            suffix += 1
        return unique

    def apple_capture_marker_binding(self, identity):
        with self._connect() as db:
            db.execute('BEGIN')
            return self._apple_unbound_snapshot(db,'item',identity,require_unbound=False)

    def apple_bind_marker_if_unchanged(self, operation_id, expected, category, native, issues):
        from business.marker_binding import commit
        return commit(self,operation_id,expected,category,native,issues)

    def apple_capture_legacy_category(self, identity, member_ids):
        from business.legacy_category_recovery import capture
        return capture(self,identity,member_ids)

    def apple_restore_legacy_category(self, operation_id, expected, document):
        from business.legacy_category_recovery import commit
        return commit(self,operation_id,expected,document)

    def apple_restore_category(self,identity,document):
        with self._connect() as db:
            old=db.execute(f'SELECT id FROM {self.APPLE_CATEGORIES} WHERE id=?',(identity,)).fetchone()
            if not old:
                title=str(document.get('title') or 'Apple')[:180]
                name=title
                index=2
                while db.execute(f'SELECT 1 FROM {self.APPLE_CATEGORIES} WHERE name=?',(name,)).fetchone():
                    name=title+f' ({index})'
                    index+=1
                db.execute(f'INSERT INTO {self.APPLE_CATEGORIES} VALUES (?,?,?,?)',(identity,name,document.get('color') or '#6B8AF7',datetime.now(timezone.utc).isoformat()))
        return self.get_category(identity)


    def apple_prepare_ordinary_calendar_create(self, identity):
        """Private Apple peer: snapshot an unbound local calendar incarnation."""
        from business.object_generations import generations_for
        if self.APPLE_ITEMS != 'calendar_events' or not isinstance(identity,str) or not identity:
            raise ValueError('invalid ordinary calendar target')
        with self._connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone()
            if row is None or self._apple_binding(db,'item',identity) is not None:
                raise ValueError('ordinary calendar target unavailable or already bound')
            return {'version':1,'local_id':identity,'generation':generations_for(db,'item',[identity])[0][1],
                    'row':dict(row),'local':self._apple_decorate(db,'item',row)}


    def apple_verify_ordinary_calendar_create(self, expected):
        from business.object_generations import generations_for
        if self.APPLE_ITEMS != 'calendar_events' or not isinstance(expected,dict) or set(expected) != {'version','local_id','generation','row','local'} or expected['version'] != 1:
            raise ValueError('invalid ordinary calendar snapshot')
        with self._connect() as db:
            db.execute('BEGIN')
            identity=expected['local_id'];row=db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone()
            if (row is None or dict(row)!=expected['row'] or self._apple_decorate(db,'item',row)!=expected['local']
                    or self._apple_binding(db,'item',identity) is not None
                    or generations_for(db,'item',[identity])[0][1]!=expected['generation']):
                raise ValueError('local calendar target changed; review before Apple write')
        return True


    def apple_finalize_ordinary_calendar_create(self, expected, native):
        """Private peer: apply native metadata only to the exact captured incarnation."""
        from business.object_generations import generations_for
        if self.APPLE_ITEMS != 'calendar_events' or not isinstance(expected,dict) or set(expected) != {'version','local_id','generation','row','local'} or expected['version'] != 1:
            raise ValueError('invalid ordinary calendar snapshot')
        if (not isinstance(native,dict) or any(not isinstance(native.get(key),str) or not native[key] for key in ('event_id','calendar_id','source_id'))):
            raise ValueError('native calendar identity requires review')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            identity=expected['local_id'];row=db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone()
            if (row is None or dict(row)!=expected['row'] or self._apple_decorate(db,'item',row)!=expected['local']
                    or self._apple_binding(db,'item',identity) is not None
                    or generations_for(db,'item',[identity])[0][1]!=expected['generation']):
                raise ValueError('local calendar target changed after Apple write; reconciliation required')
            metadata={'url':native.get('url') or '', 'alarms':native.get('alarms',[]), 'floating':bool(native.get('floating')),
                      'attendees':native.get('attendees',[]), 'native_snapshot':native, 'native_recurrence':native.get('recurrence',{}),
                      'native_series_key':native.get('series_key') or native.get('external_id')}
            self._apple_fields(db,identity,metadata,True)
            issues=['包含多条原生重复规则，请在 Apple 中编辑'] if (native.get('recurrence') or {}).get('rule_count',0)>1 else []
            binding={'apple_id':native['event_id'],'group_id':native['calendar_id'],'source_id':native['source_id'],
                     'can_delete':bool(native.get('writable')),'can_modify':bool(native.get('writable')) and not issues,
                     'issues':issues,'native':native,'series_key':native.get('external_id'),'floating':native.get('floating',False)}
            db.execute('INSERT INTO apple_bindings VALUES (?,?,?)',('item',identity,json.dumps(binding,ensure_ascii=False)))
            return self._apple_decorate(db,'item',db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone())


    def apple_capture_approved_export(self, identity):
        from business.apple_export import capture
        return capture(self, identity)

    def apple_finalize_approved_export(self, expected, native, previous=None):
        from business.apple_export import finalize
        return finalize(self, expected, native, previous)


    def apple_commit_bound_conflict(self, operation_id, item, choice, native):
        from business.apple_bound_conflict import commit
        return commit(self, operation_id, item, choice, native)

    def apple_verify_bound_conflict(self, operation_id, item, choice, native):
        from business.apple_bound_conflict import verify
        return verify(self, operation_id, item, choice, native)
