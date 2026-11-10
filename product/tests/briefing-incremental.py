"""Replay the daily collection contract with real SQLite commits and a deterministic model."""

from pathlib import Path
import tempfile
from datetime import timedelta
import unittest
from unittest.mock import patch
from uuid import uuid4
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages/alcor-secretary"))
from business.worker import Business
from business.briefing.domain.window import instant


class Incremental(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.business = Business(self.tmp.name)
        self.business.briefing.update_preferences({"time_zone": "Asia/Shanghai"})
        self.scope = {"source": "email", "accountId": "a"}

    def collect(self, end, rows=(), request=None, cursor=None):
        request = request or str(uuid4())
        self.prompts = []
        record = {"date": end[:10], "notes": "", "kind": "briefing", "sources": []}
        submissions = []
        while True:
            result = self.business.generated_briefing(
                record,
                request,
                [{"source": "email", "account_id": "a", "items": rows, "cursors": cursor or {}}],
                submissions,
                run_at=end,
                configurations=[self.scope],
            )
            if "record" in result:
                return result["record"], self.business.briefing.store.collection(request)
            model = result["model"]
            self.prompts.append(model)
            entries = model["entries"]
            fields = model["schema"]["properties"]
            submission = {}
            if "decisions" in fields:
                submission["decisions"] = [
                    dict(id=row["id"], selected=True, reason="通知", section="fyi", score=50, summary=row["title"])
                    for row in entries
                ]
            if "reports" in fields:
                ids = (
                    [row["id"] for row in entries]
                    if "decisions" in fields
                    else fields["source_item_ids"]["items"]["enum"]
                )
                titles = "、".join(row["title"] for row in entries)
                submission.update(
                    source_item_ids=ids,
                    body=titles,
                    reports=[
                        {"id": i, "content": next((row["title"] for row in entries if row["id"] == i), titles)}
                        for i in ids
                    ],
                )
            submissions.append(submission)

    @staticmethod
    def row(identity, at, **fields):
        return dict({"source_id": identity, "title": identity, "snippet": identity, "occurred_at": at}, **fields)

    def test_continuous_days_noop_and_restart(self):
        previous, _ = self.collect("2026-11-10T22:00:00+08:00", [self.row("昨天", "2026-11-10T20:00:00+08:00")])
        first, one = self.collect(
            "2026-11-11T08:00:00+08:00",
            [self.row("边界重复", "2026-11-10T22:00:00+08:00"), self.row("新闻", "2026-11-11T08:00:00+08:00")],
        )
        self.assertEqual(instant(one["window"]["start"]), instant("2026-11-10T22:00:00+08:00"))
        self.assertEqual([r["source_id"] for r in first["business"]["snapshot"]["mail"]], ["新闻"])
        self.assertNotEqual(previous["business"]["briefingId"], first["business"]["briefingId"])
        before = first["business"]["snapshot"]["briefing"]["generated_at"]
        second, two = self.collect("2026-11-11T09:00:00+08:00")
        self.assertTrue(two["unchanged"])
        self.assertEqual(two["revision"], 1)
        self.assertEqual(self.prompts, [])
        self.assertEqual(before, second["business"]["snapshot"]["briefing"]["generated_at"])
        self.business.meta.close()
        self.business = Business(self.tmp.name)
        third, three = self.collect("2026-11-11T12:00:00+08:00", [self.row("中午", "2026-11-11T11:30:00+08:00")])
        self.assertEqual(instant(three["window"]["start"]), instant("2026-11-11T09:00:00+08:00"))
        self.assertEqual(len(third["business"]["snapshot"]["items"]), 2)
        self.assertEqual(len(self.business.briefing.list_briefings()), 2)
        self.assertEqual(three["revision"], 2)
        self.assertEqual(self.business.briefing_runs.invoke("result", one["id"])["business"]["revision"], 1)

    def test_rule_changes_reconsider_unchanged_candidates(self):
        stamp = '2026-11-11T07:00:00+08:00'
        self.collect('2026-11-11T08:00:00+08:00', [self.row('公告', stamp)])
        self.collect('2026-11-11T09:00:00+08:00', [])
        self.assertEqual(self.prompts, [])
        rule = self.business.briefing.create_rule({'source':'email', 'text':'保留公告，每条信息一张卡片。'})
        result, _ = self.collect('2026-11-11T10:00:00+08:00', [])
        self.assertTrue(any('decisions' in prompt['schema']['properties'] for prompt in self.prompts))
        self.assertTrue(all(rule.text in prompt['rules'] for prompt in self.prompts))
        self.assertEqual(result['business']['snapshot']['items'][0]['title'], '公告')
        self.business.briefing.update_rule(rule.id, {'text':'保留公告和文件，每条信息一张卡片。'})
        self.collect('2026-11-11T11:00:00+08:00', [])
        self.assertTrue(any('decisions' in prompt['schema']['properties'] for prompt in self.prompts))
        self.assertTrue(all('保留公告和文件' in prompt['rules'] for prompt in self.prompts))

    def test_midnight_first_gap_and_bounds(self):
        old = self.row("旧邮件", "2026-11-07T21:45:42+08:00")
        midnight = self.row("午夜", "2026-11-08T00:00:00+08:00")
        _, one = self.collect("2026-11-09T08:00:00+08:00", [old, midnight])
        self.assertEqual(one["stats"]["outside"], 1)
        self.assertEqual(one["stats"]["accepted"], 1)
        for end in ("2026-11-11T08:00:00+08:00", "2026-11-19T08:00:00+08:00"):
            result, receipt = self.collect(end, [old])
            self.assertTrue(receipt["window"]["clipped"])
            self.assertEqual(instant(receipt["window"]["start"]), instant(end) - timedelta(hours=32))
            self.assertEqual(result["business"]["snapshot"]["mail"], [])
            self.assertEqual(self.prompts, [])

    def test_atomic_rollback_lost_ack_and_replay(self):
        _, base = self.collect("2026-11-10T22:00:00+08:00")
        stamp = "2026-11-11T08:00:00+08:00"
        request = str(uuid4())
        values = [self.row("新条目", stamp)]
        with patch.object(self.business.briefing.store, "_archive", side_effect=RuntimeError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                self.collect(stamp, values, request)
        self.assertEqual(self.business.briefing.store.checkpoint(), base["window"]["end"])
        self.assertIsNone(self.business.briefing.store.collection(request))
        self.assertIsNone(self.business.briefing.briefing_for_date("2026-11-11"))
        with patch.object(self.business, "collection_result", side_effect=RuntimeError("ack lost")):
            with self.assertRaisesRegex(RuntimeError, "ack lost"):
                self.collect(stamp, values, request)
        self.business.meta.close()
        self.business = Business(self.tmp.name)
        result, receipt = self.collect(stamp, values, request)
        self.assertEqual(self.prompts, [])
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(result["business"]["snapshot"]["items"][0]["title"], "新条目")

    def test_merge_thread_and_preserve_unrelated_report(self):
        one = self.row(
            "初次通知",
            "2026-11-11T07:00:00+08:00",
            thread_id="meeting",
            url="https://example.invalid/first",
            payload={"body": "ORIGINAL_BODY_MARKER"},
        )
        other = self.row("不相关项目", "2026-11-11T07:15:00+08:00")
        first, _ = self.collect("2026-11-11T08:00:00+08:00", [one, other])
        updated = self.row(
            "会议改期", "2026-11-11T08:50:00+08:00", thread_id="meeting", url="https://example.invalid/change"
        )
        second, _ = self.collect("2026-11-11T09:00:00+08:00", [updated, updated])
        items = second["business"]["snapshot"]["items"]
        self.assertEqual(len(items), 2)
        old_other = next(x for x in first["business"]["snapshot"]["items"] if x["source_id"] == "不相关项目")
        self.assertEqual(next(x for x in items if x["source_id"] == "不相关项目")["payload"], old_other["payload"])
        event = next(x for x in items if x["thread_id"] == "meeting")
        self.assertEqual({m["url"] for m in event["payload"]["_briefing_members"]}, {one["url"], updated["url"]})
        self.assertEqual([x["source_id"] for x in self.prompts[0]["entries"]], ["会议改期"])
        self.assertNotIn("ORIGINAL_BODY_MARKER", str(self.prompts))
        self.assertNotIn("不相关项目", str(self.prompts))

    def test_legacy_daily_view_filters_the_report_without_rewriting_its_revision(self):
        from copy import deepcopy
        from business.briefing.domain.incremental import bounded_view

        def mail(id, date):
            return dict(
                id=id,
                source="email",
                account_id="a",
                source_id=id,
                thread_id="meeting",
                category="selected",
                occurred_at=date,
                title=id,
                sender="sender",
                url="",
                metadata={},
                decision={"summary": id},
            )

        old = mail("old", "2026-11-07T21:45:42+08:00")
        valid = mail("new", "2026-11-08T10:00:00+08:00")
        card = {
            **old,
            "source_item_id": "old",
            "section": "fyi",
            "snippet": "old",
            "payload": {"_briefing_members": [old, valid]},
        }
        detail = {
            "briefing": {"id": "brf-2026-11-09", "mail_snapshot_available": True, "body": "old"},
            "mail": [old, valid],
            "items": [card],
            "preferences": {"time_zone": "Asia/Shanghai"},
        }
        previous = deepcopy(detail)
        view = bounded_view(detail)
        self.assertEqual(view["mail"], [valid])
        self.assertEqual(view["items"][0]["title"], "new")
        self.assertTrue(view["items"][0]["payload"]["_preview_only"])
        self.assertNotIn("old", view["briefing"]["body"])
        self.assertEqual(detail, previous)

    def test_late_mail_and_future_cursor(self):
        cursor = {"imap_uid": "10", "imap_uidvalidity": "1"}
        self.collect("2026-11-11T08:00:00+08:00", cursor=cursor)
        late = self.row("迟到", "2026-11-10T15:00:00+08:00")
        old = self.row("过期", "2026-11-09T15:00:00+08:00")
        future = self.row("以后", "2026-11-11T11:00:00+08:00")
        result, receipt = self.collect(
            "2026-11-11T09:00:00+08:00", [late, old, future], cursor={**cursor, "imap_uid": "13"}
        )
        rows = result["business"]["snapshot"]["mail"]
        self.assertEqual([x["source_id"] for x in rows], ["迟到"])
        self.assertEqual(rows[0]["occurred_at"], late["occurred_at"])
        self.assertTrue(rows[0]["metadata"]["observed_change_at"])
        self.assertEqual(receipt["stats"]["deferred"], 1)
        self.assertEqual(self.business.source_cursors("email", "a", self.scope)["imap_uid"], "10")
        final, _ = self.collect(
            "2026-11-11T12:00:00+08:00", [late, old, future], cursor={**cursor, "imap_uid": "13"}
        )
        self.assertEqual({x["source_id"] for x in final["business"]["snapshot"]["mail"]}, {"迟到", "以后"})


if __name__ == "__main__":
    unittest.main()
