"""A derived index of explicit document references in the profile's knowledge library."""

import re
from urllib.parse import unquote

# Consume inline code and escaped openers before matching internal links.
LINK = re.compile(
    r"(?P<code>`+)[^`\n]*?(?P=code)|\\(?:\[\[|\[)|"
    r"\[\[(?P<wiki>[^\[\]\n]{1,550})\]\]|"
    r"\[(?P<label>[^\[\]\n]{0,240})\]\((?P<file>[^()\s]{1,1024})\)"
)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def mentions(body):
    fence = None
    end = 0
    for line in body.splitlines(keepends=True):
        start = end
        end += len(line)
        marker = FENCE.match(line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        if "[[" not in line and "](" not in line:
            continue
        for match in LINK.finditer(line):
            if match["wiki"]:
                name, _, label = match["wiki"].partition("|")
                key = name.split("#", 1)[0].strip()
                label = label or name
            elif match["file"]:
                label = match["label"]
                key = unquote(match["file"].split("#", 1)[0]).removeprefix("./")
                if not key.lower().endswith(".md") or any(c in key for c in "/\\:?#"):
                    continue
            else:
                continue
            if key and len(key) <= 240:
                yield key, start + match.start(), start + match.end(), label or key


def resolve(db, keys):
    for key in keys:
        if key.startswith("id:"):
            targets = db.execute("SELECT id FROM sources WHERE id=?", (key[3:],)).fetchall()
        else:
            targets = db.execute(
                "SELECT id FROM sources WHERE title=? OR (filename<>'' AND filename=?) LIMIT 2",
                (key, key),
            ).fetchall()
        if len(targets) == 1:
            db.execute(
                "UPDATE document_refs SET target_id=? WHERE target_key=? AND target_id IS NULL AND retired=0",
                (targets[0][0], key),
            )


def file_pages(db, source_id):
    return [
        r[0]
        for r in db.execute("SELECT body FROM library_pages WHERE source_id=? ORDER BY page", (source_id,))
    ]


def index_references(db, source_id, previous_title=None):
    source = db.execute("SELECT title,note,filename,kind FROM sources WHERE id=?", (source_id,)).fetchone()
    keys = {m[0] for m in mentions(source["note"])}
    if source["kind"] == "text":
        # Text import pages are display slices; a link or Markdown fence can span their boundary.
        keys.update(m[0] for m in mentions("".join(file_pages(db, source_id))))
    previous = {
        r[0] for r in db.execute("SELECT target_key FROM document_refs WHERE source_id=?", (source_id,))
    }
    db.executemany(
        "DELETE FROM document_refs WHERE source_id=? AND target_key=?",
        ((source_id, k) for k in previous - keys),
    )
    db.executemany(
        "INSERT INTO document_refs(source_id,target_key) VALUES(?,?)",
        ((source_id, k) for k in keys - previous),
    )
    # Retain resolved IDs on rename; unresolved forward and ambiguous references may now have one target.
    resolve(
        db,
        (keys - previous)
        | {
            k
            for k in (
                source["title"],
                source["filename"],
                previous_title,
                "id:" + source_id,
            )
            if k
        },
    )


def install_references(db):
    db.execute("""CREATE TABLE document_refs(
        source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        target_key TEXT NOT NULL, target_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
        retired INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source_id,target_key))""")
    db.execute("CREATE INDEX refs_incoming ON document_refs(target_id,source_id)")
    db.execute("CREATE INDEX refs_pending ON document_refs(target_key) WHERE target_id IS NULL AND retired=0")
    db.execute("CREATE INDEX sources_title ON sources(title)")
    db.execute("CREATE INDEX sources_filename ON sources(filename) WHERE filename<>''")
    for row in db.execute("SELECT id FROM sources"):
        index_references(db, row[0])


def inline_links(db, source, text, file_page=None, *, historical=False):
    if "[[" not in text and "](" not in text:
        return []
    targets = (
        {}
        if historical
        else {
            r[0]: r[1]
            for r in db.execute(
                """SELECT r.target_key,s.id FROM document_refs r
        JOIN sources s ON s.id=r.target_id WHERE r.source_id=? AND s.archived=0 AND s.id<>?""",
                (source, source),
            )
        }
    )
    origin = 0
    length = len(text)
    if file_page is not None:
        pages = file_pages(db, source)
        origin = sum(map(len, pages[: file_page - 1]))
        text = "".join(pages)
    return [
        {"id": targets.get(k), "start": start - origin, "end": end - origin, "label": label}
        for k, start, end, label in mentions(text)
        if origin <= start < end <= origin + length
    ]


def neighbors(db, source, offset, limit):
    result = {}
    more = False
    for direction, own, peer in (
        ("outgoing", "source_id", "target_id"),
        ("incoming", "target_id", "source_id"),
    ):
        rows = db.execute(
            f"""SELECT DISTINCT s.id,s.title,s.kind FROM document_refs r
            JOIN sources s ON s.id=r.{peer} WHERE r.{own}=? AND s.id<>? AND s.archived=0
            ORDER BY s.title,s.id LIMIT ? OFFSET ?""",
            (source, source, limit + 1, offset),
        ).fetchall()
        result[direction] = [dict(row) for row in rows[:limit]]
        more |= len(rows) > limit
    result["next_offset"] = offset + limit if more else None
    return result


def graph_titles(db, collection, query):
    if not query:
        return []
    return [dict(row) for row in db.execute(
        """SELECT id,title,collection FROM sources
        WHERE archived=0 AND (? IS NULL OR collection=? OR substr(collection,1,length(?)+1)=?||'/')
          AND instr(lower(title),lower(?))>0
        ORDER BY lower(title)<>lower(?), instr(lower(title),lower(?))<>1, title, id
        LIMIT 12""",
        (collection, collection, collection, collection, query, query, query),
    )]


def graph(db, collection, query, limit, *, viewer=False, focus=""):
    where, params = "archived=0", []
    if collection is not None and not focus:
        where += " AND " + (
            "(collection=? OR substr(collection,1,length(?)+1)=?||'/')" if viewer else "collection=?"
        )
        params = [collection] * (3 if viewer else 1)
    eligible = f"WITH eligible AS (SELECT id,title,kind,collection FROM sources WHERE {where})"
    candidates = "SELECT e.*,0 AS priority FROM eligible e"
    if focus or query:
        match = "id=?" if focus else "instr(lower(title),lower(?))>0"
        eligible += f""",
            hits AS (SELECT id FROM eligible WHERE {match}),
            near AS (SELECT id FROM hits UNION SELECT target_id FROM document_refs JOIN hits ON source_id=hits.id
                     UNION SELECT source_id FROM document_refs JOIN hits ON target_id=hits.id)"""
        candidates = """SELECT e.*,CASE WHEN h.id IS NULL THEN 1 ELSE 0 END AS priority
            FROM eligible e JOIN near ON near.id=e.id LEFT JOIN hits h ON h.id=e.id"""
        params.append(focus or query)
    elif viewer:
        # Keep connected documents ahead of unrelated documents at the overview limit.
        candidates = """SELECT e.*,CASE WHEN EXISTS(
              SELECT 1 FROM document_refs r JOIN eligible peer ON peer.id=r.target_id
              WHERE r.source_id=e.id AND peer.id<>e.id
            ) OR EXISTS(
              SELECT 1 FROM document_refs r JOIN eligible peer ON peer.id=r.source_id
              WHERE r.target_id=e.id AND peer.id<>e.id
            ) THEN 0 ELSE 1 END AS priority FROM eligible e"""
    rows = db.execute(
        f"""{eligible} SELECT *,count(*) OVER() AS total FROM ({candidates})
        ORDER BY priority,title,id LIMIT ?""",
        (*params, limit),
    ).fetchall()
    ids = [r["id"] for r in rows]
    edges = []
    if ids:
        marks = ",".join("?" for _ in ids)
        edges = db.execute(
            f"""SELECT DISTINCT source_id AS source,target_id AS target FROM document_refs
            WHERE source_id IN ({marks}) AND target_id IN ({marks}) AND source_id<>target_id
            ORDER BY source_id,target_id LIMIT 2001""",
            ids + ids,
        ).fetchall()
    nodes = []
    for row in rows:
        node = {key: row[key] for key in ("id", "title", "kind")}
        if viewer:
            node.update(collection=row["collection"], matched=bool(query or focus) and row["priority"] == 0)
        nodes.append(node)
    return {
        "nodes": nodes,
        "edges": [dict(r) for r in edges[:2000]],
        "total": rows[0]["total"] if rows else 0,
        "more_edges": len(edges) > 2000,
    }
