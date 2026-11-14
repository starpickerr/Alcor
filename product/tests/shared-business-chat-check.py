"""Official Desktop: standard/Alcor chats discover and invoke task tools; synthetic data/model only."""
import json
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

    def log_message(self, *_args): pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        names = {tool['function']['name'] for tool in payload.get('tools', [])}
        text = json.dumps(payload['messages'], ensure_ascii=False)
        delta, finish = {'content': 'Synthetic title'}, 'stop'
        try:
            if 'SHARED_TASK_ENTRY' in text and 'skill' in names:
                assert 'alcor_business' in names, 'Task tool missing from the actual model catalog'
                if self.phase == 0:
                    assert all(name in text for name in ['alcor-tasks', 'alcor-bills'])
                    assert 'SQLite 文件' not in text, 'Skill bodies must load on demand'
                    self.catalogs.append(sorted(names))
                    tool, arguments = 'skill', {'name': 'alcor-tasks'}
                elif self.phase == 1:
                    content = payload['messages'][-1]['content']
                    assert '<skill_content name="alcor-tasks">' in content
                    assert 'list_todos' in content and 'include_completed:true' in content
                    tool, arguments = 'alcor_business', {'domain': 'tasks', 'method': 'list_todos', 'arguments': {'include_completed': True}}
                elif self.phase == 2:
                    rows = json.loads(payload['messages'][-1]['content'])['result']
                    assert any(row['title'] == 'Synthetic shared task' for row in rows), rows
                    tool = None
                    delta = {'content': 'SHARED_TASK_ENTRY verified through the task tool.'}
                else:
                    raise AssertionError('Unexpected task-tool phase')
                type(self).phase += 1
                if tool:
                    delta = {'tool_calls': [{'index': 0, 'id': 'shared-task-' + str(self.phase), 'type': 'function',
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


def main():
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='alcor-shared-task-') as temporary, sync_playwright() as playwright:
            home = Path(temporary)
            env = desktop.environment(home)
            docs = home / 'documents'
            docs.mkdir()
            profile = Path(env['DSH_HOME']) / 'profiles/desktop'
            profile.mkdir(parents=True)
            (profile / 'package.json').write_text(json.dumps({'name': 'shared-task-test', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
            (profile / 'cordis.patch.yml').write_text(f'''- id: workspace-controller
  config:
    documentsDirectory: {docs}
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
        baseURL: http://127.0.0.1:{server.server_port}/v1
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
            desktop.native_cli(home, 'shared-tasks-install', 'add', str(desktop.PACK), '--ignore-scripts')
            with desktop.desktop(playwright, home, 'shared-tasks') as (page, errors):
                desktop.rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'create_todo', 'arguments': {'payload': {'title': 'Synthetic shared task'}}}})
                for preset in ['standard', 'alcor', 'stella']:
                    Provider.phase = 0
                    created = desktop.rpc(page, 'session/create', {'request': {'cwd': str(docs), 'agentPreset': preset}})
                    session = created['sessionId']
                    desktop.rpc(page, 'session/selectModel', {'request': {'sessionId': session, 'provider': 'synthetic', 'model': 'synthetic'}})
                    desktop.rpc(page, 'session/prompt', {'request': {'sessionId': session, 'requestId': str(uuid4()), 'mode': 'queue', 'clientTimeZone': 'Asia/Shanghai',
                                                                 'content': [{'type': 'text', 'text': 'SHARED_TASK_ENTRY 检查事务有哪些重复的，只查询，不修改。'}]}})
                    deadline = time.monotonic() + 40
                    while Provider.phase < 3 and time.monotonic() < deadline and not Provider.failure:
                        page.wait_for_timeout(100)
                    assert not Provider.failure, Provider.failure
                    assert Provider.phase == 3, (preset, Provider.phase)
                    print('PASS:', preset, 'discovers task skill and queries the page service through alcor_business', flush=True)
                assert not errors, errors
                (desktop.ARTIFACTS / 'model-tool-catalogs.json').write_text(json.dumps(Provider.catalogs, indent=2))
            print('PASS: unchanged official Desktop, native skill loading, three preset paths and synthetic local tasks')
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
