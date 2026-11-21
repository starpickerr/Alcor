CREATE TABLE assets (
    hash TEXT PRIMARY KEY, bytes BLOB NOT NULL, index_notice TEXT NOT NULL
);
CREATE TABLE asset_pages (
    hash TEXT NOT NULL REFERENCES assets(hash), page INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY(hash, page)
);
CREATE TABLE sources (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, note TEXT NOT NULL,
    url TEXT NOT NULL, filename TEXT NOT NULL, asset_hash TEXT REFERENCES assets(hash),
    page_count INTEGER NOT NULL, index_notice TEXT NOT NULL, collection TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0,
    updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX source_files ON sources(asset_hash);
CREATE INDEX source_list ON sources(archived,updated DESC,id);
CREATE INDEX source_collection ON sources(collection,archived,updated DESC);
CREATE TABLE save_receipts (
    key TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id) ON DELETE SET NULL, fingerprint TEXT NOT NULL
);
CREATE TABLE history (
    source_id TEXT NOT NULL REFERENCES sources(id), revision INTEGER NOT NULL, metadata TEXT NOT NULL,
    PRIMARY KEY(source_id,revision)
);
CREATE VIEW library_pages AS
    SELECT id AS source_id,1 AS page,note AS body FROM sources WHERE asset_hash IS NULL
    UNION ALL SELECT s.id,p.page,p.body FROM sources s JOIN asset_pages p ON s.asset_hash=p.hash;
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER NOT NULL,
    start INTEGER NOT NULL, stop INTEGER NOT NULL, digest TEXT NOT NULL, lang TEXT NOT NULL,
    UNIQUE(source_id,page,start)
);
CREATE INDEX chunk_digest ON chunks(lang,digest);
CREATE INDEX chunks_by_digest ON chunks(digest);
CREATE VIRTUAL TABLE library_fts USING fts5(title,body);
CREATE TABLE embeddings (
    digest TEXT NOT NULL, model TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(model,digest)
);
