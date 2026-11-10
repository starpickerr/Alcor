"""Simplified briefing run: accept pre-fetched items, persist one snapshot.

The Core pipeline fetches mail/eLearning with credentials and drafts with an LLM.
Neither belongs in this plugin, so the host passes pre-fetched ``sources`` (and,
optionally, a pre-drafted ``draft``). Without a draft a deterministic, model-free
composer keeps every candidate. Cursors advance only for fully refreshed sources
and only after a non-failed snapshot is saved.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from .instructions import DEFAULT_RULE_TEXT, rule_document
from .service import BriefingService


def briefing_title(briefing_date: str) -> str:
    day = date.fromisoformat(briefing_date)
    return f"{day.year}年{day.month}月{day.day}日·简报"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compose_draft(rows, rules, briefing_date: str) -> dict:
    """Deterministic fallback drafter: select every candidate as an FYI card."""
    documents = [f"来源：{rule.source}\n{rule_document(rule)}" for rule in rules if rule.enabled]
    rule_text = DEFAULT_RULE_TEXT + (
        "\n\n## 用户规则文稿（优先于默认原则）\n\n" + "\n\n---\n\n".join(documents) if documents else "")
    mail, cards, lines = [], [], []
    ordered = sorted(rows, key=lambda row: (_parse(row[0].occurred_at), row[0].id), reverse=True)
    for index, (item, email) in enumerate(ordered):
        entry = {
            "id": item.id, "source": item.source, "account_id": item.account_id,
            "source_id": item.source_id, "thread_id": item.thread_id, "sender": item.sender,
            "sender_email": email.sender_email if email else "", "list_id": email.list_id if email else "",
            "title": item.title, "snippet": item.snippet, "occurred_at": item.occurred_at, "url": item.url,
            "body": str(item.payload.get("body") or item.snippet),
            "metadata": {k: v for k, v in item.payload.items() if k != "body"},
            "body_available": "body" in item.payload,
        }
        decision = {"id": item.id, "selected": True, "reason": "未配置筛选模型，保留全部候选",
                    "section": "fyi", "score": 50, "summary": item.snippet or item.title}
        mail.append({**entry, "category": "selected", "filter_reason": decision["reason"], "decision": decision})
        cards.append({
            "source_item_id": item.id, "section": "fyi", "score": 50, "snippet": decision["summary"],
            "sort_order": index, "source": item.source, "account_id": item.account_id,
            "source_id": item.source_id, "thread_id": item.thread_id, "sender": item.sender,
            "title": item.title, "occurred_at": item.occurred_at, "url": item.url,
            "payload": {**item.payload, "_briefing_reason": decision["reason"]},
        })
        lines.append(f"- {item.title or item.source_id}（{item.sender or item.source}）")
    body = "\n".join(lines) if lines else "本时间窗口内没有新的候选信息。"
    return {"title": briefing_title(briefing_date), "body": body, "items": cards, "mail": mail,
            "rule_text": rule_text}


class BriefingPipeline:
    def __init__(self, service: BriefingService, clock=None):
        self.service = service
        self.clock = clock or (lambda: datetime.now().astimezone())

    def run(self, *, briefing_date: Optional[str] = None, operation: str = "upsert",
            trigger: str = "manual", sources: Optional[list] = None, draft: Optional[dict] = None,
            run_at: Optional[str] = None, acquire_lock: bool = True) -> dict:
        if not sources:
            raise ValueError("no briefing sources configured")
        if operation not in {"create", "update", "upsert"}:
            raise ValueError("operation must be create or update")
        if trigger not in {"manual", "scheduled"}:
            raise ValueError("invalid briefing trigger")
        for row in sources:
            if not isinstance(row, dict) or not row.get("source"):
                raise ValueError("each source entry needs a source name")
        store = self.service.store
        if acquire_lock and not store.run_lock.acquire(blocking=False):
            raise ValueError("简报正在生成，请等待当前运行完成后刷新")
        try:
            from .window import collection_window
            finish = run_at or _iso(self.clock())
            window = collection_window(finish, store.checkpoint(), self.service.get_preferences().get('time_zone'))
            day = window['day']
            existing = self.service.briefing_for_date(day)
            if operation == "create" and existing and existing.generated_at:
                return self.service.get_briefing(existing.id)
            if operation == "update" and (not existing or not existing.generated_at):
                raise ValueError("generate this day's briefing before updating it")
            return self._run(window, trigger, sources, draft)
        finally:
            if acquire_lock:
                store.run_lock.release()

    def _run(self, window, trigger, sources, draft):
        from .incremental import collection_stats, daily_candidates, commit_daily

        service = self.service
        day = window['day']
        current = service.briefing_for_date(day)
        detail = service.get_briefing(current.id) if current else {}
        if detail:
            detail = {**detail, 'briefing':asdict(detail['briefing']), 'items':[asdict(item) for item in detail['items']]}
        configs = [{'source':src['source'],'accountId':src.get('account_id') or 'default'} for src in sources]
        stats = collection_stats()
        candidates = daily_candidates(service,sources,configs,detail,window,stats,supplied_ids=True)
        stored = compose_draft(list(candidates.rows.values()), service.list_rules(enabled_only=True), day)
        if draft is not None:
            stored.update(draft)
        record = {'kind':'briefing','date':day,'notes':'','sources':[]}
        commit_daily(service,record,str(uuid4()),window,current,detail,stored,candidates,stats,trigger)
        return service.get_briefing(current.id if current else 'brf-'+day)
