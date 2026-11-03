"""In-memory contribution snapshot and generation tracking."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class ContributionSnapshot:
    plugin_id: str
    generation: int
    source_label: str
    package_digest: str
    contributes: dict[str, Any]
    services: tuple[str, ...] = ()
    user_id: str = 'local-default'


class PluginRegistry:
    """Tracks published contributions; rejects stale generation invokes."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_plugin: dict[tuple[str, str], ContributionSnapshot] = {}
        self._generations: dict[tuple[str, str], int] = {}
        self._pending: dict[tuple[str, str], int] = {}
        self._service_owner: dict[tuple[str, str], str] = {}

    def next_generation(self, plugin_id: str, *, user_id: str = 'local-default') -> int:
        with self._lock:
            key = (user_id, plugin_id)
            generation = self._generations.get(key, 0) + 1
            self._generations[key] = generation
            self._pending[key] = generation
            return generation

    def publish(self, snapshot: ContributionSnapshot) -> None:
        with self._lock:
            snapshot = deepcopy(snapshot)
            key = (snapshot.user_id, snapshot.plugin_id)
            if self._pending.get(key) != snapshot.generation:
                raise PermissionError(f'stale or unreserved publication: {snapshot.plugin_id}')
            for service in snapshot.services:
                owner = self._service_owner.get((snapshot.user_id, service))
                if owner is not None and owner != snapshot.plugin_id:
                    raise ValueError(f"service already owned: {service} by {owner}")
            old = self._by_plugin.get(key)
            if old is not None:
                for service in old.services:
                    self._service_owner.pop((snapshot.user_id, service), None)
            self._by_plugin[key] = snapshot
            del self._pending[key]
            for service in snapshot.services:
                self._service_owner[(snapshot.user_id, service)] = snapshot.plugin_id

    def revoke(self, plugin_id: str, generation: int | None = None, *, user_id: str = 'local-default') -> None:
        with self._lock:
            key = (user_id, plugin_id)
            # Disposal also cancels loads that have not published yet.
            if generation is None or self._pending.get(key) == generation:
                self._pending.pop(key, None)
            snap = self._by_plugin.get(key)
            if snap is None:
                return
            if generation is not None and snap.generation != generation:
                return
            for service in snap.services:
                if self._service_owner.get((user_id, service)) == plugin_id:
                    del self._service_owner[(user_id, service)]
            del self._by_plugin[key]

    def get(self, plugin_id: str, *, user_id: str = 'local-default') -> ContributionSnapshot | None:
        with self._lock:
            return deepcopy(self._by_plugin.get((user_id, plugin_id)))

    def require_generation(self, plugin_id: str, generation: int, *, user_id: str = 'local-default') -> ContributionSnapshot:
        snap = self.get(plugin_id, user_id=user_id)
        if snap is None:
            raise LookupError(f"plugin not published: {plugin_id}")
        if snap.generation != generation:
            raise PermissionError(
                f"stale generation for {plugin_id}: have {snap.generation}, got {generation}"
            )
        return snap

    def owner_of(self, service: str, *, user_id: str = 'local-default') -> str | None:
        with self._lock:
            return self._service_owner.get((user_id, service))

    def list_snapshots(self, *, user_id: str = 'local-default') -> list[ContributionSnapshot]:
        with self._lock:
            return deepcopy([snap for (user, _), snap in self._by_plugin.items() if user == user_id])


__all__ = ["ContributionSnapshot", "PluginRegistry"]
