"""Actual model requests refresh the selected native credential on existing clients."""
import ctypes
import http.server
import json
import ssl
import subprocess
import threading
import sys
from types import GeneratorType

import pytest

import config as config_module
from core.security.config_credentials import read_document, save_runtime
from tests.fixtures.egress import approve_synthetic_egress

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Real macOS Keychain integration')

CONSUMERS = ['chat', 'responses', 'anthropic', 'gemini', 'embedding', 'doubao',
             'legacy-chat', 'legacy-deepseek', 'legacy-modelscope', 'legacy-mimo',
             'legacy-qianfan', 'legacy-moonshot', 'legacy-minimax', 'legacy-doubao',
             'legacy-claudeAPI', 'legacy-gemini', 'image', 'completions', 'text']


@pytest.fixture
def model_runtime(tmp_path, monkeypatch, consumer):
    certificate, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
        '-keyout', str(key), '-out', str(certificate)], check=True, capture_output=True)
    received = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            received.append(dict(self.headers))
            body = json.dumps({'id': 'synthetic', 'choices': [{'message': {'role': 'assistant', 'content': 'Synthetic reply'},
                'text': 'Synthetic reply', 'finish_reason': 'stop'}], 'usage': {'total_tokens': 1, 'prompt_tokens': 1, 'completion_tokens': 1},
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Synthetic reply'}]}],
                'content': [{'type': 'text', 'text': 'Synthetic reply'}],
                'candidates': [{'content': {'parts': [{'text': 'Synthetic reply'}]}, 'finishReason': 'STOP'}],
                'usageMetadata': {'totalTokenCount': 1}, 'data': [{'index': 0, 'url': 'https://images.example.invalid/synthetic.png', 'embedding':
                    [1.0] + [0.0] * (1023 if self.path.endswith('/multimodal') else 1)}]}).encode()
            if payload.get('stream'):
                event = {'type': 'response.output_text.delta', 'delta': 'Synthetic reply',
                         'choices': [{'delta': {'content': 'Synthetic reply'}}]}
                body = ('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if payload.get('stream') else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    original = ssl.SSLContext.load_default_certs
    def trust_test_server(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.load_verify_locations(cafile=str(certificate))
    monkeypatch.setattr(ssl.SSLContext, 'load_default_certs', trust_test_server)
    url = f'https://127.0.0.1:{server.server_port}/v1'
    provider, field, base_field = {
        'chat': ('openai', 'open_ai_api_key', 'open_ai_api_base'),
        'responses': ('openai', 'open_ai_api_key', 'open_ai_api_base'),
        'anthropic': ('claudeAPI', 'claude_api_key', 'claude_api_base'),
        'gemini': ('gemini', 'gemini_api_key', 'gemini_api_base'),
        'embedding': ('openai', 'open_ai_api_key', 'open_ai_api_base'),
        'doubao': ('doubao', 'ark_api_key', 'ark_base_url'),
        'legacy-chat': ('openai', 'open_ai_api_key', 'open_ai_api_base'),
        'legacy-deepseek': ('deepseek', 'deepseek_api_key', 'deepseek_api_base'),
        'legacy-modelscope': ('modelscope', 'modelscope_api_key', 'modelscope_base_url'),
        'legacy-mimo': ('mimo', 'mimo_api_key', 'mimo_api_base'),
        'legacy-qianfan': ('qianfan', 'qianfan_api_key', 'qianfan_api_base'),
        'legacy-moonshot': ('moonshot', 'moonshot_api_key', 'moonshot_base_url'),
        'legacy-minimax': ('MiniMax', 'minimax_api_key', 'minimax_api_base'),
        'legacy-doubao': ('doubao', 'ark_api_key', 'ark_base_url'),
        'legacy-claudeAPI': ('claudeAPI', 'claude_api_key', 'claude_api_base'),
        'legacy-gemini': ('gemini', 'gemini_api_key', 'gemini_api_base'),
        'image': ('openai', 'open_ai_api_key', 'open_ai_api_base'),
        'completions': ('openai', 'open_ai_api_key', 'open_ai_api_base'),
        'text': ('openai', 'open_ai_api_key', 'open_ai_api_base'),
    }[consumer]
    values = {field: 'SYNTHETIC_OLD', base_field: url, 'model': 'synthetic', 'bot_type': provider,
        'custom_providers': [{'id': 'other', 'name': 'Other', 'api_key': 'SYNTHETIC_UNRELATED', 'api_base': url}],
        'linkai_api_key': 'SYNTHETIC_UNRELATED', 'agent': False,
        'agent_workspace': str(tmp_path / 'workspace'), 'embedding_provider': provider,
        'embedding_model': 'synthetic', 'embedding_dimensions': 1024 if consumer == 'doubao' else 2}
    if consumer == 'responses':
        values['provider_protocols'] = {'openai': 'openai_responses'}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(values))
    runtime = config_module.Config(values)
    monkeypatch.setattr(config_module, 'config', runtime)
    monkeypatch.setattr(config_module, 'get_data_root', lambda: str(tmp_path))
    save_runtime(path, values, runtime)
    try:
        with approve_synthetic_egress(url):
            yield path, runtime, field, received
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def client_for(consumer):
    if consumer == 'text':
        from models.chatgpt.chat_gpt_bot import ChatGPTBot
        return ChatGPTBot()
    if consumer.startswith('legacy-'):
        from models.bot_factory import create_bot
        return create_bot('chatGPT' if consumer == 'legacy-chat' else consumer.removeprefix('legacy-'))
    if consumer == 'image':
        from models.openai.open_ai_image import OpenAIImage
        return OpenAIImage()
    if consumer == 'completions':
        from models.openai.open_ai_bot import OpenAIBot
        return OpenAIBot()
    if consumer in ('embedding', 'doubao'):
        from models.embedding.factory import create_default_embedding_provider
        return create_default_embedding_provider()
    from models.provider_config import resolve_binding
    from models.protocols import create_protocol_adapter
    provider, binding = resolve_binding()
    return create_protocol_adapter(provider, binding)


def invoke(client, consumer):
    if consumer == 'text':
        from models.chatgpt.chat_gpt_session import ChatGPTSession
        session = ChatGPTSession('synthetic')
        session.messages.append({'role': 'user', 'content': 'Synthetic public text'})
        return client.reply_text(session)
    if consumer == 'image':
        return client.create_img('Synthetic public text')
    if consumer == 'completions':
        from models.openai.open_ai_session import OpenAISession
        session = OpenAISession('synthetic')
        session.messages.append({'role': 'user', 'content': 'Synthetic public text'})
        return client.reply_text(session)
    if consumer in ('embedding', 'doubao'):
        return client.embed('Synthetic public text')
    result = client.call_with_tools([{'role': 'user', 'content': 'Synthetic public text'}],
                                   model='synthetic', stream=False)
    if isinstance(result, GeneratorType):
        result = list(result)
        if any(isinstance(chunk, dict) and chunk.get('error') for chunk in result):
            raise RuntimeError('Model operation rejected')
    if isinstance(result, dict) and result.get('error'):
        raise RuntimeError('Model operation rejected')
    return result


def authorization(headers):
    return headers.get('Authorization') or headers.get('x-api-key') or headers.get('x-goog-api-key')


@pytest.mark.parametrize('consumer', CONSUMERS)
def test_existing_client_uses_rotated_key_without_decrypting_other_credentials(model_runtime, consumer, monkeypatch):
    from core.security.keychain import MacOSKeychain
    path, runtime, field, received = model_runtime
    client = client_for(consumer)
    assert invoke(client, consumer) is not None
    assert len(received) == 1

    updated = read_document(path)
    updated[field] = 'SYNTHETIC_NEW'
    save_runtime(path, updated, runtime)
    original = MacOSKeychain.get
    def selected(self, name):
        value = original(self, name)
        if value == 'SYNTHETIC_UNRELATED':
            raise AssertionError('Model request must not decrypt unrelated credentials')
        return value
    monkeypatch.setattr(MacOSKeychain, 'get', selected)
    assert invoke(client, consumer) is not None
    assert len(received) == 2
    assert authorization(received[0]) in ('SYNTHETIC_OLD', 'Bearer SYNTHETIC_OLD')
    assert authorization(received[1]) in ('SYNTHETIC_NEW', 'Bearer SYNTHETIC_NEW')


@pytest.mark.parametrize('consumer', ['chat'])
@pytest.mark.parametrize('kind', ['protocol', 'embedding', 'legacy'])
@pytest.mark.parametrize('unavailable', ['missing', 'deleted'])
def test_custom_provider_keeps_identity_after_reorder_and_stops_when_unavailable(
        model_runtime, consumer, kind, unavailable, isolated_keychain):
    from core.security.credential_values import account
    path, runtime, _, received = model_runtime
    data = read_document(path)
    data['custom_providers'].append({'id': 'chosen', 'name': 'Chosen', 'api_base': runtime['open_ai_api_base'],
                                     'api_key': 'SYNTHETIC_CHOSEN', 'model': 'synthetic'})
    data['bot_type'] = data['embedding_provider'] = 'custom:chosen'
    save_runtime(path, data, runtime)
    selected_consumer = 'embedding' if kind == 'embedding' else 'legacy-chat' if kind == 'legacy' else 'chat'
    client = client_for(selected_consumer)
    invoke(client, selected_consumer)
    assert authorization(received[-1]) == 'Bearer SYNTHETIC_CHOSEN'
    data = read_document(path)
    data['custom_providers'].reverse()
    data['custom_providers'][0]['api_key'] = 'SYNTHETIC_CHANGED'
    save_runtime(path, data, runtime)
    invoke(client, selected_consumer)
    assert len(received) == 2
    assert authorization(received[-1]) == 'Bearer SYNTHETIC_CHANGED'
    data = read_document(path)
    if unavailable == 'missing':
        isolated_keychain(path).delete(account('CUSTOM:chosen', data['custom_providers'][0]['api_key']))
    else:
        data['custom_providers'] = data['custom_providers'][1:]
        save_runtime(path, data, runtime)
    try:
        invoke(client, selected_consumer)
    except (OSError, ValueError, RuntimeError):
        pass
    assert len(received) == 2


@pytest.mark.parametrize('consumer', CONSUMERS)
@pytest.mark.parametrize('unavailable', ['cleared', 'locked', 'missing'])
def test_existing_client_stops_after_credential_becomes_unavailable(model_runtime, consumer, unavailable, isolated_keychain, monkeypatch):
    from core.security.credential_values import account
    path, runtime, field, received = model_runtime
    client = client_for(consumer)
    assert invoke(client, consumer) is not None
    assert len(received) == 1
    if unavailable == 'cleared':
        changed = read_document(path)
        changed[field] = ''
        save_runtime(path, changed, runtime)
    else:
        backend = isolated_keychain(path)
        if unavailable == 'locked':
            backend.security.SecKeychainLock.argtypes = [ctypes.c_void_p]
            assert backend.security.SecKeychainLock(backend.keychain) == 0
        else:
            backend.delete(account(field, read_document(path)[field]))
    monkeypatch.setenv('OPENAI_API_KEY', 'SYNTHETIC_OLD_ENVIRONMENT')
    try:
        invoke(client, consumer)
    except (OSError, ValueError, RuntimeError):
        pass
    assert len(received) == 1


@pytest.mark.parametrize('consumer', ['chat', 'responses'])
def test_pending_stream_uses_current_key_when_request_starts(model_runtime, consumer):
    path, runtime, field, received = model_runtime
    client = client_for(consumer)
    invoke(client, consumer)
    pending = client.call_with_tools([{'role': 'user', 'content': 'Synthetic public text'}], model='synthetic', stream=True)
    data = read_document(path)
    data[field] = 'SYNTHETIC_NEW'
    save_runtime(path, data, runtime)
    assert list(pending)
    assert len(received) == 2
    assert authorization(received[-1]) == 'Bearer SYNTHETIC_NEW'
