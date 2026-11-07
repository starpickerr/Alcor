"""Conservative, persistent reconciliation of Apple Reminders and Stella todos."""

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

from apple_sync.metadata import snapshot_fields, record_baseline, changed as metadata_changed, write_fields, conflict_summaries
from apple_sync.contracts import bind_item,category_binding,process_operations,locate_remote,resolve_pending_deletion
from apple_sync.categories import synchronize_categories, synchronize_moves, reconcile_category_names, resolve_category_conflict, prime_bindings
from apple_sync.category_links import bidirectional_links, reconcile_names, validate_links
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
            "remind_at": remote.get("remind_at"), "priority": _priority(remote.get("priority"), remote=True),
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
                    apple_id TEXT PRIMARY KEY, stella_id TEXT NOT NULL UNIQUE, direction TEXT NOT NULL,
                    enabled INTEGER NOT NULL, apple_name TEXT NOT NULL, stella_name TEXT NOT NULL,
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

    def _save_map(self, mapping):
        with self._connect() as db:
            db.execute("""INSERT INTO mappings VALUES
                (:todo_id,:source_key,:list_id,:reminder_id,:external_id,:origin,:local_hash,
                 :remote_hash,:state,:conflict,:missing_since,:updated_at)
                ON CONFLICT(todo_id) DO UPDATE SET
                source_key=excluded.source_key,list_id=excluded.list_id,reminder_id=excluded.reminder_id,
                external_id=excluded.external_id,origin=excluded.origin,local_hash=excluded.local_hash,
                remote_hash=excluded.remote_hash,state=excluded.state,conflict=excluded.conflict,
                missing_since=excluded.missing_since,updated_at=excluded.updated_at""", mapping)

    def _new_map(self, local, remote, origin, source_key=None):
        self.todos.apple_apply_metadata(local["id"],snapshot_fields(remote))
        local = self.todos.get_todo(local["id"])
        record_baseline(self,local,remote)
        self.todos.apple_apply_metadata(local['id'],{'native_managed':True})
        bind_item(self.todos,local,remote,'reminders',[] if _supported(remote) else ['此提醒包含复杂重复或位置/多重提醒，完整保留；请在 Apple 中编辑'])
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

    def _recover_marked(self, remote, maps, counts):
        """Reattach a native save that succeeded before its mapping committed."""
        if not remote.get("stella_id"):
            return False
        local = self._local(remote["stella_id"])
        if local is None or local["source_type"] == "apple_reminder" or any(
                row["todo_id"] == remote["stella_id"] for row in maps):
            counts["conflicts"] += 1
            return True
        mapping = self._new_map(local, remote, "stella")
        if _hash(local) != _hash(remote, remote=True):
            mapping.update(state="conflict", conflict="Stella and Apple differ after an interrupted export")
            counts["conflicts"] += 1
        self._save_map(mapping)
        maps.append(mapping)
        return True

    def status(self):
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
        return {**native, "connected": bool(self._get("connected", False)),
                "auto_enabled": bool(self._get("auto_enabled", True)), "sync_all": bool(self._get("sync_all", False)),
                "list_ids": self._get("list_ids", []), "direction": self._get("direction", "bidirectional"),
                "lists": lists, "categories": self.todos.categories(), "last_success": self._get("last_success"), "error": listing_error or self._get("error", ""),
                "conflicts": conflict_summaries(maps, self.todos.get_todo, 'todo_id'),
                "pending_deletions": sum(row["state"] == "pending_delete" for row in maps),
                "pending_local_deletions": sum(row["state"] == "pending_local_delete" for row in maps),
                "readonly_issues":self._get('readonly_issues',[]),"category_conflicts":self._get('category_conflicts',[]), "operations":self.todos.apple_operations(),
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
            links = state['links'] if sync_all else bidirectional_links(selections, state['lists'], state['categories'])
            state = self.replace_links(links)
            self._set("sync_all", sync_all)
            self._set("sync_deletions", sync_all)
            selected = {row["stella_id"] for row in state["links"] if row["enabled"]}
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
        return {"apple_id": row["apple_id"], "stella_id": row["stella_id"], "direction": row["direction"],
                "enabled": bool(row["enabled"]), "apple_name": row["apple_name"], "stella_name": row["stella_name"],
                "name_conflict": row["name_conflict"]}

    def _save_link(self, link):
        with self._connect() as db:
            db.execute("""UPDATE category_links SET apple_name=?, stella_name=?, name_conflict=?, updated_at=?
                WHERE apple_id=?""", (link["apple_name"], link["stella_name"], link["name_conflict"],
                                      self.clock(), link["apple_id"]))

    def replace_links(self, links):
        if not isinstance(links, list):
            raise ValueError("links must be a list")
        if self.bridge.call("reminder_status").get("authorization") != "full_access":
            raise PermissionError("Reminders full access is required")
        available = {row["id"]: row for row in self.bridge.call("reminder_lists")["lists"]}
        previous = {row["apple_id"]: row for row in self._stored_links()}
        prepared = []
        for raw in links:
            if not isinstance(raw, dict):
                raise ValueError("link must be an object")
            item = {"apple_id": str(raw.get("apple_id") or "").strip(),
                    "stella_id": str(raw.get("stella_id") or "").strip(),
                    "direction": str(raw.get("direction") or "").strip(),
                    "enabled": raw.get("enabled")}
            create_name = str(raw.get("create_name") or "").strip()
            if not item["stella_id"] and create_name:
                existing = next((row for row in self.todos.categories() if row["name"] == create_name), None)
                item["stella_id"] = existing["id"] if existing else self.todos.create_category({"name": create_name, "color": available.get(item["apple_id"], {}).get("color", "#6B8AF7")})["id"]
            prepared.append(item)
        validate_links(prepared)
        rows = []
        for item in prepared:
            apple = available.get(item["apple_id"])
            if apple is None:
                old = previous.get(item["apple_id"])
                if not old or old["stella_id"] != item["stella_id"]:
                    raise ValueError("selected reminder list is unavailable")
                apple = {"title": old["apple_name"]}
            category = self.todos.get_category(item["stella_id"])
            if item['apple_id'] in available:
                self.todos.apple_bind('category',category['id'],category_binding(available[item['apple_id']]))
            old = previous.get(item["apple_id"], {})
            baseline = {key: old[key] for key in ("apple_name", "stella_name", "name_conflict")} if old.get("stella_id") == item["stella_id"] else {}
            rows.append({**item, "apple_name": apple["title"], "stella_name": category["name"],
                         "name_conflict": "", **baseline, "enabled": 1 if item["enabled"] else 0})
        with self._lock:
            now = self.clock()
            with self._connect() as db:
                db.execute("DELETE FROM category_links")
                db.executemany("""INSERT INTO category_links
                    (apple_id, stella_id, direction, enabled, apple_name, stella_name, name_conflict, updated_at)
                    VALUES (:apple_id, :stella_id, :direction, :enabled, :apple_name, :stella_name, :name_conflict, :updated_at)""",
                    [{**row, "updated_at": now} for row in rows])
            enabled = [row for row in rows if row["enabled"]]
            if enabled and not self._get("ever_connected", False):
                with self._connect() as db:
                    db.executemany("INSERT OR IGNORE INTO baseline_local VALUES (?)",
                                   [(row["id"],) for row in self.todos.list_todos() if row["source_type"] != "apple_reminder"])
                self._set("ever_connected", True)
            directions = {row["direction"] for row in enabled}
            self._set("list_ids", [row["apple_id"] for row in enabled])
            if len(directions) == 1:
                self._set("direction", directions.pop())
            self._set("connected", bool(enabled))
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
        if remote.get("stella_id"):
            marked = [row for row in maps if row["todo_id"] == remote["stella_id"]]
            if len(marked) == 1:
                return marked[0]
        external = remote.get("external_id")
        if external and sum(item.get("external_id") == external for item in inventory) == 1:
            matches = [row for row in maps if row["external_id"] == external]
            if len(matches) == 1:
                return matches[0]
        return None

    def _write_payload(self, local, mapping=None, list_id=None):
        data = {**write_fields(self,local,mapping),"list_id": list_id or mapping["list_id"], "title": local["title"],
                "notes": local["notes"], "due_on": local["due_on"], "due_time": local["due_time"],
                "timezone": local["timezone"], "planned_on": local["planned_on"],
                "remind_at": local["remind_at"], "priority": _priority(local["priority"]),
                "completed": local["status"] == "completed", "completed_at": local["completed_at"],
                "repeat_frequency": local["repeat_frequency"],
                "repeat_days": json.loads(local["repeat_days"]), "repeat_until": local["repeat_until"],
                "repeat_interval": local.get("repeat_interval", 1)}
        if mapping:
            data.update(reminder_id=mapping["reminder_id"], external_id=mapping["external_id"])
        else:
            data["stella_id"] = local["id"]
        return data

    def _apply_remote(self, mapping, local, remote):
        payload = _payload(remote)
        if mapping["origin"] == "apple":
            return self.todos.upsert_external_todo(mapping["source_key"], payload,
                                                   todo_id=local["id"], reconciled=True)
        fields = {key: value for key, value in payload.items() if key not in ("completed", "completed_at")}
        updated = self.todos.apple_apply_item(local["id"], fields)
        if updated["repeat_frequency"] == "none" and updated["status"] != "cancelled":
            wanted = "completed" if payload["completed"] else "todo" if updated["status"] == "completed" else updated["status"]
            if wanted != updated["status"]:
                updated = self.todos.apple_record_completion(local['id'],remote,payload)
        return updated

    def _native_successors(self,rows):
        retired = self._get('retired_reminder_ids',[])
        for mapping in self._maps():
            local = self._local(mapping['todo_id'])
            if not local or local.get('apple_pending') or mapping['state']!='active':
                continue
            before = (local.get('apple') or {}).get('native',{})
            if (before.get('recurrence') or {}).get('frequency','none')=='none':
                continue
            completed = next((row for row in rows if row['reminder_id']==mapping['reminder_id'] and row.get('completed')),None)
            if not completed or not completed.get('external_id'):
                continue
            following = [row for row in rows if row['list_id']==completed['list_id'] and row.get('external_id')==completed['external_id'] and not row.get('completed') and (row.get('due_on') or '')>(completed.get('due_on') or '')]
            if len(following)!=1:
                continue
            local = self.todos.apple_record_completion(local['id'],completed,_payload(following[0]))
            self._refresh_map(mapping,local,following[0])
            retired.append(completed['reminder_id'])
        self._set('retired_reminder_ids',list(dict.fromkeys(retired)))
        return [row for row in rows if row['reminder_id'] not in retired]

    def _process_remote(self, remote, mapping, direction, counts, category_id=None):
        if not _supported(remote):
            local = self._local(mapping['todo_id'])
            if local:
                local = self._apply_remote(mapping,local,remote)
                self._refresh_map(mapping,local,remote)
            counts['conflicts'] += 1
            return
        if mapping.get('state')=='conflict':
            counts['conflicts'] += 1
            return
        local = self._local(mapping["todo_id"])
        if local is None:
            if _hash(remote, remote=True) != mapping["remote_hash"]:
                self._conflict(mapping, "Apple changed a record deleted in Stella")
                counts["conflicts"] += 1
                return
            mapping.update(state="pending_delete" if direction in ("export", "bidirectional") else "deleted",
                           updated_at=self.clock())
            self._save_map(mapping)
            return
        local_extra,remote_extra = metadata_changed(self,local,remote)
        local_changed = _hash(local) != mapping["local_hash"] or local_extra
        remote_changed = _hash(remote, remote=True) != mapping["remote_hash"] or remote_extra
        if local_changed and remote_changed:
            self._conflict(mapping, "Both Stella and Apple changed this reminder")
            counts["conflicts"] += 1
            return
        if remote_changed:
            if direction not in ("import", "bidirectional"):
                self._conflict(mapping, "Apple changed an export-only reminder")
                counts["conflicts"] += 1
                return
            local = self._place_todo(self._apply_remote(mapping, local, remote), category_id, direction)
            counts["updated"] += 1
        elif local_changed:
            if direction not in ("export", "bidirectional") or not remote.get("writable", False):
                self._conflict(mapping, "Stella changed a reminder that cannot be exported")
                counts["conflicts"] += 1
                return
            remote = self.bridge.call("reminder_upsert", **self._write_payload(local, mapping))["reminder"]
            if local["source_type"] == "apple_reminder":
                local = self._place_todo(self._apply_remote(mapping, local, remote), category_id, direction)
            counts["exported"] += 1
        else:
            local = self._place_todo(local, category_id, direction)
        self._refresh_map(mapping, local, remote)

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
                baseline = {row[0] for row in db.execute("SELECT todo_id FROM baseline_local")}
            mapped = {row["todo_id"] for row in maps}
            result["new"] += sum(local["source_type"] != "apple_reminder" and local["id"] not in mapped and
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
                raise KeyError("Stella todo not found")
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
                        self._save_map(self._new_map(local, remote, "stella"))
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
            enabled_links = [row for row in self._stored_links() if row["enabled"]] if using_links else []
            selected = [row["apple_id"] for row in enabled_links] if using_links else self._get("list_ids", [])
            direction = self._get("direction", "bidirectional")
            try:
                inventory = self.bridge.call("reminders", list_ids=selected)
                successful = set(inventory["successful_list_ids"]) & set(selected)
                counts["failed_list_ids"] = sorted(set(selected) - successful)
                if counts["failed_list_ids"]:
                    with self._connect() as db:
                        db.executemany("UPDATE mappings SET missing_since='' WHERE list_id=?",
                                       [(key,) for key in counts["failed_list_ids"]])
                maps = self._maps()
                retired_ids = set(self._get('retired_reminder_ids',[]))
                rows = [row for row in inventory["reminders"] if row["list_id"] in successful and row['reminder_id'] not in retired_ids]
                rows = self._native_successors(rows)
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
                            category_id = link["stella_id"]
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
                            self._process_remote(remote, mapping, item_direction, counts, category_id)
                        elif self._recover_marked(remote, maps, counts):
                            if remote.get("stella_id"):
                                seen.add(remote["stella_id"])
                        elif item_direction in ("import", "bidirectional"):
                            key = _source_key(remote)
                            local = self._place_todo(self.todos.upsert_external_todo(key, _payload(remote, category_id)),
                                                     category_id, item_direction)
                            mapping = self._new_map(local, remote, "apple", key)
                            self._save_map(mapping)
                            maps.append(mapping)
                            seen.add(local["id"])
                            counts["imported"] += 1
                    except Exception as exc:
                        failures.append(report_sync_error("reminder", "导入提醒事项", exc))
                        counts["status"] = "error"
                now = datetime.fromisoformat(self.clock().replace("Z", "+00:00"))
                for mapping in self._maps():
                    if (mapping["list_id"] not in successful or (mapping["list_id"] in empty_unsafe and not automatic_deletions) or
                            mapping["todo_id"] in seen or mapping["state"] in
                            ("deleted", "remote_deleted", "conflict")):
                        continue
                    try:
                        found = self.bridge.call("reminder_lookup", list_id=mapping["list_id"],
                            reminder_id=mapping["reminder_id"], external_id=mapping["external_id"]).get("reminder")
                        if found:
                            self._process_remote(found, mapping, direction, counts)
                            continue
                        local = self._local(mapping["todo_id"])
                        if local is None:
                            mapping["state"] = "pending_delete"
                        elif not mapping["missing_since"]:
                            mapping["missing_since"] = self.clock()
                        elif now - datetime.fromisoformat(mapping["missing_since"].replace("Z", "+00:00")) >= timedelta(minutes=1):
                            if _hash(local) == mapping["local_hash"]:
                                mapping["state"] = "pending_local_delete"
                            else:
                                self._conflict(mapping, "Apple reminder is missing or moved")
                                counts["conflicts"] += 1
                                continue
                        mapping["updated_at"] = self.clock()
                        self._save_map(mapping)
                    except Exception as exc:
                        failures.append(report_sync_error("reminder", "核对已同步提醒事项", exc))
                        counts["status"] = "error"
                if successful and (using_links or direction in ("export", "bidirectional")):
                    available = {row["id"]: row for row in self.bridge.call("reminder_lists")["lists"]}
                    targets = [key for key in selected if key in successful and available.get(key, {}).get("writable")]
                    export_targets = [(row["apple_id"], row["stella_id"]) for row in enabled_links
                                      if row["direction"] in ("export", "bidirectional") and row["apple_id"] in targets]
                    if not using_links and targets:
                        export_targets = [(targets[0], None)]
                    if export_targets:
                        category_names = {row["id"]: row["name"] for row in self.todos.categories()}
                        with self._connect() as db:
                            baseline = {row[0] for row in db.execute("SELECT todo_id FROM baseline_local")}
                        mapped = {row["todo_id"] for row in self._maps()}
                        for local in self.todos.list_todos():
                            if (local["source_type"] == "apple_reminder" or local["id"] in mapped or
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
                                remote = self.bridge.call("reminder_upsert", **self._write_payload(
                                    local, list_id=match[0]))["reminder"]
                                self._save_map(self._new_map(local, remote, "stella"))
                                counts["exported"] += 1
                            except Exception as exc:
                                failures.append(report_sync_error("reminder", "写入 Apple 提醒事项", exc))
                                counts["status"] = "error"
                for mapping in self._maps():
                    if mapping["state"] == "pending_local_delete":
                        counts["pending_local_deletions"] += 1
                        if ((manual or automatic_deletions) and confirm_local_deletions and mapping["list_id"] in successful and
                                (mapping["list_id"] not in empty_unsafe or automatic_deletions)):
                            try:
                                found = self.bridge.call("reminder_lookup", list_id=mapping["list_id"],
                                    reminder_id=mapping["reminder_id"], external_id=mapping["external_id"])
                                if found.get("reminder") is not None:
                                    continue
                                local = self._local(mapping["todo_id"])
                                if local is not None and _hash(local) != mapping["local_hash"]:
                                    raise ValueError("Stella reminder changed before deletion")
                                if local is not None:
                                    self.todos.apple_remove_item(mapping["todo_id"],preserve_children=True)
                                mapping["state"] = "remote_deleted"
                                mapping["conflict"] = ""
                                self._save_map(mapping)
                                counts["deleted_local"] += 1
                                counts["pending_local_deletions"] -= 1
                            except ValueError as exc:
                                mapping["conflict"] = str(exc)
                                self._save_map(mapping)
                                counts["conflicts"] += 1
                                counts["status"] = "partial"
                            except Exception as exc:
                                failures.append(report_sync_error("reminder", "删除 Stella 提醒事项", exc))
                                counts["status"] = "error"
                    elif mapping["state"] == "pending_delete":
                        counts["pending_deletions"] += 1
                        if ((manual or automatic_deletions) and confirm_deletions and mapping["list_id"] in successful and
                                (not using_links or any(row["apple_id"] == mapping["list_id"] and
                                 row["direction"] in ("export", "bidirectional") for row in enabled_links))):
                            try:
                                self.bridge.call("reminder_delete", list_id=mapping["list_id"],
                                                 reminder_id=mapping["reminder_id"],
                                                 external_id=mapping["external_id"])
                                mapping["state"] = "deleted"
                                self._save_map(mapping)
                                counts["deleted_remote"] += 1
                                counts["pending_deletions"] -= 1
                            except Exception as exc:
                                failures.append(report_sync_error("reminder", "删除 Apple 提醒事项", exc))
                                counts["status"] = "error"
                counts['conflicts'] = max(counts['conflicts'],sum(row['state']=='conflict' for row in self._maps()))
                issues = [dict(todo_id=row['todo_id'],reason='复杂 Apple 提醒已只读保留') for row in self._maps() if (self._local(row['todo_id']) or {}).get('apple',{}).get('issues')]
                self._set('readonly_issues',issues)
                if issues or self._get('operation_errors',[]) or self._get('category_conflicts',[]):
                    counts['status'] = 'partial'
                if name_errors and counts["status"] == "success":
                    counts["status"] = "error"
                if counts["status"] == "success":
                    self._set("last_success", self.clock())
                    self._set("error", "")
                else:
                    self._set("error", failures[0] if failures else name_errors[0] if name_errors else "Reminders sync needs review or another attempt")
            except Exception as exc:
                failures.append(report_sync_error("reminder", "同步提醒事项", exc))
                counts["status"] = "error"
                self._set("error", failures[-1])
            return counts

    def resolve_conflict(self,todo_id,choice):
        if choice not in ('keep_stella','keep_apple'):
            raise ValueError('invalid conflict resolution')
        mapping = next((row for row in self._maps() if row['todo_id']==todo_id),None)
        if not mapping or mapping['state']!='conflict':
            raise KeyError('conflict not found')
        local = self._local(todo_id)
        remote = locate_remote(self,mapping,'reminders')
        if remote:
            mapping.update(list_id=remote['list_id'],reminder_id=remote['reminder_id'])
        if resolve_pending_deletion(self,self.todos,mapping,'reminders',choice,remote):
            return self.status()
        category = next((row['stella_id'] for row in self._stored_links() if row['apple_id']==mapping['list_id']),None)
        if choice=='keep_apple':
            if remote is None:
                if local:
                    self.todos.apple_remove_item(todo_id)
                mapping.update(state='remote_deleted',conflict='')
                self._save_map(mapping)
                return self.status()
            if local is None:
                local = self.todos.apple_restore_item(todo_id,_payload(remote),category,mapping['source_key'] if mapping['origin']=='apple' else None)
            else:
                local = self.todos.apple_place_item(todo_id,category) if category and category!=local['category_id'] else local
                local = self._apply_remote(mapping,local,remote)
        elif local is None:
            if remote:
                if not remote.get('writable'):
                    raise PermissionError('Apple 来源不允许删除')
                self.bridge.call('reminder_delete',list_id=mapping['list_id'],reminder_id=mapping['reminder_id'],external_id=mapping['external_id'])
            mapping.update(state='deleted',conflict='')
            self._save_map(mapping)
            return self.status()
        else:
            if remote and (not remote.get('writable') or not _supported(remote)):
                raise PermissionError('此 Apple 提醒只读，请在 Apple 中编辑')
            target = next((row['apple_id'] for row in self._stored_links() if row['stella_id']==local.get('category_id')),mapping['list_id'])
            payload = self._write_payload(local,mapping if remote else None,list_id=target)
            if remote:
                payload['lookup_list_id'] = remote['list_id']
            remote = self.bridge.call('reminder_upsert',**payload)['reminder']
        self._refresh_map(mapping,local,remote)
        return self.status()

    def _batch_selections(self, selections):
        if not isinstance(selections, list) or not selections:
            raise ValueError("select at least one category")
        rows, seen_apple, seen_stella = [], set(), set()
        for raw in selections:
            if not isinstance(raw, dict):
                raise ValueError("selection must be an object")
            apple_id = str(raw.get("apple_id") or "").strip()
            stella_id = str(raw.get("stella_id") or "").strip()
            if not apple_id or not stella_id:
                raise ValueError("apple_id and stella_id are required")
            if apple_id in seen_apple or stella_id in seen_stella:
                raise ValueError("category links must be one-to-one")
            seen_apple.add(apple_id)
            seen_stella.add(stella_id)
            rows.append({"apple_id": apple_id, "stella_id": stella_id})
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
            self.todos.get_category(row["stella_id"])
            if direction == "export" and not available[row["apple_id"]]["writable"]:
                raise ValueError("selected reminder list is read only")
        chosen = []
        failed = 0
        if direction == "import":
            inventory = self.bridge.call("reminders", list_ids=[row["apple_id"] for row in rows])
            successful = set(inventory["successful_list_ids"])
            failed = len([row for row in rows if row["apple_id"] not in successful])
            targets = {row["apple_id"]: row["stella_id"] for row in rows}
            for remote in inventory["reminders"]:
                if remote["list_id"] in targets and self._reminder_kept(
                        remote, start, end, include_undated, include_completed):
                    chosen.append((remote, targets[remote["list_id"]]))
        else:
            by_category = {row["stella_id"]: row["apple_id"] for row in rows}
            locals_only = [row for row in self.todos.list_todos() if row["source_type"] != "apple_reminder"]
            for stella_id, apple_id in by_category.items():
                members = [row for row in locals_only if row.get("category_id") == stella_id]
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
                    self._save_map(self._new_map(item, remote, "stella"))
                    counts["exported"] += 1
            return counts

    def start_auto(self, interval=300):
        if self._auto_started:
            return
        self._auto_started = True
        from apple_sync.runtime import EventKitAutoRunner
        self._runner = EventKitAutoRunner(self.bridge, lambda: self._get("connected", False),
                                          self.sync, kind="reminder", interval=interval)
        self._runner.start()

    def stop_auto(self):
        if self._auto_started:
            self._runner.stop()
        self._auto_started = False
