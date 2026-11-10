"""Instance ownership survives overlapping loads, disposal and external mutation."""

from dataclasses import replace

import pytest

from core.plugins.registry import ContributionSnapshot, PluginRegistry


def reserve(registry, user='alice', plugin='example.plugin', services=('example.service@1',)):
    return ContributionSnapshot(
        plugin, registry.next_generation(plugin, user_id=user), 'test', 'digest',
        {'tools': [{'name': 'original'}]}, services, user,
    )


def test_users_own_independent_instances_and_service_names():
    registry = PluginRegistry()
    alice, bob = reserve(registry), reserve(registry, 'bob')
    registry.publish(alice)
    registry.publish(bob)
    assert registry.get(alice.plugin_id, user_id='alice') == alice
    assert registry.list_snapshots(user_id='bob') == [bob]
    assert registry.list_snapshots() == []
    assert registry.owner_of('example.service@1', user_id='bob') == bob.plugin_id
    registry.revoke(alice.plugin_id, alice.generation, user_id='alice')
    assert registry.get(bob.plugin_id, user_id='bob') == bob
    assert registry.owner_of('example.service@1', user_id='alice') is None
    with pytest.raises(LookupError):
        registry.require_generation(alice.plugin_id, alice.generation, user_id='alice')


def test_service_collision_is_atomic_within_one_user():
    registry = PluginRegistry()
    original = reserve(registry)
    registry.publish(original)
    other = reserve(registry, plugin='other.plugin')
    with pytest.raises(ValueError, match='already owned'):
        registry.publish(other)
    assert registry.list_snapshots(user_id='alice') == [original]


def test_late_load_cannot_replace_new_generation_or_revoke_it():
    registry = PluginRegistry()
    old, new = reserve(registry), reserve(registry)
    registry.publish(new)
    with pytest.raises(PermissionError):
        registry.publish(old)
    registry.revoke(old.plugin_id, old.generation, user_id='alice')
    assert registry.require_generation(new.plugin_id, new.generation, user_id='alice') == new


def test_failed_candidate_keeps_active_instance_and_replacement_releases_services():
    registry = PluginRegistry()
    active = reserve(registry)
    registry.publish(active)
    candidate = reserve(registry)
    registry.revoke(candidate.plugin_id, candidate.generation, user_id='alice')
    assert registry.require_generation(active.plugin_id, active.generation, user_id='alice') == active
    replacement = reserve(registry, services=('replacement@1',))
    registry.publish(replacement)
    assert registry.owner_of('example.service@1', user_id='alice') is None
    assert registry.owner_of('replacement@1', user_id='alice') == replacement.plugin_id
    with pytest.raises(PermissionError):
        registry.require_generation(active.plugin_id, active.generation, user_id='alice')


@pytest.mark.parametrize('published', [False, True])
def test_disposed_generation_cannot_publish_again(published):
    registry = PluginRegistry()
    snapshot = reserve(registry)
    if published:
        registry.publish(snapshot)
    registry.revoke(snapshot.plugin_id, snapshot.generation, user_id='alice')
    with pytest.raises(PermissionError):
        registry.publish(snapshot)
    assert registry.get(snapshot.plugin_id, user_id='alice') is None


def test_only_the_latest_reserved_generation_can_publish_once():
    registry = PluginRegistry()
    old, new = reserve(registry), reserve(registry)
    with pytest.raises(PermissionError):
        registry.publish(old)
    with pytest.raises(PermissionError):
        registry.publish(replace(new, generation=new.generation + 1))
    registry.publish(new)
    with pytest.raises(PermissionError):
        registry.publish(replace(new, contributes={'tools': []}))


def test_registry_owns_nested_contribution_data():
    registry = PluginRegistry()
    snapshot = reserve(registry)
    registry.publish(snapshot)
    snapshot.contributes['tools'][0]['name'] = 'input mutation'
    registry.get(snapshot.plugin_id, user_id='alice').contributes['tools'].clear()
    registry.list_snapshots(user_id='alice')[0].contributes.clear()
    current = registry.require_generation(snapshot.plugin_id, snapshot.generation, user_id='alice')
    assert current.contributes == {'tools': [{'name': 'original'}]}


def test_management_list_uses_current_user(monkeypatch):
    from common.runtime_identity import identity_scope
    from core.plugins import runtime_hub

    registry = PluginRegistry()
    registry.publish(reserve(registry))
    registry.publish(reserve(registry, 'bob', plugin='bob.plugin'))
    monkeypatch.setattr(runtime_hub, 'DEFAULT_REGISTRY', registry)
    with identity_scope(user_id='alice'):
        rows = runtime_hub.list_published_plugins()
    assert [row['pluginId'] for row in rows] == ['example.plugin']
