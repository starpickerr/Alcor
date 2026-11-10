"""Probe installed community candidates with no personal-file access or external network (macOS)."""
import base64
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright
from plugin_check import CLI
from web_check import rpc, stop

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / '.local/candidates-home/profiles/candidates/node_modules'
MODEL_REQUESTS = []


class CandidateProvider(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        MODEL_REQUESTS.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        chunk = {'id': 'synthetic-candidate', 'object': 'chat.completion.chunk', 'created': 1,
            'model': 'stella-mock', 'choices': [{'index': 0,
                'delta': {'role': 'assistant', 'content': 'Synthetic candidate context captured.'}, 'finish_reason': 'stop'}]}
        self.wfile.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
        self.wfile.flush()


def memory(page, url, method, endpoint, payload=None, accepted=True):
    result = rpc(page, url, 'dshMnemon/' + method, {'endpoint': endpoint, 'payload': payload or {}})
    assert result['ok'] == accepted, result
    return result.get('value') if accepted else result['error']


def capture_context(page):
    MODEL_REQUESTS.clear()
    page.get_by_role('button', name='Continue', exact=True).click()
    page.get_by_role('button', name='New session', exact=True).last.click()
    page.get_by_role('button', name='Choose workspace', exact=True).click()
    page.get_by_role('menuitem', name=re.compile('Default workspace')).click()
    composer = page.locator('[data-composer-input][contenteditable="true"]')
    composer.fill('Synthetic standard DSH context probe')
    composer.press('Enter')
    page.get_by_role('paragraph').filter(has_text='Synthetic candidate context captured.').wait_for(timeout=25000)
    assert MODEL_REQUESTS, 'The standard DSH conversation must reach the synthetic model'
    return json.dumps([request.get('messages', []) for request in MODEL_REQUESTS])


def check_memory(page, url, phase):
    status = memory(page, url, 'read', 'status')
    snapshot = memory(page, url, 'read', 'runtime-memory')
    if phase == 'writable':
        assert snapshot['entries'] == [], snapshot
        original = 'Synthetic Stella preference: use short answers.'
        corrected = 'Synthetic Stella preference: use detailed answers.'
        mutation = {'action': 'add', 'target': 'user', 'content': original}
        assert memory(page, url, 'write', 'runtime-memory', mutation)['success']
        assert memory(page, url, 'write', 'runtime-memory', mutation)['entryCount'] == 1
        entries = memory(page, url, 'read', 'runtime-memory', {'sessionId': 'another-synthetic-session'})['entries']
        assert [entry['content'] for entry in entries] == [original], entries
        assert original not in capture_context(page), 'Disabled lifecycle must not inject runtime memory'
        print('PASS: disabled lifecycle omits the synthetic runtime preference from the standard DSH prompt')
        assert memory(page, url, 'write', 'runtime-memory', {'action': 'replace', 'target': 'user',
            'old_text': original, 'content': corrected})['success']
        snapshot = memory(page, url, 'read', 'runtime-memory')
        assert [entry['content'] for entry in snapshot['entries']] == [corrected], snapshot
        memory(page, url, 'write', 'runtime-memory', {'action': 'add', 'target': 'user',
            'content': 'x' * 9000}, accepted=False)
        exported = memory(page, url, 'pack', 'export')
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported['base64'], validate=True))) as archive:
            assert any(corrected.encode() in archive.read(name) for name in archive.namelist()), 'Export must contain the corrected memory'
        assert memory(page, url, 'write', 'runtime-memory', {'action': 'remove', 'target': 'user',
            'old_text': corrected})['success']
        assert memory(page, url, 'read', 'runtime-memory')['entries'] == []
        print('PASS: native runtime memory add, duplicate prevention, cross-session visibility, correction, oversized-entry refusal, export and delete')
        memory(page, url, 'write', 'runtime-memory', {'action': 'add', 'target': 'user',
            'content': 'Synthetic Stella private memory sentinel.'})
    elif phase == 'active':
        marker = 'Synthetic Stella private memory sentinel.'
        assert [entry['content'] for entry in snapshot['entries']] == [marker], snapshot
        assert marker in capture_context(page), 'Recheck candidate scope: active lifecycle no longer shares custom-root user memory'
        print('ADMISSION BLOCKED: active lifecycle injects the custom-root user memory into a standard DSH conversation without selecting Stella')
        memory(page, url, 'write', 'runtime-memory', {'action': 'remove', 'target': 'user', 'old_text': marker})
        assert memory(page, url, 'read', 'runtime-memory')['entries'] == []
    else:
        assert snapshot['entries'] == [], snapshot
        error = memory(page, url, 'write', 'runtime-memory', {'action': 'add', 'target': 'user',
            'content': 'Must not be stored'}, accepted=False)
        assert 'trusted-host' in error['message'] or 'read-only' in error['message'], error
        assert memory(page, url, 'read', 'runtime-memory')['entries'] == []
        print('PASS: ' + phase + ' rejects remote mutation; deleted entries remain absent after restart')
    if phase == 'readonly':
        print('Candidate Memory Spaces CLI available:', status.get('commandFound', False))


def main():
    assert Path('/usr/bin/sandbox-exec').exists(), 'This candidate probe currently requires macOS Seatbelt'
    assert 'shell.top' not in (CLI.parents[2] / 'dsh-client-ui-layout/lib/client.js').read_text()
    with tempfile.TemporaryDirectory(prefix='stella-candidate-check-') as temporary, sync_playwright() as playwright, \
            ThreadingHTTPServer(('127.0.0.1', 0), CandidateProvider) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        home = Path(temporary).resolve()
        (home / 'documents').mkdir()
        profile = home / 'profiles/candidates'
        profile.mkdir(parents=True)
        (profile / 'node_modules').symlink_to(CANDIDATES)
        (profile / 'package.json').write_text(json.dumps({'name':'stella-candidates-fixture','private':True,
            'dependencies': {'dsh-email':'0.21.3','dsh-mnemon':'0.5.39'},
            'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app','dsh-email','dsh-mnemon']}}})+'\n')
        # Disable the community updater before it is ever loaded.
        (profile / 'stardustlc-plugin-updates.json').write_text(json.dumps({'schema':1,'revision':0,
            'packages':{'dsh-email':{'checkEnabled':False,'autoInstall':False,'deferredUntil':0,'ignoredVersion':None}}})+'\n')
        configuration = f'''- id: agent-preset-registry
  config:
    default: standard
- id: workspace-controller
  config:
    documentsDirectory: {home / 'documents'}
- id: mnemon
  config:
    storageScope: custom
    runtimeUserScope: storage
    dataDir: {home / 'memory'}
    lifecycleEnabled: false
    recallMode: off
    writebackMode: off
    writeEnabled: false
    embedding:
      enabled: false
- id: mnemon-source-memory-spaces
  config:
    providers:
      - use: dsh-mnemon-provider-mnemon-native
        instanceId: mnemon-native
- id: mnemon-source-skills
  disabled: true
- id: agent-default-model
  config:
    provider: stella-test
    model: stella-mock
- id: llm-pi-ai
  config:
    providers:
      stella-test:
        api: openai-completions
        apiKeyEnv: STELLA_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: stella-mock
            name: Synthetic model
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
- id: session-telemetry-otel
  disabled: true
'''
        (home / '.credentials.yaml').write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n')
        (home / '.credentials.yaml').chmod(0o600)
        # This probe's OS boundary is separate from the unimplemented product sandbox.
        policy = home / 'candidate.sb'
        policy.write_text(f'''(version 1)
(allow default)
(deny file-read* (subpath {json.dumps(str(Path.home()))}))
(deny file-write*)
(allow file-write* (subpath {json.dumps(str(home))}) (literal "/dev/null"))
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
''')
        environment = {key:value for key,value in os.environ.items() if key in ['PATH','TMPDIR','LANG']}
        environment.update(HOME=str(home), DSH_HOME=str(home), DSH_TELEMETRY_DISABLED='1', MNEMON_DATA_DIR=str(home / 'memory'))
        for phase in ['readonly', 'writable', 'active', 'revoked']:
            active = configuration.replace('writeEnabled: false', 'writeEnabled: true\n    remoteAccess: trusted-host') if phase in ['writable', 'active'] else configuration
            if phase == 'active':
                active = active.replace('lifecycleEnabled: false', 'lifecycleEnabled: true')
            (profile / 'cordis.patch.yml').write_text(active)
            log_path = home / (phase + '.log')
            log = log_path.open('w')
            process = subprocess.Popen(['/usr/bin/sandbox-exec','-f',str(policy),'node',str(CLI),
                '--profile','candidates','--no-open','--port','0'], cwd=home, env=environment, stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+35
                while True:
                    content=log_path.read_text()
                    match=re.search(r'dsh web: (http://\S+)',content)
                    if match: break
                    if process.poll() is not None or time.monotonic()>deadline:
                        raise AssertionError(re.sub(r'token=\S+','token=[redacted]',content)[-12000:])
                    time.sleep(0.1)
                browser=playwright.chromium.launch(headless=True)
                page=browser.new_page()
                page.goto(match.group(1))
                page.get_by_role('button',name='Account menu',exact=True).wait_for()
                bundles=rpc(page,match.group(1),'pluginManager/listBundles')
                for name in ['dsh-email','dsh-mnemon']:
                    bundle=next(item for item in bundles if item['name']==name)
                    assert not bundle.get('error'), (name,bundle.get('error'))
                check_memory(page, match.group(1), phase)
                browser.close()
            finally:
                stop(process)
                log.close()
        print('PASS: unpatched official host loads candidates under OS restrictions denying personal-home reads and external network')
        server.shutdown()
        print('NOT RUN: authenticated email, Memory Spaces recall, background lifecycle and production admission')


if __name__=='__main__':
    main()
