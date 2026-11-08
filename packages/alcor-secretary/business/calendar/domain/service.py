"""User calendar with stable source links for briefing ingestion (stella.calendar plugin)."""

from __future__ import annotations

import sqlite3
import uuid
import json
import hashlib
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from business.apple_contract import AppleContract, setup_apple_contract
from business.security_receipts import read_receipt, save_receipt, validate_operation_id
from business.object_generations import ensure_object_generations, generations_for
from business.categories import default_category, ensure_required_categories, require_category

from .imports import HOLIDAY_SCHEDULES, parse_special_dates

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def load_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _instant(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _text(value, field, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{field} must be 1-{limit} characters")
    return value.strip()


class CalendarService(AppleContract):
    APPLE_ITEMS = "calendar_events"
    APPLE_CATEGORIES = "calendar_categories"

    def __init__(self, db_path):
        if db_path is None:
            raise ValueError("db_path is required (host data handle)")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(load_schema_sql())
            # Serialize the schema check and ALTERs across simultaneous HTTP requests.
            # executescript commits any earlier transaction, so acquire the write lock here.
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(calendar_events)")}
            for name, declaration in (
                ("repeat_frequency", "TEXT NOT NULL DEFAULT 'none'"),
                ("repeat_days", "TEXT NOT NULL DEFAULT '[]'"),
                ("repeat_until", "TEXT"),
                ("repeat_timezone", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE calendar_events ADD COLUMN {name} {declaration}")
            ensure_required_categories(db, 'calendar_categories', 'calendar_events', '日程', 'personal')
            ensure_object_generations(db, 'calendar_events', 'calendar_categories')
        with self._connect() as db:
            setup_apple_contract(db,self.APPLE_ITEMS)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def find_briefing_sources(self, source_keys):
        if (not isinstance(source_keys, list) or len(source_keys) > 1000 or
                any(not isinstance(key, str) or not key for key in source_keys)):
            raise ValueError('source_keys must be stable source references')
        if not source_keys:
            return []
        placeholders = ','.join('?' for _ in source_keys)
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM calendar_events WHERE source_type='briefing' AND source_key IN (" + placeholders + ")",
                source_keys)]

    def has_source_tombstone(self, source_keys):
        """Public collaboration API: whether any source_key is tombstoned."""
        candidates = [key for key in source_keys if key]
        if not candidates:
            return False
        with self._connect() as db:
            for candidate in candidates:
                if db.execute(
                    "SELECT 1 FROM source_tombstones WHERE source_key=?",
                    (candidate,),
                ).fetchone():
                    return True
        return False

    def categories(self):
        with self._connect() as db:
            return [self._apple_decorate(db, "category", row) for row in db.execute("""SELECT * FROM calendar_categories ORDER BY
                CASE id WHEN 'personal' THEN 0 WHEN 'work' THEN 1 WHEN 'study' THEN 2
                WHEN 'china_holidays' THEN 3 ELSE 4 END, created_at, name""")]

    def create_category(self, payload):
        name = _text(payload.get("name"), "name", 200)
        color = payload.get("color", "#6B8AF7")
        if not isinstance(color, str) or len(color) != 7 or color[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in color[1:]):
            raise ValueError("color must be #RRGGBB")
        category_id = str(uuid.uuid4())
        try:
            with self._connect() as db:
                db.execute("INSERT INTO calendar_categories VALUES (?,?,?,?)", (category_id, name, color, _now()))
                if payload.get('source_id'):
                    if not isinstance(payload['source_id'],str):
                        raise ValueError('source_id must be a string')
                    db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)', ('category',category_id,json.dumps({'source_id':payload['source_id'],'planned':True})))
        except sqlite3.IntegrityError as exc:
            raise ValueError("category name already exists") from exc
        return self.get_category(category_id)

    def get_category(self, category_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM calendar_categories WHERE id=?", (category_id,)).fetchone()
        if row is None:
            raise KeyError("category not found")
        with self._connect() as db:
            return self._apple_decorate(db, "category", row)

    def set_event_category(self, event_id, category_id, *, _apple=False):
        """Move an event between categories without treating that as a content edit."""
        self.get_event(event_id)
        self.get_category(require_category(category_id))
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not _apple:
                self._apple_guard(db,'item',event_id,'can_modify')
                self._apple_guard(db,'category',category_id,'can_modify')
            db.execute("UPDATE calendar_events SET category_id=? WHERE id=?", (category_id, event_id))
        return self.get_event(event_id)

    def update_category(self, category_id, payload, *, _apple=False):
        old = self.get_category(category_id)
        name = _text(payload.get("name", old["name"]), "name", 200)
        color = payload.get("color", old["color"])
        if not isinstance(color, str) or len(color) != 7 or color[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in color[1:]):
            raise ValueError("color must be #RRGGBB")
        try:
            with self._connect() as db:
                if not _apple:
                    self._apple_guard(db,"category",category_id,"can_rename")
                name = self._apple_category_name(db,category_id,name,_apple)
                db.execute("UPDATE calendar_categories SET name=?, color=? WHERE id=?", (name, color, category_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError("category name already exists") from exc
        return self.get_category(category_id)

    def _local_category_delete_snapshot(self, db, category_id, *, allow_apple=False, ignore_operation_id=None):
        category = db.execute('SELECT * FROM calendar_categories WHERE id=?', (category_id,)).fetchone()
        if not category:
            raise KeyError('category not found')
        bindings = db.execute("SELECT * FROM apple_bindings WHERE entity='category' AND local_id=?", (category_id,)).fetchall()
        if not allow_apple and any(not json.loads(row['document']).get('planned') for row in bindings):
            raise ValueError('Apple 分类不能按本机分类删除')
        operations = [row for row in db.execute("SELECT * FROM apple_operations WHERE entity='category' AND local_id=? ORDER BY id", (category_id,)) if row['id'] != ignore_operation_id]
        rows = db.execute('SELECT * FROM calendar_events WHERE category_id=? ORDER BY id', (category_id,)).fetchall()
        plans = [self._local_event_delete_snapshot(db, row['id'], allow_recurring=True, allow_apple=allow_apple, ignore_operation_id=ignore_operation_id) for row in rows]
        content = {'category': dict(category), 'category_generation': generations_for(db, 'category', [category_id]),
                   'bindings': [dict(row) for row in bindings], 'operations': [dict(row) for row in operations], 'events': [(row['id'], plan['snapshot']) for row, plan in zip(rows, plans)]}
        encoded = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        return {'snapshot': hashlib.sha256(encoded.encode()).hexdigest(), 'id': category_id,
                'targets': [{'id': category_id, 'title': category['name'], 'category': True},
                            *[{'id': row['id'], 'title': row['title'], 'recurring': row['repeat_frequency'] != 'none' or (json.loads(row['apple_data'] or '{}').get('native_recurrence') or {}).get('frequency', 'none') != 'none'} for row in rows]]}

    def prepare_local_category_delete(self, category_id, *, operation_id):
        validate_operation_id(operation_id)
        with self._connect() as db:
            db.execute('BEGIN')
            if read_receipt(db, operation_id, 'delete_category', {'id': category_id}):
                return {'replay': True, 'id': category_id}
            return self._local_category_delete_snapshot(db, category_id)

    def commit_local_category_delete(self, category_id, *, snapshot, operation_id):
        validate_operation_id(operation_id)
        if not isinstance(snapshot, str): raise ValueError('invalid deletion snapshot')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            args = {'id': category_id}
            receipt = read_receipt(db, operation_id, 'delete_category', args)
            if receipt:
                present = db.execute('SELECT 1 FROM calendar_categories WHERE id=?', (category_id,)).fetchone() is not None
                return {'replayed': True, 'id': category_id, 'receipt': receipt, 'targetConflict': present}
            if self._local_category_delete_snapshot(db, category_id)['snapshot'] != snapshot:
                raise ValueError('deletion snapshot conflict; review again')
            for row in db.execute('SELECT * FROM calendar_events WHERE category_id=? ORDER BY id', (category_id,)).fetchall():
                self._delete_event(db, row)
            db.execute('DELETE FROM calendar_categories WHERE id=?', (category_id,))
            save_receipt(db, operation_id, 'delete_category', args, category_id)
        return {'deleted': True, 'id': category_id, 'replayed': False}

    def delete_category(self, category_id):
        raise PermissionError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')

    def _validate_event(self, payload, existing=None):
        row = dict(existing or {})
        row.update(payload)
        row["title"] = _text(row.get("title"), "title")
        row["start_at"] = _instant(row.get("start_at"), "start_at")
        row["end_at"] = _instant(row.get("end_at"), "end_at")
        if (datetime.fromisoformat(row["end_at"]) < datetime.fromisoformat(row["start_at"]) or
                row.get("all_day") and row["end_at"] == row["start_at"]):
            raise ValueError("end_at must not precede start_at; all-day events require a positive duration")
        for field in ("description", "location"):
            value = row.get(field, "")
            if not isinstance(value, str):
                raise ValueError(f"{field} must be text")
            row[field] = value
        row["all_day"] = int(bool(row.get("all_day", False)))
        frequency = row.get("repeat_frequency") or "none"
        if frequency not in ("none", "daily", "weekly", "monthly", "yearly"):
            raise ValueError("invalid repeat_frequency")
        row["repeat_frequency"] = frequency
        days = row.get("repeat_days", [])
        if isinstance(days, str):
            try:
                days = json.loads(days)
            except ValueError as exc:
                raise ValueError("invalid repeat_days") from exc
        if not isinstance(days, list) or any(type(day) is not int or day < 0 or day > 6 for day in days):
            raise ValueError("repeat_days must contain weekdays 0-6")
        row["repeat_days"] = json.dumps(sorted(set(days)))
        until = row.get("repeat_until") or None
        if until:
            try:
                until = datetime.strptime(until, "%Y-%m-%d").date().isoformat()
            except (ValueError, TypeError) as exc:
                raise ValueError("repeat_until must be YYYY-MM-DD") from exc
        row["repeat_until"] = until
        zone = row.get("repeat_timezone") or "UTC"
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise ValueError("invalid repeat_timezone") from exc
        row["repeat_timezone"] = zone
        if frequency != "none" and until and until < datetime.fromisoformat(row["start_at"]).astimezone(ZoneInfo(zone)).date().isoformat():
            raise ValueError("repeat_until must be on or after start_at")
        if "category_id" in payload:
            require_category(payload["category_id"])
        if row.get("category_id"):
            self.get_category(row["category_id"])
        return row

    def get_event(self, event_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise KeyError("event not found")
        with self._connect() as db:
            return self._apple_decorate(db,"item",row)

    def list_events(self, start=None, end=None, category_id=None):
        start_at = datetime.fromisoformat(_instant(start, "start")) if start else None
        end_at = datetime.fromisoformat(_instant(end, "end")) if end else None
        if start_at and end_at and end_at <= start_at:
            raise ValueError("end must be after start")
        clauses, args = [], []
        if start_at and end_at:
            clauses.append("(repeat_frequency != 'none' OR ((end_at > ? OR (end_at = start_at AND start_at >= ?)) AND start_at < ?))")
            args.extend((start_at.isoformat(), start_at.isoformat(), end_at.isoformat()))
        elif start_at:
            clauses.append("(end_at > ? OR (end_at = start_at AND start_at >= ?))")
            args.extend((start_at.isoformat(), start_at.isoformat()))
        elif end_at:
            clauses.append("start_at < ?")
            args.append(end_at.isoformat())
        if category_id:
            clauses.append("category_id = ?")
            args.append(category_id)
        sql = "SELECT * FROM calendar_events" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY start_at, id"
        with self._connect() as db:
            templates = [self._apple_decorate(db,"item",row) for row in db.execute(sql,args)]
            exclusions = {(row["series_id"], row["occurrence_on"]) for row in db.execute(
                "SELECT series_id,occurrence_on FROM calendar_occurrence_exclusions")}
        if not start_at or not end_at:
            return templates
        if end_at - start_at > timedelta(days=370):
            raise ValueError("calendar range cannot exceed 370 days")
        results = []
        for row in templates:
            if row["repeat_frequency"] == "none":
                if ((datetime.fromisoformat(row["end_at"]) > start_at or
                     row["end_at"] == row["start_at"] and datetime.fromisoformat(row["start_at"]) >= start_at) and
                        datetime.fromisoformat(row["start_at"]) < end_at):
                    results.append(row)
            else:
                results.extend(instance for instance in self._occurrences(row, start_at, end_at)
                               if (row["id"], instance["id"].rsplit("@", 1)[-1]) not in exclusions)
        return sorted(results, key=lambda row: (row["start_at"], row["id"]))

    def exclude_occurrence(self, series_id, occurrence_on, *, _apple=False):
        raise PermissionError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')

    @staticmethod
    def _occurrences(row, start, end):
        zone = ZoneInfo(row["repeat_timezone"] or "UTC")
        original = datetime.fromisoformat(row["start_at"]).astimezone(zone)
        original_end = datetime.fromisoformat(row["end_at"]).astimezone(zone)
        wall_duration = original_end.replace(tzinfo=None) - original.replace(tzinfo=None)
        duration = datetime.fromisoformat(row["end_at"]) - datetime.fromisoformat(row["start_at"])
        first_day = original.date()
        until = datetime.fromisoformat(row["repeat_until"]).date() if row["repeat_until"] else None
        candidate = max(first_day, (start.astimezone(zone) - duration).date())
        last = end.astimezone(zone).date()
        weekdays = json.loads(row["repeat_days"] or "[]") or [first_day.weekday()]
        results = []
        while candidate <= last and (until is None or candidate <= until):
            elapsed = (candidate - first_day).days
            frequency = row["repeat_frequency"]
            matches = (frequency == "daily" or
                       frequency == "weekly" and candidate.weekday() in weekdays or
                       frequency == "monthly" and candidate.day == first_day.day or
                       frequency == "yearly" and (candidate.month, candidate.day) == (first_day.month, first_day.day))
            if elapsed >= 0 and matches:
                occurrence_start = datetime.combine(candidate, original.timetz().replace(tzinfo=None), zone).astimezone(timezone.utc)
                occurrence_end = (datetime.combine(candidate, original.timetz().replace(tzinfo=None)) + wall_duration).replace(tzinfo=zone).astimezone(timezone.utc)
                if (occurrence_end > start or occurrence_end == occurrence_start and occurrence_start >= start) and occurrence_start < end:
                    instance = dict(row)
                    instance["series_id"] = row["id"]
                    instance["id"] = f"{row['id']}@{candidate.isoformat()}"
                    instance["start_at"] = occurrence_start.isoformat()
                    instance["end_at"] = occurrence_end.isoformat()
                    results.append(instance)
            candidate += timedelta(days=1)
        return results

    def create_event(self, payload, *, _briefing_key=None, _apple=False):
        if payload.get('category_id') and not _apple:
            with self._connect() as db:
                self._apple_guard(db,'category',payload['category_id'],'can_modify')
        row = self._validate_event(payload)
        event_id, now = str(payload.get('id') or uuid.uuid4()), _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if _briefing_key:
                existing = db.execute("SELECT id FROM calendar_events WHERE source_type='briefing' AND source_key=?", (_briefing_key,)).fetchone()
                if existing:
                    return self.get_event(existing[0])
                if db.execute("SELECT 1 FROM source_tombstones WHERE source_key=?", (_briefing_key,)).fetchone():
                    raise ValueError("source event was deleted")
            row['category_id'] = row.get('category_id') or default_category(db, 'calendar_categories', '日程', 'personal')
            db.execute("""INSERT INTO calendar_events
                (id,title,description,start_at,end_at,all_day,location,category_id,source_type,source_key,source_url,user_modified,repeat_frequency,repeat_days,repeat_until,repeat_timezone,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, row["title"], row["description"], row["start_at"], row["end_at"], row["all_day"],
                 row["location"], row.get("category_id"), "briefing" if _briefing_key else None, _briefing_key, "", 0, row["repeat_frequency"], row["repeat_days"], row["repeat_until"], row["repeat_timezone"], now, now))
            self._apple_fields(db,event_id,payload,_apple)
        return self.get_event(event_id)

    def upsert_external_event(self, provider, source_key, payload, event_id=None, reconciled=False):
        """Create or refresh a sourced event without overwriting a local edit."""
        if provider != "apple_calendar":
            raise ValueError("unsupported calendar provider")
        source_key = _text(source_key, "source_key", 1000)
        row = self._validate_event(payload)
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM calendar_events WHERE source_type=? AND source_key=?",
                             (provider, source_key)).fetchone()
            if old:
                if event_id and old["id"] != event_id:
                    raise ValueError("source event mapping changed")
                if old["user_modified"] and not reconciled:
                    raise ValueError("source event was locally modified")
                db.execute("""UPDATE calendar_events SET title=?,description=?,start_at=?,end_at=?,all_day=?,
                    location=?,repeat_frequency=?,repeat_days=?,repeat_until=?,repeat_timezone=?,user_modified=0,updated_at=?
                    WHERE id=?""", (row["title"], row["description"], row["start_at"], row["end_at"],
                    row["all_day"], row["location"], row["repeat_frequency"], row["repeat_days"],
                    row["repeat_until"], row["repeat_timezone"], now, old["id"]))
                return_id = old["id"]
            else:
                if event_id:
                    raise KeyError("source event not found")
                return_id = str(uuid.uuid4())
                row['category_id'] = row.get('category_id') or default_category(db, 'calendar_categories', '日程', 'personal')
                db.execute("""INSERT INTO calendar_events
                    (id,title,description,start_at,end_at,all_day,location,category_id,source_type,source_key,
                     source_url,user_modified,repeat_frequency,repeat_days,repeat_until,repeat_timezone,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (return_id, row["title"], row["description"], row["start_at"], row["end_at"],
                     row["all_day"], row["location"], row.get("category_id"), provider, source_key,
                     "", 0, row["repeat_frequency"], row["repeat_days"], row["repeat_until"],
                     row["repeat_timezone"], now, now))
            self._apple_fields(db,return_id,payload,True)
        return self.get_event(return_id)

    def update_event(self, event_id, payload, *, _apple=False):
        if '@' in event_id and not _apple:
            identity,day = event_id.rsplit('@',1)
            old = self.get_event(identity)
            zone = ZoneInfo(old['repeat_timezone'] or 'UTC')
            lower = datetime.combine(date.fromisoformat(day),time.min,zone)
            instance = next((row for row in self._occurrences(old,lower,lower+timedelta(days=1)) if row['id']==event_id),None)
            if not instance:
                raise ValueError('所选日期不是此日程的实例')
            fields = {**instance,**payload}
            self._validate_event(fields)
            scope = payload.get('edit_scope','this')
            if scope not in ('this','future'):
                raise ValueError('请选择本次或将来所有日程')
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                binding = self._apple_guard(db,'item',identity,'can_modify')
                if not binding:
                    raise ValueError('请先同步重复日程后选择编辑本次或将来日程')
                self._apple_guard(db,'category',fields['category_id'],'can_modify')
                self._apple_queue(db,'item',identity,'edit',{'occurrence_on':day,'scope':scope,'patch':payload})
            return self.get_event(identity)
        old = self.get_event(event_id)
        if old["source_type"] == "china_holiday":
            raise ValueError("official holidays cannot be edited")
        row = self._validate_event(payload, old)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not _apple:
                self._apple_guard(db,'item',event_id,'can_modify')
                self._apple_guard(db,'category',payload.get('category_id',old['category_id']),'can_modify')
            db.execute("""UPDATE calendar_events SET title=?,description=?,start_at=?,end_at=?,all_day=?,location=?,
                category_id=?,repeat_frequency=?,repeat_days=?,repeat_until=?,repeat_timezone=?,user_modified=1,updated_at=? WHERE id=?""",
                (row["title"], row["description"], row["start_at"], row["end_at"], row["all_day"],
                 row["location"], row.get("category_id"), row["repeat_frequency"], row["repeat_days"], row["repeat_until"], row["repeat_timezone"], _now(), event_id))
            self._apple_fields(db,event_id,payload,_apple)
        return self.get_event(event_id)

    @staticmethod
    def _delete_event(db, old):
        if old['source_type'] == 'briefing' and old['source_key']:
            db.execute('INSERT OR REPLACE INTO source_tombstones VALUES (?,?)', (old['source_key'], _now()))
        db.execute('DELETE FROM calendar_events WHERE id=?', (old['id'],))

    def _local_event_delete_snapshot(self, db, event_id, *, allow_recurring=False, allow_apple=False, ignore_operation_id=None, ignore_exclusion_on=None):
        row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
        if not row:
            raise KeyError('event not found')
        if row['repeat_frequency'] != 'none' and not allow_recurring:
            raise ValueError('重复日程须使用专门的删除确认')
        category = db.execute('SELECT * FROM calendar_categories WHERE id=?', (row['category_id'],)).fetchone()
        bindings = db.execute("SELECT * FROM apple_bindings WHERE (entity='item' AND local_id=?) OR (entity='category' AND local_id=?) ORDER BY entity,local_id", (event_id, row['category_id'])).fetchall()
        allowed_sources = (None, '', 'briefing', 'special_date', 'china_holiday', 'apple_recovered', 'apple_calendar') if allow_apple else (None, '', 'briefing', 'special_date', 'china_holiday', 'apple_recovered')
        if (not allow_apple and any(not json.loads(item['document']).get('planned') for item in bindings)) or row['source_type'] not in allowed_sources:
            raise ValueError('Apple 来源或未知来源不能按本机日程删除')
        operations = [item for item in db.execute("SELECT * FROM apple_operations WHERE (entity='item' AND local_id=?) OR (entity='category' AND local_id=?) ORDER BY id", (event_id, row['category_id'])) if item['id'] != ignore_operation_id]
        exclusions = [item for item in db.execute('SELECT * FROM calendar_occurrence_exclusions WHERE series_id=? ORDER BY occurrence_on', (event_id,)) if item['occurrence_on'] != ignore_exclusion_on]
        handoffs = db.execute('SELECT * FROM agenda_handoffs WHERE calendar_event_id=? ORDER BY source_type,source_key,destination', (event_id,)).fetchall()
        content = {'event': dict(row), 'category': dict(category) if category else None,
                   'bindings': [dict(item) for item in bindings], 'operations': [dict(item) for item in operations],
                   'item_generations': generations_for(db, 'item', [event_id]),
                   'category_generations': generations_for(db, 'category', [row['category_id']] if row['category_id'] else []),
                   'exclusions': [dict(item) for item in exclusions], 'handoffs': [dict(item) for item in handoffs]}
        encoded = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        return {'snapshot': hashlib.sha256(encoded.encode()).hexdigest(), 'id': event_id, 'title': row['title'],
                'targets': [{'id': event_id, 'title': row['title']}]}

    def prepare_local_event_delete(self, event_id, *, operation_id=None):
        validate_operation_id(operation_id)
        with self._connect() as db:
            db.execute('BEGIN')
            if operation_id is not None:
                receipt = read_receipt(db, operation_id, 'delete_event', {'id': event_id})
                if receipt:
                    return {'replay': True, 'id': event_id}
            return self._local_event_delete_snapshot(db, event_id)

    def commit_local_event_delete(self, event_id, *, snapshot, operation_id=None):
        validate_operation_id(operation_id)
        if not isinstance(snapshot, str):
            raise ValueError('invalid deletion snapshot')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            arguments = {'id': event_id}
            if operation_id is not None:
                receipt = read_receipt(db, operation_id, 'delete_event', arguments)
                if receipt:
                    present = db.execute('SELECT 1 FROM calendar_events WHERE id=?', (event_id,)).fetchone() is not None
                    return {'replayed': True, 'id': event_id, 'receipt': receipt, 'targetConflict': present}
            current = self._local_event_delete_snapshot(db, event_id)
            if current['snapshot'] != snapshot:
                raise ValueError('deletion snapshot conflict; review again')
            row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
            self._delete_event(db, row)
            if operation_id is not None:
                save_receipt(db, operation_id, 'delete_event', arguments, event_id)
        return {'deleted': True, 'id': event_id, **({'replayed': False} if operation_id is not None else {})}

    def _local_occurrence_plan(self, db, event_id, occurrence_on, scope):
        if scope not in ('this', 'future'):
            raise ValueError('invalid recurrence scope')
        try: day = date.fromisoformat(occurrence_on)
        except (ValueError, TypeError) as exc: raise ValueError('invalid occurrence') from exc
        plan = self._local_event_delete_snapshot(db, event_id, allow_recurring=True)
        row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
        if row['repeat_frequency'] == 'none': raise ValueError('event is not a recurring series')
        zone = ZoneInfo(row['repeat_timezone'] or 'UTC')
        lower = datetime.combine(day, time.min, zone)
        if not self._occurrences(row, lower, lower + timedelta(days=1)):
            raise ValueError('所选日期不是此系列的实例')
        if scope == 'this' and db.execute('SELECT 1 FROM calendar_occurrence_exclusions WHERE series_id=? AND occurrence_on=?', (event_id, day.isoformat())).fetchone():
            raise ValueError('所选实例已跳过')
        content = {'series': plan['snapshot'], 'occurrence_on': day.isoformat(), 'scope': scope}
        plan['snapshot'] = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        plan['occurrence_on'], plan['scope'] = day.isoformat(), scope
        plan['reversible'] = scope == 'future' and day > datetime.fromisoformat(row['start_at']).astimezone(zone).date()
        return plan

    def prepare_local_occurrence_delete(self, event_id, occurrence_on, scope, *, operation_id):
        validate_operation_id(operation_id)
        args = {'id': event_id, 'occurrence_on': occurrence_on, 'scope': scope}
        with self._connect() as db:
            db.execute('BEGIN')
            if read_receipt(db, operation_id, 'delete_occurrence', args): return {'replay': True, 'id': event_id}
            return self._local_occurrence_plan(db, event_id, occurrence_on, scope)

    def commit_local_occurrence_delete(self, event_id, occurrence_on, scope, *, snapshot, operation_id):
        validate_operation_id(operation_id)
        if not isinstance(snapshot, str): raise ValueError('invalid deletion snapshot')
        args = {'id': event_id, 'occurrence_on': occurrence_on, 'scope': scope}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt = read_receipt(db, operation_id, 'delete_occurrence', args)
            if receipt:
                original = json.loads(receipt['targetId'])
                current = db.execute('SELECT 1 FROM calendar_events WHERE id=?', (event_id,)).fetchone()
                conflict = bool(current and generations_for(db, 'item', [event_id])[0][1] != original['generation'])
                return {'replayed': True, 'id': event_id, 'receipt': receipt, 'targetConflict': conflict}
            current = self._local_occurrence_plan(db, event_id, occurrence_on, scope)
            if current['snapshot'] != snapshot: raise ValueError('deletion snapshot conflict; review again')
            generation = generations_for(db, 'item', [event_id])[0][1]
            row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
            day = date.fromisoformat(occurrence_on); zone = ZoneInfo(row['repeat_timezone'] or 'UTC')
            if scope == 'this':
                db.execute('INSERT INTO calendar_occurrence_exclusions VALUES (?,?)', (event_id, occurrence_on))
            elif day > datetime.fromisoformat(row['start_at']).astimezone(zone).date():
                db.execute('UPDATE calendar_events SET repeat_until=?, user_modified=1, updated_at=? WHERE id=?', ((day-timedelta(days=1)).isoformat(), _now(), event_id))
            else:
                self._delete_event(db, row)
            save_receipt(db, operation_id, 'delete_occurrence', args, json.dumps({'id': event_id, 'generation': generation}, sort_keys=True))
        return {'deleted': True, 'id': event_id, 'replayed': False}

    def delete_event(self, event_id, scope=None, occurrence_on=None):
        # The public route permits only the existing reversible local future cutoff.
        if scope != 'future':
            raise PermissionError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._local_occurrence_plan(db, event_id, occurrence_on, scope)
            row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
            if date.fromisoformat(occurrence_on) <= datetime.fromisoformat(row['start_at']).astimezone(ZoneInfo(row['repeat_timezone'] or 'UTC')).date():
                raise PermissionError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')
            day = date.fromisoformat(occurrence_on)
            db.execute('UPDATE calendar_events SET repeat_until=?, user_modified=1, updated_at=? WHERE id=?',
                       ((day-timedelta(days=1)).isoformat(), _now(), event_id))

    @staticmethod
    def _import_category(db, category_id, name, color):
        db.execute("INSERT OR IGNORE INTO calendar_categories VALUES (?,?,?,?)",
                   (category_id, name, color, _now()))
        row = db.execute("SELECT id FROM calendar_categories WHERE id=?", (category_id,)).fetchone()
        if row is None:
            row = db.execute("SELECT id FROM calendar_categories WHERE name=?", (name,)).fetchone()
        return row["id"] if row else None

    def _all_day_import(self, title, start_day, end_day, annual, zone, description=""):
        try:
            start_date = date.fromisoformat(start_day)
            end_date = date.fromisoformat(end_day)
            timezone_info = ZoneInfo(zone)
        except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
            raise ValueError("invalid special date or timezone") from exc
        if end_date < start_date:
            raise ValueError("special date end must follow start")
        start = datetime.combine(start_date, time.min, timezone_info)
        end = datetime.combine(end_date + timedelta(days=1), time.min, timezone_info)
        return self._validate_event({"title": title, "description": description, "start_at": start.isoformat(),
                                     "end_at": end.isoformat(), "all_day": True, "location": "",
                                     "repeat_frequency": "yearly" if annual else "none", "repeat_timezone": zone})

    @staticmethod
    def _insert_import_event(db, row, category_id, source_type, source_key, source_url=""):
        now = _now()
        cursor = db.execute("""INSERT OR IGNORE INTO calendar_events
            (id,title,description,start_at,end_at,all_day,location,category_id,source_type,source_key,source_url,
             user_modified,repeat_frequency,repeat_days,repeat_until,repeat_timezone,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), row["title"], row["description"], row["start_at"], row["end_at"], row["all_day"],
             row["location"], category_id, source_type, source_key, source_url, 0,
             row["repeat_frequency"], row["repeat_days"], row["repeat_until"], row["repeat_timezone"], now, now))
        return cursor.rowcount

    def import_china_holidays(self, year):
        if type(year) is not int or year not in HOLIDAY_SCHEDULES:
            raise ValueError("official holiday schedule is available for 2025 and 2026")
        schedule = HOLIDAY_SCHEDULES[year]
        prepared = []
        for name, first, last in schedule["off"]:
            prepared.append((self._all_day_import(f"{name}（休）", first, last, False, "Asia/Shanghai"),
                             f"{year}:off:{name}"))
        for day in schedule["work"]:
            prepared.append((self._all_day_import("调休上班（班）", day, day, False, "Asia/Shanghai"),
                             f"{year}:work:{day}"))
        with self._connect() as db:
            category_id = self._import_category(db, "china_holidays", "中国大陆节假日", "#B35BD6")
            added = sum(self._insert_import_event(db, row, category_id, "china_holiday", key, schedule["source"])
                        for row, key in prepared)
        return {"year": year, "added": added, "total": len(prepared)}

    def create_special_date(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("special date must be an object")
        if type(payload.get("annual", False)) is not bool:
            raise ValueError("annual must be true or false")
        row = self._all_day_import(payload.get("title"), payload.get("date"), payload.get("date"),
                                   payload.get("annual", False), payload.get("timezone") or "UTC",
                                   payload.get("description") or "")
        with self._connect() as db:
            category = db.execute("SELECT id FROM calendar_categories WHERE id='personal'").fetchone()
            category_id = category['id'] if category else default_category(db, 'calendar_categories', '日程', 'personal')
            key = "manual:" + str(uuid.uuid4())
            self._insert_import_event(db, row, category_id, "special_date", key)
            event = db.execute("SELECT * FROM calendar_events WHERE source_type='special_date' AND source_key=?", (key,)).fetchone()
        return dict(event)

    def import_special_dates(self, content, format_name, zone):
        parsed = parse_special_dates(content, format_name)
        prepared = []
        for item in parsed:
            row = self._all_day_import(item["title"], item["date"], item["date"], item["annual"], zone,
                                       item["description"])
            identity = item["uid"] or f"{item['title']}\x00{item['date']}\x00{item['annual']}"
            key = ("uid:" if item["uid"] else "date:") + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            prepared.append((row, key))
        with self._connect() as db:
            category = db.execute("SELECT id FROM calendar_categories WHERE id='personal'").fetchone()
            category_id = category['id'] if category else default_category(db, 'calendar_categories', '日程', 'personal')
            added = sum(self._insert_import_event(db, row, category_id, "special_date", key)
                        for row, key in prepared)
        return {"added": added, "total": len(prepared)}

    def ingest_briefing_revision(self, detail, revision):
        briefing = detail["briefing"]
        briefing_id = briefing["id"] if isinstance(briefing, dict) else briefing.id
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT last_revision FROM briefing_applied WHERE briefing_id=?",
                             (briefing_id,)).fetchone()
            if row and row[0] >= revision:
                return 0
            count = self.ingest_briefing(detail, db=db)
            db.execute("""INSERT INTO briefing_applied VALUES (?,?)
                ON CONFLICT(briefing_id) DO UPDATE SET last_revision=excluded.last_revision""",
                (briefing_id, revision))
            return count

    def ingest_briefing(self, detail, db=None):
        """Legacy entry point is read-only; decisions execute via BriefingActions.

        Reclassification is never permission to delete user business records.
        Existing source links are adopted by the durable decision service.
        """
        return 0

    def _upsert_sourced_event(self, source_key, item, start, end, all_day, db=None):
        if db is None:
            with self._connect() as connection:
                return self._upsert_sourced_event(source_key, item, start, end, all_day, connection)
        preferred = "study" if item.source == "elearning" else "work"
        category_id = preferred if db.execute("SELECT 1 FROM calendar_categories WHERE id=?", (preferred,)).fetchone() else None
        row = self._validate_event({"title": item.title, "description": item.snippet,
                                    "start_at": start, "end_at": end, "all_day": all_day,
                                    "location": "", **({"category_id": category_id} if category_id else {})})
        if db.execute("SELECT 1 FROM source_tombstones WHERE source_key=?", (source_key,)).fetchone():
            return None
        old = db.execute("SELECT * FROM calendar_events WHERE source_type='briefing' AND source_key=?", (source_key,)).fetchone()
        if old and old["user_modified"]:
            return dict(old)
        row["category_id"] = old["category_id"] if old else category_id or default_category(db, "calendar_categories", "日程", "personal")
        event_id = old["id"] if old else str(uuid.uuid4())
        now = _now()
        db.execute("""INSERT INTO calendar_events
                (id,title,description,start_at,end_at,all_day,location,category_id,source_type,source_key,source_url,user_modified,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_type,source_key) DO UPDATE SET title=excluded.title,description=excluded.description,
                start_at=excluded.start_at,end_at=excluded.end_at,all_day=excluded.all_day,source_url=excluded.source_url,
                updated_at=excluded.updated_at""",
                (event_id, row["title"], row["description"], row["start_at"], row["end_at"], row["all_day"],
                 row["location"], row["category_id"], "briefing", source_key, item.url, 0, now, now))
        return dict(db.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone())


    def sync_briefing_facts(self, event_id, changes, baseline, force=False):
        """Update actual source times atomically without replacing user fields."""
        changes = {key:value for key,value in changes.items() if key in {"start_at","end_at"}}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM calendar_events WHERE id=?",(event_id,)).fetchone()
            if current is None:
                raise KeyError("event not found")
            current = dict(current)
            normalized = self._validate_event(changes,current)
            conflicts = {key:{"current":current[key],"source":normalized[key]} for key in changes
                         if current[key] != normalized[key] and current[key] != baseline.get(key)}
            if conflicts and not force:
                return current, conflicts
            if changes:
                db.execute("UPDATE calendar_events SET start_at=?,end_at=?,updated_at=? WHERE id=?",
                           (normalized["start_at"],normalized["end_at"],_now(),event_id))
            return dict(db.execute("SELECT * FROM calendar_events WHERE id=?",(event_id,)).fetchone()), {}


    def apple_prepare_calendar_rebind(self, category_id, old_group, source_id, mappings):
        from business.calendar_rebind import prepare
        return prepare(self,category_id,old_group,source_id,mappings)


    def apple_commit_calendar_rebind(self, operation_id, expected, new_category, native_items):
        from business.calendar_rebind import commit
        return commit(self,operation_id,expected,new_category,native_items)


    def apple_capture_calendar_sync(self, event_id, target_category_id=None):
        """Trusted Apple peer: read one complete target and generation atomically."""
        from business.object_generations import generations_for
        with self._connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM calendar_events WHERE id=?',(event_id,)).fetchone()
            if row is None:
                return None
            ids = sorted({x for x in (row['category_id'],target_category_id) if x})
            categories=[]
            for identity in ids:
                category=db.execute('SELECT * FROM calendar_categories WHERE id=?',(identity,)).fetchone()
                if not category: raise ValueError('calendar inbound category unavailable')
                categories.append({'row':dict(category),'local':self._apple_decorate(db,'category',category)})
            return {'version':1, 'local_id':event_id, 'generation':generations_for(db,'item',[event_id])[0][1],
                    'row':dict(row), 'local':self._apple_decorate(db,'item',row), 'categories':categories,
                    'category_generations':generations_for(db,'category',ids)}


    def apple_capture_calendar_missing(self, event_id):
        """Capture all delete-related rows in one trusted owner read transaction."""
        with self._connect() as db:
            db.execute('BEGIN')
            row=db.execute('SELECT * FROM calendar_events WHERE id=?',(event_id,)).fetchone()
            if row is None: return None
            category_id=row['category_id']
            categories=[]
            if category_id:
                category=db.execute('SELECT * FROM calendar_categories WHERE id=?',(category_id,)).fetchone()
                if not category: raise ValueError('calendar missing category unavailable')
                categories.append({'row':dict(category),'local':self._apple_decorate(db,'category',category)})
            capture={'version':1,'local_id':event_id,'generation':generations_for(db,'item',[event_id])[0][1],
                     'row':dict(row),'local':self._apple_decorate(db,'item',row),'categories':categories,
                     'category_generations':generations_for(db,'category',[category_id] if category_id else [])}
            delete=self._local_event_delete_snapshot(db,event_id,allow_apple=True,allow_recurring=True)
            return {'version':1,'capture':capture,'deletion_snapshot':delete['snapshot']}


    def apple_remove_calendar_inbound(self, expected, source_id, group_id, event_id, expected_generation, deletion_snapshot):
        """Archive and delete only the observed bound incarnation, atomically."""
        from business.apple_recovery import capture_inbound_delete
        required={'version','local_id','generation','row','local','categories','category_generations'}
        if (not isinstance(expected,dict) or set(expected)!=required or expected.get('version')!=1
                or any(not isinstance(x,str) or not x for x in (source_id,group_id,event_id,expected_generation))
                or expected['generation']!=expected_generation or not isinstance(deletion_snapshot,str) or len(deletion_snapshot)!=64):
            raise ValueError('INBOUND_CHANGED: 原日程身份无法核实，请先核对')
        identity=expected['local_id']
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone()
            if (not row or dict(row)!=expected['row'] or self._apple_decorate(db,'item',row)!=expected['local']
                    or generations_for(db,'item',[identity])[0][1]!=expected_generation):
                raise ValueError('INBOUND_CHANGED: 本地日程已变化，请先核对')
            category_ids=[entry['row']['id'] for entry in expected['categories']]
            current=[]
            for category_id in category_ids:
                cat=db.execute('SELECT * FROM calendar_categories WHERE id=?',(category_id,)).fetchone()
                if not cat: raise ValueError('INBOUND_CHANGED: 日程分类已变化，请先核对')
                current.append({'row':dict(cat),'local':self._apple_decorate(db,'category',cat)})
            if current!=expected['categories'] or generations_for(db,'category',category_ids)!=expected['category_generations']:
                raise ValueError('INBOUND_CHANGED: 日程分类已变化，请先核对')
            observed=self._local_event_delete_snapshot(db,identity,allow_apple=True,allow_recurring=True)
            if observed['snapshot']!=deletion_snapshot:
                raise ValueError('INBOUND_CHANGED: 日程关联数据已变化，请先核对')
            binding=self._apple_binding(db,'item',identity) or {}
            if (binding.get('source_id')!=source_id or binding.get('group_id')!=group_id
                    or binding.get('apple_id')!=event_id):
                raise ValueError('INBOUND_CHANGED: Apple 日程身份已变化，请先核对')
            capture_inbound_delete(db,'calendar','item',identity)
            self._delete_event(db,row)
            db.execute("DELETE FROM apple_bindings WHERE entity='item' AND local_id=?",(identity,))
            db.execute('DELETE FROM apple_completion_history WHERE local_id=?',(identity,))
        return True


    def apple_apply_calendar_inbound(self, expected, payload, native, metadata_only=False):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._apply_calendar_inbound(db, expected, payload, native, metadata_only)

    def _apply_calendar_inbound(self, db, expected, payload, native, metadata_only=False):
        """Trusted Apple peer: apply only to the exact observed incarnation."""
        from business.object_generations import generations_for
        if (not isinstance(expected,dict) or set(expected)!={'version','local_id','generation','row','local','categories','category_generations'}
                or expected['version']!=1 or not isinstance(payload,dict) or not isinstance(native,dict)
                or type(metadata_only) is not bool):
            raise ValueError('invalid calendar inbound snapshot')
        if any(not isinstance(native.get(k),str) or not native[k] for k in ('event_id','calendar_id','source_id')):
            raise ValueError('calendar inbound identity is unavailable')
        identity=expected['local_id']
        # Validation does not mutate. The transaction rechecks the entire observed state.
        validated = None if metadata_only else self._validate_event(payload,expected['row'])
        row=db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone()
        if (row is None or dict(row)!=expected['row'] or self._apple_decorate(db,'item',row)!=expected['local']
                or generations_for(db,'item',[identity])[0][1]!=expected['generation']):
            raise ValueError('INBOUND_CHANGED: 本地日程已变化，请选择保留内容')
        category_ids=[category['row']['id'] for category in expected['categories']]
        current_categories=[]
        for category_id in category_ids:
            category=db.execute('SELECT * FROM calendar_categories WHERE id=?',(category_id,)).fetchone()
            if not category: raise ValueError('INBOUND_CHANGED: 日程分类已变化，请重新确认')
            current_categories.append({'row':dict(category),'local':self._apple_decorate(db,'category',category)})
        if current_categories!=expected['categories'] or generations_for(db,'category',category_ids)!=expected['category_generations']:
            raise ValueError('INBOUND_CHANGED: 日程分类已变化，请重新确认')
        binding=self._apple_binding(db,'item',identity) or {}
        if (binding.get('group_id')!=native['calendar_id'] or
                (binding.get('source_id') and binding['source_id']!=native['source_id'])):
            raise ValueError('calendar inbound source or container changed')
        prior_native=binding.get('native') or {}
        if (binding.get('apple_id')!=native['event_id'] and
                (not prior_native.get('external_id') or prior_native.get('external_id')!=native.get('external_id'))):
            raise ValueError('calendar inbound item identity changed')
        if validated is not None:
            category=validated.get('category_id')
            if category and not db.execute('SELECT 1 FROM calendar_categories WHERE id=?',(category,)).fetchone():
                raise ValueError('calendar inbound category changed')
            modified=0 if row['source_type']=='apple_calendar' else 1
            db.execute('''UPDATE calendar_events SET title=?,description=?,start_at=?,end_at=?,all_day=?,location=?,
                category_id=?,repeat_frequency=?,repeat_days=?,repeat_until=?,repeat_timezone=?,user_modified=?,updated_at=? WHERE id=?''',
                (validated['title'],validated['description'],validated['start_at'],validated['end_at'],validated['all_day'],
                 validated['location'],category,validated['repeat_frequency'],validated['repeat_days'],validated['repeat_until'],
                 validated['repeat_timezone'],modified,_now(),identity))
            self._apple_fields(db,identity,payload,True)
        if metadata_only and payload.get('category_id') and payload['category_id'] != row['category_id']:
            if payload['category_id'] not in category_ids:
                raise ValueError('calendar target category was not captured')
            db.execute('UPDATE calendar_events SET category_id=? WHERE id=?',(payload['category_id'],identity))
        snapshot={'url':native.get('url') or '', 'alarms':native.get('alarms',[]), 'floating':bool(native.get('floating')),
                  'attendees':native.get('attendees',[]), 'native_snapshot':native, 'native_recurrence':native.get('recurrence',{}),
                  'native_series_key':native.get('series_key') or native.get('external_id')}
        if metadata_only:
            snapshot={k:v for k,v in snapshot.items() if k not in expected['local']}
        self._apple_fields(db,identity,snapshot,True)
        issues=['包含多条原生重复规则，请在 Apple 中编辑'] if (native.get('recurrence') or {}).get('rule_count',0)>1 else []
        updated={'apple_id':native['event_id'],'group_id':native['calendar_id'],'source_id':native['source_id'],
                 'can_delete':bool(native.get('writable')),'can_modify':bool(native.get('writable')) and not issues,
                 'issues':issues,'native':native,'series_key':native.get('external_id'),'floating':native.get('floating',False)}
        if binding.get('desired_title') and binding['desired_title']!=updated.get('title'):
            updated['desired_title']=binding['desired_title']
        db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)',('item',identity,json.dumps(updated,ensure_ascii=False)))
        return self._apple_decorate(db,'item',db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone())


    def _verify_calendar_move(self, db, expected, old_group, target):
        required={'version','local_id','generation','row','local','categories','category_generations'}
        if (not isinstance(expected,dict) or set(expected)!=required or expected['version']!=1
                or not isinstance(target,dict) or not target.get('writable')
                or any(not isinstance(value,str) or not value for value in (old_group,target.get('id'),target.get('source_id')))
                or old_group==target['id']):
            raise ValueError('invalid calendar move evidence')
        identity=expected['local_id']
        row=db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone()
        if (not row or dict(row)!=expected['row'] or self._apple_decorate(db,'item',row)!=expected['local']
                or generations_for(db,'item',[identity])[0][1]!=expected['generation']):
            raise ValueError('calendar move local item changed; review required')
        categories=[]
        for captured in expected['categories']:
            category=db.execute('SELECT * FROM calendar_categories WHERE id=?',(captured['row']['id'],)).fetchone()
            if not category: raise ValueError('calendar move category unavailable')
            categories.append({'row':dict(category),'local':self._apple_decorate(db,'category',category)})
        ids=[entry['row']['id'] for entry in categories]
        if categories!=expected['categories'] or generations_for(db,'category',ids)!=expected['category_generations']:
            raise ValueError('calendar move category changed; review required')
        binding=self._apple_binding(db,'item',identity) or {}
        destination=self._apple_binding(db,'category',row['category_id']) or {}
        source_categories=[entry for entry in categories if (entry['local'].get('apple') or {}).get('apple_id')==old_group]
        if (binding.get('group_id')!=old_group or binding.get('source_id')!=target['source_id']
                or destination.get('apple_id')!=target['id'] or destination.get('source_id')!=target['source_id']
                or len(source_categories)!=1 or (source_categories[0]['local'].get('apple') or {}).get('source_id')!=target['source_id']):
            raise ValueError('calendar move source or destination binding changed')
        return binding


    def apple_verify_calendar_move(self, expected, old_group, target):
        with self._connect() as db:
            db.execute('BEGIN')
            self._verify_calendar_move(db,expected,old_group,target)
        return True


    def apple_commit_calendar_move(self, operation_id, expected, old_group, target, native):
        validate_operation_id(operation_id)
        if (not isinstance(native,dict) or not native.get('event_id') or native.get('calendar_id')!=target.get('id')
                or native.get('source_id')!=target.get('source_id')):
            raise ValueError('calendar move native identity changed')
        arguments={'expected':expected,'old_group':old_group,'target':target,'native':native}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt=read_receipt(db,operation_id,'calendar_move',arguments)
            identity=expected['local_id']
            if receipt:
                binding=self._apple_binding(db,'item',identity) or {}
                if (generations_for(db,'item',[identity])[0][1]!=expected['generation']
                        or binding.get('group_id')!=target['id'] or binding.get('apple_id')!=native['event_id']):
                    raise ValueError('calendar move receipt conflicts with current incarnation')
                return {**json.loads(receipt['targetId']),'replayed':True}
            binding=self._verify_calendar_move(db,expected,old_group,target)
            updated={**binding,'apple_id':native['event_id'],'group_id':native['calendar_id'],'source_id':native['source_id'],
                     'native':native,'series_key':native.get('external_id'),'floating':native.get('floating',False)}
            self._apple_fields(db,identity,{'native_snapshot':native,
                               'native_series_key':native.get('series_key') or native.get('external_id')},True)
            db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)',('item',identity,json.dumps(updated,ensure_ascii=False)))
            local=self._apple_decorate(db,'item',db.execute('SELECT * FROM calendar_events WHERE id=?',(identity,)).fetchone())
            result={'operation_id':operation_id,'generation':expected['generation'],'local':local,'replayed':False}
            save_receipt(db,operation_id,'calendar_move',arguments,json.dumps(result,ensure_ascii=False,sort_keys=True))
            return result
