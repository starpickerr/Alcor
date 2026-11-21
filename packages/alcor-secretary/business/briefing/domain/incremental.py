"""Reconcile one bounded batch with the selected sources of the same daily snapshot."""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid5, NAMESPACE_URL
import json
from .generator import thread_key, report_body
from .window import instant, in_window, previous_midnight


def activity(row):
    meta = row.get("metadata", row.get("payload", {}))
    return instant(meta.get("observed_change_at") or meta.get("source_updated_at") or row["occurred_at"])


def fingerprint(row):
    fields = (
        "source",
        "account_id",
        "source_id",
        "thread_id",
        "sender",
        "sender_email",
        "list_id",
        "title",
        "snippet",
        "url",
        "body",
    )
    return (
        tuple(row.get(key, "") for key in fields),
        "" if row["source"] == "local" else row["occurred_at"],
        {k: v for k, v in row.get("metadata", {}).items() if k not in ("collected_at", "observed_change_at")},
    )


def saved_thread(row):
    meta = row.get("metadata", row.get("payload", {}))
    return row["source"], row["account_id"], meta.get("entity_id") or row["thread_id"] or row["source_id"]


def reusable_reports(detail, rows, permitted, decisions):
    old_members, new_members = defaultdict(set), defaultdict(set)
    for row in detail.get("mail", []):
        old_members[saved_thread(row)].add(row["id"])
    for identity, (item, _) in rows.items():
        new_members[thread_key(item)].add(identity)
    previous, reusable = {}, {}
    for card in detail.get("items", []):
        identity = card["source_item_id"]
        text = card.get("payload", {}).get("_briefing_report")
        if identity not in permitted or not text or card["payload"].get("_preview_only"):
            continue
        key = saved_thread(card)
        if not old_members[key] <= permitted:
            continue
        previous[key] = text
        if old_members[key] == new_members[key] and old_members[key] <= decisions.keys():
            reusable[identity] = text
    return previous, reusable


def source_scope(configuration):
    return json.dumps(
        {key: configuration.get(key) for key in ("source", "accountId", "tool", "method", "arguments")},
        sort_keys=True,
        ensure_ascii=False,
    )


def source_window(window, pending, configuration):
    gap = pending.get(configuration["source"] + ":" + configuration["accountId"])
    if gap and gap["scope"] == source_scope(configuration):
        if instant(gap["start"]) < instant(window["floor"]):
            return {"start": window["floor"], "include_start": True}
        return {"start": gap["start"], "include_start": gap["include_start"]}
    return {"start": window["start"], "include_start": window["include_start"]}


def source_cursors(store, source, account, configuration=None):
    with store._conn() as db:
        cursors = dict(
            db.execute(
                "SELECT cursor_key,cursor_value FROM sync_state WHERE source=? AND account_id=?", (source, account)
            )
        )
    scope = cursors.pop("_briefing_scope", None)
    return {} if configuration is not None and scope != source_scope(configuration) else cursors


def collection_stats():
    return {key: 0 for key in ("received", "outside", "unchanged", "accepted", "deferred")}


@dataclass
class DailyCandidates:
    rows: dict
    changed_sources: dict
    sources: list
    pending_windows: dict
    unchanged: set
    decisions: dict
    reports: dict
    previous_reports: dict


def daily_candidates(service, selected, configurations, detail, window, stats, *, supplied_ids=False):
    stamp = window["end"]
    configured = [row for row in selected if row["source"] != "local"]
    if configured and all(row.get("errors") for row in configured):
        raise ValueError("来源采集失败：" + "；".join(str(row["errors"][0]) for row in configured))
    pending = service.store.pending_source_windows()
    preserved, unchanged = {}, set()
    rows = {}
    normalized = []
    prior_ids = set()
    catalog = {}
    for index, source in enumerate(selected):
        collected_at = source.get("collected_at")
        before = dict(stats)
        account = source.get("account_id") or "default"
        configuration = (
            configurations[index]
            if index < len(configurations)
            else {"source": source["source"], "accountId": account}
        )
        scope = source_scope(configuration)
        key = source["source"] + ":" + account
        interval = source_window(window, pending, configuration)
        start = interval["start"]
        entry_window = {**window, **interval}
        coverage = dict(source=source["source"], account_id=account, scope=scope,
                        name=configuration.get("label") or source.get("name") or source["source"], start=start, end=stamp)
        if source.get("errors"):
            pending[key] = {"scope": scope, **interval}
            normalized.append({**coverage, "uncovered": True})
            continue
        pending.pop(key, None)
        previous_window = (detail.get("briefing", {}).get("source_windows") or {}).get(key, {})
        prior = (
            {
                row["id"]: row
                for row in detail.get("mail", [])
                if row["source"] == source["source"] and row["account_id"] == account
            }
            if previous_window.get("scope") == scope
            else {}
        )
        # Remove pre-upgrade leakage on the next current-day update.
        prior = {identity: row for identity, row in prior.items() if activity(row) >= instant(window["floor"])}
        prior_ids.update(prior)
        for row in prior.values():
            candidate = service.build_source_item(
                {
                    **row,
                    "payload": {
                        **row.get("metadata", {}),
                        **({"body": row["body"]} if row.get("body_available", True) else {}),
                    },
                }
            )
            rows[candidate[0].id] = candidate
            unchanged.add(candidate[0].id)
        cursors = dict(source.get("cursors") or {})
        previous_cursors = source_cursors(service.store, source["source"], account, configuration)
        late_mail = (
            source["source"] == "email"
            and previous_cursors.get("imap_uid")
            and previous_cursors.get("imap_uidvalidity") == cursors.get("imap_uidvalidity")
            and instant(previous_cursors.get("until") or start) >= instant(window["floor"])
            and int(cursors.get("imap_uid") or 0) > int(previous_cursors["imap_uid"])
        )
        deferred = False
        for item in source.get("items") or []:
            stats["received"] += 1
            identity = json.dumps([source["source"], account, item["source_id"]], ensure_ascii=False)
            payload = {
                **item,
                "source": source["source"],
                "account_id": account,
                "id": (item.get("id") if supplied_ids else None) or "src-" + str(uuid5(NAMESPACE_URL, identity)),
            }
            metadata = {
                k: v
                for k, v in (item.get("payload") or {}).items()
                if k not in ("collected_at", "observed_change_at", "time_kind")
            }
            occurred = item["occurred_at"]
            changed = (
                item.get("source_updated_at")
                or item.get("updated_at")
                or metadata.get("source_updated_at")
                or metadata.get("updated_at")
                or occurred
            )
            published = item.get("published_at") or metadata.get("published_at")
            if published:
                metadata["published_at"] = published
            if changed != occurred or published:
                metadata["source_updated_at"] = changed
            # Connectors without a source timestamp may explicitly report an observation at T.
            if source["source"] == "elearning" and instant(occurred) == instant(stamp) and not published:
                metadata["time_kind"] = "observed"
            candidate = service.build_source_item({**payload, "payload": metadata})
            obj, email = candidate
            old = prior.get(obj.id)
            entry = {
                **asdict(obj),
                "metadata": {k: v for k, v in metadata.items() if k != "body"},
                "body": str(metadata.get("body") or obj.snippet),
                "sender_email": email.sender_email if email else "",
                "list_id": email.list_id if email else "",
            }
            previous_item = catalog.get(obj.id)
            known_update = (
                (previous_item[0].payload.get("source_updated_at") or previous_item[0].occurred_at)
                if previous_item
                else old and (old.get("metadata", {}).get("source_updated_at") or old["occurred_at"])
            )
            if known_update and instant(changed) < instant(known_update):
                stats["outside"] += 1
                continue
            if old and fingerprint(entry) == fingerprint(old):
                stats["unchanged"] += 1
                continue
            if instant(changed) > instant(stamp):
                deferred = True
                stats["deferred"] += 1
                continue
            # A changed, already known resource with no new server timestamp is an
            # observed correction. A newly delivered IMAP UID is evidence of late arrival.
            if not in_window(changed, entry_window):
                if instant(changed) < instant(window["floor"]) or not (old or late_mail):
                    stats["outside"] += 1
                    continue
                if not old and late_mail:
                    previous = service.store.get_source_item_by_key(obj.source, obj.account_id, obj.source_id)
                    if previous:
                        known, sender = previous
                        known_entry = {
                            **asdict(known),
                            "metadata": {k: v for k, v in known.payload.items() if k != "body"},
                            "body": str(known.payload.get("body") or known.snippet),
                            "sender_email": sender.sender_email if sender else "",
                            "list_id": sender.list_id if sender else "",
                        }
                        if fingerprint(entry) == fingerprint(known_entry):
                            stats["unchanged"] += 1
                            continue
                obj.payload["observed_change_at"] = collected_at or stamp
            if collected_at:
                obj.payload["collected_at"] = collected_at
            rows[obj.id] = catalog[obj.id] = candidate
            unchanged.discard(obj.id)
            stats["accepted"] += 1
        if deferred:
            # Do not consume transport tokens past a future-dated row.
            cursors = previous_cursors
        normalized.append(
            {
                **coverage,
                "cursors": {**cursors, "until": stamp, "_briefing_scope": scope},
            }
        )
        stats.setdefault("by_source", {})[key] = {
            "pages": source.get("pages", 1),
            **{k: stats[k] - before[k] for k in ("received", "accepted", "unchanged", "outside", "deferred")},
        }
        preserved.update(
            {
                identity: row["decision"]
                for identity, row in prior.items()
                if identity in unchanged and row.get("decision")
            }
        )
    prior_reports, reports = reusable_reports(detail, rows, prior_ids, preserved)
    return DailyCandidates(rows, catalog, normalized, pending, unchanged, preserved, reports, prior_reports)


def bounded_view(detail):
    """Read pre-upgrade snapshots within their date's floor; revisions stay verbatim."""
    if not detail or not detail["briefing"].get("mail_snapshot_available"):
        return detail
    report = detail["briefing"]
    floor = previous_midnight(report["id"][4:14], detail.get("preferences", {}).get("time_zone"))
    mail = [row for row in detail["mail"] if activity(row) >= floor]
    if len(mail) == len(detail["mail"]):
        return detail
    allowed = {r["id"]: r for r in mail}
    cards = []
    for card in detail["items"]:
        members = card["payload"].get("_briefing_members") or [{"id": card["source_item_id"]}]
        surviving = [
            allowed[m["id"]]
            for m in members
            if m["id"] in allowed and allowed[m["id"]].get("category") == "selected"
        ]
        if not surviving:
            continue
        latest = max(surviving, key=activity)
        mixed = len(surviving) != len(members)
        updated = dict(card)
        if mixed:
            updated.update({key: latest[key] for key in ("title", "sender", "url", "occurred_at", "source_id")})
            updated["snippet"] = latest["decision"]["summary"]
        updated["payload"] = {
            **(dict(latest.get("metadata", {})) if mixed else card["payload"]),
            **({"_preview_only": True} if mixed else {}),
            "_briefing_report": updated["snippet"]
            if mixed
            else card["payload"].get("_briefing_report") or updated["snippet"],
            "_briefing_members": [m for m in members if m["id"] in allowed],
        }
        cards.append(updated)
    return {
        **detail,
        "items": cards,
        "mail": mail,
        "briefing": {
            **report,
            "fetched_count": len(mail),
            "kept_count": sum(r["category"] == "selected" for r in mail),
            "filtered_count": sum(r["category"] == "filtered" for r in mail),
            "body": report_body(cards),
        },
    }


def commit_daily(service, record, request_id, window, current, detail, draft, candidates, stats, trigger):
    normalized = candidates.sources
    start, stamp = window["start"], window["end"]
    windows = {row["source"] + ":" + row["account_id"]: row for row in normalized}
    failed = {key for key, row in windows.items() if row.get("uncovered")}
    keys = windows.keys() - failed
    old_windows = detail.get("briefing", {}).get("source_windows", {})
    status = "partial" if failed else "completed"
    names = {"email": "邮件", "elearning": "eLearning"}
    warnings = []
    for key in sorted(failed):
        row = windows[key]
        label = names.get(row["source"], row["name"]) + "未覆盖"
        if sum(other["source"] == row["source"] for other in normalized) > 1:
            account = row["name"] if row["name"] != row["source"] else row["account_id"]
            label += "（" + account + "）"
        warnings.append(label)
    warning = "、".join(dict.fromkeys(warnings))
    same_scopes = all(
        old_windows.get(row["source"] + ":" + (row.get("account_id") or "default"), {}).get("scope")
        == row["scope"]
        for row in normalized
    )
    old_visible = {row["id"] for row in detail.get("mail", []) if row["source"] + ":" + row["account_id"] in keys}
    unchanged_day = bool(
        current
        and current.status == status
        and current.error_message == warning
        and all(bool(old_windows.get(k, {}).get("uncovered")) == (k in failed) for k in windows)
        and current.rule_text == draft["rule_text"]
        and same_scopes
        and old_visible == candidates.rows.keys() == candidates.unchanged
        and not stats["accepted"]
    )

    # Keep unsampled sources on the day without passing their text to this Agent.
    def carried(row):
        key = row["source"] + ":" + row["account_id"]
        same_scope = key not in failed or old_windows.get(key, {}).get("scope") == windows[key]["scope"]
        return key not in keys and same_scope and activity(row) >= instant(window["floor"])

    safe_detail = bounded_view(detail)
    carry_mail = [row for row in safe_detail.get("mail", []) if carried(row)]
    carry_cards = [row for row in safe_detail.get("items", []) if carried(row)]
    if len(old_visible) + len(carry_mail) != len(detail.get("mail", [])):
        unchanged_day = False
    scoped_body = draft["body"]
    if unchanged_day and not carry_mail:
        scoped_body = current.body
    mail = [*draft["mail"], *carry_mail]
    cards = [*draft["items"], *carry_cards]
    if carry_mail:
        cards.sort(
            key=lambda c: (
                c["section"] != "action",
                -c["score"],
                -instant(c["occurred_at"]).timestamp(),
                c["source_item_id"],
            )
        )
        for i, card in enumerate(cards):
            card["sort_order"] = i
        body = report_body(cards)
    else:
        body = scoped_body
    source_windows = dict(old_windows)
    updates = []
    for key, row in windows.items():
        source_windows[key] = {k: v for k, v in row.items() if k != "cursors"}
        updates.extend((row["source"], row["account_id"], k, str(v)) for k, v in row.get("cursors", {}).items())
    receipt = dict(
        id=request_id,
        record=record,
        window=window,
        stats=stats,
        sources=sorted(keys),
        uncovered=sorted(failed),
        pending_windows=candidates.pending_windows,
        body=scoped_body if scoped_body != body else None,
    )
    service.complete_briefing(
        current.id if current else "brf-" + record["date"],
        {
            "trigger": trigger,
            "status": status,
            "run_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "title": draft["title"],
            "body": body,
            "items": cards,
            "mail": mail,
            "fetched_count": len(mail),
            "kept_count": sum(r["category"] == "selected" for r in mail),
            "filtered_count": sum(r["category"] == "filtered" for r in mail),
            "rule_text": draft["rule_text"],
            "window_start": start,
            "window_end": stamp,
            "source_windows": source_windows,
            "updated_sources": sorted(keys),
            "error_message": warning,
            "cursor_updates": updates,
        },
        collection=receipt,
        source_items=candidates.changed_sources.values(),
        unchanged=unchanged_day,
    )
