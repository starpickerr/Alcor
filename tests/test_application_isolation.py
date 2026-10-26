"""A usable child sandbox is not evidence of application-wide protection."""
import pytest


def test_no_guard_means_core_and_desktop_are_not_protected(monkeypatch):
    from core.security.isolation import application_isolation
    report = application_isolation()
    assert report['status'] == 'unavailable'
    assert report['protected'] is False
    assert report['scope'] == 'application'
    assert {'core', 'desktop-main', 'desktop-helpers'} <= set(report['unprotected'])


def test_application_protection_cannot_be_forged_by_environment(monkeypatch):
    from core.security.isolation import application_isolation, require_application_isolation
    monkeypatch.setenv('STELLA_APPLICATION_ISOLATED', '1')
    monkeypatch.setenv('STELLA_GUARD_VERIFIED', '1')
    assert application_isolation()['protected'] is False
    with pytest.raises(PermissionError, match='Application'):
        require_application_isolation()


def test_runtime_inventory_separates_application_and_child_sandbox(monkeypatch):
    from core.security.control import RuntimeControl
    from types import SimpleNamespace
    monkeypatch.setattr('core.security.control.require_owner', lambda: None)
    monkeypatch.setattr('agent.permission.process_sandbox.capability', lambda: dict(status='enabled', reason='child sandbox usable'))
    monkeypatch.setattr('core.plugins.package_ops.PackageStore', lambda root: SimpleNamespace(packages=SimpleNamespace(glob=lambda pattern: [])))
    monkeypatch.setattr('agent.tools.tool_manager.ToolManager', lambda: SimpleNamespace(_load_mcp_configs=lambda: []))
    result = RuntimeControl().inventory()
    assert result['isolation']['status'] == 'enabled'
    assert result['isolation']['scope'] == 'child-process'
    assert result['application_isolation']['protected'] is False


def test_macos_endpoint_security_preflight_is_not_enforcement():
    import json
    from pathlib import Path
    import subprocess
    import sys
    if sys.platform != 'darwin':
        pytest.skip('Native macOS preflight requires macOS')
    probe = Path('scripts/probe_application_isolation.py')
    assert probe.is_file()
    reply = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=45)
    assert reply.returncode == 0, reply.stderr
    report = json.loads(reply.stdout)
    assert report['protected'] is False
    assert report['scope'] == 'preflight'
    assert report['endpoint_security']['result'] in range(7)
    assert report['endpoint_security']['status'] in {'available', 'unavailable'}
