#!/usr/bin/env python3
"""Synthetic manager/owner databases and bridge only; never uses EventKit."""
import json
import multiprocessing as mp
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'packages/alcor-apple-sync/python'))
from apple_sync.bridge import BridgeError
from apple_sync.category_create import CreatePending, create_category, guard_category_ids_in_db, pending_in_db, pending_sources


def connect(path):
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row;return db


class Owner:
    def __init__(self,path,mode='ok'):self.path,self.mode=path,mode
    def apple_capture_category_absence(self,ident):
        with connect(self.path) as db:
            row=db.execute('SELECT * FROM owner WHERE id=?',(ident,)).fetchone()
        if not row:raise KeyError(ident)
        return {'version':1,'local_id':ident,'local':json.loads(row['local']),'snapshot':row['snapshot']}
    def apple_category_incarnation(self,expected):
        if self.apple_capture_category_absence(expected['local_id'])!=expected:raise ValueError('CATEGORY_CHANGED')
        with connect(self.path) as db:return db.execute('SELECT generation FROM owner WHERE id=?',(expected['local_id'],)).fetchone()[0]
    def apple_apply_category_if_unchanged(self,expected,payload,binding):
        if self.mode=='edit':
            with connect(self.path) as db:db.execute("UPDATE owner SET snapshot='changed' WHERE id=?",(expected['local_id'],))
        if self.mode=='unknown':raise RuntimeError('owner response lost')
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM owner WHERE id=?',(expected['local_id'],)).fetchone()
            if not row or row['snapshot']!=expected['snapshot'] or json.loads(row['local'])!=expected['local']:
                raise ValueError('CATEGORY_CHANGED')
            local=json.loads(row['local']);local['apple']=binding
            db.execute('UPDATE owner SET local=? WHERE id=?',(json.dumps(local),expected['local_id']))


class Bridge:
    def __init__(self,mode='ok', gate=None):self.mode,self.gate,self.calls=mode,gate,0
    def call(self,op,**kw):
        if op in ('status','reminder_status'):return {'category_create_version':1}
        if op in ('calendars','reminder_lists'):
            return {'source_ids':['s1','s2'],'calendars':[],'lists':[]}
        self.calls+=1
        if self.gate:
            self.gate[0].set();self.gate[1].wait(5)
        if self.mode=='reject':raise BridgeError('pre-save rejected',native_no_side_effects=True)
        if self.mode=='unknown':raise BridgeError('lost response')
        saved={'id':'native-'+kw['source_id'],'source_id':kw['source_id'],'title':kw['title'],
               'color':'#FFFFFF' if self.mode=='color' else kw['color'],'writable':True,'immutable':False}
        Path(kw['receipt_file']).write_text(json.dumps({'binding':kw['receipt_binding'],'saved':saved}))
        return {'list' if op=='reminder_list_create' else 'calendar':saved}


class Sync:
    def __init__(self,path,bridge):self.db_path=Path(path);self.bridge=bridge
    @contextmanager
    def _connect(self):
        db=connect(self.db_path)
        try:
            with db:yield db
        finally:db.close()
    def clock(self):return '2026-01-01T00:00:00Z'


def do_create(db,owner,mode,source,queue,gate=None,ownermode='ok'):
    bridge=Bridge(mode,gate);sync=Sync(db,bridge);service=Owner(owner,ownermode)
    local=service.apple_capture_category_absence('local')['local']
    try:
        result=create_category(sync,service,'calendar',local,{'source_ids':['s1','s2']},source_id=source)
        queue.put(('ok',result,bridge.calls))
    except BaseException as error:queue.put((type(error).__name__,str(error),bridge.calls))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=self.root/'manager.db';self.owner=self.root/'owner.db'
        with connect(self.db) as db:
            db.executescript('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT); CREATE TABLE category_links (apple_id TEXT PRIMARY KEY,alcor_id TEXT UNIQUE,direction TEXT,enabled INTEGER,apple_name TEXT,alcor_name TEXT,name_conflict TEXT,updated_at TEXT);')
        self.local={'id':'local','name':'Work','color':'#112233','apple':{}}
        with connect(self.owner) as db:
            db.execute('CREATE TABLE owner (id TEXT PRIMARY KEY,local TEXT,snapshot TEXT,generation TEXT)')
            db.execute('INSERT INTO owner VALUES (?,?,?,?)',('local',json.dumps(self.local),'original','generation-1'))
        self.bridge=Bridge();self.sync=Sync(self.db,self.bridge);self.service=Owner(self.owner)
    def tearDown(self):self.temp.cleanup()
    def create(self,source='s1'):return create_category(self.sync,self.service,'calendar',self.local,{'source_ids':['s1','s2']},source_id=source)
    def test_normal(self):
        saved=self.create();self.assertEqual(saved['id'],'native-s1');self.assertEqual(pending_sources(self.sync,'calendar'),set())
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT alcor_id FROM category_links').fetchone()[0],'local')
        self.assertEqual(self.service.apple_capture_category_absence('local')['local']['apple']['apple_id'],'native-s1')
    def test_presend_reject_releases(self):
        self.bridge.mode='reject'
        with self.assertRaises(BridgeError):self.create()
        self.assertEqual(pending_sources(self.sync,'calendar'),set())
        self.bridge.mode='ok';self.create();self.assertEqual(self.bridge.calls,2)
    def test_unknown_blocks_retry_and_only_that_source(self):
        self.bridge.mode='unknown'
        with self.assertRaises(CreatePending):self.create()
        self.assertEqual(pending_sources(self.sync,'calendar'),{'s1'})
        with self.assertRaises(CreatePending):self.create('s2')
        self.assertEqual(self.bridge.calls,1)
        with self.sync._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertEqual(pending_in_db(db,'calendar')[0]['incarnation'],'generation-1')
            with self.assertRaises(CreatePending):guard_category_ids_in_db(db,'calendar',['local'])
            guard_category_ids_in_db(db,'calendar',['another'])
    def test_owner_unknown_and_concurrent_edit_are_retained(self):
        for mode in ('unknown','edit'):
            with self.subTest(mode=mode):
                if mode=='edit':
                    # Independent synthetic database for this subcase.
                    self.tearDown();self.setUp()
                self.service.mode=mode
                with self.assertRaises(CreatePending):self.create()
                self.assertEqual(pending_sources(self.sync,'calendar'),{'s1'})
                with self.assertRaises(CreatePending):self.create()
                self.assertEqual(self.bridge.calls,1)
                with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM category_links').fetchone()[0],0)
    def test_two_processes_same_category(self):
        ctx=mp.get_context('spawn');started=ctx.Event();release=ctx.Event();q=ctx.Queue()
        first=ctx.Process(target=do_create,args=(self.db,self.owner,'ok','s1',q,(started,release)))
        first.start();self.assertTrue(started.wait(5))
        second=ctx.Process(target=do_create,args=(self.db,self.owner,'ok','s1',q))
        second.start();second.join(5);self.assertFalse(second.is_alive());result=q.get(timeout=2)
        self.assertEqual(result[0],'CreatePending');self.assertEqual(result[2],0)
        release.set();first.join(5);self.assertFalse(first.is_alive());self.assertEqual(q.get(timeout=2)[0],'ok')
    def test_same_name_other_source_does_not_rebind(self):
        inventory={'source_ids':['s1','s2'],'calendars':[{'id':'other','title':'Work','source_id':'s2'}]}
        saved=create_category(self.sync,self.service,'calendar',self.local,inventory,source_id='s1')
        self.assertEqual(saved['id'],'native-s1')
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT apple_id FROM category_links').fetchone()[0],'native-s1')
    def test_incorrect_native_color_retains_unknown(self):
        self.bridge.mode='color'
        with self.assertRaises(CreatePending):self.create()
        self.assertEqual(self.bridge.calls,1)
        with self.sync._connect() as db:
            self.assertEqual(pending_in_db(db,'calendar')[0]['state'],'sending')
            self.assertEqual(db.execute('SELECT count(*) FROM category_links').fetchone()[0],0)

    def test_category_claim_blocks_create_in_same_manager_transaction(self):
        from apple_sync.group_scan_claims import schema as scan_schema
        with self.sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');scan_schema(db,create=True)
            db.execute('INSERT INTO calendar_scan_attempts VALUES (?,?,?,?,?,?,?)',('existing','s1','group','scanning','{}','now','{}'))
            db.execute('INSERT INTO calendar_scan_groups VALUES (?,?,?)',('group','s1','existing'))
            db.execute('INSERT INTO calendar_scan_items VALUES (?,?)',('category:local','existing'))
        with self.assertRaises(CreatePending):self.create()
        self.assertEqual(self.bridge.calls,0)
        with self.sync._connect() as db:
            self.assertEqual(pending_in_db(db,'calendar'),[])

    def test_tampered_journal_is_never_overwritten_or_released(self):
        # A competing writer alters the claim before an explicitly rejected send.
        bridge=self.bridge;original=bridge.call
        def tamper_then_reject(op,**kw):
            if op=='create_calendar':
                with connect(self.db) as db:
                    db.execute("UPDATE category_create_intents SET source_id='s2',document=?",(json.dumps({'tampered':True}),))
                raise BridgeError('rejected',native_no_side_effects=True)
            return original(op,**kw)
        bridge.call=tamper_then_reject
        with self.assertRaises(ValueError):self.create()
        with self.sync._connect() as db:
            row=db.execute('SELECT * FROM category_create_intents').fetchone()
            self.assertEqual(row['source_id'],'s2');self.assertEqual(json.loads(row['document']),{'tampered':True})
            self.assertEqual(row['state'],'sending')
            with self.assertRaises(ValueError):pending_in_db(db,'calendar')

    def test_tamper_after_owner_commit_never_advances_manager_link(self):
        owner=self.service;original=owner.apple_apply_category_if_unchanged
        def tamper(*args):
            original(*args)
            with connect(self.db) as db:
                db.execute("UPDATE category_create_intents SET state='competing',document=?",(json.dumps({'changed':True}),))
        owner.apple_apply_category_if_unchanged=tamper
        with self.assertRaises(CreatePending):self.create()
        with self.sync._connect() as db:
            row=db.execute('SELECT * FROM category_create_intents').fetchone()
            self.assertEqual(row['state'],'competing');self.assertEqual(json.loads(row['document']),{'changed':True})
            with self.assertRaises(ValueError):pending_in_db(db,'calendar')
            self.assertEqual(db.execute('SELECT count(*) FROM category_links').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT state FROM calendar_scan_attempts').fetchone()[0],'uncertain')

    def test_incarnation_in_key_document_and_receipt_binding(self):
        self.bridge.mode='unknown'
        with self.assertRaises(CreatePending):self.create()
        with self.sync._connect() as db:
            columns=db.execute('PRAGMA table_info(category_create_intents)').fetchall()
            self.assertEqual([r['name'] for r in sorted((r for r in columns if r['pk']),key=lambda r:r['pk'])],['kind','local_id','incarnation'])
            document=pending_in_db(db,'calendar')[0]['document']
            self.assertEqual(document['incarnation'],'generation-1')
            from apple_sync.group_scan_claims import digest
            identity={k:document[k] for k in ('expected','incarnation','source_id','kind','local_id','token')}
            self.assertEqual(document['binding'],digest(identity))

    def test_legacy_receipt_never_replayed_or_bypassed(self):
        directory=self.root/'native-receipts';directory.mkdir()
        (directory/'category-local.json').write_text(json.dumps({'saved':{'id':'older'}}))
        with self.assertRaises(CreatePending):self.create()
        self.assertEqual(self.bridge.calls,0)
        self.assertEqual(pending_sources(self.sync,'calendar'),{'s1'})
        with self.assertRaises(CreatePending):self.create()
        self.assertEqual(self.bridge.calls,0)
    def test_recreated_id_never_inherits_pending_intent(self):
        self.bridge.mode='unknown'
        with self.assertRaises(CreatePending):self.create()
        with connect(self.owner) as db:
            db.execute("UPDATE owner SET generation='generation-2',snapshot='new' WHERE id='local'")
        with self.assertRaises(CreatePending):self.create()
        self.assertEqual(self.bridge.calls,1)
        with self.sync._connect() as db:
            row=pending_in_db(db,'calendar')[0]
            self.assertEqual(row['incarnation'],'generation-1')

if __name__=='__main__':unittest.main(verbosity=2)
