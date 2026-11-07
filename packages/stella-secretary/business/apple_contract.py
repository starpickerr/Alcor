"""Durable Apple bindings and operations. Only the trusted Apple peer can acknowledge writes."""
import json
import uuid
from datetime import datetime, timezone,timedelta


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
        if binding and binding.get('planned'):
            return None
        if binding and not binding.get(operation, False):
            raise PermissionError('Apple 来源不允许此操作，请先检查账户或来源权限')
        if entity=='item':
            item = db.execute(f'SELECT category_id FROM {self.APPLE_ITEMS} WHERE id=?',(identity,)).fetchone()
            if item and db.execute("SELECT 1 FROM apple_operations WHERE entity='category' AND local_id=? AND action='delete' AND state='pending'",(item[0],)).fetchone():
                raise ValueError('所属分类正在等待 Apple 确认删除，可先取消分类删除')
        if db.execute("SELECT 1 FROM apple_operations WHERE entity=? AND local_id=? AND action='delete' AND state='pending'", (entity,identity)).fetchone():
            raise ValueError('正在等待 Apple 确认删除；可重试同步或取消删除')
        return binding

    def _apple_decorate(self, db, entity, row):
        binding = self._apple_binding(db, entity, row['id'])
        pending = db.execute("SELECT id,action,error,document FROM apple_operations WHERE entity=? AND local_id=? AND state='pending' ORDER BY created_at", (entity,row['id'])).fetchall()
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

    def _apple_queue(self, db, entity, identity, action, document=None):
        document = document or {}
        encoded = json.dumps(document,sort_keys=True,ensure_ascii=False)
        old = db.execute("SELECT id FROM apple_operations WHERE entity=? AND local_id=? AND action=? AND document=? AND state='pending'", (entity,identity,action,encoded)).fetchone()
        identity_op = old[0] if old else str(uuid.uuid4())
        if not old:
            db.execute('INSERT INTO apple_operations(id,entity,local_id,action,document,created_at) VALUES (?,?,?,?,?,?)',
                       (identity_op,entity,identity,action,encoded,datetime.now(timezone.utc).isoformat()))
        return {'pending':True,'operation_id':identity_op}

    def apple_operations(self):
        with self._connect() as db:
            return [{**dict(row),'document':json.loads(row['document']), 'binding':self._apple_binding(db,row['entity'],row['local_id'])}
                    for row in db.execute("SELECT * FROM apple_operations WHERE state='pending' ORDER BY created_at,id")]

    def cancel_apple_operation(self, operation_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
            if not row or row['state'] != 'pending':
                raise ValueError('待处理操作已完成或不存在')
            document = json.loads(row['document'])
            if row['action']=='exclude':
                db.execute('DELETE FROM calendar_occurrence_exclusions WHERE series_id=? AND occurrence_on=?',(row['local_id'],document['occurrence_on']))
            db.execute("UPDATE apple_operations SET state='cancelled',error='' WHERE id=?", (operation_id,))
        return {'cancelled':True}

    def apple_operation_error(self, operation_id, error):
        with self._connect() as db:
            db.execute("UPDATE apple_operations SET error=? WHERE id=? AND state='pending'", (str(error),operation_id))
        return True

    def apple_finish_operation(self, operation_id):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
            if not row:
                raise KeyError('Apple operation not found')
            if row['state'] != 'pending':
                return {'state':row['state']}
            if row['action']=='truncate':
                document = json.loads(row['document'])
                until = (datetime.fromisoformat(document['occurrence_on']).date()-timedelta(days=1)).isoformat()
                db.execute('UPDATE calendar_events SET repeat_until=? WHERE id=?',(until,row['local_id']))
            if row['action'] == 'delete':
                identity = row['local_id']
                if self.APPLE_ITEMS == 'calendar_events':
                    items = db.execute('SELECT * FROM calendar_events WHERE '+('category_id=?' if row['entity']=='category' else 'id=?'), (identity,)).fetchall()
                    if row['entity']=='item' and json.loads(row['document']).get('scope')=='future' and items:
                        target = dict(items[0])
                        data = json.loads(target.get('apple_data') or '{}')
                        series = data.get('native_series_key')
                        snapshot = data.get('native_snapshot') or {}
                        if series:
                            for other in db.execute('SELECT * FROM calendar_events WHERE id!=?',(identity,)).fetchall():
                                metadata = json.loads(other['apple_data'] or '{}')
                                native = metadata.get('native_snapshot') or {}
                                if metadata.get('native_series_key')==series and native.get('calendar_id')==snapshot.get('calendar_id') and other['start_at']>=target['start_at']:
                                    self._delete_event(db,other)
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
        if self.APPLE_ITEMS == 'todos':
            self._sync_scheduler_links()
        return {'state':'done'}

    def apple_apply_category(self, category_id, payload):
        return self.update_category(category_id,payload,_apple=True)

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

    def apple_restore_item(self, identity, payload, category_id, source_key=None):
        payload = {**payload,'id':identity,'category_id':category_id}
        with self._connect() as db:
            if source_key:
                db.execute('DELETE FROM source_tombstones WHERE source_key=?',(source_key,))
        if self.APPLE_ITEMS=='calendar_events':
            # Trusted external restoration retains the previous local identity.
            row = self.create_event(payload,_apple=True)
            if source_key:
                with self._connect() as db:
                    db.execute("UPDATE calendar_events SET source_type='apple_calendar',source_key=? WHERE id=?",(source_key,identity))
            return self.get_event(identity)
        row = self.create_todo(payload,_external_source_key=source_key)
        return self.get_todo(identity)

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
        return self.exclude_occurrence(identity,day,_apple=True)

    def apple_remove_item(self,identity,preserve_children=False):
        with self._connect() as db:
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

    def apple_remove_category(self,identity):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            items = db.execute(f'SELECT * FROM {self.APPLE_ITEMS} WHERE category_id=?',(identity,)).fetchall()
            for item in items:
                if self.APPLE_ITEMS=='calendar_events':
                    self._delete_event(db,item)
                else:
                    self._delete_todo_tree(db,item['id'])
                db.execute("DELETE FROM apple_bindings WHERE entity='item' AND local_id=?",(item['id'],))
                db.execute("UPDATE apple_operations SET state='done',error='' WHERE entity='item' AND local_id=? AND state='pending'",(item['id'],))
                db.execute('DELETE FROM apple_completion_history WHERE local_id=?',(item['id'],))
            db.execute(f'DELETE FROM {self.APPLE_CATEGORIES} WHERE id=?',(identity,))
            db.execute("DELETE FROM apple_bindings WHERE entity='category' AND local_id=?",(identity,))
        if self.APPLE_ITEMS=='todos':
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
