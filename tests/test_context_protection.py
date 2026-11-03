"""Protection changes stop old context without deleting its history."""
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from agent.memory.conversation_store import ConversationStore


def change_policy(tmp_path):
    from core.security.protected_paths import ProtectedPathControl
    folder = tmp_path / 'new-private'
    folder.mkdir()
    owner = ProtectedPathControl()
    owner.replace([str(folder)], expected_revision=owner.inventory()['revision'])


def test_policy_change_blocks_saved_context_and_clear_context_keeps_history(tmp_path):
    store = ConversationStore(tmp_path / 'history.db')
    store.append_messages('session', [{'role': 'user', 'content': 'SYNTHETIC_OLD_CONTEXT'}])
    assert store.load_messages('session')
    change_policy(tmp_path)
    with pytest.raises(PermissionError, match='context|上下文'):
        ConversationStore(tmp_path / 'history.db').load_messages('session')
    with sqlite3.connect(tmp_path / 'history.db') as db:
        assert 'SYNTHETIC_OLD_CONTEXT' in db.execute('SELECT content FROM messages').fetchone()[0]
    store.clear_context('session')
    store.append_messages('session', [{'role': 'user', 'content': 'NEW_CONTEXT'}])
    assert store.load_messages('session') == [{'role': 'user', 'content': 'NEW_CONTEXT'}]
    assert store.load_history_page('session')['total'] == 2


def test_unverified_historical_context_requires_clear_before_reuse(tmp_path):
    path = tmp_path / 'legacy.db'
    store = ConversationStore(path)
    store.append_messages('legacy', [{'role': 'user', 'content': 'KEEP_LEGACY_DATA'}])
    with sqlite3.connect(path) as db:
        db.execute('UPDATE sessions SET protection_revision=-1')
    with pytest.raises(PermissionError, match='context|上下文'):
        store.load_messages('legacy')
    store.clear_context('legacy')
    assert store.load_messages('legacy') == []


def test_agent_context_cannot_continue_after_policy_change(tmp_path):
    from agent.protocol.agent import Agent
    agent = Agent('', model=object(), enable_skills=False)
    agent.messages = [{'role': 'user', 'content': 'SYNTHETIC_OLD_CONTEXT'}]
    change_policy(tmp_path)
    with pytest.raises(PermissionError, match='context|上下文'):
        agent.run_stream('continue')
    assert agent.messages[0]['content'] == 'SYNTHETIC_OLD_CONTEXT'


def test_initializer_does_not_silently_discard_a_protection_refusal(tmp_path, monkeypatch):
    from bridge.agent_initializer import AgentInitializer
    import agent.memory
    store = ConversationStore(tmp_path / 'history.db')
    store.append_messages('session', [{'role': 'user', 'content': 'SYNTHETIC_OLD_CONTEXT'}])
    change_policy(tmp_path)
    monkeypatch.setattr(agent.memory, 'get_conversation_store', lambda _: store)
    initializer = object.__new__(AgentInitializer)
    with pytest.raises(PermissionError, match='context|上下文'):
        initializer._restore_conversation_history(SimpleNamespace(workspace_dir=str(tmp_path), messages=[], messages_lock=threading.Lock()), 'session')


def test_legacy_schema_migration_preserves_data_without_certifying_unknown_context(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE sessions (session_id TEXT PRIMARY KEY, created_at INTEGER, last_active INTEGER, msg_count INTEGER);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, seq INTEGER, role TEXT, content TEXT, created_at INTEGER);
            INSERT INTO sessions VALUES ('legacy', 1, 1, 1);
            INSERT INTO messages VALUES (1, 'legacy', 0, 'user', '"KEEP_LEGACY_DATA"', 1);
        ''')
    store = ConversationStore(path)
    with pytest.raises(PermissionError, match='上下文'):
        store.load_messages('legacy')
    assert 'KEEP_LEGACY_DATA' in str(store.load_history_page('legacy'))
    store.clear_context('legacy')
    store.append_messages('legacy', [{'role': 'user', 'content': 'NEW_CONTEXT'}])
    assert store.load_messages('legacy') == [{'role': 'user', 'content': 'NEW_CONTEXT'}]


def test_model_permit_keeps_the_context_guard_after_its_call_scope_ends(tmp_path):
    from core.security.service import SecurityService, model_authorization_scope
    from core.security.protected_paths import current_revision, check_context_revision
    revision = current_revision()
    with model_authorization_scope(lambda: check_context_revision(revision)):
        permit = SecurityService.authorize_egress('https://public.invalid', b'public', sources=('public:fixture',), wait=False)
    permit.check()
    change_policy(tmp_path)
    with pytest.raises(PermissionError, match='上下文'):
        permit.check()


def test_new_or_explicitly_cleared_agent_context_can_run(tmp_path, monkeypatch):
    from agent.protocol.agent import Agent
    from agent.protocol.agent_stream import AgentStreamExecutor
    agent = Agent('', model=object(), enable_skills=False)
    change_policy(tmp_path)
    agent.clear_history()
    monkeypatch.setattr(agent, 'get_full_system_prompt', lambda **kwargs: '')
    monkeypatch.setattr(AgentStreamExecutor, 'run_stream', lambda self, message: 'NORMAL_NEW_CONTEXT')
    assert agent.run_stream('new question') == 'NORMAL_NEW_CONTEXT'


def test_a_previously_queued_turn_cannot_revive_after_policy_change_and_context_reset(tmp_path):
    store = ConversationStore(tmp_path / 'history.db')
    store.queue_user_message('session', {'role': 'user', 'content': 'QUEUED_BEFORE_PROTECTION'}, run_id='old-run')
    change_policy(tmp_path)
    store.clear_context('session')
    with pytest.raises(PermissionError, match='上下文'):
        store.start_run_admission('old-run')
    assert store.load_messages('session') == []
    assert store.list_run_admissions('session')[0]['status'] == 'queued'
    store.queue_user_message('session', {'role': 'user', 'content': 'NEW_CONTEXT'}, run_id='new-run')
    store.start_run_admission('new-run')
    assert store.load_messages('session') == [{'role': 'user', 'content': 'NEW_CONTEXT'}]


def test_explicit_history_clear_can_start_a_new_turn_after_protection_changes(tmp_path, monkeypatch):
    from tests.test_queued_run_admission import _bridge
    from bridge.agent_bridge import AgentBridge
    from bridge.context import Context, ContextType
    from bridge.reply import Reply, ReplyType
    bridge, store = _bridge(tmp_path, monkeypatch, lambda *args, **kwargs: Reply(ReplyType.TEXT, 'NORMAL_NEW_CONTEXT'))
    store.append_messages('session', [{'role': 'user', 'content': 'OLD_CONTEXT'}])
    change_policy(tmp_path)
    reply = AgentBridge.agent_reply(bridge, 'new question', Context(ContextType.TEXT, 'new question', {'session_id': 'session'}), clear_history=True)
    assert reply.type == ReplyType.TEXT, reply.content
    assert reply.content == 'NORMAL_NEW_CONTEXT'
