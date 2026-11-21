"""Exercise an existing v1 library, forward links, stable targets and duplicate names."""

import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages/alcor-secretary"))
from business.knowledge import Knowledge
from business.knowledge_index import connect


def check(path):
    with connect(path) as db:
        db.executescript(
            (
                Path(__file__).resolve().parents[2] / "packages/alcor-secretary/business/knowledge_schema.sql"
            ).read_text()
        )
        db.execute("PRAGMA user_version=1")
        db.execute("""INSERT INTO sources(id,title,kind,note,url,filename,page_count,index_notice,collection)
                      VALUES('old','提纲','note','参考 [[课程大纲]]','','',1,'','课程')""")
    db.close()
    library = Knowledge(path)

    def call(method, **args):
        return library.invoke(method, args, None)

    try:
        assert call("graph")["nodes"][0]["id"] == "old"
        syllabus = call("save", title="课程大纲", text="第三章")
        sid = syllabus["id"]
        assert call("links", id=sid)["incoming"][0]["id"] == "old"
        other = call("save", title="课程大纲", text="同名")
        note = call("save", title="笔记", text="[[课程大纲]] 和 `[[提纲]]`")
        assert not call("links", id=note["id"])["outgoing"]
        syllabus = call("edit", id=sid, revision=1, title="新名称", text="第三章")
        assert call("links", id="old")["outgoing"][0]["id"] == sid
        assert call("links", id=note["id"])["outgoing"][0]["id"] == other["id"]
        content = ("[[id:" + sid + "|大纲]] [[提纲|参考]] [[提纲]]\n```md\n[[课程大纲]]\n```").encode()
        md = call(
            "store_file",
            title="讲义",
            filename="讲义.md",
            text="",
            kind="text",
            content=base64.b64encode(content).decode(),
            pages=[content.decode()],
        )
        referencing = call(
            "save",
            title="索引",
            text="[讲义](%E8%AE%B2%E4%B9%89.md) [站外](https://example.org/a.md)",
        )
        assert call("links", id=referencing["id"])["outgoing"][0]["id"] == md["id"]
        assert {x["id"] for x in call("links", id=md["id"])["outgoing"]} == {"old", sid}
        assert {r["id"] for r in call("graph", query="新名称")["nodes"]} == {"old", sid, md["id"]}
        pages = ["```md\n分段", "[[课程大纲]]\n```\n[[新", "名称]] 与 [[提纲]]"]
        split = call(
            "store_file",
            title="跨页",
            filename="多页.md",
            text="",
            kind="text",
            content=base64.b64encode("".join(pages).encode()).decode(),
            pages=pages,
        )
        assert {r["id"] for r in call("links", id=split["id"])["outgoing"]} == {sid, "old"}
        assert call("read", id=split["id"], page=2, include_links=True)["body_links"] == []
        page = call("read", id=split["id"], page=3, include_links=True)
        (span,) = page["body_links"]
        assert page["text"][span["start"] : span["end"]] == "[[提纲]]"
        scoped = call("graph", collection="课程")
        assert [x["id"] for x in scoped["nodes"]] == ["old"] and not scoped["edges"]
        assert call("archive", id=sid, revision=2)["archived"]
        assert not call("links", id="old")["outgoing"]
        call("restore", id=sid, revision=3)
        assert call("links", id="old")["outgoing"][0]["id"] == sid
        call("edit", id="old", revision=1, title="提纲", text="独立资料")
        assert "old" not in [x["id"] for x in call("links", id=sid)["incoming"]]
        archived = call("archive", id=md["id"], revision=1)
        call("erase_archived", id=md["id"], revision=archived["revision"])
        assert not call("links", id=referencing["id"])["outgoing"]
        call("save", title="讲义.md", text="另一份同名资料")
        assert not call("links", id=referencing["id"])["outgoing"]
        (unresolved,) = call("read", id=referencing["id"], include_links=True)["body_links"]
        assert unresolved["label"] == "讲义" and unresolved["id"] is None
        assert not library.db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        library.close()


with tempfile.TemporaryDirectory() as temp:
    check(Path(temp) / "library.sqlite3")
