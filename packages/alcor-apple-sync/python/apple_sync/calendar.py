"""Persistent, conservative reconciliation between EventKit and Alcor schedules."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from apple_sync.metadata import snapshot_fields, record_baseline, write_fields, conflict_summaries
from apple_sync.contracts import bind_item,category_binding,process_operations,locate_remote,resolve_pending_deletion,native_delete_preview
from apple_sync.categories import synchronize_categories, synchronize_moves, reconcile_category_names, resolve_category_conflict, prime_bindings, verify_category_source
from apple_sync.category_links import bidirectional_links, reconcile_names, validate_links
from apple_sync.bridge import AppleCalendarBridge
from apple_sync.runtime import report_sync_error
from apple_sync.timezones import normalize_timezone


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="seconds")


def _content(row):
    return {key: (_instant(row[key]) if key in ("start_at", "end_at") else bool(row[key]) if key == "all_day" else row.get(key) or "")
            for key in ("title", "description", "start_at", "end_at", "all_day", "location")}


def _fingerprint(row, *, series=False, remote=False):
    data = _content(row)
    if series:
        if remote:
            rule = row.get("recurrence") or {}
            data["repeat_frequency"] = rule.get("frequency") or "none"
            data["repeat_days"] = sorted(rule.get("days") or [])
            data["repeat_until"] = (rule.get("until") or "")[:10]
            data["repeat_interval"] = rule.get("interval", 1)
            data["repeat_count"] = rule.get("count", 0)
            data["rule_count"] = rule.get("rule_count", 0 if data["repeat_frequency"] == "none" else 1)
            for key in ("month_days", "months", "week_numbers", "year_days", "set_positions"):
                data[key] = sorted(rule.get(key) or [])
        else:
            data["repeat_frequency"] = row.get("repeat_frequency") or "none"
            days = row.get("repeat_days") or []
            data["repeat_days"] = sorted(json.loads(days) if isinstance(days, str) else days)
            data["repeat_until"] = row.get("repeat_until") or ""
            data["repeat_interval"] = 1
            data["repeat_count"] = 0
            data["rule_count"] = 0 if data["repeat_frequency"] == "none" else 1
            for key in ("month_days", "months", "week_numbers", "year_days", "set_positions"):
                data[key] = []
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _source_key(remote):
    key = "\0".join((remote["calendar_id"], remote.get("external_id") or remote["event_id"],
                      remote.get("occurrence_date") or remote["start_at"], remote["event_id"]))
    return "eventkit:" + hashlib.sha256(key.encode()).hexdigest()


def _payload(remote, category_id=None):
    data = {**_content(remote),**snapshot_fields(remote)}
    if category_id is not None:
        data["category_id"] = category_id
    data["repeat_frequency"] = "none"
    # EventKit may emit fixed-offset labels (GMT+0800) that ZoneInfo rejects.
    data["repeat_timezone"] = normalize_timezone(remote.get("timezone"))
    return data


class AppleCalendarSync:
    DIRECTIONS = {"import", "export", "bidirectional"}

    def __init__(self, bridge=None, db_path=None, calendar_port=None, clock=None):
        self.bridge = bridge or AppleCalendarBridge()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.calendar = calendar_port
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
                    if 'stella_id' in cols or 'alcor_id' not in cols:
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
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS baseline_local (alcor_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS mappings (
                    id INTEGER PRIMARY KEY, alcor_id TEXT NOT NULL UNIQUE,
                    source_key TEXT NOT NULL UNIQUE, calendar_id TEXT NOT NULL,
                    event_id TEXT NOT NULL, external_id TEXT NOT NULL,
                    occurrence_date TEXT NOT NULL, origin TEXT NOT NULL,
                    local_hash TEXT NOT NULL, remote_hash TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active', conflict TEXT NOT NULL DEFAULT '',
                    missing_since TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS apple_map_calendar ON mappings(calendar_id);
                CREATE TABLE IF NOT EXISTS category_links (
                    apple_id TEXT PRIMARY KEY,
                    alcor_id TEXT NOT NULL UNIQUE,
                    direction TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    apple_name TEXT NOT NULL,
                    alcor_name TEXT NOT NULL,
                    name_conflict TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(mappings)")}
            if "missing_since" not in columns:
                db.execute("ALTER TABLE mappings ADD COLUMN missing_since TEXT NOT NULL DEFAULT ''")

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
            return [dict(row) for row in db.execute("SELECT * FROM mappings ORDER BY id")]

    def _save_map(self, mapping, *, connection=None):
        if connection is None:
            with self._connect() as db:
                return self._save_map(mapping, connection=db)
        db = connection
        if mapping.get("id"):
            db.execute("""UPDATE mappings SET alcor_id=:alcor_id,source_key=:source_key,
                calendar_id=:calendar_id,event_id=:event_id,external_id=:external_id,
                occurrence_date=:occurrence_date,origin=:origin,local_hash=:local_hash,
                remote_hash=:remote_hash,state=:state,conflict=:conflict,
                missing_since=:missing_since,updated_at=:updated_at
                WHERE id=:id""", mapping)
        else:
            db.execute("""INSERT INTO mappings
                (alcor_id,source_key,calendar_id,event_id,external_id,occurrence_date,
                 origin,local_hash,remote_hash,state,conflict,missing_since,updated_at)
                VALUES (:alcor_id,:source_key,:calendar_id,:event_id,:external_id,:occurrence_date,
                        :origin,:local_hash,:remote_hash,:state,:conflict,:missing_since,:updated_at)""", mapping)


    def _new_map(self, local, remote, origin, source_key=None):
        from apple_sync.category_rebuild import guard_retained
        guard_retained(self,local['id'])
        from apple_sync.marker_recovery import guard_pending
        guard_pending(self,local['id'])
        self.calendar.apple_apply_metadata(local["id"],snapshot_fields(remote))
        local = self.calendar.get_event(local["id"])
        record_baseline(self,local,remote)
        bind_item(self.calendar,local,remote,'calendar',['包含多条原生重复规则，请在 Apple 中编辑'] if (remote.get('recurrence') or {}).get('rule_count',0)>1 else [])
        return self._mapping_from_native(local,remote,origin,source_key)

    def _mapping_from_native(self, local, remote, origin, source_key=None):
        series = origin == "alcor"
        return {"alcor_id": local["id"], "source_key": source_key or _source_key(remote),
                "calendar_id": remote["calendar_id"], "event_id": remote.get("event_id") or "",
                "external_id": remote.get("external_id") or "",
                "occurrence_date": remote.get("occurrence_date") or remote["start_at"],
                "origin": origin, "local_hash": _fingerprint(local, series=series),
                "remote_hash": _fingerprint(remote, series=series, remote=True),
                "state": "active", "conflict": "", "missing_since": "", "updated_at": self.clock()}

    def _refresh_map(self, mapping, local, remote):
        mapping.update(self._new_map(local, remote, mapping["origin"], mapping["source_key"]))
        self._save_map(mapping)

    def status(self):
        from apple_sync.item_restore import pending as restore_pending
        from apple_sync.approved_delete import operation_status, completed_import_links
        from apple_sync.bound_conflict import pending as conflict_pending
        from apple_sync.category_rebuild import status as retained_status
        from apple_sync.marker_recovery import status as marker_status
        from apple_sync.category_create import status as create_status
        creates=create_status(self,'calendar')
        creates += [row for row in self._get('category_create_errors',[]) if row['local_id'] not in {entry['local_id'] for entry in creates}]
        from apple_sync.group_scan_claims import status as scan_status
        listing_error = ""
        native = self.bridge.call("status")
        selected = self._get("calendar_ids", [])
        calendars = []
        if native.get("authorization") == "full_access":
            try:
                inventory = self.bridge.call("calendars")
                calendars = inventory["calendars"]
                self._set('available_sources',inventory.get('sources',[]))
                self._set('default_source_id',inventory.get('default_source_id'))
            except Exception as exc:
                listing_error = report_sync_error("calendar", "读取日历列表", exc)
        maps = self._maps()
        restoring = restore_pending(self)
        resolving = conflict_pending(self)
        with self._connect() as db:
            identity_candidates=[json.loads(row[0]) for row in db.execute("SELECT value FROM settings WHERE key LIKE 'calendar-identity-candidate:%' ORDER BY key")]
        categories = self.calendar.categories()
        category_ids = {row['id'] for row in categories}
        category_conflicts = self._get('category_conflicts', [])
        finished_imports = completed_import_links(self)
        category_conflicts = category_conflicts + [{'apple_id': link['apple_id'], 'alcor_id': link['alcor_id'], 'kind': 'legacy_delete', 'reason': '旧版分类删除尚未核对；Apple 分类与关联已保留，需要重新确认具体来源与操作。'} for link in self._stored_links() if link['alcor_id'] not in category_ids and (link['apple_id'],link['alcor_id']) not in finished_imports and not any(row.get('apple_id') == link['apple_id'] and row.get('kind') == 'legacy_delete' for row in category_conflicts)]
        direct_helper = getattr(self.bridge, 'helper_path', None)
        direct_watch = direct_helper is not None and direct_helper.suffix != '.app'
        return {**native, "watch_degraded": bool(self._get('connected', False) and self._get('auto_enabled', True) and native.get('watch_control_version') != 1 and not direct_watch), "connected": bool(self._get("connected", False)),
                "auto_enabled": bool(self._get("auto_enabled", True)), "sync_all": bool(self._get("sync_all", False)), "calendar_ids": selected,
                "direction": self._get("direction", "bidirectional"), "calendars": calendars,
                "categories": categories,
                "range_state":self._get('last_range'),"coverage":self._get('coverage',[]),"last_success": self._get("last_success"), "error": listing_error or self._get("error", ""),
                "conflicts": conflict_summaries([row for row in maps if row['state']=='conflict' or row['alcor_id'] in restoring or row['alcor_id'] in resolving], self.calendar.get_event, 'alcor_id', restoring, resolving),
                "legacy_deletions": [dict(alcor_id=row['alcor_id'], reason='旧版删除尚未核对，Apple 日程及关联已保留。') for row in maps if row['state']=='pending_delete'],
                "pending_deletions": sum(row["state"] == "pending_delete" for row in maps),
                "pending_local_deletions": sum(row["state"] == "pending_local_delete" for row in maps),
                "category_conflicts":category_conflicts, "operations":operation_status(self,self.calendar.apple_operations()),
                "scan_claims":scan_status(self), "category_creates":creates, "retained_categories":retained_status(self), "marker_conflicts":marker_status(self), "identity_candidates":identity_candidates,
                "sources":self._get('available_sources',[]),"default_source_id":self._get('default_source_id'),
                "links": [self._public_link(row) for row in self._stored_links()]}


    def request_access(self):
        result = self.bridge.call("request_access")
        return {**result, **self.status()}

    def start_sync(self, selections=None, confirm_deletions=False, confirm_local_deletions=False):
        with self._lock:
            state = self.status()
            if state.get("authorization") != "full_access":
                raise PermissionError("需要系统完整访问授权")
            previous_links = state["links"]
            sync_all = selections is None
            deleted_categories = synchronize_categories(self, self.calendar, 'calendar') if sync_all else 0
            state = self.status()
            if sync_all and not state['calendars'] and deleted_categories:
                self._set('sync_all', True)
                self._set('sync_deletions', True)
                self._set('connected', True)
                self._set('auto_enabled', True)
                self.resume_auto()
                self._set('error', '')
                self._set('last_success', self.clock())
                return {'state': self.status(), 'result': {'status': 'success', 'deleted_calendars': deleted_categories}}
            if sync_all and not state['calendars']:
                raise ValueError('没有可同步的来源，请检查系统账户或授权后重试')
            links = state['links'] if sync_all else bidirectional_links(selections, state['calendars'], state['categories'], existing_links=previous_links)
            same_selection = {(row["apple_id"],row["alcor_id"]) for row in links if row["enabled"]} == {(row["apple_id"],row["alcor_id"]) for row in previous_links if row["enabled"]}
            state = self.replace_links(links)
            self._set("sync_all", sync_all)
            self._set("sync_deletions", self._get("sync_deletions", sync_all) if same_selection else sync_all)
            selected = {row["alcor_id"] for row in state["links"] if row["enabled"]}
            with self._connect() as db:
                db.executemany("DELETE FROM baseline_local WHERE alcor_id=?",
                               [(row["id"],) for row in self.calendar.list_events() if row.get("category_id") in selected])
            result = self.sync(manual=True, confirm_deletions=confirm_deletions,
                               confirm_local_deletions=confirm_local_deletions)
            self._set("auto_enabled", True)
        self.resume_auto()
        return {"state": self.status(), "result": result}

    def configure(self, calendars, direction):
        if direction not in self.DIRECTIONS:
            raise ValueError("invalid sync direction")
        if not isinstance(calendars, list) or not calendars or len(calendars) != len(set(calendars)):
            raise ValueError("select at least one distinct calendar")
        if self.bridge.call("status").get("authorization") != "full_access":
            raise PermissionError("Calendar full access is required")
        available = {row["id"]: row for row in self.bridge.call("calendars")["calendars"]}
        if any(key not in available for key in calendars):
            raise ValueError("selected calendar is unavailable")
        if direction in ("export", "bidirectional") and not any(available[key]["writable"] for key in calendars):
            raise ValueError("select a writable calendar for export")
        with self._lock:
            if not self._get("ever_connected", False):
                with self._connect() as db:
                    db.executemany("INSERT OR IGNORE INTO baseline_local VALUES (?)",
                                   [(row["id"],) for row in self.calendar.list_events() if row["source_type"] != "apple_calendar"])
                self._set("ever_connected", True)
            self._set("calendar_ids", calendars)
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
            """Replace ongoing category mappings. Unlisted mappings are removed; events stay."""
            if not isinstance(links, list):
                raise ValueError("links must be a list")
            from apple_sync.export_reservations import guard_link_changes
            held = guard_link_changes(self, links)
            local_only = set(self.calendar.apple_local_only_categories())
            local_names = {row['name'] for row in self.calendar.categories() if row['id'] in local_only}
            if any(isinstance(row, dict) and (row.get('alcor_id') in local_only or row.get('create_name') in local_names) for row in links):
                raise ValueError('恢复的本机分类不能自动关联 Apple 来源')
            if self.bridge.call("status").get("authorization") != "full_access":
                raise PermissionError("Calendar full access is required")
            available = {row["id"]: row for row in self.bridge.call("calendars")["calendars"]}
            previous = {row["apple_id"]: row for row in self._stored_links()}
            from apple_sync.category_links import changed_link_groups
            from apple_sync.group_scan_claims import WorkClaim
            groups = changed_link_groups(self,links,previous,available)
            if not groups:
                from apple_sync.category_links import restore_link_selection
                restore_link_selection(self,previous,'calendar_ids')
                return self.status()
            from apple_sync.category_connections import prepare,guard_discovery,connect
            prepared,specs=prepare(self.calendar,links,available,groups,previous,preserve_missing)
            validate_links(prepared)
            category_ids={row['alcor_id'] for row in prepared if row['apple_id'] in groups}
            category_ids.update(previous[key]['alcor_id'] for key in groups if key in previous)
            evidence = {'kind':'replace_links','previous':previous,'requested':prepared,'owners':specs,'native':available}
            with WorkClaim(self,groups,[],evidence,category_ids=category_ids,category_kind='calendar') as claim:
                with self._connect() as db:
                    db.execute('BEGIN IMMEDIATE');claim._check(db)
                    guard_discovery(db,'calendar',previous,available,groups)
                fresh={row['id']:row for row in self.bridge.call('calendars')['calendars']}
                if fresh!=available:raise ValueError('native category inventory changed before linking')
                rows = []
                for item in prepared:
                    calendar = available.get(item["apple_id"])
                    if calendar is None:
                        old = previous.get(item["apple_id"])
                        if not old or old["alcor_id"] != item["alcor_id"]:
                            raise ValueError("selected calendar is unavailable")
                        calendar = {"title": old["apple_name"]}
                    try:
                        category = connect(self.calendar,claim,specs[item['apple_id']],available[item['apple_id']]) if item['apple_id'] in specs else self.calendar.get_category(item["alcor_id"])
                    except KeyError:
                        old = previous.get(item['apple_id'])
                        if not preserve_missing or not old or any(item[key] != (bool(old[key]) if key == 'enabled' else old[key]) for key in ('alcor_id','direction','enabled')):
                            raise
                        rows.append({**old, 'enabled': 1 if old['enabled'] else 0})
                        continue
                    old = previous.get(item["apple_id"], {})
                    baseline = {key: old[key] for key in ("apple_name", "alcor_name", "name_conflict")} if old.get("alcor_id") == item["alcor_id"] else {}
                    rows.append({**item, "apple_name": calendar["title"], "alcor_name": category["name"],
                                 "name_conflict": "", **baseline, "enabled": 1 if item["enabled"] else 0})
                with self._lock:
                    now = self.clock()
                    claim.started('manager_committing')
                    with self._connect() as db:
                        db.execute('BEGIN IMMEDIATE');claim._check(db)
                        guard_discovery(db,'calendar',previous,available,groups)
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
                                           [(row['id'],) for row in self.calendar.list_events() if row['source_type']!='apple_calendar'])
                            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('ever_connected','true'))
                        directions={row['direction'] for row in enabled}
                        state={'calendar_ids':[row['apple_id'] for row in enabled],'connected':bool(enabled)}
                        if len(directions)==1:state['direction']=directions.pop()
                        db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',[(key,json.dumps(value)) for key,value in state.items()])
            return self.status()


    def _reconcile_category_names(self):
        return reconcile_category_names(self,self.calendar,'calendar')

    def resolve_category_conflict(self,apple_id,choice,replacement_id=None,source_id=None,field=None):
        with self._lock:
            from apple_sync.export_reservations import guard_group
            guard_group(self, apple_id)
            if replacement_id:
                guard_group(self, replacement_id)
            return resolve_category_conflict(self,self.calendar,'calendar',apple_id,choice,replacement_id,source_id,field)


    def prime_bindings(self):
        return prime_bindings(self,self.calendar,'calendar')

    def cancel_operation(self,operation_id):
        if self._get('operation-receipt:'+operation_id):
            raise ValueError('Apple 已确认此操作，正在完成本地核对，请继续同步')
        self.calendar.cancel_apple_operation(operation_id)
        return self.status()

    def prepare_delete_snapshot(self, entity, local_id, scope=None, occurrence_on=None):
        with self._lock:
            return native_delete_preview(self, self.calendar, 'calendar', entity, local_id, scope, occurrence_on)

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
                and self.bridge.call("status").get("authorization") == "full_access"):
            self.start_auto()

    def items(self, calendar_id, start=None, end=None):
        if not start or not end:
            raise ValueError("explicit calendar time window is required")
        try:
            first = datetime.fromisoformat(start.replace("Z", "+00:00"))
            last = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if first.tzinfo is None or last.tzinfo is None or first >= last:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise ValueError("invalid calendar time window") from exc
        if self.bridge.call("status").get("authorization") != "full_access":
            raise PermissionError("Calendar full access is required")
        if calendar_id not in {row["id"] for row in self.bridge.call("calendars")["calendars"]}:
            raise ValueError("selected calendar is unavailable")
        inventory = self.bridge.call("events", calendar_ids=[calendar_id], start=start, end=end)
        if calendar_id not in inventory["successful_calendar_ids"]:
            raise ValueError("selected calendar could not be scanned")
        return [row for row in inventory["events"] if row["calendar_id"] == calendar_id]

    def lookup_item(self, calendar_id, event_id):
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id is required")
        if self.bridge.call("status").get("authorization") != "full_access":
            raise PermissionError("Calendar full access is required")
        if calendar_id not in {row["id"] for row in self.bridge.call("calendars")["calendars"]}:
            raise ValueError("selected calendar is unavailable")
        remote = self.bridge.call("lookup", calendar_id=calendar_id, event_id=event_id).get("event")
        if remote is None or remote["calendar_id"] != calendar_id or remote["event_id"] != event_id:
            raise KeyError("Apple event_id not found or ambiguous")
        return remote

    def preview(self, calendars, direction):
        if direction not in self.DIRECTIONS:
            raise ValueError("invalid sync direction")
        if not isinstance(calendars, list) or not calendars or len(set(calendars)) != len(calendars):
            raise ValueError("select at least one distinct calendar")
        if self.bridge.call("status").get("authorization") != "full_access":
            raise PermissionError("Calendar full access is required")
        available = {row["id"]: row for row in self.bridge.call("calendars")["calendars"]}
        if any(key not in available for key in calendars):
            raise ValueError("selected calendar is unavailable")
        if direction in ("export", "bidirectional") and not any(available[key]["writable"] for key in calendars):
            raise ValueError("select a writable calendar for export")
        now = datetime.fromisoformat(self.clock().replace("Z", "+00:00"))
        window = ((now - timedelta(days=365)).isoformat(), (now + timedelta(days=730)).isoformat())
        inventory = self.bridge.call("events", calendar_ids=calendars, start=window[0], end=window[1])
        successful = set(inventory["successful_calendar_ids"]) & set(calendars)
        result = {"new": 0, "updated": 0, "skipped": 0, "conflicts": 0,
                  "failed_calendar_ids": sorted(set(calendars) - successful), "window": list(window)}
        maps = self._maps()
        for row in inventory["events"]:
            if row["calendar_id"] not in successful:
                continue
            mapping = self._match(row, maps)
            if mapping is None:
                result["new" if direction in ("import", "bidirectional") else "skipped"] += 1
                continue
            local = self._local(mapping["alcor_id"])
            if local is None or mapping["state"] == "conflict":
                result["conflicts"] += 1
                continue
            series = mapping["origin"] == "alcor"
            local_changed = _fingerprint(local, series=series) != mapping["local_hash"]
            remote_changed = _fingerprint(row, series=series, remote=True) != mapping["remote_hash"]
            if (local_changed and remote_changed or
                    local_changed and direction == "import" or remote_changed and direction == "export"):
                result["conflicts"] += 1
            elif local_changed or remote_changed:
                result["updated"] += 1
            else:
                result["skipped"] += 1
        if direction in ("export", "bidirectional") and self._get("ever_connected", False):
            with self._connect() as db:
                from apple_sync.category_rebuild import retained_ids
                from apple_sync.marker_recovery import pending_ids
                baseline = {row[0] for row in db.execute("SELECT alcor_id FROM baseline_local")} | retained_ids(self) | pending_ids(self)
            mapped = {row["alcor_id"] for row in maps}
            result["new"] += sum(row["source_type"] not in ("apple_calendar", "apple_recovered") and row["id"] not in mapped and
                                 row["id"] not in baseline for row in self.calendar.list_events())
        return result

    def _one_time_items(self, direction, calendar_id, item_ids):
        if direction not in ("import", "export"):
            raise ValueError("one-time direction must be import or export")
        if not isinstance(item_ids, list) or not item_ids or any(
                not isinstance(key, str) or not key.strip() for key in item_ids) or len(set(item_ids)) != len(item_ids):
            raise ValueError("explicit distinct event_id or Alcor event IDs are required")
        if self.bridge.call("status").get("authorization") != "full_access":
            raise PermissionError("Calendar full access is required")
        available = {row["id"]: row for row in self.bridge.call("calendars")["calendars"]}
        if calendar_id not in available:
            raise ValueError("selected calendar is unavailable")
        if direction == "export" and not available[calendar_id]["writable"]:
            raise ValueError("selected calendar is read only")
        if direction == "export":
            items = [self._local(key) for key in item_ids]
            if any(row is None for row in items):
                raise KeyError("Alcor event not found")
            maps = {row["alcor_id"]: row for row in self._maps()}
            if any((row["source_type"] == "apple_calendar" and row["id"] not in maps) or
                   (row["id"] in maps and maps[row["id"]]["calendar_id"] != calendar_id)
                   for row in items):
                raise ValueError("event cannot be exported to the selected calendar")
            return items
        return [self.lookup_item(calendar_id, key) for key in item_ids]

    def preview_items(self, direction, calendar_id, item_ids):
        with self._lock:
            items = self._one_time_items(direction, calendar_id, item_ids)
            maps = self._maps()
            result = {"new": 0, "updated": 0, "skipped": 0, "conflicts": 0, "item_ids": item_ids}
            for item in items:
                if direction == "import":
                    mapping = self._match(item, maps)
                    if mapping is None:
                        result["new"] += 1
                    else:
                        local = self._local(mapping["alcor_id"])
                        if local is None or mapping["state"] == "conflict" or _fingerprint(
                                local, series=mapping["origin"] == "alcor") != mapping["local_hash"]:
                            result["conflicts"] += 1
                        elif _fingerprint(item, series=mapping["origin"] == "alcor",
                                          remote=True) != mapping["remote_hash"]:
                            result["updated"] += 1
                        else:
                            result["skipped"] += 1
                else:
                    mapping = next((row for row in maps if row["alcor_id"] == item["id"]), None)
                    if mapping is None:
                        result["new"] += 1
                    elif mapping["state"] == "conflict":
                        result["conflicts"] += 1
                    elif _fingerprint(item, series=mapping["origin"] == "alcor") != mapping["local_hash"]:
                        result["updated"] += 1
                    else:
                        result["skipped"] += 1
            return result

    def one_time(self, direction, calendar_id, item_ids, *, confirm=False):
        if direction == 'export':
            raise PermissionError('导出仅可通过真实会话的具体单次审批执行')
        from apple_sync.approved_export import guard_unresolved
        guard_unresolved(self)
        if confirm is not True:
            raise ValueError("explicit confirmation is required")
        with self._lock:
            items = self._one_time_items(direction, calendar_id, item_ids)
            counts = {"status": "success", "imported": 0, "updated": 0,
                      "exported": 0, "conflicts": 0, "item_ids": item_ids}
            for item in items:
                remote = item if direction == "import" else None
                local = item if direction == "export" else None
                mapping = next((row for row in self._maps() if row["alcor_id"] == local["id"]), None) if local else None
                if mapping and direction == "export":
                    remote = self.bridge.call("lookup", calendar_id=calendar_id,
                        event_id=mapping["event_id"], external_id=mapping["external_id"],
                        occurrence_date=mapping["occurrence_date"]).get("event")
                    if remote is None:
                        raise ValueError("mapped Apple event is unavailable")
                anchor = remote["start_at"] if remote else local["start_at"]
                center = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
                window = ((center - timedelta(days=2)).isoformat(),
                          (center + timedelta(days=2)).isoformat())
                if direction == "import" or mapping:
                    self._process_remote(remote, self._maps(), direction, window, counts)
                else:
                    remote = self._ordinary_create(local, calendar_id, window)
                    counts["exported"] += 1
            return counts


    def _batch_bounds(self, start, end):
        try:
            start_day = date.fromisoformat(start)
            end_day = date.fromisoformat(end)
        except (TypeError, ValueError) as exc:
            raise ValueError("time range must be YYYY-MM-DD") from exc
        zone = ZoneInfo("Asia/Shanghai")
        start_at = datetime.combine(start_day, clock_time.min, zone)
        end_at = datetime.combine(end_day + timedelta(days=1), clock_time.min, zone)
        if end_at <= start_at:
            raise ValueError("time range must include at least one day")
        return start_at, end_at

    def _batch_selections(self, selections):
        if not isinstance(selections, list) or not selections:
            raise ValueError("select at least one category")
        rows = []
        seen_apple, seen_alcor = set(), set()
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

    def _batch_events(self, direction, selections, start, end):
        if direction not in ("import", "export"):
            raise ValueError("one-time direction must be import or export")
        rows = self._batch_selections(selections)
        bounds = self._batch_bounds(start, end)
        if self.bridge.call("status").get("authorization") != "full_access":
            raise PermissionError("Calendar full access is required")
        available = {row["id"]: row for row in self.bridge.call("calendars")["calendars"]}
        for row in rows:
            if row["apple_id"] not in available:
                raise ValueError("selected calendar is unavailable")
            self.calendar.get_category(row["alcor_id"])
            if direction == "export" and not available[row["apple_id"]]["writable"]:
                raise ValueError("selected calendar is read only")
        failed = 0
        chosen = []
        if direction == "import":
            inventory = self.bridge.call("events", calendar_ids=[row["apple_id"] for row in rows],
                                         start=bounds[0].isoformat(), end=bounds[1].isoformat())
            successful = set(inventory["successful_calendar_ids"])
            failed = len([row for row in rows if row["apple_id"] not in successful])
            targets = {row["apple_id"]: row["alcor_id"] for row in rows}
            for remote in inventory["events"]:
                if remote["calendar_id"] not in targets:
                    continue
                moment = datetime.fromisoformat(remote["start_at"].replace("Z", "+00:00"))
                if not (bounds[0] <= moment < bounds[1]):
                    continue
                chosen.append((remote, targets[remote["calendar_id"]]))
        else:
            by_category = {row["alcor_id"]: row["apple_id"] for row in rows}
            for local in self.calendar.list_events():
                apple_id = by_category.get(local.get("category_id"))
                if apple_id is None or local["source_type"] in ("apple_calendar", "apple_recovered"):
                    continue
                moment = datetime.fromisoformat(local["start_at"].replace("Z", "+00:00"))
                if bounds[0] <= moment < bounds[1]:
                    chosen.append((local, apple_id))
        return chosen, failed, bounds

    def preview_batch(self, direction, selections, start, end):
        """Count a category-and-range transfer without connecting ongoing sync."""
        with self._lock:
            chosen, failed, _bounds = self._batch_events(direction, selections, start, end)
            maps = self._maps()
            new = skipped = 0
            identities = []
            for item, target in chosen:
                if direction == "import":
                    mapped = self._match(item, maps) is not None
                    identities.append(("import", item["calendar_id"], item.get("event_id"), target))
                else:
                    mapped = any(row["alcor_id"] == item["id"] for row in maps)
                    identities.append(("export", item["id"], target))
                if mapped:
                    skipped += 1
                else:
                    new += 1
            preview = {"new": new, "skipped": skipped, "failed": failed}
            token = secrets.token_urlsafe(24)
            self._batch_preview = (token, direction, json.dumps(self._batch_selections(selections), sort_keys=True),
                                   start, end, tuple(identities), preview, time.monotonic() + 600)
            return {"preview": preview, "preview_token": token}

    def transfer_batch(self, direction, selections, start, end, token, *, confirm=False):
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
                    direction != pending[1] or selection_key != pending[2] or start != pending[3] or
                    end != pending[4] or time.monotonic() > pending[7]):
                raise ValueError("matching Apple category preview and confirmation are required")
            chosen, failed, bounds = self._batch_events(direction, selections, start, end)
            identities = []
            for item, target in chosen:
                if direction == "import":
                    identities.append(("import", item["calendar_id"], item.get("event_id"), target))
                else:
                    identities.append(("export", item["id"], target))
            if tuple(identities) != pending[5] or failed != pending[6]["failed"]:
                self._batch_preview = None
                raise ValueError("Apple sync inventory changed; preview again")
            self._batch_preview = None
            counts = {"status": "success", "imported": 0, "exported": 0, "skipped": 0, "failed": failed}
            for item, target in chosen:
                if direction == "import":
                    if self._match(item, self._maps()):
                        counts["skipped"] += 1
                        continue
                    anchor = datetime.fromisoformat(item["start_at"].replace("Z", "+00:00"))
                    window = ((anchor - timedelta(days=2)).isoformat(), (anchor + timedelta(days=2)).isoformat())
                    before = counts["imported"]
                    self._process_remote(item, self._maps(), "import", window, counts, category_id=target)
                    if counts["imported"] == before:
                        counts["skipped"] += 1
                else:
                    mapping = next((row for row in self._maps() if row["alcor_id"] == item["id"]), None)
                    if mapping:
                        counts["skipped"] += 1
                        continue
                    window = ((bounds[0] - timedelta(days=2)).isoformat(), (bounds[1] + timedelta(days=2)).isoformat())
                    remote = self._ordinary_create(item, target, window)
                    counts["exported"] += 1
            return counts


    def is_connected(self):
        return bool(self._get("connected", False))

    @staticmethod
    def _match(remote, maps, ambiguous=False):
        for row in maps:
            if row["calendar_id"] == remote["calendar_id"] and row["event_id"] == remote.get("event_id") and row["occurrence_date"] == remote.get("occurrence_date"):
                return row
        if (remote.get('recurrence') or {}).get('frequency','none')=='none':
            direct = [row for row in maps if row['calendar_id']==remote['calendar_id'] and row['event_id']==remote.get('event_id')]
            if len(direct)==1:
                return direct[0]
        matches = [row for row in maps if not ambiguous and row["calendar_id"] == remote["calendar_id"] and
                   row["external_id"] and row["external_id"] == remote.get("external_id") and
                   row["occurrence_date"] == remote.get("occurrence_date")]
        return matches[0] if len(matches) == 1 else None

    def _local(self, alcor_id):
        try:
            return self.calendar.get_event(alcor_id)
        except KeyError:
            return None

    def _conflict(self, mapping, reason):
        mapping["state"] = "conflict"
        mapping["conflict"] = reason
        mapping["updated_at"] = self.clock()
        self._save_map(mapping)

    def _write_payload(self, local, mapping=None, calendar_id=None, window=None, *, include_receipt=True):
        from apple_sync.category_rebuild import guard_retained
        guard_retained(self,local['id'])
        from apple_sync.marker_recovery import guard_pending
        guard_pending(self,local['id'])
        if include_receipt:
            from apple_sync.export_reservations import guard_write
            guard_write(self, local['id'])
        data = {**_content(local),**write_fields(self,local,mapping,include_receipt=include_receipt)}
        data.update({"calendar_id": calendar_id or mapping["calendar_id"],
                     "alcor_id": local["id"] if local["source_type"] != "apple_calendar" else "",
                     "repeat_frequency": local["repeat_frequency"],
                     "repeat_days": json.loads(local["repeat_days"]),
                     "repeat_until": local["repeat_until"],
                     "repeat_timezone": local["repeat_timezone"],
                     "series": bool(local["source_type"] != "apple_calendar" and
                                    (local["repeat_frequency"] != "none" or mapping is not None))})
        if mapping:
            data.update(event_id=mapping["event_id"], external_id=mapping["external_id"],
                        occurrence_date=mapping["occurrence_date"])
        else:
            data["occurrence_date"] = local["start_at"]
        if mapping and local.get('edit_scope'):
            data['series'] = local['edit_scope']=='future'
        if window and mapping is None:
            start = datetime.fromisoformat(local["start_at"])
            if not (datetime.fromisoformat(window[0]) <= start < datetime.fromisoformat(window[1])):
                window = ((start - timedelta(days=2)).isoformat(), (start + timedelta(days=2)).isoformat())
        if window:
            data.update(scan_start=window[0], scan_end=window[1])
        return data


    def _place_in_category(self, local, category_id, direction):
        if local and category_id and direction in ("import", "bidirectional") and local.get("category_id") != category_id:
            return self.calendar.apple_place_item(local["id"], category_id)
        return local

    def _process_remote(self, remote, maps, direction, window, counts, ambiguous=False, moved_candidate=None,
                        category_id=None, expected_source_id=None, scan_token=None):
        from apple_sync.export_reservations import guard_group
        guard_group(self, remote['calendar_id'])
        from apple_sync.marker_recovery import pending_native
        if pending_native(self,remote):
            counts['conflicts']+=1;counts['status']='partial';return
        mapping = self._match(remote, maps, ambiguous) or moved_candidate
        from apple_sync.category_rebuild import retained_ids
        retained=retained_ids(self)
        if remote.get('alcor_id') in retained or mapping and mapping['alcor_id'] in retained:
            counts['conflicts']+=1;return
        if moved_candidate is not None:
            mapping["calendar_id"] = remote["calendar_id"]
            mapping["event_id"] = remote["event_id"]
        if mapping is None:
            candidates = [row for row in maps if row['origin']=='alcor' and row['calendar_id']==remote['calendar_id'] and
                          ((remote.get('alcor_id') and row['alcor_id']==remote['alcor_id']) or
                           (remote.get('external_id') and row['external_id']==remote['external_id']))]
            existing = candidates[0] if len(candidates)==1 else None
            if existing:
                series = self._local(existing['alcor_id'])
                day = datetime.fromisoformat(remote['start_at'].replace('Z','+00:00')).astimezone(ZoneInfo(series['repeat_timezone'] or 'UTC')).date().isoformat() if series else ''
                if not series or series['repeat_frequency']=='none' or series.get('repeat_until') and day>series['repeat_until']:
                    existing = None
            if existing:
                # EventKit expands a Alcor-owned series into multiple instances.
                # The Alcor series already renders those instances itself.
                if remote.get("is_detached"):
                    series = self._local(existing["alcor_id"])
                    if direction != "export" and series and series["repeat_frequency"] != "none":
                        day = datetime.fromisoformat(remote["occurrence_date"].replace("Z", "+00:00"))
                        day = day.astimezone(ZoneInfo(series["repeat_timezone"] or "UTC")).date().isoformat()
                        self.calendar.apple_exclude(series["id"], day)
                        key = _source_key(remote)
                        local = self._place_in_category(
                            self.calendar.upsert_external_event("apple_calendar", key, _payload(remote, category_id)),
                            category_id, direction)
                        exception_map = self._new_map(local, remote, "apple", key)
                        self._save_map(exception_map)
                        maps.append(exception_map)
                        counts["imported"] += 1
                    else:
                        self._conflict(existing, "Apple changed one occurrence of a Alcor series")
                        counts["conflicts"] += 1
                return
            local = self._local(remote['alcor_id']) if remote.get('alcor_id') else None
            if local and local["source_type"] != "apple_calendar":
                # The native write may have succeeded immediately before an app crash.
                from apple_sync.marker_recovery import recover
                mapping = recover(self,self.calendar,'calendar',local,remote,scan_token=scan_token,scan_safe=scan_token is not None)
                if mapping is None:
                    counts['conflicts']+=1;counts['status']='partial';return
                maps.append(mapping)
                return
        if mapping is None:
            if direction == "export":
                return
            key = _source_key(remote)
            local = self._place_in_category(
                self.calendar.upsert_external_event("apple_calendar", key, _payload(remote, category_id)), category_id, direction)
            mapping = self._new_map(local, remote, "apple", key)
            self._save_map(mapping)
            maps.append(mapping)
            counts["imported"] += 1
            return
        if mapping["state"] in ("remote_deleted", "locally_deleted", "deleted"):
            return
        capture = self.calendar.apple_capture_calendar_sync(mapping["alcor_id"], category_id if direction in ('import','bidirectional') else None)
        local = capture['local'] if capture else None
        if local is None:
            if _fingerprint(remote, series=mapping["origin"] == "alcor", remote=True) != mapping["remote_hash"]:
                self._conflict(mapping, "Apple changed a record deleted in Alcor")
                counts["conflicts"] += 1
                return
            mapping["state"] = "pending_delete" if direction in ("export", "bidirectional") else "locally_deleted"
            self._save_map(mapping)
            return
        guard = self._get('calendar-binding-generation:'+local['id'])
        if guard and (guard.get('generation') != capture['generation'] or guard.get('group_id') != remote.get('calendar_id') or guard.get('source_id') != remote.get('source_id')):
            before=dict(mapping);previous_baseline=self._get('item-metadata:'+local['id'])
            self._record_inbound_conflict(mapping,before,previous_baseline,capture,remote)
            counts['conflicts'] += 1;counts['status'] = 'partial'
            return
        series = mapping["origin"] == "alcor"
        local_hash = _fingerprint(local, series=series)
        remote_hash = _fingerprint(remote, series=series, remote=True)
        from apple_sync.metadata import metadata
        before = dict(mapping)
        previous_baseline = self._get('item-metadata:'+local['id'])
        if previous_baseline:
            local_extra = previous_baseline[0] != metadata(local)
            remote_extra = previous_baseline[1] != metadata(remote,True)
        else:
            projected = {**local, **{key:value for key,value in snapshot_fields(remote).items() if key not in local}}
            mismatch = metadata(projected)['timezone'] != metadata(remote,True)['timezone'] or bool(projected.get('floating')) != bool(remote.get('floating'))
            local_extra = remote_extra = mismatch
        local_changed = local_hash != mapping["local_hash"] or local_extra
        remote_changed = remote_hash != mapping["remote_hash"] or remote_extra
        if mapping["state"] == "conflict":
            return
        if local_changed and remote_changed:
            self._conflict(mapping, "Both Alcor and Apple changed this event")
            counts["conflicts"] += 1
        elif remote_changed:
            if direction == "export":
                self._conflict(mapping, "Apple changed an export-only event")
                counts["conflicts"] += 1
            else:
                fields = _payload(remote,category_id) if mapping['origin']=='apple' else self._series_payload(local,remote,category_id)
                try:
                    applied = self.calendar.apple_apply_calendar_inbound(capture,fields,remote)
                except ValueError as error:
                    if not str(error).startswith('INBOUND_CHANGED:'): raise
                    self._record_inbound_conflict(mapping,before,previous_baseline,capture,remote)
                    counts['conflicts'] += 1
                    counts['status'] = 'partial'
                    return
                self._commit_inbound_snapshot(mapping,before,previous_baseline,applied,remote,capture['generation'])
                counts['updated'] += 1
        elif local_changed:
            if direction == "import":
                self._conflict(mapping, "Alcor changed an import-only event")
                counts["conflicts"] += 1
            elif not remote.get("writable", False):
                self._conflict(mapping, "Apple calendar is read only")
                counts["conflicts"] += 1
            else:
                changed = self.bridge.call("upsert", **self._write_payload(local, mapping, window=window))["event"]
                if expected_source_id is not None and (changed.get('source_id') != expected_source_id or changed.get('calendar_id') != remote['calendar_id']):
                    raise ValueError('native write returned a different source or container')
                self._refresh_map(mapping, local, changed)
                counts["exported"] += 1
        else:
            placement = {'category_id':category_id} if category_id and direction in ('import','bidirectional') else {}
            try:
                applied = self.calendar.apple_apply_calendar_inbound(capture,placement,remote,True)
            except ValueError as error:
                if not str(error).startswith('INBOUND_CHANGED:'): raise
                self._record_inbound_conflict(mapping,before,previous_baseline,capture,remote)
                counts['conflicts'] += 1
                counts['status'] = 'partial'
                return
            self._commit_inbound_snapshot(mapping,before,previous_baseline,applied,remote,capture['generation'])


    def sync(self, manual=False, confirm_deletions=False, confirm_local_deletions=False, scan_start=None, scan_end=None):
        if scan_start is not None or scan_end is not None:
            self._range(scan_start,scan_end)
        with self._lock:
            failures = []
            counts = {"status": "success", "imported": 0, "updated": 0, "exported": 0,
                      "deleted_local": 0, "deleted_remote": 0, "conflicts": 0,
                      "pending_deletions": 0, "pending_local_deletions": 0,
                      "failed_calendar_ids": []}
            if not self._get("connected", False):
                counts["status"] = "disconnected"
                return counts
            native = self.bridge.call("status")
            if native.get("authorization") != "full_access":
                counts["status"] = native.get("authorization", "unavailable")
                return counts
            automatic_deletions = self._get("sync_deletions", False)
            confirm_deletions = confirm_deletions or automatic_deletions
            confirm_local_deletions = confirm_local_deletions or automatic_deletions
            if self._get("sync_all", False):
                try:
                    counts['deleted_categories'] = synchronize_categories(self, self.calendar, 'calendar')
                except Exception as exc:
                    counts['status'] = 'error'
                    self._set('error', report_sync_error('calendar', '同步分类', exc))
                    return counts
            if not self._get('sync_all',False):
                _,errors = process_operations(self,self.calendar,'calendar')
                self._set('operation_errors',errors)
            for key,value in self._get('operation_counts',{}).items():
                counts[key] += value
            try:
                counts['exported'] += synchronize_moves(self, self.calendar, 'calendar', lambda row, mapping, remote: _fingerprint(row, series=mapping['origin'] == 'alcor', remote=remote))
            except Exception as exc:
                counts['status'] = 'error'
                self._set('error', report_sync_error('calendar', '同步分类移动', exc))
                return counts
            stored_links = self._stored_links()
            using_links = bool(stored_links)
            name_errors = self._reconcile_category_names() if using_links else []
            existing_categories = {row['id'] for row in self.calendar.categories()}
            enabled_links = [row for row in self._stored_links() if row["enabled"] and row['alcor_id'] in existing_categories] if using_links else []
            if any(row['enabled'] and row['alcor_id'] not in existing_categories for row in stored_links):
                counts['status'] = 'partial'
            direction = self._get("direction", "bidirectional")
            selected = [row["apple_id"] for row in enabled_links] if using_links else self._get("calendar_ids", [])
            now = datetime.fromisoformat(self.clock().replace("Z", "+00:00"))
            try:
                horizon = now.replace(year=now.year + 2)
            except ValueError:
                horizon = now.replace(year=now.year + 2, day=28)
            window = ((now-timedelta(days=365)).isoformat(),horizon.isoformat())
            if scan_start is not None or scan_end is not None:
                window = self._range(scan_start,scan_end)
            from apple_sync import group_scan_claims as scan_claims
            try:
                destinations = self.bridge.call('calendars')
            except Exception as exc:
                counts['status'] = 'error'
                self._set('error', report_sync_error('calendar', '读取同步来源', exc))
                return counts
            completed, held = [], []
            blocked_move_groups = {group for error in self._get('calendar_move_errors', []) for group in error['group_ids']}
            for group_id in dict.fromkeys(selected):
                if group_id in blocked_move_groups:
                    held.append(group_id)
                    counts['status'] = 'partial'
                    continue
                token = None
                item_phase_started = False
                try:
                    link=next((row for row in enabled_links if row['apple_id']==group_id),None)
                    if link:
                        target=next((row for row in destinations.get('calendars',[]) if row.get('id')==group_id),{})
                        if target:verify_category_source(self.calendar.get_category(link['alcor_id']),link,target)
                    target = scan_claims.destination(destinations, group_id)
                    token = scan_claims.acquire(self, target['source_id'], group_id, window)
                    fresh = scan_claims.destination(self.bridge.call('calendars'), group_id)
                    if fresh != target:
                        raise scan_claims.ScanUnavailable('calendar destination changed after claim')
                    if link:verify_category_source(self.calendar.get_category(link['alcor_id']),link,fresh)
                    item_phase_started = True
                    result, safe = self._sync_claimed_calendar(
                        group_id, target['source_id'], token, direction, window, using_links, enabled_links)
                    for key in ('imported','updated','exported','deleted_local','deleted_remote','conflicts','pending_deletions'):
                        counts[key] += result[key]
                    counts['failed_calendar_ids'].extend(result['failed_calendar_ids'])
                    if result['blocked_local_ids']:
                        counts.setdefault('blocked_local_ids', []).extend(result['blocked_local_ids'])
                    if result['status'] != 'success':
                        counts['status'] = 'error' if result['status'] == 'error' or counts['status'] == 'error' else 'partial'
                    if safe:
                        scan_claims.complete(self, token)
                        completed.append(group_id)
                    else:
                        scan_claims.uncertain(self, token)
                        held.append(group_id)
                except scan_claims.ScanUnavailable as exc:
                    counts['failed_calendar_ids'].append(group_id)
                    failures.append(str(exc))
                    if token and (item_phase_started or not scan_claims.cancel_unstarted_scan(self,token,target['source_id'],group_id,list(window),exc)):
                        scan_claims.uncertain(self, token)
                        held.append(group_id)
                    if counts['status'] != 'error':
                        counts['status'] = 'partial'
                except scan_claims.ScanHeld:
                    held.append(group_id)
                    if token:
                        scan_claims.uncertain(self, token)
                    if counts['status'] != 'error':
                        counts['status'] = 'partial'
                except Exception as exc:
                    if token:
                        scan_claims.uncertain(self, token)
                        held.append(group_id)
                    failures.append(report_sync_error('calendar', '同步日历', exc))
                    counts['status'] = 'error'
            counts['pending_local_deletions'] = sum(row['state'] == 'pending_local_delete' for row in self._maps())
            if held:
                counts['export_held_groups'] = sorted(set(held))
            counts['scan_claims'] = scan_claims.status(self)
            if name_errors and counts['status'] == 'success':
                counts['status'] = 'error'
            if counts['status'] == 'success':
                coverage = self._get('coverage', [])
                coverage.append({'start':window[0], 'end':window[1], 'checked_at':self.clock(), 'calendar_ids':sorted(completed)})
                self._set('coverage', coverage[-100:])
                self._set('last_success', self.clock())
                self._set('error', '')
            else:
                self._set('error', failures[0] if failures else name_errors[0] if name_errors else
                          '部分日历正在同步或结果待核对；其他日历继续同步，请先核对 Apple，不要重复发送。')
            return counts


    @staticmethod
    def _range(start,end):
        try:
            lower = datetime.fromisoformat(start.replace('Z','+00:00'))
            upper = datetime.fromisoformat(end.replace('Z','+00:00'))
            if lower.tzinfo is None or upper.tzinfo is None or upper<=lower or upper-lower>timedelta(days=370):
                raise ValueError('invalid range')
            return lower.astimezone(timezone.utc).isoformat(),upper.astimezone(timezone.utc).isoformat()
        except (ValueError,TypeError,AttributeError) as error:
            raise ValueError('日历加载范围须包含时区且不超过 370 天') from error

    def sync_range(self,start,end):
        lower,upper = self._range(start,end)
        with self._lock:
            if not self._get('connected',False):
                result = {'status':'cached','reason':'同步已暂停，仅显示本地已保存日程'}
                self._set('last_range',result)
                return result
            selected = sorted(row['apple_id'] for row in self._stored_links() if row['enabled']) or sorted(self._get('calendar_ids',[]))
            now = datetime.fromisoformat(self.clock().replace('Z','+00:00'))
            from apple_sync.export_reservations import held_groups
            from apple_sync.group_scan_claims import status as scan_status
            paused = held_groups(self) | {row['group_id'] for row in scan_status(self)}
            for row in reversed(self._get('coverage',[]) if not (paused & set(selected)) else []):
                if row['start']<=lower and row['end']>=upper and row.get('calendar_ids')==selected and now-datetime.fromisoformat(row['checked_at'].replace('Z','+00:00'))<timedelta(minutes=5):
                    result = {'status':'success','start':lower,'end':upper,'checked_at':row['checked_at']}
                    self._set('last_range',result)
                    return result
            try:
                result = self.sync(scan_start=lower,scan_end=upper)
            except Exception as error:
                result = {'status':'error','reason':str(error)}
            result = {**result,'start':lower,'end':upper,'reason':result.get('reason') or self._get('error','')}
            self._set('last_range',result)
            return result


    @staticmethod
    def _series_payload(local, remote, category_id=None):
        fields = _payload(remote,category_id)
        rule = remote.get('recurrence') or {}
        simple = (rule.get('frequency','none') in ('none','daily','weekly','monthly','yearly') and
                  rule.get('interval',1)==1 and rule.get('rule_count',1)<=1 and not rule.get('count') and
                  not any(rule.get(key) for key in ('month_days','months','week_numbers','year_days','set_positions')) and
                  not any(day.get('week') for day in rule.get('weekday_ordinals',[])))
        if local.get('repeat_frequency')!='none' and simple:
            fields.update(repeat_frequency=rule.get('frequency','none'), repeat_days=rule.get('days',[]),
                          repeat_until=(rule.get('until') or '')[:10] or None)
        return fields

    def resolve_conflict(self, alcor_id, choice):
        with self._lock:
            from apple_sync.item_conflicts import resolve
            return resolve(self,self.calendar,'calendar',alcor_id,choice)


    def start_auto(self, interval=300):
        """Retry periodically and reconcile promptly after EventKit notifications."""
        if self._auto_started:
            return
        self._auto_started = True
        from apple_sync.runtime import EventKitAutoRunner
        runner = EventKitAutoRunner(self.bridge, lambda: self._get("connected", False),
                                    lambda: self._run_auto_sync(runner.stop_event), interval=interval)
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


    def _commit_inbound_snapshot(self, mapping, before, previous_baseline, local, remote, generation, *, claim=None, connection=None):
        """Manager CAS after owner commit; retain recovery evidence on any mismatch."""
        from apple_sync.metadata import metadata
        series = before['origin'] == 'alcor'
        updated = {**before, 'calendar_id':remote['calendar_id'], 'event_id':remote.get('event_id') or before['event_id'],
                   'external_id':remote.get('external_id') or '', 'occurrence_date':remote.get('occurrence_date') or remote['start_at'],
                   'local_hash':_fingerprint(local,series=series), 'remote_hash':_fingerprint(remote,series=series,remote=True),
                   'state':'active', 'conflict':'', 'missing_since':'', 'updated_at':self.clock()}
        baseline=[metadata(local),metadata(remote,True)]
        with (nullcontext(connection) if connection is not None else self._connect()) as db:
            if connection is None:db.execute('BEGIN IMMEDIATE')
            if claim is not None:claim._check(db)
            row=db.execute('SELECT * FROM mappings WHERE id=?',(before['id'],)).fetchone()
            saved=db.execute('SELECT value FROM settings WHERE key=?',('item-metadata:'+before['alcor_id'],)).fetchone()
            current_baseline=json.loads(saved[0]) if saved else None
            if not row or dict(row)!=before or current_baseline!=previous_baseline:
                raise ValueError('calendar mapping changed during inbound sync; reconciliation required')
            guard_key='calendar-binding-generation:'+before['alcor_id']
            old_guard=db.execute('SELECT value FROM settings WHERE key=?',(guard_key,)).fetchone()
            guard={'generation':generation,'source_id':remote['source_id'],'group_id':remote['calendar_id']}
            if old_guard and json.loads(old_guard[0])!=guard:
                raise ValueError('calendar binding incarnation changed during inbound sync')
            self._save_map(updated,connection=db)
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('item-metadata:'+before['alcor_id'],json.dumps(baseline)))
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(guard_key,json.dumps(guard,sort_keys=True)))
        mapping.clear();mapping.update(updated)


    def _record_inbound_conflict(self, mapping, before, previous_baseline, expected, remote, *, reason=None, connection=None):
        """Retain both observations and current local data without applying either."""
        identity=before['alcor_id'];current_local=self._local(identity)
        record={'version':1,'reason':reason or '本地日程在同步期间变化，请选择保留内容',
                'observed':expected,'current_local':current_local,'remote':remote}
        with (nullcontext(connection) if connection is not None else self._connect()) as db:
            if connection is None: db.execute('BEGIN IMMEDIATE')
            if remote.get('absence_evidence') == 'bounded_lookup_null':
                from apple_sync.group_scan_claims import schema as scan_schema
                from apple_sync.export_reservations import schema as export_schema
                tables = ['calendar_scan_groups'] if scan_schema(db) else []
                if export_schema(db):
                    tables += ['trusted_export_reservations', 'ordinary_export_claims']
                if any(db.execute(f'SELECT 1 FROM {table} WHERE group_id!=? LIMIT 1',
                                  (remote['calendar_id'],)).fetchone() for table in tables):
                    return False
            row=db.execute('SELECT * FROM mappings WHERE id=?',(before['id'],)).fetchone()
            saved=db.execute('SELECT value FROM settings WHERE key=?',('item-metadata:'+identity,)).fetchone()
            if not row or dict(row)!=before or (json.loads(saved[0]) if saved else None)!=previous_baseline:
                raise ValueError('calendar conflict state changed; reconciliation required')
            updated={**before,'state':'conflict','conflict':record['reason'],'updated_at':self.clock()}
            self._save_map(updated,connection=db)
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('calendar-inbound-conflict:'+identity,json.dumps(record,ensure_ascii=False)))
        mapping.clear();mapping.update(updated)
        return True


    def _ordinary_create(self, local, calendar_id, window, *, scan_token=None, scan_source=None):
        # Isolated candidate: claim stays until exact owner + manager commits complete.
        # Any error after claim is retained; production needs a visible recovery path.
        from uuid import uuid4
        from apple_sync.metadata import metadata
        from apple_sync.export_reservations import ordinary_begin, ordinary_finish_calendar
        inventory = self.bridge.call('calendars').get('calendars',[])
        targets = [row for row in inventory if row.get('id') == calendar_id and row.get('source_id') and row.get('writable')]
        if len(targets) != 1:
            raise ValueError('Apple calendar source or target is unavailable; review required')
        target = targets[0]
        if scan_token and target['source_id'] != scan_source:
            raise ValueError('calendar source changed after scan claim')
        payload = self._write_payload(local, calendar_id=calendar_id, window=window)
        expected = self.calendar.apple_prepare_ordinary_calendar_create(local['id'])
        if expected['local'] != local:
            raise ValueError('local calendar changed before Apple write')
        prior_baseline = self._get('item-metadata:' + local['id'])
        attempt = uuid4().hex
        ordinary_begin(self, local['id'], calendar_id, attempt, expected, scan_token=scan_token, scan_source=scan_source)
        self.calendar.apple_verify_ordinary_calendar_create(expected)
        remote = self.bridge.call('upsert', **payload, expected_source_id=target['source_id'],
                                  expected_destination_snapshot=target)['event']
        if (remote.get('calendar_id') != calendar_id or remote.get('source_id') != target['source_id']
                or not remote.get('event_id')):
            raise ValueError('Apple calendar result identity changed; reconciliation required')
        finalized = self.calendar.apple_finalize_ordinary_calendar_create(expected, remote)
        mapping = {'alcor_id':finalized['id'],'source_key':_source_key(remote),
                   'calendar_id':remote['calendar_id'],'event_id':remote['event_id'],
                   'external_id':remote.get('external_id') or '',
                   'occurrence_date':remote.get('occurrence_date') or remote['start_at'],
                   'origin':'alcor','local_hash':_fingerprint(finalized,series=True),
                   'remote_hash':_fingerprint(remote,series=True,remote=True),
                   'state':'active','conflict':'','missing_since':'','updated_at':self.clock()}
        baseline = [metadata(finalized),metadata(remote,True)]
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM settings WHERE key=?',('item-metadata:'+local['id'],)).fetchone()
            current = json.loads(row[0]) if row else None
            if (current != prior_baseline or db.execute('SELECT 1 FROM mappings WHERE alcor_id=? OR source_key=? OR (calendar_id=? AND event_id=?)',
                    (local['id'],mapping['source_key'],calendar_id,mapping['event_id'])).fetchone()):
                raise ValueError('calendar mapping changed after Apple write; reconciliation required')
            self._save_map(mapping,connection=db)
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('item-metadata:'+local['id'],json.dumps(baseline)))
            guard={'generation':expected['generation'],'source_id':target['source_id'],'group_id':calendar_id}
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('calendar-binding-generation:'+local['id'],json.dumps(guard,sort_keys=True)))
        ordinary_finish_calendar(self, local['id'], calendar_id, attempt, remote)
        return remote


    def _archive_identity_absence(self, mapping, before, baseline, missing, source_id, token, direction):
        """Archive unchanged nonrecurring data after a complete local identity query."""
        from apple_sync.metadata import metadata
        if not missing or direction not in ('import', 'bidirectional') or not self._get('sync_deletions', False):
            return None
        capture = missing['capture']; local = capture['local']; binding = local.get('apple') or {}
        prior = binding.get('native') or {}; group_id = before['calendar_id']
        guard = self._get('calendar-binding-generation:'+local['id'])
        links = self._stored_links()
        if (not before.get('external_id') or binding.get('source_id') != source_id or
                binding.get('group_id') != group_id or binding.get('apple_id') != before['event_id'] or
                prior.get('external_id') != before['external_id'] or local.get('repeat_frequency') != 'none' or
                (prior.get('recurrence') or {}).get('frequency') != 'none' or prior.get('is_detached') is not False or
                guard != {'generation':capture['generation'], 'source_id':source_id, 'group_id':group_id} or
                links and not any(row['apple_id']==group_id and row['alcor_id']==local.get('category_id') for row in links) or
                not baseline or baseline[0] != metadata(local) or
                baseline[1] != metadata(prior,True) or
                _fingerprint(local, series=before['origin']=='alcor') != before['local_hash']):
            return None
        native_status = self.bridge.call('status')
        if native_status.get('authorization') != 'full_access' or type(native_status.get('identity_lookup_version')) is not int or native_status['identity_lookup_version'] != 1:
            return None
        from apple_sync.bridge import BridgeError
        try:
            response = self.bridge.call('lookup_identity', calendar_id=group_id, event_id=before['event_id'],
                                        external_id=before['external_id'], expected_source_id=source_id)
        except BridgeError:
            return None  # A failed read is not an observation of absence.
        proof = response.get('identity_observation') or {}
        if (type(proof.get('version')) is not int or proof['version'] != 1 or proof.get('status') != 'absent' or
                proof.get('source_type') not in ('caldav','exchange','mobileme') or proof.get('candidates') != [] or
                any(proof.get(key) != value for key,value in {'event_id':before['event_id'],
                    'external_id':before['external_id'],'calendar_id':group_id,'source_id':source_id}.items())):
            return None
        observation = {**proof, 'absence_evidence':'complete_local_identity_query'}
        def check(db, evidence=None):
            held = db.execute('SELECT a.state,a.evidence FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) JOIN calendar_scan_items i USING(attempt_id) WHERE a.attempt_id=? AND g.group_id=? AND g.source_id=? AND i.local_id=?',
                              (token,group_id,source_id,local['id'])).fetchone()
            row = db.execute('SELECT * FROM mappings WHERE id=?',(before['id'],)).fetchone()
            saved = db.execute('SELECT value FROM settings WHERE key=?',('item-metadata:'+local['id'],)).fetchone()
            if not held or held['state'] != 'scanning' or (evidence is not None and json.loads(held['evidence']) != evidence) or not row or dict(row) != before or (json.loads(saved[0]) if saved else None) != baseline:
                raise ValueError('calendar absence claim or mapping changed; review required')
        evidence = {'kind':'calendar_identity_absence','stage':'owner_committing','mapping':before,
                    'capture':missing,'native_observation':observation}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');check(db)
            db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',(json.dumps(evidence,ensure_ascii=False),token))
        try:
            self.calendar.apple_remove_calendar_inbound(capture,source_id,group_id,before['event_id'],
                                                         capture['generation'],missing['deletion_snapshot'])
        except ValueError as error:
            if not str(error).startswith('INBOUND_CHANGED:'): raise
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE');check(db,evidence)
                self._record_inbound_conflict(mapping,before,baseline,capture,observation,
                    reason='Apple 本机已无该身份，但本地内容或关联已变化；双方证据已保留，请核对。',connection=db)
                evidence['stage']='owner_rejected'
                db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',(json.dumps(evidence,ensure_ascii=False),token))
            return 'conflict'
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');check(db,evidence)
            updated={**before,'state':'remote_deleted','conflict':'','missing_since':'','updated_at':self.clock()}
            self._save_map(updated,connection=db)
            evidence['stage']='owner_committed'
            db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',(json.dumps(evidence,ensure_ascii=False),token))
        mapping.clear();mapping.update(updated)
        return 'archived'

    def _hold_cross_container_candidate(self, remote, maps, token):
        collisions=[row for row in maps if row['calendar_id']!=remote['calendar_id'] and
                    ((remote.get('alcor_id') and row['alcor_id']==remote['alcor_id']) or
                     (remote.get('external_id') and row['external_id']==remote['external_id']))]
        key=hashlib.sha256(json.dumps([remote['source_id'],remote['calendar_id'],remote['event_id'],remote.get('occurrence_date')]).encode()).hexdigest()
        if not collisions and not self._get('calendar-identity-candidate:'+key):return False
        with self._connect() as db:
            original_claim=db.execute('SELECT * FROM calendar_scan_attempts WHERE attempt_id=?',(token,)).fetchone()
            original_claim=dict(original_claim) if original_claim else None
        direct=next((row for row in maps if row['calendar_id']==remote['calendar_id'] and row['event_id']==remote['event_id'] and row['occurrence_date']==remote.get('occurrence_date')),None)
        binding=(self._local(direct['alcor_id']) or {}).get('apple') if direct else None
        independent=not collisions or bool(binding and binding.get('source_id')==remote['source_id'] and binding.get('group_id')==remote['calendar_id'])
        if any(row['alcor_id']==remote.get('alcor_id') for row in collisions):independent=False
        elif collisions and not independent:
            independent=True
            try:
                inventory=self.bridge.call('calendars')
            except Exception:
                inventory={'calendars':[]}  # Native read failure cannot prove two live objects.
            selected=set(self._get('calendar_ids',[]))
            for old in collisions:
                bound=(self._local(old['alcor_id']) or {}).get('apple') or {}
                group=next((row for row in inventory['calendars'] if row['id']==old['calendar_id']),None)
                if old['calendar_id'] not in selected or not group or group.get('source_id') not in inventory.get('source_ids',[]) or bound.get('source_id')!=group['source_id'] or bound.get('group_id')!=old['calendar_id'] or bound.get('apple_id')!=old['event_id']:
                    independent=False;break
                try:
                    found=self.bridge.call('lookup',**{key:old[key] for key in ('calendar_id','event_id','external_id','occurrence_date')}).get('event')
                except Exception:
                    found=None
                if not found or found.get('source_id')!=bound['source_id'] or any(found.get(key)!=old[key] for key in ('calendar_id','event_id','external_id','occurrence_date')):
                    independent=False;break
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            claim=db.execute('SELECT a.* FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) WHERE a.attempt_id=? AND g.group_id=? AND g.source_id=?',(token,remote['calendar_id'],remote['source_id'])).fetchone()
            if not claim or claim['state']!='scanning' or dict(claim)!=original_claim:raise ValueError('calendar candidate scan claim changed')
            for old in collisions+([direct] if direct else []):
                current=db.execute('SELECT * FROM mappings WHERE id=?',(old['id'],)).fetchone()
                if not current or dict(current)!=old:independent=False
            if independent:
                db.execute('DELETE FROM settings WHERE key=?',('calendar-identity-candidate:'+key,));return False
            record={'key':key,'state':'pending','native':remote,'mapping_ids':sorted(row['alcor_id'] for row in collisions),'mappings':collisions,
                    'reason':'其他日历有相同系列标识，但原对象或来源无法核实。双方已保留，未自动移动、合并或重复导入；请核对来源。','observed_at':self.clock()}
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('calendar-identity-candidate:'+key,json.dumps(record,ensure_ascii=False)))
        return True

    def _sync_claimed_calendar(self, group_id, source_id, token, direction, window, using_links, enabled_links):
        selected = [group_id]
        failures, name_errors = [], []
        counts = {"status": "success", "imported": 0, "updated": 0, "exported": 0,
                  "deleted_local": 0, "deleted_remote": 0, "conflicts": 0,
                  "pending_deletions": 0, "pending_local_deletions": 0,
                  "failed_calendar_ids": []}
        try:
            inventory = self.bridge.call("events", calendar_ids=selected, start=window[0], end=window[1])
            successful = set(inventory["successful_calendar_ids"]) & set(selected)
            counts["failed_calendar_ids"] = sorted(set(selected) - successful)
            if counts["failed_calendar_ids"]:
                with self._connect() as db:
                    db.executemany("UPDATE mappings SET missing_since='' WHERE calendar_id=?",
                                   [(key,) for key in counts["failed_calendar_ids"]])
            if any(row.get('calendar_id') != group_id or row.get('source_id') != source_id for row in inventory['events']):
                raise ValueError('scan returned an untrusted source or container identity')
            from apple_sync.group_scan_claims import claim_items, verify_binding, ScanHeld
            maps = self._maps()
            seen = set()
            identity_counts = {}
            for row in inventory["events"]:
                identity = (row["calendar_id"], row.get("external_id"), row.get("occurrence_date"))
                identity_counts[identity] = identity_counts.get(identity, 0) + 1
            blocked_ids = set()
            for remote in inventory["events"]:
                if remote["calendar_id"] not in successful:
                    continue
                from apple_sync.marker_recovery import pending_native
                if pending_native(self,remote):
                    counts['conflicts']+=1;counts['status']='partial';continue
                try:
                    identity = (remote["calendar_id"], remote.get("external_id"), remote.get("occurrence_date"))
                    ambiguous = identity_counts[identity] > 1
                    event_direction = direction
                    category_id = None
                    if using_links:
                        link = next((row for row in enabled_links if row["apple_id"] == remote["calendar_id"]), None)
                        if link is None:
                            continue
                        event_direction = link["direction"]
                        category_id = link["alcor_id"]
                    moved = None
                    if self._hold_cross_container_candidate(remote,maps,token):
                        counts['conflicts']+=1;counts['status']='partial';continue
                    touched = self._match(remote, maps, ambiguous)
                    local_ids = [touched['alcor_id']] if touched else [remote['alcor_id']] if remote.get('alcor_id') else []
                    local_ids += [row['alcor_id'] for row in maps if row['calendar_id'] == group_id and
                                  row['origin'] == 'alcor' and remote.get('external_id') and row['external_id'] == remote['external_id']]
                    try:
                        claim_items(self, token, local_ids)
                    except ScanHeld:
                        blocked_ids.update(local_ids)
                        counts['status'] = 'partial'
                        continue
                    for local_id in local_ids:
                        verify_binding(self, local_id, source_id, group_id, remote)
                    self._process_remote(remote, maps, event_direction, window, counts, ambiguous, moved, category_id, expected_source_id=source_id,scan_token=token)
                    found = self._match(remote, maps, ambiguous)
                    if found:
                        seen.add(found["alcor_id"])
                except Exception as exc:
                    raise
            maps = self._maps()
            from apple_sync.category_rebuild import retained_ids
            from apple_sync.marker_recovery import pending_ids
            protected=retained_ids(self)
            for mapping in maps:
                if mapping["alcor_id"] in protected:
                    counts["status"]="partial";continue
                if mapping["calendar_id"] not in successful or mapping["alcor_id"] in seen or mapping["alcor_id"] in blocked_ids:
                    continue
                if mapping["state"] in ("deleted", "remote_deleted", "locally_deleted", "conflict", "instance_excluded", "pending_delete"):
                    continue
                try:
                    from apple_sync.group_scan_claims import ScanHeld
                    try:
                        claim_items(self, token, [mapping['alcor_id']])
                    except ScanHeld:
                        blocked_ids.add(mapping['alcor_id'])
                        counts['status'] = 'partial'
                        continue
                    verify_binding(self, mapping["alcor_id"], source_id, group_id)
                    before = dict(mapping)
                    previous_baseline = self._get('item-metadata:'+mapping["alcor_id"])
                    missing = self.calendar.apple_capture_calendar_missing(mapping["alcor_id"])
                    capture = missing['capture'] if missing else None
                    local = capture['local'] if capture else None
                    if local is not None:
                        start = datetime.fromisoformat(local["start_at"].replace("Z", "+00:00"))
                        end = datetime.fromisoformat(local["end_at"].replace("Z", "+00:00"))
                        if end <= datetime.fromisoformat(window[0]) or start >= datetime.fromisoformat(window[1]):
                            continue
                    found = self.bridge.call("lookup", **{key: mapping[key] for key in
                        ("calendar_id", "event_id", "external_id", "occurrence_date")},
                        scan_start=window[0], scan_end=window[1]).get("event")
                    if found:
                        if found.get('calendar_id') != group_id or found.get('source_id') != source_id:
                            raise ValueError('lookup crossed the claimed source or container')
                        found_direction = direction
                        found_category = None
                        if using_links:
                            found_link = next((row for row in enabled_links
                                               if row["apple_id"] == found["calendar_id"]), None)
                            if found_link is None:
                                continue
                            found_direction = found_link["direction"]
                            found_category = found_link["alcor_id"]
                        self._process_remote(found, maps, found_direction, window, counts,
                                             category_id=found_category, expected_source_id=source_id)
                        continue
                    item_direction = next((row['direction'] for row in enabled_links
                                           if row['apple_id'] == group_id), direction) if using_links else direction
                    outcome = self._archive_identity_absence(mapping, before, previous_baseline, missing,
                                                              source_id, token, item_direction)
                    if outcome:
                        counts['deleted_local' if outcome == 'archived' else 'conflicts'] += 1
                        if outcome != 'archived': counts['status'] = 'partial'
                        continue
                    # EventKit's old ID and bounded lookup cannot prove a global deletion.
                    observation = {key: mapping[key] for key in
                                   ('calendar_id', 'event_id', 'external_id', 'occurrence_date')}
                    observation.update(source_id=source_id, scan_start=window[0], scan_end=window[1],
                                       absence_evidence='bounded_lookup_null')
                    recorded = self._record_inbound_conflict(
                        mapping, before, previous_baseline, capture, observation,
                        reason='Apple 未在当前查询范围找到该日程，无法确认已删除；本地数据已保留，请核对来源与关联。')
                    if recorded:
                        counts['conflicts'] += 1
                    else:
                        blocked_ids.add(mapping['alcor_id'])
                    counts['status'] = 'partial'
                except Exception as exc:
                    raise
            if successful and (using_links or direction in ("export", "bidirectional")):
                calendars = {row["id"]: row for row in self.bridge.call("calendars")["calendars"]}
                targets = [key for key in selected if key in successful and calendars.get(key, {}).get("writable")]
                export_targets = [(row["apple_id"], row["alcor_id"]) for row in enabled_links
                                  if row["direction"] in ("export", "bidirectional") and row["apple_id"] in targets]
                if not using_links and targets:
                    export_targets = [(targets[0], None)]
                if export_targets:
                    with self._connect() as db:
                        from apple_sync.category_rebuild import retained_ids
                        from apple_sync.marker_recovery import pending_ids
                        baseline = {row[0] for row in db.execute("SELECT alcor_id FROM baseline_local")} | retained_ids(self) | pending_ids(self)
                    mapped = {row["alcor_id"] for row in self._maps()}
                    for local in self.calendar.list_events():
                        if local["source_type"] in ("apple_calendar", "apple_recovered") or local["id"] in mapped or local["id"] in baseline:
                            continue
                        match = next((item for item in export_targets
                                      if item[1] is None or local.get("category_id") == item[1]), None)
                        if match is None:
                            continue
                        try:
                            claim_items(self, token, [local['id']])
                            remote = self._ordinary_create(local, match[0], window, scan_token=token, scan_source=source_id)
                            if remote.get('source_id') != source_id or remote.get('calendar_id') != group_id:
                                raise ValueError('native create returned a different source or container')
                            counts["exported"] += 1
                        except Exception as exc:
                            raise
            for mapping in self._maps():
                if mapping["state"] != "pending_delete" or mapping["calendar_id"] not in successful:
                    continue
                counts["pending_deletions"] += 1
                counts["status"] = "partial"
            counts["pending_local_deletions"] = sum(
                row["state"] == "pending_local_delete" for row in self._maps())
            if counts["failed_calendar_ids"]:
                counts["status"] = "partial" if counts["status"] == "success" else counts["status"]
            counts['conflicts'] = max(counts['conflicts'],sum(row['state']=='conflict' for row in self._maps()))
            if counts['conflicts'] and counts['status'] == 'success':
                counts['status'] = 'partial'
            if self._get('operation_errors',[]) or self._get('category_conflicts',[]):
                counts['status'] = 'partial'
            if name_errors and counts["status"] == "success":
                counts["status"] = "error"
        except Exception as exc:
            failures.append(report_sync_error("calendar", "同步日历", exc))
            counts["status"] = "error"

        counts['blocked_local_ids'] = sorted(blocked_ids) if 'blocked_ids' in locals() else []
        return counts, not failures
