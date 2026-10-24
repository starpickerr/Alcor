"""Managed connection metadata is read without importing or starting a Server."""
import json
import sys

import pytest

from agent.tools.tool_manager import ToolManager
from core.mcp.managed import configurations


@pytest.mark.parametrize('frozen', [False, True])
def test_managed_config_loads_description_from_the_server_package(tmp_path, monkeypatch, frozen):
    monkeypatch.setenv('STELLA_MANAGED_MCP', '1')
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'backend/stella-backend/stella-backend'))
    for domain in ('email', 'elearning'):
        root = tmp_path / domain
        monkeypatch.setenv(f'STELLA_{domain.upper()}_MCP_ROOT', str(root))
        package = (tmp_path / f'backend/{domain}-mcp/_internal' if frozen else root) / f'{domain}_mcp'
        package.mkdir(parents=True)
        (package / 'capability.json').write_text(json.dumps({
            'name': domain, 'description': {'zh': f'来自{domain}包的说明', 'en': 'Package description', 'secret': 'DO_NOT_EXPOSE'},
        }))
    manager = ToolManager()
    rows = {row['name']: row for row in configurations(manager)}
    assert rows['email']['description'] == {'zh': '来自email包的说明', 'en': 'Package description'}
    assert rows['elearning']['description']['zh'] == '来自elearning包的说明'
    assert not manager._mcp_tool_instances and manager._mcp_registry is None


@pytest.mark.parametrize('content', ['invalid json', '[]', '{"name":"other","description":"wrong"}', ' ' * 70_000])
def test_unusable_server_metadata_does_not_break_connection_configuration(tmp_path, monkeypatch, content):
    monkeypatch.setenv('STELLA_MANAGED_MCP', '1')
    for domain in ('email', 'elearning'):
        root = tmp_path / domain
        monkeypatch.setenv(f'STELLA_{domain.upper()}_MCP_ROOT', str(root))
        package = root / f'{domain}_mcp'
        package.mkdir(parents=True)
        (package / 'capability.json').write_text(content)
    rows = configurations(ToolManager())
    assert len(rows) == 2
    assert all(not row.get('description') for row in rows)


def test_server_metadata_symlink_cannot_read_outside_its_package(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLA_MANAGED_MCP', '1')
    for domain in ('email', 'elearning'):
        root = tmp_path / domain
        monkeypatch.setenv(f'STELLA_{domain.upper()}_MCP_ROOT', str(root))
        package = root / f'{domain}_mcp'
        package.mkdir(parents=True)
        outside = tmp_path / f'{domain}.json'
        outside.write_text(json.dumps({'name': domain, 'description': 'DO_NOT_EXPOSE'}))
        (package / 'capability.json').symlink_to(outside)
    assert all(not row.get('description') for row in configurations(ToolManager()))
