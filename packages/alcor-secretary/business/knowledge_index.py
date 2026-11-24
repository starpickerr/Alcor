"""Rebuildable passage indexes. The source page owns the text and citation coordinates."""

import array
import hashlib
import heapq
import json
import math
import re
import sqlite3
import subprocess
import sys
import threading
import unicodedata
from collections import defaultdict
from pathlib import Path

from business.knowledge_graph import install_references

CJK = r"[\u3400-\u9fff\uf900-\ufaff]"
EMBED = Path(__file__).resolve().parent.parent / "native" / "knowledge-embedding.js"


def tokens(text, query=False):
    text = unicodedata.normalize("NFKC", text).casefold()
    # Put boundaries between Latin words/numbers and Han characters before tokenizing.
    runs = re.findall(CJK + r"+|[a-z0-9]+|[^\W\d_a-z\u3400-\u9fff\uf900-\ufaff]+", text)
    out = []
    for word in runs:
        if re.fullmatch(CJK + "+", word):
            if not query or len(word) == 1:
                out.extend(word)
            out.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            out.append(word)
    return out


def passages(text):
    start = 0
    while start < len(text):
        end = min(start + 520, len(text))
        if end < len(text):
            breaks = list(re.finditer(r"\n+|[。！？.!?](?:\s+|$)|[。！？]", text[start + 260 : end]))
            if breaks:
                end = start + 260 + breaks[-1].end()
        if text[start:end].strip():
            yield start, end, text[start:end]
        if end == len(text):
            break
        start = end - 64


def connect(path):
    db = sqlite3.connect(path, timeout=8)
    db.row_factory = sqlite3.Row
    return db


def index_source(db, source_id, *, metadata_only=False):
    selected = "source_id=? AND page=0" if metadata_only else "source_id=?"
    old = db.execute("SELECT id FROM chunks WHERE " + selected, (source_id,)).fetchall()
    db.executemany("DELETE FROM library_fts WHERE rowid=?", ((row[0],) for row in old))
    db.execute("DELETE FROM chunks WHERE " + selected, (source_id,))
    source = db.execute("SELECT title,note,url,filename FROM sources WHERE id=?", (source_id,)).fetchone()
    meta = "\n".join(
        s for s in (source["title"], source["url"], source["note"] if source["filename"] else "") if s
    )

    def add(page, start, end, body):
        digest = hashlib.sha256(body.encode()).hexdigest()
        language = "zh-Hans" if re.search(CJK, body) else "en"
        row = db.execute(
            "INSERT INTO chunks(source_id,page,start,stop,digest,lang) VALUES(?,?,?,?,?,?)",
            (source_id, page, start, end, digest, language),
        )
        db.execute(
            "INSERT INTO library_fts(rowid,title,body) VALUES(?,?,?)",
            (row.lastrowid, " ".join(tokens(source["title"])) if page == 0 else "", " ".join(tokens(body))),
        )

    for start, end, body in passages(meta):
        add(0, start, end, body)
    if metadata_only:
        return
    for page, body in db.execute(
        "SELECT page,body FROM library_pages WHERE source_id=? ORDER BY page", (source_id,)
    ):
        for start, end, chunk in passages(body):
            add(page, start, end, chunk)


def install(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 3:
            return
        if version not in (0, 1, 2):
            raise ValueError("请升级 Alcor 后打开此知识库。")
        if version == 0:
            schema = Path(__file__).with_name("knowledge_schema.sql").read_text(encoding="utf-8")
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
        if version < 2:
            install_references(db)
        db.execute("CREATE TABLE library_folders (path TEXT PRIMARY KEY)")
        db.execute("INSERT INTO library_folders SELECT DISTINCT collection FROM sources WHERE collection<>''")
        db.execute("UPDATE sources SET archived=0 WHERE archived=1")
        db.execute("PRAGMA user_version=3")


def chunk_text(db, chunk):
    row = db.execute(
        """SELECT s.title,s.url,s.note,s.filename,p.body FROM sources s
        LEFT JOIN library_pages p ON p.source_id=s.id AND p.page=? WHERE s.id=?""",
        (chunk["page"], chunk["source_id"]),
    ).fetchone()
    if row is None:
        return ""
    body = (
        row["body"]
        if chunk["page"]
        else "\n".join(s for s in (row["title"], row["url"], row["note"] if row["filename"] else "") if s)
    )
    return body[chunk["start"] : chunk["stop"]]


def encode(language, texts):
    if sys.platform != "darwin":
        return None
    data = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", str(EMBED)],
        input=json.dumps({"language": language, "texts": texts}),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    result = json.loads(data.stdout)
    if not result:
        return None
    vectors = []
    for values in result["vectors"]:
        norm = math.sqrt(sum(v * v for v in values)) if values else 0
        vectors.append(
            array.array("f", (v / norm for v in values)).tobytes() if norm and math.isfinite(norm) else b""
        )
    return result["model"], vectors


class SemanticIndex:
    def __init__(self, path):
        self.path = path
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.models = {}
        self.cache = {}
        if sys.platform == "darwin":
            self.thread = threading.Thread(target=self.work, daemon=True, name="knowledge-embeddings")
            self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(2)

    def work(self):
        db = connect(self.path)
        while not self.stop.is_set():
            self.wake.clear()
            try:
                for lang in ("zh-Hans", "en"):
                    if not self.models.get(lang):
                        info = encode(lang, [])
                        if not info:
                            continue
                        self.models[lang] = info[0]
                        with db:
                            db.execute(
                                "DELETE FROM embeddings WHERE model LIKE ? AND model<>?",
                                (f"apple-sentence/{lang}/%", info[0]),
                            )
                    model = self.models[lang]
                    while not self.stop.is_set():
                        rows = db.execute(
                            """SELECT c.* FROM chunks c WHERE c.lang=? AND NOT EXISTS
                            (SELECT 1 FROM embeddings e WHERE e.model=? AND e.digest=c.digest)
                            GROUP BY c.digest LIMIT 24""",
                            (lang, model),
                        ).fetchall()
                        if not rows:
                            break
                        texts = [chunk_text(db, row) for row in rows]
                        result = encode(lang, texts)
                        if not result or result[0] != model:
                            self.models.pop(lang, None)
                            break
                        with db:
                            # A concurrent edit can retire a chunk while the encoder is running.
                            for row, text, vector in zip(rows, texts, result[1]):
                                if hashlib.sha256(text.encode()).hexdigest() != row["digest"]:
                                    continue
                                db.execute(
                                    "INSERT OR IGNORE INTO embeddings SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM chunks WHERE digest=?)",
                                    (row["digest"], model, vector, row["digest"]),
                                )
            except (OSError, ValueError, TypeError, sqlite3.Error, subprocess.SubprocessError):
                pass  # Lexical search stays available. Missing embeddings are retried after a wake or timeout.
            if not self.stop.is_set():
                self.wake.wait(60)
        db.close()

    def nearest(self, db, query, archived, limit, collection):
        lang = "zh-Hans" if re.search(CJK, query) else "en"
        model = self.models.get(lang)
        if not model or not db.execute("SELECT 1 FROM embeddings WHERE model=? LIMIT 1", (model,)).fetchone():
            return []
        key = (model, query)
        if key not in self.cache:
            try:
                result = encode(lang, [query])
            except (OSError, ValueError, subprocess.SubprocessError):
                return []
            if not result or result[0] != model or not result[1][0]:
                return []
            if len(self.cache) >= 64:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = result[1][0]
        # Exact cosine on the local collection; the index contains only compact normalized float32s.
        vector = memoryview(self.cache[key]).cast("f")
        selected = "" if collection is None else " AND s.collection=?"
        params = [model, archived, lang] + ([] if collection is None else [collection])
        rows = db.execute(
            """SELECT c.id,e.vector FROM chunks c JOIN sources s ON c.source_id=s.id
            JOIN embeddings e ON c.digest=e.digest AND e.model=? WHERE s.archived=? AND c.lang=?"""
            + selected,
            params,
        )
        try:
            import numpy as np
        except ImportError:
            np = None

        def similarities():
            while batch := rows.fetchmany(256):
                batch = [row for row in batch if len(row["vector"]) == len(vector) * 4]
                if np is not None and batch:
                    matrix = np.frombuffer(b"".join(r["vector"] for r in batch), dtype=np.float32).reshape(
                        len(batch), -1
                    )
                    scores = np.einsum("ij,j->i", matrix, np.asarray(vector))
                else:
                    scores = (
                        sum(a * b for a, b in zip(vector, memoryview(r["vector"]).cast("f"))) for r in batch
                    )
                for row, score in zip(batch, scores):
                    if score >= 0.40:
                        yield float(score), row["id"]

        return [id for _, id in heapq.nlargest(limit, similarities())]


def retrieve(db, semantic, query, archived, limit, collection=None):
    parts = list(dict.fromkeys(tokens(query, query=True)))[:64]
    if not parts:
        return []
    match = " OR ".join('"' + part + '"' for part in parts)
    selected = "" if collection is None else " AND s.collection=?"
    params = [match, archived] + ([] if collection is None else [collection]) + [limit]
    lex = [
        row[0]
        for row in db.execute(
            """SELECT f.rowid FROM library_fts f JOIN chunks c ON c.id=f.rowid
        JOIN sources s ON s.id=c.source_id WHERE library_fts MATCH ? AND s.archived=?"""
            + selected
            + " ORDER BY bm25(library_fts,4,1),f.rowid LIMIT ?",
            params,
        )
    ]
    # Exact identifiers (course codes, ticket numbers) should not acquire unrelated semantic matches.
    is_identifier = re.fullmatch(r"(?=[a-z0-9._-]*[0-9])[a-z0-9._-]+", query, re.I)
    dense = [] if is_identifier else semantic.nearest(db, query, archived, limit, collection)
    ranks = defaultdict(float)
    for ids in [lex, dense]:
        for rank, id in enumerate(ids, 1):
            ranks[id] += 1 / (30 + rank)
    return sorted(ranks, key=ranks.get, reverse=True)
