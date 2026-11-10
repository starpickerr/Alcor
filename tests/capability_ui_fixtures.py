"""Adapt legacy endpoint fixtures through the production capability projection."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.capabilities.catalog import CapabilityCatalog


def fulfill_capabilities(route, backend, *, known_plugins=(), known_mcp=()):
    def read(path, key):
        replies = []
        class Capture:
            request = SimpleNamespace(url='http://127.0.0.1:9876' + path, method='GET', post_data=None, post_data_json={})
            def fulfill(self, **kwargs):
                replies.append(kwargs.get('json') or json.loads(kwargs.get('body', '{}')))
        backend(Capture())
        value = replies[0] if replies else {}
        if value.get('status') == 'error':
            raise RuntimeError('Fixture inventory unavailable')
        return value.get(key, [])

    def plugins():
        rows = read('/api/plugins/runtime', 'plugins')
        names = {'stella.calendar':'日程','stella.tasks':'事务','stella.briefing':'简报','stella.knowledge':'知识'}
        for identifier in known_plugins:
            if not any(row['pluginId'] == identifier for row in rows):
                rows.append(dict(pluginId=identifier, name=names.get(identifier,identifier), version='1.0.0', intent='enabled', state='stopped'))
        return rows

    def mcps():
        rows = read('/api/mcp', 'servers')
        for identifier in known_mcp:
            if not any(row['name'] == identifier for row in rows):
                rows.append(dict(name=identifier, enabled=True, state='ready', transport='stdio', tools=[], settings_url='/' + identifier))
        return rows

    def skills():
        return read('/api/skills', 'skills')
    skill_manager = SimpleNamespace(get_skills_config=lambda: {row['name']:row for row in skills()},
        get_skill=lambda name: SimpleNamespace(skill=SimpleNamespace(content=next((row.get('description', '') for row in skills() if row['name']==name), ''))))
    catalog = CapabilityCatalog(Path('/unused-capability-fixture'), plugin_manager=SimpleNamespace(inventory=lambda user: plugins()),
        skill_manager=skill_manager, tool_rows=lambda: read('/api/tools','tools'), mcp_rows=mcps)
    query = parse_qs(urlparse(route.request.url).query)
    try:
        data = {'status':'success','detail':catalog.detail(query['kind'][0],query['id'][0])} if 'id' in query else catalog.list()
    except (KeyError, ValueError):
        data = {'status':'error','message':'Capability not found'}
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data), headers={
        'Access-Control-Allow-Origin':os.environ.get('STELLA_TEST_URL','http://127.0.0.1:5173'), 'Access-Control-Allow-Credentials':'true'})
