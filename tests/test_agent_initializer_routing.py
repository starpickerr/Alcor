"""Covers the real initialize_agent body.

Every other multi-agent test replaces the initializer with a fake, which is how
`name 'profile' is not defined` reached a running gateway: nothing executed the
function. These are slow (they build a full agent) but they exercise the path a
real message takes.
"""

import os

import pytest

from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from common.runtime_identity import identity_scope
from common.user_data_migration import migrate_local_default


@pytest.fixture
def two_agents(tmp_path):
    registry = AgentRegistry(
        [
            AgentProfile("sales", "Sales", str(tmp_path / "sales")),
            AgentProfile("support", "Support", str(tmp_path / "support")),
        ],
        default_agent_id="sales",
    )
    set_agent_registry(registry)
    migrate_local_default(tmp_path / "sales")
    yield registry
    set_agent_registry(None)


@pytest.fixture
def initializer(monkeypatch):
    from bridge.agent_bridge import AgentBridge
    from bridge.bridge import Bridge

    # Routing tests must not leave background jobs running against later fixtures.
    monkeypatch.setattr("agent.scheduler.service.SchedulerService.start", lambda self: None)
    monkeypatch.setattr("agent.evolution.trigger.start_evolution_trigger", lambda bridge: None)
    monkeypatch.setattr("bridge.agent_initializer.AgentInitializer._start_daily_flush_timer", lambda self: None)
    yield AgentBridge(Bridge()).initializer
    from agent.scheduler.integration import reset_scheduler_services
    reset_scheduler_services()


def _realpath(path):
    return os.path.realpath(str(path))


def test_initialize_agent_follows_the_ambient_identity(two_agents, initializer, tmp_path):
    with identity_scope(agent_id="support"):
        agent = initializer.initialize_agent(session_id="s1")

    assert agent.agent_id == "support"
    assert agent.agent_profile.name == "Support"
    assert _realpath(agent.workspace_dir) == _realpath(tmp_path / "support")


def test_explicit_agent_id_overrides_the_ambient_identity(two_agents, initializer, tmp_path):
    with identity_scope(agent_id="support"):
        agent = initializer.initialize_agent(session_id="s1", agent_id="sales")

    assert agent.agent_id == "sales"
    assert _realpath(agent.workspace_dir) == _realpath(tmp_path / "sales")


def test_absent_identity_uses_the_default_agent(two_agents, initializer, tmp_path):
    agent = initializer.initialize_agent(session_id="s1")

    assert agent.agent_id == "sales"
    assert _realpath(agent.workspace_dir) == _realpath(tmp_path / "sales")


def test_agent_start_does_not_copy_model_credentials_into_environment_file(
        two_agents, initializer, tmp_path, monkeypatch):
    from config import conf
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setitem(conf(), 'open_ai_api_key', 'SYNTHETIC_MODEL_SECRET')
    initializer.initialize_agent(session_id='s1')
    path = tmp_path / '.stella' / '.env'
    assert not path.exists() or 'SYNTHETIC_MODEL_SECRET' not in path.read_text()
