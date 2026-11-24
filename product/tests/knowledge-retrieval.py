"""Deterministic storage/retrieval regressions; on macOS also exercise the real local encoder."""

import base64
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages/alcor-secretary"))
from business.knowledge import Knowledge
from business.knowledge_index import encode


def check(root):
    store = Knowledge(root / "library.sqlite3")

    def call(op, **args):
        return store.invoke(op, args, None)

    try:
        content = base64.b64encode(b"%PDF-1.7 synthetic fixture").decode()
        # Duplicate filenames and overlapping pages must never change the provenance.
        paragraphs = "本课程共有四个单元，每个单元有一次讨论课。\n" * 56
        args = dict(
            title="课程手册",
            collection="秋季课程",
            filename="课程.pdf",
            kind="pdf",
            content=content,
            pages=["目录", paragraphs + "算法实验编号 CS204X，提交窗口为周三下午。"],
        )
        a = call("store_file", **args)
        assert call("store_file", **args)["id"] == a["id"]
        b = call("store_file", **(args | {"title": "存档副本", "collection": "参考档案"}))
        assert b["id"] != a["id"]
        assert store.db.execute("select count(*) from assets").fetchone()[0] == 1
        assert store.db.execute("select count(*) from asset_pages").fetchone()[0] == 2
        rows = call("search", query="CS204X", collection="秋季课程")["items"]
        hit = rows[0]
        assert hit["id"] == a["id"] and hit["page"] == 2 and hit["start"] > 0, hit
        body = call("read", id=hit["id"], page=hit["page"], revision=hit["revision"])["text"]
        assert body[hit["start"] :].startswith(hit["snippet"]) and "CS204X" in hit["snippet"]
        assert all(row["id"] == a["id"] for row in rows)
        assert call("collections") == ["参考档案", "秋季课程"]
        before = call("save", title="签发流程", text="凭票据 AC330 到前台登记。")
        hit = call("search", query="AC330")["items"][0]
        after = call(
            "edit",
            id=before["id"],
            revision=before["revision"],
            title="签发流程",
            text="改凭票据 KB773 线上登记。",
        )
        assert "AC330" in call("read", id=hit["id"], revision=hit["revision"])["text"]
        assert "KB773" in call("read", id=after["id"])["text"]
        assert all("AC330" not in r["snippet"] for r in call("search", query="AC330")["items"])
        call("reindex", id=a["id"])
        assert call("search", query="CS204X", collection="秋季课程")["items"][0]["id"] == a["id"]
        before_ids = list(
            store.db.execute("SELECT id FROM chunks WHERE source_id=? AND page>0 ORDER BY id", (a["id"],))
        )
        a = call("edit", id=a["id"], revision=a["revision"], title=a["title"], text="重点复习图论")
        after_ids = list(
            store.db.execute("SELECT id FROM chunks WHERE source_id=? AND page>0 ORDER BY id", (a["id"],))
        )
        assert before_ids and before_ids == after_ids
        corpus = [
            ("登录指南", "忘记登录口令，可以通过绑定的邮箱重新设置凭证。"),
            ("考试范围", "微积分期末仅考前三章，矩阵乘法不在考试范围。"),
            ("交通公告", "轮渡每晚从七号码头出发。"),
            ("健身记录", "这周安排了跑步活动。"),
            ("Cooking", "Roast the potatoes with fresh rosemary and olive oil."),
            ("Access", "If you forgot your password, request a reset link via email."),
        ]
        for title, text in corpus:
            call("save", title=title, text=text, collection="评测")
        if sys.platform == "darwin" and all(encode(lang, []) for lang in ("en", "zh-Hans")):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if len(store.semantic.models) == 2 and all(
                    store.db.execute(
                        """SELECT count(*) FROM chunks c
                    WHERE c.lang=? AND NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.model=? AND e.digest=c.digest)""",
                        (lang, model),
                    ).fetchone()[0]
                    == 0
                    for lang, model in store.semantic.models.copy().items()
                ):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Embeddings did not finish")
            for question, answer in [
                ("找回账户密码", "登录指南"),
                ("Locked out of my account", "Access"),
            ]:
                rows = call("search", query=question, collection="评测")["items"]
                assert rows and rows[0]["title"] == answer, (question, rows)
                print("Local semantic retrieval:", question, "->", rows[0]["title"])
        for source, remaining in ((a, 1), (b, 0)):
            archived = call("archive", id=source["id"], revision=source["revision"])
            call("erase_archived", id=source["id"], revision=archived["revision"])
            assert store.db.execute("SELECT count(*) FROM assets").fetchone()[0] == remaining
        assert store.db.execute("pragma integrity_check").fetchone()[0] == "ok"
    finally:
        store.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp:
        check(Path(temp))
