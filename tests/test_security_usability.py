"""Business decisions authorize necessary transport without duplicate prompts."""
import pytest

from common.runtime_identity import identity_scope
from core.security.connections import service_id
from core.security.service import SecurityService
from core.security.run_policy import resolve_run_policy, save_run_policy
from agent.permission.gateway import SecurityGateway


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write', 'full-access'])
def test_configured_model_multiturn_transport_needs_no_approval(monkeypatch, mode):
    url = 'https://chosen-model.invalid/v1/chat'
    identifier, origin = service_id(url)
    monkeypatch.setattr('core.security.connections.connection_catalog', lambda: [dict(id=identifier, endpoint=origin, owners=['model:test'])])
    with identity_scope(session_id='model-' + mode):
        resolve_run_policy('model-' + mode)
        save_run_policy('session', 'model-' + mode, {'filesystem_mode': mode})
        before = SecurityGateway.pending()
        for body in (b'first', b'second'):
            permit = SecurityService.authorize_egress(url, body, sources=('private:model-context',), wait=False)
            permit.check_request(url, body)
        assert SecurityGateway.pending() == before


def test_full_access_private_transport_in_background_is_immediate():
    from agent.runtime.tool_invocation import scheduled_permission_scope
    with identity_scope(task_id='transport', agent_id='default'):
        with scheduled_permission_scope({'id':'transport', 'run_permission_mode':'full-access'}):
            permit = SecurityService.authorize_egress('https://model.invalid', b'private', sources=('private:task',), wait=False)
            permit.check()
            assert not SecurityGateway.pending()


def test_custom_network_deny_prevents_dns_and_prompts(monkeypatch):
    import socket
    from core.security.requests_transport import post
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('denied transport reached DNS'))
    with identity_scope(session_id='no-network'):
        resolve_run_policy('no-network')
        save_run_policy('session', 'no-network', {'filesystem_mode':'full-access','permission_mode':'custom','rules':{'network':'deny'}})
        with pytest.raises(PermissionError):
            post('https://model.invalid', json={'messages':[]})
        assert not SecurityGateway.pending()


def test_providers_is_a_read_without_configuration_or_process_side_effects(monkeypatch):
    from agent.tools.agent_hub.agent_hub_tool import AgentHubTool
    from core.services import agent_hub_gateway as gateway
    monkeypatch.setattr(gateway, 'apply_user_preferences', lambda: pytest.fail('list wrote preferences'))
    def invoke(method, params=None):
        assert method == 'status_overview'
        return {'providers':[{'providerConfigId':'p','enabled':True,'lastProbe':None}]}
    monkeypatch.setattr(gateway, 'invoke_ok', invoke)
    result = AgentHubTool()._providers()
    assert result['providers'][0]['lastProbe'] is None


def test_allowed_mcp_read_receipt_authorizes_only_its_exact_transport(monkeypatch):
    import json
    import urllib.request
    from types import SimpleNamespace
    from agent.tools.mcp.mcp_client import McpClient
    from agent.tools.mcp.mcp_tool import McpTool
    from agent.runtime.tool_invocation import permission_denial, invocation_scope
    from agent.permission.gateway import register_tool, Capability
    from core.security.capabilities import mcp_descriptor
    from core.security.resource_grants import ResourceGrantStore
    config = {'name':'known','type':'streamable-http','url':'https://mcp.invalid','toolOperations':{'read':'read'}}
    descriptor = mcp_descriptor('known','read',config)
    tool_name = McpTool.name_for('known','read')
    register_tool(tool_name, Capability.EXTERNAL, connection='mcp:known', descriptor=descriptor)
    captured = []
    def open_request(*args, **kwargs):
        kwargs['permit'].check()
        captured.append(kwargs['permit'])
        return SimpleNamespace(status=200)
    monkeypatch.setattr('core.security.http_transport.open_request', open_request)
    with identity_scope(agent_id='default',session_id='mcp-read'):
        resolve_run_policy('mcp-read')
        grant = ResourceGrantStore().issue(user_id='local-default',subject='session:default:mcp-read',resource={'kind':'connection','id':'mcp:known'},operations=['read'])
        values = {'q':'original'}
        assert permission_denial(tool_name, values, capability_descriptor=descriptor, noninteractive=True) is None
        client = McpClient(config)
        def request(values):
            return urllib.request.Request(config['url'],data=json.dumps({'method':'tools/call','params':{'name':'read','arguments':values}}).encode(),method='POST')
        with invocation_scope('read-one'):
            client._open_remote(request(values),timeout=1)
            with pytest.raises(PermissionError):
                client._open_remote(request({'q':'changed'}),timeout=1)
            ResourceGrantStore().revoke(grant['grant_id'],user_id='local-default',expected_revision=grant['revision'])
            with pytest.raises(PermissionError):
                captured[0].check()
        assert len(captured)==1
        assert not [row for row in SecurityGateway.pending() if row.get('session')=='mcp-read']


@pytest.mark.parametrize('rule', ['network', 'external'])
def test_necessary_model_transport_preserves_explicit_custom_deny(monkeypatch, rule):
    url='https://selected.invalid/chat'
    identifier, origin=service_id(url)
    monkeypatch.setattr('core.security.connections.connection_catalog', lambda:[dict(id=identifier,endpoint=origin,owners=['model:selected'])])
    with identity_scope(session_id='custom-'+rule):
        resolve_run_policy('custom-'+rule)
        save_run_policy('session','custom-'+rule,{'filesystem_mode':'full-access','permission_mode':'custom','rules':{rule:'deny'}})
        with pytest.raises(PermissionError):
            SecurityService.authorize_egress(url,b'context',sources=('private:model-context',),wait=False)


def test_sse_control_request_honors_network_deny_before_dns(monkeypatch):
    import socket
    import urllib.request
    from agent.tools.mcp.mcp_client import McpClient
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:pytest.fail('Denied SSE request reached DNS'))
    with identity_scope(session_id='sse-denied'):
        resolve_run_policy('sse-denied')
        save_run_policy('session','sse-denied',{'filesystem_mode':'full-access','permission_mode':'custom','rules':{'network':'deny'}})
        client=McpClient({'name':'sse','type':'sse','url':'https://sse.invalid'})
        with pytest.raises(PermissionError):
            client._open_remote(urllib.request.Request('https://sse.invalid',method='GET'),timeout=1)


def test_agent_stream_never_retries_local_policy_error(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from agent.protocol.agent_stream import AgentStreamExecutor
    from agent.tools.tool_manager import ToolManager
    error = PermissionError('network timeout: destination denied')
    model = SimpleNamespace(model='synthetic', call_stream=Mock(side_effect=error))
    executor = AgentStreamExecutor(agent=SimpleNamespace(), model=model, system_prompt='', tools=[], messages=[])
    monkeypatch.setattr(executor, '_validate_and_fix_messages', lambda: None)
    monkeypatch.setattr(executor, '_prepare_messages', lambda: [])
    monkeypatch.setattr(executor, '_identify_complete_turns', lambda: [])
    monkeypatch.setattr(executor, '_emit_event', lambda *a: None)
    monkeypatch.setattr(ToolManager, 'sync_mcp_into_agent', lambda *a: None)
    with pytest.raises(PermissionError) as caught:
        executor._call_llm_stream()
    assert caught.value is error
    assert model.call_stream.call_count == 1


def test_shared_mcp_transport_revocation_is_scoped_to_each_conversation(monkeypatch):
    import json
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from agent.tools.mcp.mcp_client import McpClient
    from agent.tools.mcp.mcp_tool import McpTool
    from agent.runtime.tool_invocation import permission_denial, invocation_scope
    from agent.permission.gateway import register_tool, Capability
    from core.security.capabilities import mcp_descriptor
    from core.security.resource_grants import ResourceGrantStore
    config = {'name':'shared','type':'streamable-http','url':'https://shared.invalid','toolOperations':{'read':'read'}}
    descriptor = mcp_descriptor('shared', 'read', config)
    tool = McpTool.name_for('shared', 'read')
    register_tool(tool, Capability.EXTERNAL, connection='mcp:shared', descriptor=descriptor)
    client = McpClient(config)
    def opened(*args, **kwargs):
        kwargs['permit'].check()
        return SimpleNamespace(status=200, permit=kwargs['permit'])
    monkeypatch.setattr('core.security.http_transport.open_request', opened)
    def request(session):
        with identity_scope(agent_id='default', session_id=session):
            resolve_run_policy(session)
            grant = ResourceGrantStore().issue(user_id='local-default', subject='session:default:' + session,
                resource={'kind':'connection','id':'mcp:shared'}, operations=['read'])
            args = {'q':session}
            assert permission_denial(tool, args, capability_descriptor=descriptor, noninteractive=True) is None
            with invocation_scope(session):
                response = client._open_remote(urllib.request.Request(config['url'],
                    data=json.dumps({'method':'tools/call','params':{'name':'read','arguments':args}}).encode(), method='POST'), timeout=1)
                return grant, response.permit
    with ThreadPoolExecutor(max_workers=2) as pool:
        (grant_a, permit_a), (_, permit_b) = list(pool.map(request, ['parallel-a', 'parallel-b']))
    ResourceGrantStore().revoke(grant_a['grant_id'], user_id='local-default', expected_revision=grant_a['revision'])
    with pytest.raises(PermissionError):
        permit_a.check()
    permit_b.check()
