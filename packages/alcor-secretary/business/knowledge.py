"""Private, profile-local library. Originals and searchable pages commit together."""

import base64
import hashlib
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

from business.knowledge_graph import graph, neighbors, index_references, inline_links, resolve
from business.knowledge_index import connect, install, index_source, chunk_text, retrieve, SemanticIndex


def string(value, limit, name, required=False):
    if not isinstance(value, str) or len(value) > limit or "\0" in value or (required and not value.strip()):
        raise ValueError(f"{name}无效或过长。")
    return value.strip()


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("页码、版本或范围无效。")
    return value


def source_url(value):
    url = string(value, 2048, "网址")
    if url:
        parts = urlsplit(url)
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.username
            or parts.password
            or re.search(r"[\s\\\x00-\x1f]", url)
        ):
            raise ValueError("请填写有效的 HTTP 或 HTTPS 网页地址。")
    return url


class Knowledge:
    def __init__(self, path):
        self.db = connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        install(self.db)
        self.semantic = SemanticIndex(path)

    def close(self):
        self.semantic.close()
        self.db.close()

    def header(self, id):
        row = self.db.execute(
            "SELECT id,title,kind,note,url,filename,page_count,index_notice,revision,archived,updated,collection FROM sources WHERE id=?",
            (string(id, 80, "资料编号", True),),
        ).fetchone()
        if not row:
            raise ValueError("找不到这份资料。")
        return dict(row)

    def invoke(self, method, args, key):
        if method in ("save", "store_file", "create_note"):
            return self.save(args, key, method == "store_file", allow_empty=method == "create_note")
        if method in ("plan_folder_delete", "delete_folder"):
            return self.folder_delete(args, method == "delete_folder")
        if method == "search":
            return self.search(args)
        if method in ("graph", "graph_view"):
            collection = args.get("collection")
            if collection is not None:
                collection = string(collection, 100, "资料集")
            viewer = method == "graph_view"
            return graph(
                self.db,
                collection,
                string(args.get("query", ""), 240, "搜索词"),
                integer(args.get("limit", 240), 1, 240),
                viewer=viewer,
                focus=string(args.get("focus_id", ""), 80, "资料编号") if viewer else "",
            )
        if method == "collections":
            return [
                r[0]
                for r in self.db.execute(
                    "SELECT path FROM library_folders UNION SELECT collection FROM sources WHERE collection<>'' ORDER BY 1"
                )
            ]
        if method == "create_folder":
            parent = string(args.get("parent", ""), 100, "上级文件夹")
            name = string(args.get("name"), 100, "文件夹名称", True)
            if name in (".", "..") or any(c in name for c in "/\\"):
                raise ValueError("文件夹名称不能含有斜线，也不能是 . 或 ..。")
            path = string(f"{parent}/{name}" if parent else name, 100, "文件夹路径", True)
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.require_folder(parent)
                self.db.execute("INSERT OR IGNORE INTO library_folders(path) VALUES(?)", (path,))
            return {"path": path}
        if method == "parsed_asset":
            digest = string(args.get("digest"), 64, "文件指纹", True)
            row = self.db.execute("SELECT index_notice FROM assets WHERE hash=?", (digest,)).fetchone()
            if row is None:
                return None
            pages = [
                r[0]
                for r in self.db.execute("SELECT body FROM asset_pages WHERE hash=? ORDER BY page", (digest,))
            ]
            return {"pages": pages, "index_notice": row["index_notice"]}
        source = self.header(args.get("id"))
        if method == "links":
            return neighbors(
                self.db,
                source["id"],
                integer(args.get("offset", 0), 0, 2**31 - 1),
                integer(args.get("limit", 25), 1, 50),
            )
        if method == "read":
            expected = args.get("revision", source["revision"])
            historical = expected != source["revision"]
            if historical:
                old = self.db.execute(
                    "SELECT metadata FROM history WHERE source_id=? AND revision=?",
                    (source["id"], integer(expected, 1, 2**53 - 1)),
                ).fetchone()
                if not old:
                    raise ValueError("此版本已不可用，请打开当前资料。")
                source = json.loads(old[0])
            page = integer(args.get("page", 1), 1, source["page_count"])
            row = self.db.execute(
                "SELECT body FROM library_pages WHERE source_id=? AND page=?", (source["id"], page)
            ).fetchone()
            text = source["note"] if historical and not source["filename"] else row[0]
            links = {}
            if args.get("include_links") is True:
                if not source["filename"] or source["kind"] == "text":
                    links["body_links"] = inline_links(
                        self.db,
                        source["id"],
                        text,
                        page if source["kind"] == "text" and source["page_count"] > 1 else None,
                        historical=historical,
                    )
                if source["filename"] and source["note"]:
                    links["note_links"] = inline_links(
                        self.db, source["id"], source["note"], historical=historical
                    )
            return {
                **source,
                **links,
                "page": page,
                "historical": historical,
                "text": text,
            }
        if method == "original" and source["filename"]:
            raw = self.db.execute(
                "SELECT a.bytes FROM sources s JOIN assets a ON a.hash=s.asset_hash WHERE s.id=?",
                (source["id"],),
            ).fetchone()[0]
            return {
                "filename": source["filename"],
                "kind": source["kind"],
                "content": base64.b64encode(raw).decode("ascii"),
            }
        if method == "erase_archived":
            return self.erase(source["id"], args.get("revision"), archived_only=True)
        if method == "delete":
            return self.erase(source["id"], args.get("revision"))
        if method == "move":
            revision = integer(args.get("revision"), 1, 2**53 - 1)
            previous = string(args.get("from"), 100, "原位置")
            destination = string(args.get("collection"), 100, "目标文件夹")
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.require_folder(destination)
                cursor = self.db.execute(
                    """UPDATE sources SET collection=?,revision=revision+1
                       WHERE id=? AND revision=? AND collection=? AND archived=0""",
                    (destination, source["id"], revision, previous),
                )
                if not cursor.rowcount:
                    raise ValueError("资料或保存位置已变化，请刷新后重试。")
                self.db.execute(
                    "INSERT INTO history VALUES(?,?,?)",
                    (source["id"], revision, json.dumps(source, ensure_ascii=False)),
                )
            return self.header(source["id"])
        if method == "reindex":
            with self.db:
                index_source(self.db, source["id"])
                index_references(self.db, source["id"])
            self.semantic.wake.set()
            return self.header(source["id"])
        if method not in ("edit", "archive", "restore"):
            raise ValueError("不支持的知识库操作。")
        revision = integer(args.get("revision"), 1, 2**53 - 1)
        title, note, url = source["title"], source["note"], source["url"]
        archived = int(method == "archive")
        if method == "edit":
            title = string(args.get("title"), 240, "标题", True)
            note = string(args.get("text", ""), 64000, "正文")
            url = source_url(args.get("url", source["url"]))
            if source["kind"] == "web" and not (note or url):
                raise ValueError("请填写笔记或网页地址。")
        collection = string(args.get("collection", source["collection"]), 100, "资料集")
        with self.db:
            cursor = self.db.execute(
                """UPDATE sources SET title=?,note=?,url=?,archived=?,collection=?,
                kind=CASE WHEN filename<>'' THEN kind WHEN ?='' THEN 'note' ELSE 'web' END,
                revision=revision+1,updated=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=? AND revision=? AND archived=?""",
                (
                    title,
                    note,
                    url,
                    archived,
                    collection,
                    url,
                    source["id"],
                    revision,
                    int(method == "restore"),
                ),
            )
            if not cursor.rowcount:
                raise ValueError("资料已变化，请重新打开后操作。")
            if collection:
                self.db.execute("INSERT OR IGNORE INTO library_folders VALUES(?)", (collection,))
            self.db.execute(
                "INSERT INTO history VALUES(?,?,?)",
                (source["id"], revision, json.dumps(source, ensure_ascii=False)),
            )
            if method == "edit":
                index_references(self.db, source["id"], source["title"])
                index_source(
                    self.db, source["id"], metadata_only=bool(source["filename"]) or note == source["note"]
                )
                self.db.execute(
                    "DELETE FROM embeddings WHERE NOT EXISTS(SELECT 1 FROM chunks WHERE chunks.digest=embeddings.digest)"
                )
        if method == "edit":
            self.semantic.wake.set()
        return self.header(source["id"])

    def erase(self, source_id, revision, archived_only=False):
        revision = integer(revision, 1, 2**53 - 1)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            source = self.db.execute(
                "SELECT asset_hash,title,filename FROM sources WHERE id=? AND revision=? AND (NOT ? OR archived=1)",
                (source_id, revision, archived_only),
            ).fetchone()
            if source is None:
                raise ValueError("资料已变化，请重新打开后操作。")
            self.erase_source(source_id, source)
            resolve(self.db, {source[key] for key in ("title", "filename") if source[key]})
            self.db.execute(
                "DELETE FROM embeddings WHERE NOT EXISTS(SELECT 1 FROM chunks WHERE chunks.digest=embeddings.digest)"
            )
        return {"removed": True}

    def require_folder(self, path):
        if path and not self.db.execute(
            """SELECT 1 FROM library_folders WHERE path=?
               OR substr(path,1,length(?)+1)=?||'/' LIMIT 1""", (path, path, path)
        ).fetchone():
            raise ValueError("目标文件夹已不可用，请重新选择。")

    def erase_source(self, source_id, source):
        self.db.execute(
            "DELETE FROM library_fts WHERE rowid IN (SELECT id FROM chunks WHERE source_id=?)",
            (source_id,),
        )
        self.db.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
        self.db.execute("DELETE FROM history WHERE source_id=?", (source_id,))
        # Keep existing citations from redirecting to a document with the same name.
        self.db.execute("UPDATE document_refs SET retired=1 WHERE target_id=?", (source_id,))
        self.db.execute("DELETE FROM sources WHERE id=?", (source_id,))
        asset = source["asset_hash"]
        if asset and not self.db.execute("SELECT 1 FROM sources WHERE asset_hash=?", (asset,)).fetchone():
            self.db.execute("DELETE FROM asset_pages WHERE hash=?", (asset,))
            self.db.execute("DELETE FROM assets WHERE hash=?", (asset,))

    def folder_delete(self, args, commit):
        path = string(args.get("path"), 100, "文件夹路径", True)
        params = (path, path, path)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE" if commit else "BEGIN")
            folders = [r[0] for r in self.db.execute(
                "SELECT path FROM library_folders WHERE path=? OR substr(path,1,length(?)+1)=?||'/' ORDER BY path",
                params,
            )]
            if not folders:
                raise ValueError("文件夹已不存在。")
            sources = self.db.execute(
                """SELECT id,revision,asset_hash,title,filename FROM sources WHERE collection=?
                   OR substr(collection,1,length(?)+1)=?||'/' ORDER BY id""", params
            ).fetchall()
            revisions = [(source["id"], source["revision"]) for source in sources]
            fingerprint = hashlib.sha256(json.dumps([folders, revisions]).encode()).hexdigest()
            if not commit:
                subfolders = set()
                for folder in folders:
                    while folder != path:
                        subfolders.add(folder)
                        folder = folder.rpartition("/")[0]
                return {"fingerprint": fingerprint, "documents": len(sources), "folders": len(subfolders)}
            if args.get("fingerprint") != fingerprint:
                raise ValueError("文件夹内容已变化，请重新确认删除范围。")
            for source in sources:
                self.erase_source(source["id"], source)
            resolve(self.db, {source[key] for source in sources for key in ("title", "filename") if source[key]})
            self.db.execute("DELETE FROM library_folders WHERE path=? OR substr(path,1,length(?)+1)=?||'/'", params)
            self.db.execute("DELETE FROM embeddings WHERE NOT EXISTS(SELECT 1 FROM chunks WHERE chunks.digest=embeddings.digest)")
        return {"removed": True}

    def save(self, args, key, file, allow_empty=False):
        title = string(args.get("title"), 240, "标题", True)
        note = string(args.get("text", ""), 64000, "正文")
        url = source_url(args.get("url", ""))
        collection = string(args.get("collection", ""), 100, "资料集")
        filename, blob, notice = "", None, ""
        if file:
            kind = args.get("kind")
            if kind not in ("image", "pdf", "text"):
                raise ValueError("暂不支持此文件格式。")
            filename = string(args.get("filename"), 240, "文件名", True)
            content = string(args.get("content"), 7 * 1024 * 1024, "文件", True)
            blob = base64.b64decode(content, validate=True)
            if not 0 < len(blob) <= 5 * 1024 * 1024:
                raise ValueError("文件须在 5 MB 以内。")
            pages = args.get("pages")
            if (
                not isinstance(pages, list)
                or not 1 <= len(pages) <= 500
                or any(not isinstance(p, str) for p in pages)
                or sum(map(len, pages)) > (5 * 1024 * 1024 if kind == "text" else 2_000_000)
            ):
                raise ValueError("文档超出可索引范围。")
            notice = string(args.get("index_notice", ""), 240, "索引状态")
        else:
            if not allow_empty and not note and not url:
                raise ValueError("请填写笔记或网页地址。")
            kind, pages = ("web" if url else "note"), [note]
        # Extraction can vary across OS updates. Retry identity depends on the original input.
        fingerprint = hashlib.sha256(
            json.dumps(
                [title, note, url, filename, kind, hashlib.sha256(blob or b"").hexdigest()]
                + ([collection] if collection else []),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        source_id = str(uuid4())
        asset_hash = hashlib.sha256(blob).hexdigest() if blob is not None else None
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if key:
                receipt = self.db.execute(
                    "SELECT source_id,fingerprint FROM save_receipts WHERE key=?", (key,)
                ).fetchone()
                if receipt:
                    if receipt["fingerprint"] != fingerprint:
                        raise ValueError("同一次收藏请求的内容已变化。")
                    if receipt["source_id"] is None:
                        raise ValueError("这条收藏已经删除。")
                    return self.header(receipt["source_id"])
            if allow_empty:
                self.require_folder(collection)
            if asset_hash:
                same = self.db.execute(
                    """SELECT id FROM sources WHERE asset_hash=? AND collection=?
                    AND title=? AND note=? AND archived=0 LIMIT 1""",
                    (asset_hash, collection, title, note),
                ).fetchone()
                if same:
                    if key:
                        self.db.execute(
                            "INSERT INTO save_receipts VALUES(?,?,?)", (key, same[0], fingerprint)
                        )
                    return {**self.header(same[0]), "existing": True}
            if asset_hash:
                inserted_asset = self.db.execute(
                    "INSERT OR IGNORE INTO assets VALUES(?,?,?)", (asset_hash, blob, notice)
                )
                if inserted_asset.rowcount:
                    self.db.executemany(
                        "INSERT INTO asset_pages VALUES(?,?,?)",
                        ((asset_hash, i, p) for i, p in enumerate(pages, 1)),
                    )
                else:
                    cached = self.invoke("parsed_asset", {"digest": asset_hash}, None)
                    pages, notice = cached["pages"], cached["index_notice"]
            self.db.execute(
                """INSERT INTO sources(id,title,kind,note,url,filename,asset_hash,page_count,index_notice,collection)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    source_id,
                    title,
                    kind,
                    note,
                    url,
                    filename,
                    asset_hash,
                    len(pages),
                    notice,
                    collection,
                ),
            )
            if collection:
                self.db.execute("INSERT OR IGNORE INTO library_folders VALUES(?)", (collection,))
            if key:
                self.db.execute("INSERT INTO save_receipts VALUES(?,?,?)", (key, source_id, fingerprint))
            index_source(self.db, source_id)
            index_references(self.db, source_id)
        self.semantic.wake.set()
        return self.header(source_id)

    def search(self, args):
        query = string(args.get("query", ""), 240, "搜索词")
        offset = integer(args.get("offset", 0), 0, 2**31 - 1)
        limit = integer(args.get("limit", 25), 1, 50)
        archived = args.get("archived", False)
        if type(archived) is not bool:
            raise ValueError("归档筛选无效。")
        collection = args.get("collection")
        if collection is not None:
            collection = string(collection, 100, "资料集")
        select_collection = "" if collection is None else " AND s.collection=?"
        if not query:
            rows = self.db.execute(
                """SELECT s.id,s.title,s.kind,s.updated,s.revision,s.archived,s.collection,1 AS page,
                substr(coalesce(nullif(s.note,''),p.body),1,220) AS snippet FROM sources s
                JOIN library_pages p ON p.source_id=s.id AND p.page=1 WHERE archived=?"""
                + select_collection
                + " ORDER BY updated DESC,s.id LIMIT ? OFFSET ?",
                [archived] + ([] if collection is None else [collection]) + [limit + 1, offset],
            ).fetchall()
            items = [dict(r) for r in rows]
            return {"items": items[:limit], "next_offset": offset + limit if len(items) > limit else None}
        # Location and revision travel with each excerpt, including multiple passages from one document.
        ids = retrieve(self.db, self.semantic, query, archived, 200, collection)
        if args.get("grouped") and ids:
            owners = dict(
                self.db.execute(
                    "SELECT id,source_id FROM chunks WHERE id IN (" + ", ".join("?" for _ in ids) + ")", ids
                )
            )
            best = {}
            for hit in ids:
                best.setdefault(owners[hit], hit)
            ids = list(best.values())
        items = []
        for id in ids[offset : offset + limit]:
            row = self.db.execute(
                """SELECT c.*,s.title,s.kind,s.updated,s.revision,s.archived,s.collection FROM chunks c
                JOIN sources s ON s.id=c.source_id WHERE c.id=?""",
                (id,),
            ).fetchone()
            excerpt = chunk_text(self.db, row)
            items.append(
                {
                    "id": row["source_id"],
                    "title": row["title"],
                    "kind": row["kind"],
                    "updated": row["updated"],
                    "revision": row["revision"],
                    "archived": row["archived"],
                    "collection": row["collection"],
                    "page": max(1, row["page"]),
                    "passage": id,
                    "start": row["start"],
                    "section": "source" if row["page"] == 0 else "page",
                    "snippet": excerpt,
                }
            )
        return {"items": items, "next_offset": offset + limit if offset + limit < len(ids) else None}
