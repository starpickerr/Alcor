"""Conservative, persistent reconciliation of Apple Reminders and Alcor todos."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apple_sync.metadata import snapshot_fields, record_baseline, write_fields, conflict_summaries
from apple_sync.contracts import bind_item,process_operations,native_delete_preview
from apple_sync.categories import synchronize_categories, synchronize_moves, reconcile_category_names, resolve_category_conflict, prime_bindings, verify_category_source
from apple_sync.category_links import bidirectional_links, validate_links
from apple_sync.bridge import AppleCalendarBridge
from apple_sync.runtime import report_sync_error


def _instant(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(
        timespec="seconds")


def _priority(value, *, remote=False):
    if remote:
        return 3 if value in (1, 2, 3, 4) else 2 if value in (5, 6) else 1 if value in (7, 8, 9) else 0
    return {0: 0, 1: 9, 2: 5, 3: 1}.get(value, 0)


def _payload(remote, category_id=None):
    from apple_sync.timezones import normalize_timezone
    rule = remote.get("recurrence") or {}
    return {**snapshot_fields(remote),**({"category_id": category_id} if category_id is not None else {}), "title": remote["title"], "notes": remote.get("notes") or "",
            "due_on": remote.get("due_on"), "due_time": remote.get("due_time"),
            "timezone": normalize_timezone(remote.get("timezone")), "planned_on": remote.get("planned_on"),
            "remind_at": remote.get("remind_at") if (rule.get("frequency") or "none") == "none" else None, "priority": _priority(remote.get("priority"), remote=True),
            "repeat_frequency": (rule.get("frequency") or "none") if _supported(remote) else "none", "repeat_days": (rule.get("days") or []) if _supported(remote) else [],
            "repeat_until": (rule.get("until") or "")[:10] or None,
            "repeat_interval": rule.get("interval", 1) if _supported(remote) else 1,
            "completed": bool(remote.get("completed")), "completed_at": remote.get("completed_at")}


def _compatible(row, *, remote=False):
    data = _payload(row) if remote else row
    days = data.get("repeat_days") or []
    if isinstance(days, str):
        days = json.loads(days)
    return {"title": data.get("title") or "", "notes": data.get("notes") or "",
            "due_on": data.get("due_on") or "", "due_time": data.get("due_time") or "",
            "timezone": data.get("timezone") or "UTC", "planned_on": data.get("planned_on") or "",
            "remind_at": _instant(data.get("remind_at")) or "", "priority": data.get("priority") or 0,
            "repeat_frequency": data.get("repeat_frequency") or "none", "repeat_days": sorted(days),
            "repeat_until": data.get("repeat_until") or "",
            "completed": bool(data.get("completed")) if remote else data.get("status") == "completed",
            "completed_at": _instant(data.get("completed_at")) or ""}


def _hash(row, *, remote=False):
    content = _compatible(row, remote=remote)
    interval = (row.get("recurrence") or {}).get("interval", 1) if remote else row.get("repeat_interval", 1)
    # Keep existing interval-1 mappings valid across this additive upgrade.
    if interval != 1:
        content["repeat_interval"] = interval
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _source_key(remote):
    identity = remote.get("external_id") or "\0".join((remote["list_id"], remote["reminder_id"]))
    return "eventkit:" + hashlib.sha256(identity.encode()).hexdigest()


def _supported(remote):
    rule = remote.get("recurrence") or {}
    return (remote.get("alarm_count", 0) <= 1 and
            (not remote.get("alarm_count") or remote.get("remind_at") is not None) and
            rule.get("rule_count", 0) <= 1 and
            rule.get("frequency", "none") in ("none", "daily", "weekly", "monthly", "yearly") and
            type(rule.get("interval", 1)) is int and 1 <= rule.get("interval", 1) <= 99 and not rule.get("count") and
            (rule.get("frequency") == "weekly" or not rule.get("days")) and
            not any(rule.get(key) for key in ("month_days", "months", "week_numbers",
                                              "year_days", "set_positions")))


class AlarmBaselineAmbiguous(ValueError):
    pass


class AppleReminderSync:
    DIRECTIONS = {"import", "export", "bidirectional"}

    def __init__(self, bridge=None, db_path=None, todo_port=None, clock=None):
        self.bridge = bridge or AppleCalendarBridge()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.todos = todo_port
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._lock = threading.RLock()
        self._auto_started = False
        with self._connect() as db:
            # A legacy or mixed schema must only be changed by the supervised cold migration.
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected = {'settings', 'baseline_local', 'mappings', 'category_links'}
            if tables & expected:
                if not expected <= tables:
                    raise RuntimeError('Incomplete Apple database schema')
                cols = {row[1] for row in db.execute("PRAGMA table_info(category_links)")}
                if 'stella_id' in cols or 'stella_name' in cols or not {'alcor_id', 'alcor_name'} <= cols:
                    raise RuntimeError('Apple database requires supervised cold migration')
                for table in ('baseline_local', 'mappings'):
                    cols = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                    if 'stella_id' in cols or 'todo_id' not in cols:
                        raise RuntimeError('Apple database requires supervised cold migration')
                if db.execute("SELECT 1 FROM mappings WHERE origin NOT IN ('alcor','apple') OR origin IS NULL LIMIT 1").fetchone():
                    raise RuntimeError('Unexpected Apple mapping origin')
                version = db.execute("SELECT value FROM settings WHERE key='_alcor_apple_schema_version'").fetchone()
                if version:
                    try:
                        parsed = json.loads(version[0])
                    except (ValueError, TypeError) as exc:
                        raise RuntimeError('Unsupported Apple database schema version') from exc
                    if type(parsed) is not int or parsed != 1:
                        raise RuntimeError('Unsupported Apple database schema version')
            db.executescript("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS baseline_local (todo_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS mappings (
                    todo_id TEXT PRIMARY KEY,source_key TEXT NOT NULL UNIQUE,
                    list_id TEXT NOT NULL,reminder_id TEXT NOT NULL,external_id TEXT NOT NULL,
                    origin TEXT NOT NULL,local_hash TEXT NOT NULL,remote_hash TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active',conflict TEXT NOT NULL DEFAULT '',
                    missing_since TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS reminder_map_list ON mappings(list_id);
                CREATE TABLE IF NOT EXISTS category_links (
                    apple_id TEXT PRIMARY KEY, alcor_id TEXT NOT NULL UNIQUE, direction TEXT NOT NULL,
                    enabled INTEGER NOT NULL, apple_name TEXT NOT NULL, alcor_name TEXT NOT NULL,
                    name_conflict TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _get(self, key, default=None):
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def _set(self, key, value):
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def _maps(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM mappings")]

    def _save_map(self, mapping, connection=None):
        if connection is None:
            with self._connect() as db:
                return self._save_map(mapping, connection=db)
        connection.execute("""INSERT INTO mappings VALUES
                (:todo_id,:source_key,:list_id,:reminder_id,:external_id,:origin,:local_hash,
                 :remote_hash,:state,:conflict,:missing_since,:updated_at)
                ON CONFLICT(todo_id) DO UPDATE SET
                source_key=excluded.source_key,list_id=excluded.list_id,reminder_id=excluded.reminder_id,
                external_id=excluded.external_id,origin=excluded.origin,local_hash=excluded.local_hash,
                remote_hash=excluded.remote_hash,state=excluded.state,conflict=excluded.conflict,
                missing_since=excluded.missing_since,updated_at=excluded.updated_at""", mapping)

    def _new_map(self, local, remote, origin, source_key=None):
        from apple_sync.category_rebuild import guard_retained
        guard_retained(self,local['id'])
        from apple_sync.marker_recovery import guard_pending
        guard_pending(self,local['id'])
        self.todos.apple_apply_metadata(local["id"],snapshot_fields(remote))
        local = self.todos.get_todo(local["id"])
        record_baseline(self,local,remote)
        self._set("reminder-local-remind-at:"+local["id"], {"value": local.get("remind_at")})
        self.todos.apple_apply_metadata(local['id'],{'native_managed':True})
        bind_item(self.todos,local,remote,'reminders',[] if _supported(remote) else ['此提醒包含复杂重复或位置/多重提醒，完整保留；请在 Apple 中编辑'])
        return self._mapping_from_native(local,remote,origin,source_key)

    def _mapping_from_native(self, local, remote, origin, source_key=None):
        return {"todo_id": local["id"], "source_key": source_key or _source_key(remote),
                "list_id": remote["list_id"], "reminder_id": remote["reminder_id"],
                "external_id": remote.get("external_id") or "", "origin": origin,
                "local_hash": _hash(local), "remote_hash": _hash(remote, remote=True),
                "state": "active", "conflict": "", "missing_since": "", "updated_at": self.clock()}

    def _refresh_map(self, mapping, local, remote):
        self._save_map(self._new_map(local, remote, mapping["origin"], mapping["source_key"]))

    def _local(self, todo_id):
        try:
            return self.todos.get_todo(todo_id)
        except KeyError:
            return None

    def _conflict(self, mapping, reason):
        mapping.update(state="conflict", conflict=reason, updated_at=self.clock())
        self._save_map(mapping)

    def _recover_marked(self, remote, maps, counts, *, scan_token=None, work_claim=None, scan_safe=False):
        """Reattach a native save that succeeded before its mapping committed."""
        from apple_sync.marker_recovery import pending_native
        if pending_native(self,remote):
            counts['conflicts']+=1
            counts['status']='partial'
            return True
        if not remote.get("alcor_id"):
            return False
        from apple_sync.category_rebuild import retained_ids
        if remote['alcor_id'] in retained_ids(self):
            counts['conflicts']+=1
            return True
        if scan_token is None and work_claim is None:
            from apple_sync.group_scan_claims import WorkClaim
            with WorkClaim(self,{remote['list_id']:remote.get('source_id')},[remote['alcor_id']],
                           {'kind':'reminder_marker','native':remote}) as claim:
                return self._recover_marked(remote,self._maps(),counts,work_claim=claim)
        if scan_token is not None:
            from apple_sync.group_scan_claims import claim_items
            claim_items(self,scan_token,[remote['alcor_id']])
        local = self._local(remote["alcor_id"])
        if local is None or local["source_type"] == "apple_reminder" or any(
                row["todo_id"] == remote["alcor_id"] for row in maps):
            counts["conflicts"] += 1
            return True
        from apple_sync.marker_recovery import recover,MarkerRecoveryError
        try:
            mapping=recover(self,self.todos,'reminders',local,remote,scan_token=scan_token,work_claim=work_claim,scan_safe=scan_safe)
        except Exception as error:
            if scan_token:
                from apple_sync.group_scan_claims import uncertain
                uncertain(self,scan_token)
            raise MarkerRecoveryError(str(error)) from error
        if mapping is None:
            counts['conflicts']+=1
            counts['status']='partial'
            return True
        if mapping['state']=='conflict':
            counts['conflicts']+=1
        maps.append(mapping)
        return True

    def _hold_identity_candidate(self, remote, token):
        # Same native ID in another location is a collision, not a new item or a match.
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            mappings=[dict(row) for row in db.execute('SELECT * FROM mappings WHERE reminder_id=? AND list_id<>?',
                                                       (remote['reminder_id'],remote['list_id']))]
            direct=db.execute('SELECT 1 FROM mappings WHERE reminder_id=? AND list_id=?',
                              (remote['reminder_id'],remote['list_id'])).fetchone()
            if not mappings or direct:
                return False
            claim=db.execute('SELECT a.* FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) '
                             'WHERE a.attempt_id=? AND g.group_id=? AND g.source_id=?',
                             (token,remote['list_id'],remote['source_id'])).fetchone()
            if not claim or claim['state']!='scanning' or json.loads(claim['evidence'])!={}:
                raise ValueError('reminder candidate scan claim changed')
            key=hashlib.sha256(json.dumps([remote['source_id'],remote['list_id'],remote['reminder_id']]).encode()).hexdigest()
            record={'key':key,'state':'pending','native':remote,'mapping_ids':sorted(row['todo_id'] for row in mappings),
                    'mappings':mappings,'observed_at':self.clock(),
                    'reason':'已有相同原生 ID 的关联，但来源或位置不足以确认身份。原项目与 Apple 候选均已保留，未合并或重复导入。请先核对 Apple 来源。'}
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('reminder-identity-candidate:'+key,json.dumps(record,ensure_ascii=False)))
        return True

    def status(self):
        from apple_sync.item_restore import pending as restore_pending
        from apple_sync.approved_delete import operation_status, completed_import_links
        from apple_sync.bound_conflict import pending as conflict_pending
        from apple_sync.category_rebuild import status as retained_status
        from apple_sync.marker_recovery import status as marker_status
        from apple_sync.category_create import status as create_status
        creates=create_status(self,'reminders')
        creates += [row for row in self._get('category_create_errors',[]) if row['local_id'] not in {entry['local_id'] for entry in creates}]
        from apple_sync.group_scan_claims import status as scan_status
        listing_error = ""
        native = self.bridge.call("reminder_status")
        lists = []
        if native.get("authorization") == "full_access":
            try:
                inventory = self.bridge.call("reminder_lists")
                lists = inventory["lists"]
                self._set('available_sources',inventory.get('sources',[]))
                self._set('default_source_id',inventory.get('default_source_id'))
            except Exception as exc:
                listing_error = report_sync_error("reminder", "读取提醒事项列表", exc)
        maps = self._maps()
        categories = self.todos.categories()
        category_ids = {row['id'] for row in categories}
        category_conflicts = self._get('category_conflicts', [])
        finished_imports = completed_import_links(self)
        category_conflicts = category_conflicts + [{'apple_id': link['apple_id'], 'alcor_id': link['alcor_id'], 'kind': 'legacy_delete', 'reason': '旧版分类删除尚未核对；Apple 分类与关联已保留，需要重新确认具体来源与操作。'} for link in self._stored_links() if link['alcor_id'] not in category_ids and (link['apple_id'],link['alcor_id']) not in finished_imports and not any(row.get('apple_id') == link['apple_id'] and row.get('kind') == 'legacy_delete' for row in category_conflicts)]
        direct_helper = getattr(self.bridge, 'helper_path', None)
        direct_watch = direct_helper is not None and direct_helper.suffix != '.app'
        with self._connect() as db:
            identity_candidates=[json.loads(row[0]) for row in db.execute("SELECT value FROM settings WHERE key LIKE 'reminder-identity-candidate:%' ORDER BY key")]
        return {**native, "watch_degraded": bool(self._get('connected', False) and self._get('auto_enabled', True) and native.get('watch_control_version') != 1 and not direct_watch), "connected": bool(self._get("connected", False)),
                "auto_enabled": bool(self._get("auto_enabled", True)), "sync_all": bool(self._get("sync_all", False)),
                "scan_claims": scan_status(self), "category_creates":creates, "retained_categories":retained_status(self), "marker_conflicts":marker_status(self), "identity_candidates":identity_candidates, "list_ids": self._get("list_ids", []), "direction": self._get("direction", "bidirectional"),
                "lists": lists, "categories": categories, "last_success": self._get("last_success"), "error": listing_error or self._get("error", ""),
                "conflicts": conflict_summaries(maps, self.todos.get_todo, 'todo_id', restore_pending(self), conflict_pending(self)),
                "legacy_deletions": [dict(todo_id=row['todo_id'], reason='旧版删除尚未核对，Apple 提醒事项及关联已保留。') for row in maps if row['state']=='pending_delete'],
                "pending_deletions": sum(row["state"] == "pending_delete" for row in maps),
                "pending_local_deletions": sum(row["state"] == "pending_local_delete" for row in maps),
                "readonly_issues":self._get('readonly_issues',[]),"category_conflicts":category_conflicts, "operations":operation_status(self,self.todos.apple_operations()),
                "sources":self._get('available_sources',[]),"default_source_id":self._get('default_source_id'),
                "links": [self._public_link(row) for row in self._stored_links()]}

    def request_access(self):
        result = self.bridge.call("reminder_request_access")
        return {**result, **self.status()}

    def start_sync(self, selections=None, confirm_deletions=False, confirm_local_deletions=False):
        with self._lock:
            state = self.status()
            if state.get("authorization") != "full_access":
                raise PermissionError("需要系统完整访问授权")
            previous_links = state["links"]
            sync_all = selections is None
            deleted_categories = synchronize_categories(self, self.todos, 'reminders') if sync_all else 0
            state = self.status()
            if sync_all and not state['lists'] and deleted_categories:
                self._set('sync_all', True)
                self._set('sync_deletions', True)
                self._set('connected', True)
                self._set('auto_enabled', True)
                self.resume_auto()
                self._set('error', '')
                return {'state': self.status(), 'result': {'status': 'success', 'deleted_lists': deleted_categories}}
            if sync_all and not state['lists']:
                raise ValueError('没有可同步的来源，请检查系统账户或授权后重试')
            links = state['links'] if sync_all else bidirectional_links(selections, state['lists'], state['categories'], existing_links=previous_links)
            same_selection = {(row["apple_id"],row["alcor_id"]) for row in links if row["enabled"]} == {(row["apple_id"],row["alcor_id"]) for row in previous_links if row["enabled"]}
            state = self.replace_links(links)
            self._set("sync_all", sync_all)
            self._set("sync_deletions", self._get("sync_deletions", sync_all) if same_selection else sync_all)
            selected = {row["alcor_id"] for row in state["links"] if row["enabled"]}
            names = {row["name"]: row["id"] for row in self.todos.categories() if row["id"] in selected}
            for row in self.todos.list_todos():
                if not row.get("category_id") and row.get("list_name") in names:
                    self.todos.apple_place_item(row["id"], names[row["list_name"]])
            with self._connect() as db:
                db.executemany("DELETE FROM baseline_local WHERE todo_id=?",
                               [(row["id"],) for row in self.todos.list_todos() if row.get("category_id") in selected])
            result = self.sync(manual=True, confirm_deletions=confirm_deletions,
                               confirm_local_deletions=confirm_local_deletions)
            self._set("auto_enabled", True)
        self.resume_auto()
        return {"state": self.status(), "result": result}

    def _validate_selection(self, list_ids, direction):
        if direction not in self.DIRECTIONS:
            raise ValueError("invalid sync direction")
        if not isinstance(list_ids, list) or not list_ids or len(set(list_ids)) != len(list_ids):
            raise ValueError("select at least one distinct reminder list")
        if self.bridge.call("reminder_status").get("authorization") != "full_access":
            raise PermissionError("Reminders full access is required")
        available = {row["id"]: row for row in self.bridge.call("reminder_lists")["lists"]}
        if any(key not in available for key in list_ids):
            raise ValueError("selected reminder list is unavailable")
        if direction in ("export", "bidirectional") and not any(available[key]["writable"] for key in list_ids):
            raise ValueError("select a writable reminder list for export")
        return available

    def configure(self, list_ids, direction):
        self._validate_selection(list_ids, direction)
        with self._lock:
            if not self._get("ever_connected", False):
                with self._connect() as db:
                    db.executemany("INSERT OR IGNORE INTO baseline_local VALUES (?)",
                                   [(row["id"],) for row in self.todos.list_todos() if row["source_type"] != "apple_reminder"])
                self._set("ever_connected", True)
            self._set("list_ids", list_ids)
            self._set("direction", direction)
            self._set("connected", True)
        self.resume_auto()
        return self.status()

    def _stored_links(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM category_links ORDER BY apple_id")]

    @staticmethod
    def _public_link(row):
        return {"apple_id": row["apple_id"], "alcor_id": row["alcor_id"], "direction": row["direction"],
                "enabled": bool(row["enabled"]), "apple_name": row["apple_name"], "alcor_name": row["alcor_name"],
                "name_conflict": row["name_conflict"]}

    def _save_link(self, link):
        with self._connect() as db:
            db.execute("""UPDATE category_links SET apple_name=?, alcor_name=?, name_conflict=?, updated_at=?
                WHERE apple_id=?""", (link["apple_name"], link["alcor_name"], link["name_conflict"],
                                      self.clock(), link["apple_id"]))

    def replace_links(self, links, *, preserve_missing=False):
        with self._lock:
            if not isinstance(links, list):
                raise ValueError("links must be a list")
            local_only = set(self.todos.apple_local_only_categories())
            local_names = {row['name'] for row in self.todos.categories() if row['id'] in local_only}
            if any(isinstance(row, dict) and (row.get('alcor_id') in local_only or row.get('create_name') in local_names) for row in links):
                raise ValueError('恢复的本机分类不能自动关联 Apple 来源')
            if self.bridge.call("reminder_status").get("authorization") != "full_access":
                raise PermissionError("Reminders full access is required")
            available = {row["id"]: row for row in self.bridge.call("reminder_lists")["lists"]}
            previous = {row["apple_id"]: row for row in self._stored_links()}
            from apple_sync.category_links import changed_link_groups
            from apple_sync.group_scan_claims import WorkClaim
            groups = changed_link_groups(self,links,previous,available)
            if not groups:
                from apple_sync.category_links import restore_link_selection
                restore_link_selection(self,previous,'list_ids')
                return self.status()
            from apple_sync.category_connections import prepare,guard_discovery,connect
            prepared,specs=prepare(self.todos,links,available,groups,previous,preserve_missing)
            validate_links(prepared)
            category_ids={row['alcor_id'] for row in prepared if row['apple_id'] in groups}
            category_ids.update(previous[key]['alcor_id'] for key in groups if key in previous)
            evidence = {'kind':'replace_links','previous':previous,'requested':prepared,'owners':specs,'native':available}
            with WorkClaim(self,groups,[],evidence,category_ids=category_ids,category_kind='reminders') as claim:
                with self._connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    claim._check(db)
                    guard_discovery(db,'reminders',previous,available,groups)
                fresh={row['id']:row for row in self.bridge.call('reminder_lists')['lists']}
                if fresh!=available:
                    raise ValueError('native category inventory changed before linking')
                rows = []
                for item in prepared:
                    apple = available.get(item["apple_id"])
                    if apple is None:
                        old = previous.get(item["apple_id"])
                        if not old or old["alcor_id"] != item["alcor_id"]:
                            raise ValueError("selected reminder list is unavailable")
                        apple = {"title": old["apple_name"]}
                    try:
                        category = connect(self.todos,claim,specs[item['apple_id']],available[item['apple_id']]) if item['apple_id'] in specs else self.todos.get_category(item["alcor_id"])
                    except KeyError:
                        old = previous.get(item['apple_id'])
                        if not preserve_missing or not old or any(item[key] != (bool(old[key]) if key == 'enabled' else old[key]) for key in ('alcor_id','direction','enabled')):
                            raise
                        rows.append({**old, 'enabled': 1 if old['enabled'] else 0})
                        continue
                    old = previous.get(item["apple_id"], {})
                    baseline = {key: old[key] for key in ("apple_name", "alcor_name", "name_conflict")} if old.get("alcor_id") == item["alcor_id"] else {}
                    rows.append({**item, "apple_name": apple["title"], "alcor_name": category["name"],
                                 "name_conflict": "", **baseline, "enabled": 1 if item["enabled"] else 0})
                with self._lock:
                    now = self.clock()
                    claim.started('manager_committing')
                    with self._connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        claim._check(db)
                        guard_discovery(db,'reminders',previous,available,groups)
                        current = {row['apple_id']:dict(row) for row in db.execute('SELECT * FROM category_links ORDER BY apple_id')}
                        if current != previous:
                            raise ValueError('category links changed before conditional replacement')
                        db.executemany('DELETE FROM category_links WHERE apple_id=?', [(key,) for key in groups])
                        db.executemany("""INSERT INTO category_links
                            (apple_id, alcor_id, direction, enabled, apple_name, alcor_name, name_conflict, updated_at)
                            VALUES (:apple_id, :alcor_id, :direction, :enabled, :apple_name, :alcor_name, :name_conflict, :updated_at)""",
                            [{**row, "updated_at": now} for row in rows if row["apple_id"] in groups])
                        enabled = [row for row in rows if row['enabled']]
                        ever=db.execute("SELECT value FROM settings WHERE key='ever_connected'").fetchone()
                        if enabled and not (ever and json.loads(ever[0])):
                            db.executemany('INSERT OR IGNORE INTO baseline_local VALUES (?)',
                                           [(row['id'],) for row in self.todos.list_todos() if row['source_type']!='apple_reminder'])
                            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('ever_connected','true'))
                        directions={row['direction'] for row in enabled}
                        state={'list_ids':[row['apple_id'] for row in enabled],'connected':bool(enabled)}
                        if len(directions)==1:
                            state['direction']=directions.pop()
                        db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',[(key,json.dumps(value)) for key,value in state.items()])
            return self.status()

    def _reconcile_category_names(self):
        return reconcile_category_names(self,self.todos,'reminders')

    def resolve_category_conflict(self,apple_id,choice,replacement_id=None,source_id=None,field=None):
        with self._lock:
            return resolve_category_conflict(self,self.todos,'reminders',apple_id,choice,replacement_id,source_id,field)

    def prime_bindings(self):
        return prime_bindings(self,self.todos,'reminders')

    def cancel_operation(self,operation_id):
        if self._get('operation-receipt:'+operation_id):
            raise ValueError('Apple 已确认此操作，正在完成本地核对，请继续同步')
        self.todos.cancel_apple_operation(operation_id)
        return self.status()

    def prepare_delete_snapshot(self, entity, local_id, scope=None, occurrence_on=None):
        with self._lock:
            return native_delete_preview(self, self.todos, 'reminders', entity, local_id, scope, occurrence_on)

    def _place_todo(self, local, category_id, direction):
        if local and category_id and direction in ("import", "bidirectional") and local.get("category_id") != category_id:
            return self.todos.apple_place_item(local["id"], category_id)
        return local

    def disconnect(self):
        with self._lock:
            self._set("connected", False)
            self.stop_auto()
            return self.status()

    def set_auto(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        with self._lock:
            self._set("auto_enabled", enabled)
            if enabled:
                self.resume_auto()
            else:
                self.stop_auto()
        return self.status()

    def resume_auto(self):
        if (self._get("auto_enabled", True) and self._get("connected", False)
                and self.bridge.call("reminder_status").get("authorization") == "full_access"):
            self.start_auto()

    def items(self, list_id):
        self._validate_selection([list_id], "import")
        inventory = self.bridge.call("reminders", list_ids=[list_id])
        if list_id not in inventory["successful_list_ids"]:
            raise ValueError("selected reminder list could not be scanned")
        return [row for row in inventory["reminders"] if row["list_id"] == list_id]

    def lookup_item(self, list_id, reminder_id):
        if not isinstance(reminder_id, str) or not reminder_id:
            raise ValueError("reminder_id is required")
        matches = [row for row in self.items(list_id) if row["reminder_id"] == reminder_id]
        if len(matches) != 1:
            raise KeyError("Apple reminder_id not found or ambiguous")
        return matches[0]

    @staticmethod
    def _match(remote, maps, inventory):
        direct = [row for row in maps if row["list_id"] == remote["list_id"] and
                  row["reminder_id"] == remote["reminder_id"]]
        if len(direct) == 1:
            return direct[0]
        if remote.get("alcor_id"):
            marked = [row for row in maps if row["todo_id"] == remote["alcor_id"]]
            if len(marked) == 1:
                return marked[0]
        external = remote.get("external_id")
        if external and sum(item.get("external_id") == external for item in inventory) == 1:
            matches = [row for row in maps if row["external_id"] == external]
            if len(matches) == 1:
                return matches[0]
        return None

    def _write_payload(self, local, mapping=None, list_id=None, *, allow_ambiguous=False, include_receipt=True):
        from apple_sync.category_rebuild import guard_retained
        guard_retained(self,local['id'])
        from apple_sync.marker_recovery import guard_pending
        guard_pending(self,local['id'])
        data = {**write_fields(self,local,mapping,include_receipt=include_receipt),"list_id": list_id or mapping["list_id"], "title": local["title"],
                "notes": local["notes"], "due_on": local["due_on"], "due_time": local["due_time"],
                "timezone": local["timezone"], "planned_on": local["planned_on"],
                "priority": _priority(local["priority"]),
                "completed": local["status"] == "completed", "completed_at": local["completed_at"],
                "repeat_frequency": local["repeat_frequency"],
                "repeat_days": json.loads(local["repeat_days"]), "repeat_until": local["repeat_until"],
                "repeat_interval": local.get("repeat_interval", 1)}
        baseline = self._get("reminder-local-remind-at:"+local["id"])
        if mapping and baseline is None and not allow_ambiguous:
            snapshot = local.get("native_snapshot") or {}
            if "remind_at" not in snapshot or _instant(local.get("remind_at")) != _instant(_payload(snapshot).get("remind_at")):
                raise AlarmBaselineAmbiguous("Reminder alarm baseline is ambiguous; review before writing")
        remind_changed = not mapping or _instant(local.get("remind_at")) != _instant(baseline["value"] if baseline else None)
        if remind_changed and not data.get("alarms"):
            # An empty default alarm list must not hide an explicit legacy reminder.
            if not mapping and local.get("remind_at"):
                data.pop("alarms", None)
            if "alarms" not in data:
                data["remind_at"] = local.get("remind_at")
        if mapping:
            data.update(reminder_id=mapping["reminder_id"], external_id=mapping["external_id"])
        else:
            data["alcor_id"] = local["id"]
        return data

    def _apply_remote(self, mapping, local, remote, *, expected=None, metadata_only=False):
        expected = expected or self.todos.apple_capture_reminder_sync(local['id'])
        return self.todos.apple_apply_reminder_inbound(expected, _payload(remote), remote,
                                                       metadata_only=metadata_only)

    def _reminder_baselines(self, identity, connection=None):
        if connection is None:
            with self._connect() as db:
                return self._reminder_baselines(identity, connection=db)
        result = {}
        for prefix in ('item-metadata:', 'reminder-local-remind-at:', 'reminder-binding-generation:'):
            row = connection.execute('SELECT value FROM settings WHERE key=?', (prefix+identity,)).fetchone()
            result[prefix] = json.loads(row[0]) if row else None
        return result

    def _commit_reminder_snapshot(self, mapping, before, baselines, local, remote, generation, *, completion_journal=None, move_from=None, claim=None):
        from apple_sync.metadata import metadata
        updated = {**before, 'list_id':remote['list_id'], 'reminder_id':remote['reminder_id'],
                   'external_id':remote.get('external_id') or '', 'local_hash':_hash(local),
                   'remote_hash':_hash(remote,remote=True), 'state':'active', 'conflict':'',
                   'missing_since':'', 'updated_at':self.clock()}
        guard = {'generation':generation,'source_id':remote['source_id'],'group_id':remote['list_id']}
        if baselines['reminder-binding-generation:'] not in (None, guard, move_from):
            raise RuntimeError('reminder binding incarnation changed; reconciliation required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if claim is not None:
                claim._check(db)
            row = db.execute('SELECT * FROM mappings WHERE todo_id=?',(before['todo_id'],)).fetchone()
            if not row or dict(row)!=before or self._reminder_baselines(before['todo_id'],db)!=baselines:
                raise RuntimeError('reminder mapping changed after owner commit; reconciliation required')
            if completion_journal is not None:
                key='reminder-completion:'+completion_journal['operation']['id']
                saved=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
                if not saved or json.loads(saved[0])!=completion_journal:
                    raise RuntimeError('completion receipt changed before mapping commit')
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps({**completion_journal,'stage':'done'})))
                if completion_journal['completed']['reminder_id']!=remote['reminder_id']:
                    old=db.execute("SELECT value FROM settings WHERE key='retired_reminder_ids'").fetchone()
                    retired=json.loads(old[0]) if old else []
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('retired_reminder_ids',json.dumps(list(dict.fromkeys(retired+[completion_journal['completed']['reminder_id']])))))
            self._save_map(updated,connection=db)
            values = {'item-metadata:':[metadata(local),metadata(remote,True)],
                      'reminder-local-remind-at:':{'value':local.get('remind_at')},
                      'reminder-binding-generation:':guard}
            for prefix,value in values.items():
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(prefix+before['todo_id'],json.dumps(value)))
            db.execute('DELETE FROM settings WHERE key=?',('reminder-missing:'+before['todo_id'],))
        mapping.clear()
        mapping.update(updated)

    def _reminder_conflict(self, mapping, before, baselines, expected, remote, reason, *, claim=None):
        record = {'version':1,'reason':reason,'observed':expected,'current_local':self._local(before['todo_id']),'remote':remote}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if claim is not None:
                claim._check(db)
            row=db.execute('SELECT * FROM mappings WHERE todo_id=?',(before['todo_id'],)).fetchone()
            if not row or dict(row)!=before or self._reminder_baselines(before['todo_id'],db)!=baselines:
                raise RuntimeError('reminder conflict state changed; reconciliation required')
            updated={**before,'state':'conflict','conflict':reason,'updated_at':self.clock()}
            self._save_map(updated,connection=db)
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('reminder-inbound-conflict:'+before['todo_id'],json.dumps(record,ensure_ascii=False)))
        mapping.clear()
        mapping.update(updated)

    def _native_successors(self,rows):
        from apple_sync.category_rebuild import retained_ids
        protected=retained_ids(self)
        held_candidates=set()
        retired = self._get('retired_reminder_ids',[])
        for mapping in self._maps():
            local = self._local(mapping['todo_id'])
            if mapping['todo_id'] in protected:
                # A completed/next-instance pair is not permission to re-associate a retained member.
                binding=(local or {}).get('apple') or {}
                for row in rows:
                    if row.get('list_id')==mapping['list_id'] and (row.get('reminder_id')==mapping['reminder_id'] or
                            mapping.get('external_id') and row.get('external_id')==mapping['external_id'] and
                            row.get('source_id')==binding.get('source_id')):
                        held_candidates.add((row.get('list_id'),row.get('reminder_id')))
                continue
            if not local or local.get('apple_pending') or mapping['state']!='active':
                continue
            native = (local.get('apple') or {}).get('native',{})
            if (native.get('recurrence') or {}).get('frequency','none')=='none':
                continue
            completed = next((row for row in rows if row['list_id']==mapping['list_id'] and row['reminder_id']==mapping['reminder_id'] and row.get('completed')),None)
            if not completed or not completed.get('external_id'):
                continue
            following = [row for row in rows if row['list_id']==completed['list_id'] and row.get('source_id')==completed.get('source_id') and row.get('external_id')==completed['external_id'] and not row.get('completed') and (row.get('due_on') or '')>(completed.get('due_on') or '')]
            if len(following)!=1:
                continue
            expected=self.todos.apple_capture_reminder_sync(local['id'])
            before=dict(mapping)
            baselines=self._reminder_baselines(local['id'])
            try:
                local=self.todos.apple_apply_reminder_inbound(expected,_payload(following[0]),following[0],previous_completion=completed)
            except ValueError as exc:
                if not str(exc).startswith('INBOUND_CHANGED:'):
                    raise
                self._reminder_conflict(mapping,before,baselines,expected,following[0],str(exc))
                continue
            self._commit_reminder_snapshot(mapping,before,baselines,local,following[0],expected['generation'])
            retired.append(completed['reminder_id'])
        self._set('retired_reminder_ids',list(dict.fromkeys(retired)))
        return [row for row in rows if row['reminder_id'] not in retired and (row.get('list_id'),row.get('reminder_id')) not in held_candidates]

    def _process_remote(self, remote, mapping, direction, counts, category_id=None):
        from apple_sync.category_rebuild import retained_ids
        if mapping['todo_id'] in retained_ids(self):
            counts['conflicts']+=1
            return
        from apple_sync.metadata import metadata
        if mapping.get('state')=='conflict':
            counts['conflicts']+=1
            return
        local=self._local(mapping['todo_id'])
        if local is None:
            if _hash(remote,remote=True)!=mapping['remote_hash']:
                self._conflict(mapping,'Apple changed a record deleted in Alcor')
                counts['conflicts']+=1
                return
            mapping.update(state='pending_delete' if direction in ('export','bidirectional') else 'deleted',updated_at=self.clock())
            self._save_map(mapping)
            return
        expected=self.todos.apple_capture_reminder_sync(local['id'],category_id)
        local=expected['local']
        before=dict(mapping)
        baselines=self._reminder_baselines(local['id'])
        if category_id and local.get('category_id') != category_id:
            # Compare placement to the persisted map, native binding and original
            # category link, not merely two current category values.
            binding=local.get('apple') or {}
            native=binding.get('native') or {}
            origin=next((row['local'].get('apple') or {} for row in expected['categories'] if row['row']['id']==category_id),{})
            source=remote.get('source_id')
            guard={'generation':expected['generation'],'source_id':source,'group_id':mapping['list_id']}
            verified=(source and source==native.get('source_id')==origin.get('source_id') and
                      binding.get('source_id') in (None,'',source) and
                      remote.get('reminder_id')==mapping['reminder_id']==binding.get('apple_id')==native.get('reminder_id') and
                      remote.get('list_id')==mapping['list_id']==binding.get('group_id')==native.get('list_id')==origin.get('apple_id') and
                      baselines['reminder-binding-generation:'] in (None,guard))
            if not verified:
                self._reminder_conflict(mapping,before,baselines,expected,remote,
                    '原分类位置基线无法核验，已保留当前本地分类与 Apple 内容，请核对关联')
                counts['conflicts']+=1
            # Verified local placement differs while native placement is still at
            # the durable original. Keep the latest user choice until move can run.
            counts['status']='partial'
            return
        old=baselines['item-metadata:']
        if old:
            local_extra=old[0]!=metadata(local)
            remote_extra=old[1]!=metadata(remote,True)
        else:
            local_extra=remote_extra=(metadata(local)['timezone']!=metadata(remote,True)['timezone'] or bool(local.get('floating'))!=bool(remote.get('floating')))
        local_changed=_hash(local)!=mapping['local_hash'] or local_extra
        remote_changed=_hash(remote,remote=True)!=mapping['remote_hash'] or remote_extra
        reason=None
        if local_changed and remote_changed:
            reason='Both Alcor and Apple changed this reminder'
        elif remote_changed and direction not in ('import','bidirectional'):
            reason='Apple changed an export-only reminder'
        elif local_changed and (direction not in ('export','bidirectional') or not remote.get('writable',False) or not _supported(remote)):
            reason='Alcor changed a reminder that cannot be exported'
        if reason:
            self._reminder_conflict(mapping,before,baselines,expected,remote,reason)
            counts['conflicts']+=1
            return
        try:
            if local_changed:
                payload={**self._write_payload(local,mapping),'expected_source_id':remote['source_id'],'expected_snapshot':remote}
                self.todos.apple_verify_reminder_sync(expected,remote)
                remote=self.bridge.call('reminder_upsert',**payload)['reminder']
                applied=self._apply_remote(mapping,local,remote,expected=expected)
                counts['exported']+=1
            else:
                applied=self._apply_remote(mapping,local,remote,expected=expected,
                                           metadata_only=not remote_changed and (not category_id or local.get('category_id')==category_id))
                if remote_changed:
                    counts['updated']+=1
            self._commit_reminder_snapshot(mapping,before,baselines,applied,remote,expected['generation'])
            if not _supported(remote):
                counts['conflicts']+=1
        except (ValueError,AlarmBaselineAmbiguous) as exc:
            if not (isinstance(exc,AlarmBaselineAmbiguous) or str(exc).startswith('INBOUND_CHANGED:')):
                raise
            self._reminder_conflict(mapping,before,baselines,expected,remote,str(exc))
            counts['conflicts']+=1
            counts['status']='partial'

    def preview(self, list_ids, direction):
        self._validate_selection(list_ids, direction)
        inventory = self.bridge.call("reminders", list_ids=list_ids)
        successful = set(inventory["successful_list_ids"])
        maps = self._maps()
        external_counts = {key: sum(row.get("external_id") == key for row in inventory["reminders"])
                           for key in {row.get("external_id") for row in inventory["reminders"] if row.get("external_id")}}
        result = {"new": 0, "updated": 0, "skipped": 0, "conflicts": 0,
                  "failed_list_ids": sorted(set(list_ids) - successful)}
        for row in inventory["reminders"]:
            if row["list_id"] not in successful:
                continue
            mapping = self._match(row, maps, inventory["reminders"])
            if (not _supported(row) or row.get("external_id") and
                    external_counts[row["external_id"]] > 1):
                result["conflicts"] += 1
            elif mapping is None:
                result["new" if direction in ("import", "bidirectional") else "skipped"] += 1
            else:
                local = self._local(mapping["todo_id"])
                if local is None:
                    result["conflicts"] += 1
                    continue
                local_changed = _hash(local) != mapping["local_hash"]
                remote_changed = _hash(row, remote=True) != mapping["remote_hash"]
                if (local_changed and remote_changed or
                        remote_changed and direction == "export" or
                        local_changed and direction == "import"):
                    result["conflicts"] += 1
                elif local_changed or remote_changed:
                    result["updated"] += 1
                else:
                    result["skipped"] += 1
        if direction in ("export", "bidirectional") and self._get("ever_connected", False):
            with self._connect() as db:
                from apple_sync.category_rebuild import retained_ids
                from apple_sync.marker_recovery import pending_ids
                baseline = {row[0] for row in db.execute("SELECT todo_id FROM baseline_local")} | retained_ids(self) | pending_ids(self)
            mapped = {row["todo_id"] for row in maps}
            result["new"] += sum(local["source_type"] not in ("apple_reminder", "apple_recovered") and local["id"] not in mapped and
                                 local["id"] not in baseline and local["status"] != "cancelled"
                                 for local in self.todos.list_todos())
        return result

    def _one_time_items(self, direction, list_id, item_ids):
        if direction not in ("import", "export"):
            raise ValueError("one-time direction must be import or export")
        if not isinstance(item_ids, list) or not item_ids or any(
                not isinstance(key, str) or not key.strip() for key in item_ids) or len(set(item_ids)) != len(item_ids):
            raise ValueError("explicit distinct reminder_id or todo_id values are required")
        self._validate_selection([list_id], direction)
        if direction == "export":
            items = [self._local(key) for key in item_ids]
            if any(row is None for row in items):
                raise KeyError("Alcor todo not found")
            maps = {row["todo_id"]: row for row in self._maps()}
            if any((row["source_type"] == "apple_reminder" and row["id"] not in maps) or
                   (row["id"] in maps and maps[row["id"]]["list_id"] != list_id)
                   for row in items):
                raise ValueError("todo cannot be exported to the selected list")
            return items
        rows = self.items(list_id)
        external_counts = {key: sum(row.get("external_id") == key for row in rows)
                           for key in {row.get("external_id") for row in rows if row.get("external_id")}}
        items = []
        for key in item_ids:
            matches = [row for row in rows if row["list_id"] == list_id and row["reminder_id"] == key]
            if len(matches) != 1:
                raise KeyError("Apple reminder_id not found or ambiguous")
            if not _supported(matches[0]):
                raise ValueError("Apple reminder has unsupported fields")
            external = matches[0].get("external_id")
            if external and external_counts[external] != 1:
                raise ValueError("Apple reminder identity is ambiguous")
            items.append(matches[0])
        return items

    def preview_items(self, direction, list_id, item_ids):
        with self._lock:
            items = self._one_time_items(direction, list_id, item_ids)
            maps = self._maps()
            result = {"new": 0, "updated": 0, "skipped": 0, "conflicts": 0,
                      "item_ids": item_ids}
            for item in items:
                if direction == "import":
                    mapping = self._match(item, maps, items)
                    if mapping is None:
                        result["new"] += 1
                    else:
                        local = self._local(mapping["todo_id"])
                        if local is None or mapping["state"] == "conflict" or _hash(local) != mapping["local_hash"]:
                            result["conflicts"] += 1
                        elif _hash(item, remote=True) != mapping["remote_hash"]:
                            result["updated"] += 1
                        else:
                            result["skipped"] += 1
                else:
                    mapping = next((row for row in maps if row["todo_id"] == item["id"]), None)
                    if mapping is None:
                        result["new"] += 1
                    elif mapping["state"] == "conflict":
                        result["conflicts"] += 1
                    elif _hash(item) != mapping["local_hash"]:
                        result["updated"] += 1
                    else:
                        result["skipped"] += 1
            return result

    def one_time(self, direction, list_id, item_ids, *, confirm=False):
        if direction == 'export':
            raise PermissionError('导出仅可通过真实会话的具体单次审批执行')
        from apple_sync.approved_export import guard_unresolved
        guard_unresolved(self)
        if confirm is not True:
            raise ValueError("explicit confirmation is required")
        with self._lock:
            items = self._one_time_items(direction, list_id, item_ids)
            counts = {"status": "success", "imported": 0, "updated": 0,
                      "exported": 0, "conflicts": 0, "item_ids": item_ids}
            if direction == "import":
                inventory = self.bridge.call("reminders", list_ids=[list_id])["reminders"]
                for remote in items:
                    maps = self._maps()
                    mapping = self._match(remote, maps, inventory)
                    if mapping:
                        self._process_remote(remote, mapping, "import", counts)
                    elif self._recover_marked(remote, maps, counts):
                        continue
                    else:
                        key = _source_key(remote)
                        local = self.todos.upsert_external_todo(key, _payload(remote))
                        self._save_map(self._new_map(local, remote, "apple", key))
                        counts["imported"] += 1
            else:
                for local in items:
                    mapping = next((row for row in self._maps() if row["todo_id"] == local["id"]), None)
                    if mapping:
                        remote = self.bridge.call("reminder_lookup", list_id=list_id,
                            reminder_id=mapping["reminder_id"], external_id=mapping["external_id"]).get("reminder")
                        if remote is None:
                            raise ValueError("mapped Apple reminder is unavailable")
                        self._process_remote(remote, mapping, "export", counts)
                    else:
                        remote = self.bridge.call("reminder_upsert", **self._write_payload(
                            local, list_id=list_id))["reminder"]
                        self._save_map(self._new_map(local, remote, "alcor"))
                        counts["exported"] += 1
            return counts

    def sync(self, manual=False, confirm_deletions=False, confirm_local_deletions=False):
        with self._lock:
            failures = []
            counts = {"status": "success", "imported": 0, "updated": 0, "exported": 0,
                      "deleted_local": 0, "deleted_remote": 0, "conflicts": 0,
                      "pending_deletions": 0, "pending_local_deletions": 0, "failed_list_ids": []}
            if not self._get("connected", False):
                counts["status"] = "disconnected"
                return counts
            if self.bridge.call("reminder_status").get("authorization") != "full_access":
                counts["status"] = "permission_required"
                return counts
            automatic_deletions = self._get("sync_deletions", False)
            confirm_deletions = confirm_deletions or automatic_deletions
            confirm_local_deletions = confirm_local_deletions or automatic_deletions
            if self._get("sync_all", False):
                try:
                    counts['deleted_categories'] = synchronize_categories(self, self.todos, 'reminders')
                except Exception as exc:
                    counts['status'] = 'error'
                    self._set('error', report_sync_error('reminders', '同步分类', exc))
                    return counts
            if not self._get('sync_all',False):
                _,errors = process_operations(self,self.todos,'reminders')
                self._set('operation_errors',errors)
            for key,value in self._get('operation_counts',{}).items():
                counts[key] += value
            try:
                counts['exported'] += synchronize_moves(self, self.todos, 'reminders', lambda row, mapping, remote: _hash(row, remote=remote))
            except Exception as exc:
                counts['status'] = 'error'
                self._set('error', report_sync_error('reminders', '同步分类移动', exc))
                return counts
            stored_links = self._stored_links()
            using_links = bool(stored_links)
            name_errors = self._reconcile_category_names() if using_links else []
            existing_categories = {row['id'] for row in self.todos.categories()}
            enabled_links = [row for row in self._stored_links() if row["enabled"] and row['alcor_id'] in existing_categories] if using_links else []
            if any(row['enabled'] and row['alcor_id'] not in existing_categories for row in stored_links):
                counts['status'] = 'partial'
            selected = [row["apple_id"] for row in enabled_links] if using_links else self._get("list_ids", [])
            direction = self._get("direction", "bidirectional")
            from apple_sync import group_scan_claims as claims
            try:
                inventory = self.bridge.call('reminder_lists')
                groups = {row['id']:row for row in inventory.get('lists',[])}
                if len(groups) != len(inventory.get('lists',[])):
                    raise ValueError('reminder list inventory is ambiguous')
            except Exception as exc:
                counts['status']='error'
                self._set('error',report_sync_error('reminder','读取同步来源',exc))
                return counts
            # This is the first item inventory used by inbound-move/absence logic.
            # Capture before it, so later missing markers never absorb intervening edits.
            missing_captures={}
            for mapping in self._maps():
                if mapping['list_id'] in selected and mapping['state'] not in ('deleted','remote_deleted','conflict','pending_delete'):
                    try:
                        missing_captures[mapping['todo_id']]=self.todos.apple_capture_reminder_missing(mapping['todo_id'])
                    except (ValueError,KeyError) as exc:
                        missing_captures[mapping['todo_id']]={'error':str(exc)}
            deferred_inbound_moves=set()
            if using_links:
                from apple_sync.reminder_moves import reconcile_inbound_moves
                try:
                    verified_links=[]
                    for link in enabled_links:
                        try:
                            verify_category_source(self.todos.get_category(link['alcor_id']),link,groups.get(link['apple_id'],{}))
                        except claims.ScanUnavailable:
                            continue
                        verified_links.append(link)
                    imported_moves,deferred_inbound_moves,move_errors=reconcile_inbound_moves(self,self.todos,verified_links,inventory,missing_captures)
                    counts['updated']+=imported_moves
                    if move_errors:
                        counts['status']='partial'
                        failures.extend(move_errors)
                except Exception as exc:
                    counts['status']='error'
                    failures.append(report_sync_error('reminder','读取跨分类位置',exc))
            held=[]
            for group_id in dict.fromkeys(selected):
                token=None
                source_id=None
                item_phase_started=False
                try:
                    target=groups.get(group_id)
                    if not target:
                        raise claims.ScanUnavailable('reminder list is unavailable')
                    source_id=target.get('source_id')
                    if not source_id or source_id not in inventory.get('source_ids',[]):
                        raise claims.ScanUnavailable('reminder source identity is unavailable')
                    link=next((row for row in enabled_links if row['apple_id']==group_id),None)
                    if link:
                        verify_category_source(self.todos.get_category(link['alcor_id']),link,target)
                    token=claims.acquire(self,source_id,group_id,{'kind':'reminder_scan'})
                    current=self.bridge.call('reminder_lists')
                    found=[row for row in current.get('lists',[]) if row.get('id')==group_id]
                    if found != [target] or source_id not in current.get('source_ids',[]):
                        raise claims.ScanUnavailable('reminder list changed before scan')
                    if link:
                        verify_category_source(self.todos.get_category(link['alcor_id']),link,found[0])
                    item_phase_started=True
                    result,safe=self._sync_claimed_reminder_list(group_id,source_id,token,direction,using_links,enabled_links,
                                                               automatic_deletions,manual,confirm_local_deletions,missing_captures,deferred_inbound_moves)
                    for key,value in result.items():
                        if key in ('status','failed_list_ids'):
                            continue
                        counts[key]+=value
                    counts['failed_list_ids']+=result['failed_list_ids']
                    if result['status']!='success':
                        counts['status']='error' if result['status']=='error' else 'partial'
                    if safe:
                        # Finish only a fully returned per-list execution, with the same manager snapshots.
                        self._complete_reminder_scan(token,group_id)
                    else:
                        claims.uncertain(self,token)
                        held.append(group_id)
                except Exception as exc:
                    if isinstance(exc,claims.ScanUnavailable):
                        counts['failed_list_ids'].append(group_id)
                    retain,verification_error=self._failed_reminder_scan(token,source_id,group_id,item_phase_started,exc)
                    if retain:
                        held.append(group_id)
                    partial=isinstance(exc,(claims.ScanHeld,claims.ScanUnavailable)) and verification_error is None
                    if counts['status']!='error':
                        counts['status']='partial' if partial else 'error'
                    failures.append(str(exc) if partial else report_sync_error('reminder','同步清单',exc))
                    if verification_error:
                        failures.append(verification_error)
            counts['scan_claims']=claims.status(self)
            if held:
                counts['held_list_ids']=sorted(set(held))
            if name_errors and counts['status']=='success':
                counts['status']='error'
            if counts['status']=='success':
                self._set('last_success',self.clock())
                self._set('error','')
            else:
                self._set('error',failures[0] if failures else name_errors[0] if name_errors else '部分提醒事项待核对；其他清单继续同步')
            return counts

    def _failed_reminder_scan(self, token, source_id, group_id, item_phase_started, reason):
        from apple_sync import group_scan_claims as claims
        if not token:
            return False,None
        if not item_phase_started:
            try:
                if claims.cancel_unstarted_scan(self,token,source_id,group_id,{'kind':'reminder_scan'},reason):
                    return False,None
            except Exception as exc:
                # If verification/SQLite failed, do not attempt a second mutation.
                return True,report_sync_error('reminder','未发送扫描释放核验失败，保留占用',exc)
        try:
            claims.uncertain(self,token)
        except Exception as exc:
            return True,report_sync_error('reminder','扫描结果状态未知，保留占用',exc)
        return True,None

    def _complete_reminder_scan(self, token, group_id):
        from apple_sync import group_scan_claims as claims
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            claims.schema(db)
            row=db.execute('SELECT a.state,a.window,a.evidence,a.source_id,g.source_id AS held_source FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) WHERE g.attempt_id=? AND g.group_id=?',(token,group_id)).fetchone()
            expected_items={item[0] for item in db.execute('SELECT todo_id FROM mappings WHERE list_id=?',(group_id,))}
            held_items={item[0] for item in db.execute('SELECT local_id FROM calendar_scan_items WHERE attempt_id=?',(token,))}
            if (not row or row['state']!='scanning' or json.loads(row['window'])!={'kind':'reminder_scan'}
                    or json.loads(row['evidence'])!={} or row['source_id']!=row['held_source'] or expected_items!=held_items):
                raise ValueError('reminder scan claim changed before completion')
            db.execute("UPDATE calendar_scan_attempts SET state='done' WHERE attempt_id=?",(token,))
            db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=?',(token,))
            db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?',(token,))

    def _sync_claimed_reminder_list(self, group_id, source_id, token, direction, using_links, enabled_links,
                                    automatic_deletions, manual, confirm_local_deletions, missing_captures, deferred_inbound_moves):
        from apple_sync.group_scan_claims import claim_items, ScanHeld
        from apple_sync.marker_recovery import MarkerRecoveryError
        selected = [group_id]
        failures, name_errors = [], []
        counts = {"status":"success","imported":0,"updated":0,"exported":0,"deleted_local":0,
                  "deleted_remote":0,"conflicts":0,"pending_deletions":0,"pending_local_deletions":0,"failed_list_ids":[]}
        claim_items(self,token,[row['todo_id'] for row in self._maps() if row['list_id']==group_id])
        try:
            try:
                inventory = self.bridge.call("reminders", list_ids=selected)
            except Exception as exc:
                # No owner or native write has begun. The exact scan claim can finish.
                counts['status']='error'
                counts['failed_list_ids']=selected
                self._set('error',report_sync_error('reminder','读取提醒库存',exc))
                return counts, True
            successful = set(inventory["successful_list_ids"]) & set(selected)
            if any(row.get('list_id') != group_id or row.get('source_id') != source_id for row in inventory['reminders']):
                raise ValueError('reminder inventory source identity is unavailable or changed')
            counts["failed_list_ids"] = sorted(set(selected) - successful)
            if counts["failed_list_ids"]:
                with self._connect() as db:
                    db.executemany("UPDATE mappings SET missing_since='' WHERE list_id=?",
                                   [(key,) for key in counts["failed_list_ids"]])
            maps = self._maps()
            retired_ids = set(self._get('retired_reminder_ids',[]))
            rows = [row for row in inventory["reminders"] if row["list_id"] in successful and row['reminder_id'] not in retired_ids]
            rows = self._native_successors(rows)
            maps = self._maps()
            external_counts = {key: sum(row.get("external_id") == key for row in rows)
                               for key in {row.get("external_id") for row in rows if row.get("external_id")}}
            empty_unsafe = {key for key in successful if not any(row["list_id"] == key for row in rows) and
                            any(mapping["list_id"] == key and mapping["state"] == "active" and
                                mapping["origin"] == "apple" for mapping in maps)}
            if counts["failed_list_ids"] or empty_unsafe:
                counts["status"] = "partial"
            seen = set()
            for remote in rows:
                try:
                    item_direction = direction
                    category_id = None
                    if using_links:
                        link = next((row for row in enabled_links if row["apple_id"] == remote["list_id"]), None)
                        if link is None:
                            continue
                        item_direction = link["direction"]
                        category_id = link["alcor_id"]
                    if self._hold_identity_candidate(remote,token):
                        counts['conflicts']+=1
                        counts['status']='partial'
                        continue
                    if (remote.get("external_id") and
                            external_counts[remote["external_id"]] > 1):
                        mapping = self._match(remote, maps, rows)
                        if mapping:
                            self._conflict(mapping, "Apple reminder identity or recurrence is ambiguous")
                            seen.add(mapping["todo_id"])
                        counts["conflicts"] += 1
                        continue
                    mapping = self._match(remote, maps, rows)
                    if mapping:
                        seen.add(mapping["todo_id"])
                        if mapping['list_id'] != group_id:
                            raise ScanHeld('reminder moved across containers; joint identity review required')
                        claim_items(self,token,[mapping['todo_id']])
                        self._process_remote(remote, mapping, item_direction, counts, category_id)
                    elif self._recover_marked(remote, maps, counts,scan_token=token,scan_safe=not failures):
                        if remote.get("alcor_id"):
                            seen.add(remote["alcor_id"])
                    elif item_direction in ("import", "bidirectional"):
                        key = _source_key(remote)
                        local = self._place_todo(self.todos.upsert_external_todo(key, _payload(remote, category_id)),
                                                 category_id, item_direction)
                        claim_items(self,token,[local['id']])
                        mapping = self._new_map(local, remote, "apple", key)
                        self._save_map(mapping)
                        maps.append(mapping)
                        seen.add(local["id"])
                        counts["imported"] += 1
                except MarkerRecoveryError:
                    raise
                except ScanHeld as exc:
                    failures.append(str(exc))
                    counts['status']='partial'
                except Exception as exc:
                    failures.append(report_sync_error("reminder", "导入提醒事项", exc))
                    counts["status"] = "error"
            for mapping in self._maps():
                if (mapping['list_id'] not in successful or mapping['todo_id'] in seen or
                        mapping['state'] in ('deleted','remote_deleted','conflict','pending_delete')):
                    continue
                if mapping['todo_id'] in deferred_inbound_moves:
                    counts['status']='partial'
                    continue
                self._reconcile_missing_reminder(mapping, missing_captures.get(mapping['todo_id']), counts,
                    allow_delete=(manual or automatic_deletions) and confirm_local_deletions)
            if successful and (using_links or direction in ("export", "bidirectional")):
                available = {row["id"]: row for row in self.bridge.call("reminder_lists")["lists"]}
                targets = [key for key in selected if key in successful and available.get(key, {}).get("writable")]
                export_targets = [(row["apple_id"], row["alcor_id"]) for row in enabled_links
                                  if row["direction"] in ("export", "bidirectional") and row["apple_id"] in targets]
                if not using_links and targets:
                    export_targets = [(targets[0], None)]
                if export_targets:
                    category_names = {row["id"]: row["name"] for row in self.todos.categories()}
                    with self._connect() as db:
                        from apple_sync.category_rebuild import retained_ids
                        from apple_sync.marker_recovery import pending_ids
                        baseline = {row[0] for row in db.execute("SELECT todo_id FROM baseline_local")} | retained_ids(self) | pending_ids(self)
                    mapped = {row["todo_id"] for row in self._maps()}
                    for local in self.todos.list_todos():
                        if (local["source_type"] in ("apple_reminder", "apple_recovered") or local["id"] in mapped or
                                local["id"] in baseline or local["status"] == "cancelled"):
                            continue
                        match = next((item for item in export_targets
                                      if item[1] is None or local.get("category_id") == item[1] or
                                      (not local.get("category_id") and local.get("list_name") == category_names.get(item[1]))), None)
                        if match is None:
                            continue
                        try:
                            if match[1] and local.get("category_id") != match[1]:
                                local = self.todos.apple_place_item(local["id"], match[1])
                            self._ordinary_create(local,match[0],source_id,token)
                            counts["exported"] += 1
                        except Exception as exc:
                            from apple_sync.group_scan_claims import ScanUnavailable
                            failures.append(str(exc) if isinstance(exc,ScanUnavailable) else report_sync_error("reminder", "写入 Apple 提醒事项", exc))
                            counts["status"] = "partial" if isinstance(exc,ScanUnavailable) and counts['status']!='error' else "error"
            for mapping in self._maps():
                if mapping['list_id']!=group_id:
                    continue
                if mapping['state']=='pending_local_delete':
                    counts['pending_local_deletions']+=1
                    counts['status']='partial'
                elif mapping['state']=='pending_delete':
                    counts['pending_deletions']+=1
                    counts['status']='partial'
            counts['conflicts'] = max(counts['conflicts'],sum(row['state']=='conflict' for row in self._maps()))
            issues = [dict(todo_id=row['todo_id'],reason='复杂 Apple 提醒已只读保留') for row in self._maps() if (self._local(row['todo_id']) or {}).get('apple',{}).get('issues')]
            self._set('readonly_issues',issues)
            if counts['conflicts'] or issues or self._get('operation_errors',[]) or self._get('category_conflicts',[]) or self._get('reminder_move_errors',[]):
                counts['status'] = 'partial'
            if name_errors and counts["status"] == "success":
                counts["status"] = "error"
        except Exception as exc:
            failures.append(report_sync_error("reminder", "同步提醒事项", exc))
            counts["status"] = "error"
            self._set("error", failures[-1])
        return counts, counts['status'] != 'error'


    def _ordinary_create(self, local, list_id, source_id, token):
        """Freeze one normal create under its existing scan; unknown results stay held."""
        from apple_sync.group_scan_claims import claim_items, digest, ScanUnavailable
        from apple_sync.metadata import metadata
        inventory=self.bridge.call('reminder_lists')
        targets=[row for row in inventory['lists'] if row['id']==list_id]
        if (len(targets)!=1 or targets[0].get('source_id')!=source_id or
                source_id not in inventory.get('source_ids',[]) or not targets[0].get('writable')):
            raise ScanUnavailable('提醒清单来源已变化；尚未创建，请核对关联')
        target=targets[0]
        expected=self.todos.apple_capture_approved_export(local['id'])
        if expected['local']!=local or local.get('apple'):
            raise ScanUnavailable('本地提醒在创建前变化；内容已保留')
        for category in expected['categories']:
            if category.get('apple'):
                verify_category_source(category,{'alcor_id':category['id'],'apple_id':list_id},target)
        baselines=self._reminder_baselines(local['id'])
        payload=self._write_payload(local,list_id=list_id,include_receipt=False)
        binding=digest({'kind':'ordinary_reminder_create','owner':expected,'target':target,'payload':payload})
        path=self.db_path.parent/'native-receipts'/(binding+'.json')
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if path.exists() or path.is_symlink():
            raise ValueError('提醒创建已有回执或结果未知；请核对，不会重新发送')
        evidence={'kind':'ordinary_reminder_create','stage':'sending','local_id':local['id'],
                  'owner':expected,'target':target,'payload':payload,'receipt_file':str(path),'binding':binding,
                  'reason':'提醒创建结果待核对；本地内容与回执已保留，不会自动重发'}
        claim_items(self,token,[local['id']])
        def check(db, document):
            row=db.execute('SELECT a.state,a.window,a.evidence,a.source_id,g.source_id AS held_source FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) WHERE a.attempt_id=? AND g.group_id=?',(token,list_id)).fetchone()
            item=db.execute('SELECT attempt_id FROM calendar_scan_items WHERE local_id=?',(local['id'],)).fetchone()
            if (not row or row['state']!='scanning' or json.loads(row['window'])!={'kind':'reminder_scan'}
                    or json.loads(row['evidence'])!=document or row['source_id']!=source_id or row['held_source']!=source_id
                    or not item or item[0]!=token or self._reminder_baselines(local['id'],db)!=baselines
                    or db.execute('SELECT 1 FROM mappings WHERE todo_id=?',(local['id'],)).fetchone()):
                raise ValueError('reminder create claim or local mapping changed; review required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            check(db,{})
            db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',(json.dumps(evidence),token))
        if self.todos.apple_capture_approved_export(local['id'])!=expected:
            # No native call has begun; only this exact claim can drop the new item.
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                check(db,evidence)
                db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE attempt_id=?",(token,))
                db.execute('DELETE FROM calendar_scan_items WHERE local_id=? AND attempt_id=?',(local['id'],token))
            raise ScanUnavailable('本地提醒在发送前变化；尚未创建，已保留新内容')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            check(db,evidence)
        remote=self.bridge.call('reminder_upsert',**payload,expected_source_id=source_id,
                               expected_destination_snapshot=target,expected_absent=True,
                               receipt_file=str(path),receipt_binding=binding)['reminder']
        if (remote.get('source_id')!=source_id or remote.get('list_id')!=list_id or not remote.get('reminder_id')
                or json.loads(path.read_text())!={'binding':binding,'saved':remote}):
            raise ValueError('提醒创建的来源、对象或回执不一致；保留待核对，不会重发')
        saved={**evidence,'stage':'native_saved','native':remote}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            check(db,evidence)
            db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',(json.dumps(saved),token))
        finalized=self.todos.apple_finalize_approved_export(expected,remote)
        mapping=self._mapping_from_native(finalized,remote,'alcor')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            check(db,saved)
            if db.execute('SELECT 1 FROM mappings WHERE source_key=? OR (list_id=? AND reminder_id=?)',(mapping['source_key'],list_id,remote['reminder_id'])).fetchone():
                raise ValueError('reminder create result already has a mapping; review required')
            self._save_map(mapping,connection=db)
            values={'item-metadata:':[metadata(finalized),metadata(remote,True)],
                    'reminder-local-remind-at:':{'value':finalized.get('remind_at')},
                    'reminder-binding-generation:':{'generation':expected['generation'],'source_id':source_id,'group_id':list_id}}
            for prefix,value in values.items():
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(prefix+local['id'],json.dumps(value)))
            db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE attempt_id=?",(token,))
        return remote


    def _reminder_absence_evidence(self, mapping):
        """Existing complete reminders API, only for a mapped missing item.

        A single-list lookup is not deletion evidence. Require every currently
        exposed list and source to remain available across the complete fetch.
        Identity changes or incomplete sources remain a conflict, never a match.
        """
        before=self.bridge.call('reminder_lists')
        groups=before.get('lists',[])
        ids=[row['id'] for row in groups]
        if not ids or len(ids)!=len(set(ids)) or any(not row.get('source_id') or row['source_id'] not in before.get('source_ids',[]) for row in groups):
            raise ValueError('INBOUND_CHANGED: reminder source inventory is incomplete')
        inventory=self.bridge.call('reminders',list_ids=ids)
        if set(inventory.get('successful_list_ids',[]))!=set(ids) or inventory.get('failed_list_ids'):
            raise ValueError('INBOUND_CHANGED: complete reminder inventory is unavailable')
        after=self.bridge.call('reminder_lists')
        if before!=after:
            raise ValueError('INBOUND_CHANGED: reminder sources changed during absence check')
        sources={row['id']:row['source_id'] for row in groups}
        if mapping['list_id'] not in sources:
            raise ValueError('INBOUND_CHANGED: reminder list is unavailable')
        rows=inventory.get('reminders',[])
        if any(row.get('source_id')!=sources.get(row.get('list_id')) for row in rows):
            raise ValueError('INBOUND_CHANGED: reminder inventory contains unverified source identity')
        possible=[row for row in rows if row.get('reminder_id')==mapping['reminder_id'] or
                  mapping.get('external_id') and row.get('external_id')==mapping['external_id']]
        if possible:
            raise ValueError('INBOUND_CHANGED: missing reminder may have moved or changed identity; review required')
        return {'version':1,'source_id':sources[mapping['list_id']], 'groups':groups,
                'source_ids':before['source_ids'],'complete_list_ids':ids}

    def _reconcile_missing_reminder(self, mapping, observed, counts, *, allow_delete):
        from apple_sync.category_rebuild import retained_ids
        if mapping['todo_id'] in retained_ids(self):
            counts['status']='partial'
            return
        before=dict(mapping)
        baselines=self._reminder_baselines(mapping['todo_id'])
        previous=self._get('reminder-missing:'+mapping['todo_id'])
        prior=previous
        try:
            if not observed or observed.get('error'):
                raise ValueError('INBOUND_CHANGED: no trustworthy pre-scan owner snapshot')
            capture=observed['capture']
            evidence=self._reminder_absence_evidence(mapping)
            if capture['local'].get('apple',{}).get('source_id')!=evidence['source_id']:
                raise ValueError('INBOUND_CHANGED: missing reminder source cannot be verified')
            self.todos.apple_verify_reminder_sync(capture)
            if not previous:
                if mapping['missing_since']:
                    raise ValueError('INBOUND_CHANGED: historical absence has no first-observation owner snapshot')
                previous={'version':1,'observed':observed,'mapping':before,'evidence':evidence,'first_seen':self.clock()}
                updated={**before,'missing_since':self.clock(),'updated_at':self.clock()}
            else:
                def comparable(row):
                    return {key:value for key,value in row.items() if key not in ('state','missing_since','updated_at')}
                if (previous.get('version')!=1 or previous['observed']!=observed or
                        comparable(previous['mapping'])!=comparable(before) or previous['evidence']!=evidence):
                    raise ValueError('INBOUND_CHANGED: reminder changed since first complete absence')
                elapsed=datetime.fromisoformat(self.clock().replace('Z','+00:00'))-datetime.fromisoformat(previous['first_seen'].replace('Z','+00:00'))
                updated={**before,'updated_at':self.clock()}
                if elapsed>=timedelta(minutes=1):
                    updated['state']='pending_local_delete'
                    if allow_delete:
                        self.todos.apple_remove_reminder_inbound(capture,evidence['source_id'],mapping['list_id'],
                            mapping['reminder_id'],capture['generation'],observed['deletion_snapshot'])
                        updated.update(state='remote_deleted',conflict='')
                        counts['deleted_local']+=1
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT * FROM mappings WHERE todo_id=?',(mapping['todo_id'],)).fetchone()
                saved=db.execute('SELECT value FROM settings WHERE key=?',('reminder-missing:'+mapping['todo_id'],)).fetchone()
                if not current or dict(current)!=before or self._reminder_baselines(mapping['todo_id'],db)!=baselines or (json.loads(saved[0]) if saved else None)!=prior:
                    raise RuntimeError('reminder missing manager snapshot changed; reconciliation required')
                self._save_map(updated,connection=db)
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('reminder-missing:'+mapping['todo_id'],json.dumps(previous)))
            mapping.clear()
            mapping.update(updated)
        except ValueError as exc:
            if not str(exc).startswith('INBOUND_CHANGED:'):
                raise
            self._reminder_conflict(mapping,before,baselines,observed,{'absence':'unverified'},str(exc))
            counts['conflicts']+=1
            counts['status']='partial'

    def resolve_conflict(self, todo_id, choice):
        with self._lock:
            from apple_sync.item_conflicts import resolve
            return resolve(self,self.todos,'reminders',todo_id,choice)


    def _batch_selections(self, selections):
        if not isinstance(selections, list) or not selections:
            raise ValueError("select at least one category")
        rows, seen_apple, seen_alcor = [], set(), set()
        for raw in selections:
            if not isinstance(raw, dict):
                raise ValueError("selection must be an object")
            apple_id = str(raw.get("apple_id") or "").strip()
            alcor_id = str(raw.get("alcor_id") or "").strip()
            if not apple_id or not alcor_id:
                raise ValueError("apple_id and alcor_id are required")
            if apple_id in seen_apple or alcor_id in seen_alcor:
                raise ValueError("category links must be one-to-one")
            seen_apple.add(apple_id)
            seen_alcor.add(alcor_id)
            rows.append({"apple_id": apple_id, "alcor_id": alcor_id})
        return rows

    @staticmethod
    def _in_dates(value, start, end):
        text = str(value or "")[:10]
        return len(text) == 10 and start <= text <= end

    def _reminder_kept(self, remote, start, end, include_undated, include_completed):
        if remote.get("completed") and not include_completed:
            return False
        due = remote.get("due_on") or ""
        planned = remote.get("planned_on") or ""
        if not due and not planned:
            return include_undated
        return self._in_dates(due, start, end) or self._in_dates(planned, start, end)

    def _expand_todo_tree(self, rows, start, end, include_undated, include_completed):
        by_id = {row["id"]: row for row in rows}
        chosen = set()
        for row in rows:
            if row["status"] == "cancelled":
                continue
            if row["status"] == "completed" and not include_completed:
                continue
            dated = self._in_dates(row.get("due_on"), start, end) or self._in_dates(row.get("planned_on"), start, end)
            undated = not row.get("due_on") and not row.get("planned_on")
            if dated or (undated and include_undated):
                chosen.add(row["id"])
        changed = True
        while changed:
            changed = False
            for row in rows:
                parent = row.get("parent_id")
                if row["id"] in chosen and parent in by_id and parent not in chosen:
                    if by_id[parent]["status"] != "cancelled" and (
                            include_completed or by_id[parent]["status"] != "completed"):
                        chosen.add(parent)
                        changed = True
        pending = list(chosen)
        while pending:
            parent_id = pending.pop()
            for row in rows:
                if row.get("parent_id") != parent_id or row["id"] in chosen or row["status"] == "cancelled":
                    continue
                if row["status"] == "completed" and not include_completed:
                    continue
                chosen.add(row["id"])
                pending.append(row["id"])
        return [by_id[key] for key in chosen]

    def _batch_items(self, direction, selections, start, end, include_undated, include_completed):
        if direction not in ("import", "export"):
            raise ValueError("one-time direction must be import or export")
        try:
            datetime.fromisoformat(start)
            datetime.fromisoformat(end)
        except (TypeError, ValueError) as exc:
            raise ValueError("time range must be YYYY-MM-DD") from exc
        if end < start:
            raise ValueError("time range must include at least one day")
        rows = self._batch_selections(selections)
        if self.bridge.call("reminder_status").get("authorization") != "full_access":
            raise PermissionError("Reminders full access is required")
        available = {row["id"]: row for row in self.bridge.call("reminder_lists")["lists"]}
        for row in rows:
            if row["apple_id"] not in available:
                raise ValueError("selected reminder list is unavailable")
            self.todos.get_category(row["alcor_id"])
            if direction == "export" and not available[row["apple_id"]]["writable"]:
                raise ValueError("selected reminder list is read only")
        chosen = []
        failed = 0
        if direction == "import":
            inventory = self.bridge.call("reminders", list_ids=[row["apple_id"] for row in rows])
            successful = set(inventory["successful_list_ids"])
            failed = len([row for row in rows if row["apple_id"] not in successful])
            targets = {row["apple_id"]: row["alcor_id"] for row in rows}
            for remote in inventory["reminders"]:
                if remote["list_id"] in targets and self._reminder_kept(
                        remote, start, end, include_undated, include_completed):
                    chosen.append((remote, targets[remote["list_id"]]))
        else:
            by_category = {row["alcor_id"]: row["apple_id"] for row in rows}
            locals_only = [row for row in self.todos.list_todos() if row["source_type"] not in ("apple_reminder", "apple_recovered")]
            for alcor_id, apple_id in by_category.items():
                members = [row for row in locals_only if row.get("category_id") == alcor_id]
                ids = {row["id"] for row in members}
                pending = list(ids)
                while pending:
                    parent_id = pending.pop()
                    for row in locals_only:
                        if row.get("parent_id") == parent_id and row["id"] not in ids:
                            members.append(row)
                            ids.add(row["id"])
                            pending.append(row["id"])
                for local in self._expand_todo_tree(members, start, end, include_undated, include_completed):
                    chosen.append((local, apple_id))
        return chosen, failed

    def preview_batch(self, direction, selections, start, end, *, include_undated=False, include_completed=False):
        with self._lock:
            chosen, failed = self._batch_items(direction, selections, start, end, include_undated, include_completed)
            maps = self._maps()
            new = skipped = 0
            identities = []
            for item, target in chosen:
                if direction == "import":
                    mapped = self._match(item, maps, [row for row, _target in chosen]) is not None
                    identities.append(("import", item["list_id"], item["reminder_id"], target))
                else:
                    mapped = any(row["todo_id"] == item["id"] for row in maps)
                    identities.append(("export", item["id"], target))
                if mapped:
                    skipped += 1
                else:
                    new += 1
            preview = {"new": new, "skipped": skipped, "failed": failed}
            token = secrets.token_urlsafe(24)
            self._batch_preview = (token, direction, json.dumps(self._batch_selections(selections), sort_keys=True),
                                   start, end, bool(include_undated), bool(include_completed),
                                   tuple(identities), preview, time.monotonic() + 600)
            return {"preview": preview, "preview_token": token}

    def transfer_batch(self, direction, selections, start, end, token, *, confirm=False,
                       include_undated=False, include_completed=False):
        if direction == 'export':
            raise PermissionError('导出仅可通过真实会话的具体单次审批执行')
        from apple_sync.approved_export import guard_unresolved
        guard_unresolved(self)
        if confirm is not True:
            raise ValueError("explicit confirmation is required")
        with self._lock:
            pending = getattr(self, "_batch_preview", None)
            selection_key = json.dumps(self._batch_selections(selections), sort_keys=True)
            if (not isinstance(token, str) or not pending or not secrets.compare_digest(token, pending[0]) or
                    direction != pending[1] or selection_key != pending[2] or start != pending[3] or end != pending[4] or
                    bool(include_undated) != pending[5] or bool(include_completed) != pending[6] or
                    time.monotonic() > pending[9]):
                raise ValueError("matching Apple category preview and confirmation are required")
            chosen, failed = self._batch_items(direction, selections, start, end, include_undated, include_completed)
            identities = []
            for item, target in chosen:
                if direction == "import":
                    identities.append(("import", item["list_id"], item["reminder_id"], target))
                else:
                    identities.append(("export", item["id"], target))
            if tuple(identities) != pending[7] or failed != pending[8]["failed"]:
                self._batch_preview = None
                raise ValueError("Apple sync inventory changed; preview again")
            self._batch_preview = None
            counts = {"status": "success", "imported": 0, "exported": 0, "skipped": 0, "failed": failed}
            for item, target in chosen:
                if direction == "import":
                    if self._match(item, self._maps(), [row for row, _target in chosen]):
                        counts["skipped"] += 1
                        continue
                    local = self._place_todo(self.todos.upsert_external_todo(_source_key(item), _payload(item, target)),
                                             target, "import")
                    self._save_map(self._new_map(local, item, "apple"))
                    counts["imported"] += 1
                else:
                    if any(row["todo_id"] == item["id"] for row in self._maps()):
                        counts["skipped"] += 1
                        continue
                    remote = self.bridge.call("reminder_upsert", **self._write_payload(item, list_id=target))["reminder"]
                    self._save_map(self._new_map(item, remote, "alcor"))
                    counts["exported"] += 1
            return counts

    def start_auto(self, interval=300):
        if self._auto_started:
            return
        self._auto_started = True
        from apple_sync.runtime import EventKitAutoRunner
        runner = EventKitAutoRunner(self.bridge, lambda: self._get("connected", False),
                                    lambda: self._run_auto_sync(runner.stop_event), kind="reminder", interval=interval)
        self._runner = runner
        self._runner.start()

    def _run_auto_sync(self, stop_event):
        with self._lock:
            if stop_event.is_set() or not self._get('auto_enabled', True) or not self._get('connected', False):
                return None
            return self.sync()

    def stop_auto(self):
        if self._auto_started:
            self._runner.stop()
        self._auto_started = False
