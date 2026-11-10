"""Verify optional activation and a model request on the unpatched official DSH host."""
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright
from plugin_check import CLI, NativeProvider, launch
from web_check import rpc, stop, open_secretary, wait_secretary, start_secretary_chat

ROOT = Path(__file__).resolve().parents[2]


def check(home, keychain, security):
    privacy_version = json.loads((ROOT / 'packages/alcor-privacy/package.json').read_text())['version']
    privacy = ROOT / f'.local/artifacts/alcor-privacy-{privacy_version}.tgz'
    version = json.loads((ROOT / 'packages/alcor-secretary/package.json').read_text())['version']
    secretary = ROOT / f'.local/artifacts/alcor-secretary-{version}.tgz'
    assert privacy.exists() and secretary.exists(), 'Pack privacy and secretary before this check'
    assert 'shell.top' not in (CLI.parents[2] / 'dsh-client-ui-layout/lib/client.js').read_text()
    home.mkdir()
    profile = home / 'profiles/keychain'
    profile.mkdir(parents=True)
    (profile / 'package.json').write_text(json.dumps({'name': 'stella-keychain-host-fixture', 'private': True,
        'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}) + '\n')
    environment = {key: value for key, value in os.environ.items() if key in ['PATH', 'TMPDIR', 'LANG']}
    environment.update(HOME=str(home), DSH_HOME=str(home), DSH_TELEMETRY_DISABLED='1',
        PATH=str(ROOT / 'node_modules/.bin') + os.pathsep + environment['PATH'])
    credentials = home / '.credentials.yaml'
    original = 'version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: old-file-value-is-retained\n'
    credentials.write_text(original)
    credentials.chmod(0o600)
    with (home / 'install.log').open('w') as log:
        subprocess.run(['node', str(CLI), 'plugin', '--profile', 'keychain', 'add', str(privacy), str(secretary), '--ignore-scripts'],
            cwd=home, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
    assert not (profile / 'cordis.patch.yml').exists(), 'Installing privacy must not replace the active credential provider'
    server = ThreadingHTTPServer(('127.0.0.1', 0), NativeProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = f'''- id: agent-default-model
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
    (profile / 'cordis.patch.yml').write_text(config)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1280, 'height': 850})
            with (home / 'disabled.log').open('w') as log:
                process, url = launch(environment, log, profile='keychain')
                try:
                    page = context.new_page()
                    page.goto(url)
                    wait_secretary(page)
                    value = rpc(page, url, 'credentials/describe', {'refs': ['STELLA_SYNTHETIC_KEY']})
                    assert value['STELLA_SYNTHETIC_KEY']['source'] == 'file', value
                    page.close()
                finally:
                    stop(process)
            retained_credentials = credentials.read_text()
            assert 'old-file-value-is-retained' in retained_credentials
            active = config + f'''- id: credentials
  disabled: true
- id: alcor-keychain
  disabled: false
  config:
    keychainPath: {json.dumps(os.environ['STELLA_TEST_KEYCHAIN'])}
'''
            (profile / 'cordis.patch.yml').write_text(active)
            for iteration in range(2):
                with (home / f'active-{iteration}.log').open('w') as log:
                    process, url = launch(environment, log, profile='keychain')
                    try:
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.add_locator_handler(page.get_by_role('button', name='Continue', exact=True), lambda button: button.click())
                        page.goto(url)
                        wait_secretary(page)
                        if iteration == 0:
                            assert not rpc(page, url, 'credentials/describe', {'refs': ['STELLA_SYNTHETIC_KEY']})['STELLA_SYNTHETIC_KEY']['configured']
                            rpc(page, url, 'credentials/set', {'ref': 'STELLA_SYNTHETIC_KEY', 'value': 'synthetic-test-key'})
                        value = rpc(page, url, 'credentials/describe', {'refs': ['STELLA_SYNTHETIC_KEY']})
                        assert value == {'STELLA_SYNTHETIC_KEY': {'configured': True, 'writable': True, 'source': 'keychain'}}, value
                        if iteration == 0:
                            open_secretary(page)
                            start_secretary_chat(page)
                            composer = page.locator('[data-composer-input][contenteditable="true"]')
                            composer.fill('Call alcor_overview using the Keychain-backed synthetic provider')
                            composer.press('Enter')
                            page.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=25000)
                        else:
                            assert security.SecKeychainLock(keychain) == 0
                            try:
                                response = page.request.post(url.split('/?')[0] + '/api/credentials/describe', data={
                                    'type': 'client-request', 'rpcId': 'keychain-locked', 'method': 'credentials/describe',
                                    'payload': {'args': {'refs': ['STELLA_SYNTHETIC_KEY']}}})
                                result = response.json()['result']
                                assert not result['ok'] and 'Keychain unavailable' in result['error']['message'], result
                                assert 'synthetic-test-key' not in response.text()
                            finally:
                                password = b'isolated-stella-dsh-synthetic-keychain'
                                assert security.SecKeychainUnlock(keychain, len(password), password, True) == 0
                            assert rpc(page, url, 'credentials/describe', {'refs': ['STELLA_SYNTHETIC_KEY']}) == value
                        assert credentials.read_text() == retained_credentials, credentials.read_text()
                        assert not errors, errors
                        page.close()
                    except Exception:
                        print('Isolated host log:', re.sub(r'token=\S+', 'token=[redacted]', Path(log.name).read_text())[-4000:])
                        raise
                    finally:
                        stop(process)
            browser.close()
    finally:
        server.shutdown()
    print('PASS: stock DSH native install defaults disabled, explicit provider activation, native credential Remote, synthetic model/tool round trip, restart, lock refusal and unlock recovery; original credential file retained')
