"""Official Desktop: shared business tools, native policy and plugin lifecycle; synthetic data/model only."""
import argparse
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4
from playwright.sync_api import sync_playwright
import official_desktop_check as desktop


class Provider(BaseHTTPRequestHandler):
    phase = 0
    failure = None
    catalogs = []
    expected_id = None
    enabled = True
    empty_session = None
    denied = set()

    def handle(self):
        # Chromium closes unused loopback connections when a test desktop exits.
        try:
            super().handle()
        except ConnectionResetError:
            if not self.close_connection:
                raise

    def log_message(self, *_args): pass

    def policy_call(self, messages, user_text, names):
        last = messages[-1]
        if 'CHILD_SCOPE_PROBE' in user_text:
            if last['role'] == 'tool':
                assert '本机业务数据仅供用户当前主会话使用' in last['content'], last
                self.denied.add('child')
            else:
                assert 'alcor_business' in names, names
                return 'alcor_business', {'domain': 'tasks', 'method': 'list_todos'}
        elif last['role'] != 'tool':
            if 'spawned' not in self.denied:
                return 'qa_unprompted_session', {'session_id': self.empty_session}
        elif '本机业务数据仅供用户当前主会话使用' in last['content']:
            self.denied.add('unprompted')
            return 'alcor_business', {'domain': 'tasks', 'method': 'categories'}
        elif 'Synthetic host veto' in last['content']:
            self.denied.add('host')
            return 'subagent', {'description': 'Check child authorization', 'prompt': 'CHILD_SCOPE_PROBE'}
        else:
            assert 'started subagent' in last['content'], last
            self.denied.add('spawned')

    def native_call(self, messages, text, names):
        assert ('alcor_business' in names) == self.enabled
        assert ('alcor-tasks' in text) == self.enabled
        assert ('alcor-bills' in text) == self.enabled
        assert 'pwsh' in names and 'read' in names, names
        if self.phase == 0:
            return 'read', {'file_path': 'synthetic.txt'}
        if self.phase == 1:
            assert 'synthetic-file-ok' in messages[-1]['content'], messages[-1]
            return 'pwsh', {'description': 'Print the synthetic PowerShell probe result',
                            'command': "Write-Output ('pwsh-' + (7 * 6))"}
        if self.phase == 2:
            content = messages[-1]['content']
            assert 'pwsh-42' in content and '[exit code: 1]' not in content, content
            return None
        raise AssertionError('Unexpected native tool phase')

    def business_call(self, messages, text, names):
        assert names.count('alcor_business') == 1 and names.count('skill') == 1, names
        if self.phase == 0:
            assert all(name in text for name in ['alcor-tasks', 'alcor-bills'])
            assert 'SQLite 文件' not in text, 'Skill bodies must load on demand'
            self.catalogs.append(sorted(names))
            return 'skill', {'name': 'alcor-tasks'}
        if self.phase == 1:
            content = messages[-1]['content']
            assert '<skill_content name="alcor-tasks">' in content
            assert 'list_todos' in content and 'include_completed:true' in content
            return 'alcor_business', {'domain': 'tasks', 'method': 'list_todos', 'arguments': {'include_completed': True}}
        if self.phase == 2:
            rows = json.loads(messages[-1]['content'])['result']
            assert any(row['id'] == self.expected_id and row['title'] == 'Synthetic shared task' for row in rows), rows
            return None
        raise AssertionError('Unexpected task-tool phase')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        names = [tool['function']['name'] for tool in payload.get('tools', [])]
        messages = payload['messages']
        user_text = json.dumps([m['content'] for m in messages if m['role'] == 'user'], ensure_ascii=False)
        text = json.dumps(messages, ensure_ascii=False)
        delta, finish = {'content': 'Synthetic response'}, 'stop'
        try:
            call = None
            if names and ('SHARED_POLICY_ENTRY' in user_text or 'CHILD_SCOPE_PROBE' in user_text):
                call = self.policy_call(messages, user_text, names)
            elif names and 'SHARED_NATIVE_ENTRY' in user_text:
                call = self.native_call(messages, text, names)
                type(self).phase += 1
            elif names and 'SHARED_TASK_ENTRY' in user_text:
                call = self.business_call(messages, text, names)
                type(self).phase += 1
            if call:
                tool, arguments = call
                delta = {'tool_calls': [{'index': 0, 'id': str(uuid4()), 'type': 'function',
                                         'function': {'name': tool, 'arguments': json.dumps(arguments)}}]}
                finish = 'tool_calls'
        except Exception as error:
            type(self).failure = repr(error)
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for choice in [{'delta': {'role': 'assistant'}, 'finish_reason': None}, {'delta': delta, 'finish_reason': None}, {'delta': {}, 'finish_reason': finish}]:
            event = {'id': 'shared-task-native', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'synthetic', 'choices': [{'index': 0, **choice}]}
            self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


def configure(home, env, port):
    docs = home / 'documents'
    docs.mkdir()
    profile = Path(env['DSH_HOME']) / 'profiles/desktop'
    profile.mkdir(parents=True)
    (profile / 'package.json').write_text(json.dumps({'name': 'shared-task-test', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
    (profile / 'cordis.patch.yml').write_text(f'''- id: workspace-controller
  config:
    documentsDirectory: {docs.as_posix()}
- id: agent-default-model
  config:
    provider: synthetic
    model: synthetic
- id: llm-pi-ai
  config:
    providers:
      synthetic:
        api: openai-completions
        apiKeyEnv: ALCOR_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:{port}/v1
        models:
          - id: synthetic
            name: Synthetic
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
''')
    credential = Path(env['DSH_HOME']) / '.credentials.yaml'
    credential.write_text('version: 1\nrefs:\n  ALCOR_SYNTHETIC_KEY: synthetic-test-key\n')
    credential.chmod(0o600)
    return docs


def prompt(page, docs, preset, marker):
    Provider.phase = 0
    Provider.failure = None
    created = desktop.rpc(page, 'session/create', {'request': {'cwd': str(docs), 'agentPreset': preset}})
    assert created['agentPreset'] == preset, created
    session = created['sessionId']
    desktop.rpc(page, 'session/selectModel', {'request': {'sessionId': session, 'provider': 'synthetic', 'model': 'synthetic'}})
    desktop.rpc(page, 'session/prompt', {'request': {'sessionId': session, 'requestId': str(uuid4()), 'mode': 'queue', 'clientTimeZone': 'Asia/Shanghai',
                                                 'content': [{'type': 'text', 'text': marker}]}})
    deadline = time.monotonic() + 40
    def complete():
        if marker == 'SHARED_POLICY_ENTRY':
            return Provider.denied == {'unprompted', 'host', 'child', 'spawned'}
        return Provider.phase == 3
    while not complete() and time.monotonic() < deadline and not Provider.failure:
        page.wait_for_timeout(100)
    assert not Provider.failure, Provider.failure
    assert complete(), (preset, marker, Provider.phase, Provider.denied)


def chats(page, docs):
    task = desktop.rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'create_todo',
                       'arguments': {'payload': {'title': 'Synthetic shared task'}}}})
    Provider.expected_id = task['id']
    for preset in ('standard', 'alcor', 'stella'):
        prompt(page, docs, preset, 'SHARED_TASK_ENTRY 检查事务有哪些重复的，只查询，不修改。')
        print('PASS:', preset, 'loaded alcor-tasks and queried synthetic task', task['id'], flush=True)


def windows_check(playwright, home, port, app):
    import official_shared_windows as win
    release = next(row for row in desktop.RELEASE['windows']['compatibleReleases'] if row['version'] == '0.2.2-rc.3')
    win.verify(app, release)
    print('Verified official Windows Desktop', release['version'], release['binarySha256'], flush=True)
    env = win.environment(home)
    docs = configure(home, env, port)
    (docs / 'synthetic.txt').write_text('synthetic-file-ok')
    patch = Path(env['DSH_HOME']) / 'profiles/desktop/cordis.patch.yml'
    fixture = Path(__file__).resolve().parent / 'fixtures/shared-business-policy.mjs'
    with patch.open('a', encoding='utf8') as stream:
        stream.write('- insert:\n    - id: synthetic-policy\n      name: ' + json.dumps(fixture.as_posix()) + '\n')
    cli, select = win.profile_commands(app, home, env)
    cli('add', desktop.PACK.resolve(strict=True), '--ignore-scripts')
    manifest = Path(env['DSH_HOME']) / 'profiles/desktop/package.json'
    position = json.loads(manifest.read_text())['dsh']['profile']['bundles'].index('@alcor/secretary')
    with win.desktop(playwright, app, home, env, 'business-enabled') as page:
        chats(page, docs)
        empty = desktop.rpc(page, 'session/create', {'request': {'cwd': str(docs), 'agentPreset': 'standard'}})
        Provider.empty_session = empty['sessionId']
        prompt(page, docs, 'standard', 'SHARED_POLICY_ENTRY')
        print('PASS: unprompted session, genuine subagent and explicit host veto denied business access', flush=True)
        prompt(page, docs, 'standard', 'SHARED_NATIVE_ENTRY')
        print('PASS: standard DSH native read and pwsh with Alcor enabled', flush=True)
    select(False, position)
    Provider.enabled = False
    with win.desktop(playwright, app, home, env, 'business-disabled') as page:
        prompt(page, docs, 'standard', 'SHARED_NATIVE_ENTRY')
        print('PASS: tools and guides withdrawn, native read and pwsh executed', flush=True)
    select(True, position)
    Provider.enabled = True
    with win.desktop(playwright, app, home, env, 'business-restored') as page:
        prompt(page, docs, 'standard', 'SHARED_TASK_ENTRY')
        print('PASS: restarted with one business tool and original task', Provider.expected_id, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, help='Signed official Windows rc.3 executable')
    args = parser.parse_args()
    if os.name == 'nt' and not args.app:
        parser.error('Windows requires --app')
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # Windows session paths can exceed MAX_PATH; use extended syntax for Python cleanup.
        temp_root = '\\\\?\\' + os.path.realpath(tempfile.gettempdir()) if os.name == 'nt' else None
        with tempfile.TemporaryDirectory(prefix='alcor-qa-', dir=temp_root) as temporary, sync_playwright() as playwright:
            home = Path(temporary.removeprefix('\\\\?\\'))
            if os.name == 'nt':
                windows_check(playwright, home, server.server_port, args.app.resolve(strict=True))
            else:
                desktop.verify_release()
                docs = configure(home, desktop.environment(home), server.server_port)
                desktop.native_cli(home, 'shared-tasks-install', 'add', str(desktop.PACK), '--ignore-scripts')
                with desktop.desktop(playwright, home, 'shared-tasks') as (page, errors):
                    chats(page, docs)
                    assert not errors, errors
            (desktop.ARTIFACTS / 'model-tool-catalogs.json').write_text(json.dumps(Provider.catalogs, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
