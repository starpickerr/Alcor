"""Exercise the subscription plugin on signed DSH with a disposable Profile and synthetic OpenAI transport."""
import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from web_check import settings


def profile(home, fixture):
    path = home / 'dsh/profiles/desktop'
    path.mkdir(parents=True)
    initialize_fresh(fixture, home / 'dsh', path)
    docs = home / 'documents'
    docs.mkdir()
    (home / 'dsh/synthetic-plan.json').write_text('{}')
    (path / 'package.json').write_text(json.dumps({'name': 'synthetic-plan', 'private': True, 'dsh': {
        'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
    (path / 'cordis.yml').write_text('[]\n')
    (path / 'pnpm-workspace.yaml').write_text('packages: ["."]\nnodeLinker: hoisted\nautoInstallPeers: false\n')
    test_plugin = Path(__file__).with_name('fixtures') / 'chatgpt-plan.mjs'
    (path / 'cordis.patch.yml').write_text(f'- insert:\n    - id: synthetic-plan-transport\n      name: {json.dumps(str(test_plugin))}\n'
        f'- id: workspace-controller\n  config:\n    documentsDirectory: {docs}\n'
        '- id: session-telemetry-otel\n  disabled: true\n')
    return docs


def request(page, endpoint, **kwargs):
    return desktop.rpc(page, endpoint, {'request': kwargs})


def login(page, home):
    begin = lambda: request(page, 'alcorChatgpt/beginLogin')['browserUrl']
    first = begin()
    assert urlsplit(first).hostname == '127.0.0.1'
    assert not request(page, 'alcorChatgpt/cancelLogin')['pending']
    response = page.request.get(begin(), max_redirects=0)
    assert response.status == 302
    url = urlsplit(response.headers['location'])
    assert url.netloc == 'auth.openai.com'
    params = parse_qs(url.query)
    assert params['client_id'] == ['dynamic_agent_client']
    assert params['code_challenge_method'] == ['S256']
    callback = params['redirect_uri'][0]
    assert urlsplit(callback).hostname == '127.0.0.1'
    (home / 'dsh/synthetic-plan.json').write_text(json.dumps({'nonce': params['nonce'][0], 'challenge': params['code_challenge'][0]}))
    response = page.request.get(callback + '?' + urlencode({'state': params['state'][0], 'code': 'synthetic', 'client_id': 'oaiapp_synthetic_dsh'}))
    assert response.ok, response.status
    state = request(page, 'alcorChatgpt/accountState')
    assert state['accounts'][0]['connected'] and state['activeId']


def model_catalog(page):
    return json.dumps(desktop.rpc(page, 'session/modelCatalog'))


def check_login_control(page):
    dialog = settings(page)
    dialog.get_by_role('navigation').get_by_role('button', name=re.compile('^(Models?|模型)$')).click()
    expect(dialog.get_by_role('button', name=re.compile('使用 ChatGPT 订阅登录'))).to_be_visible()
    page.keyboard.press('Escape')


def conversations(page, home, sessions):
    def logged(event):
        path = home / 'dsh/synthetic-events'
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if path.exists() and event in path.read_text().splitlines():
                return
            page.wait_for_timeout(100)
        raise AssertionError('Native chat did not reach ' + event)

    for preset, session in sessions.items():
        opts = {'sessionId': session}
        request(page, 'session/selectModel', **opts, provider='alcor-chatgpt', model='plan-test')
        def prompt(marker):
            request(page, 'session/prompt', **opts, requestId=str(uuid4()), mode='queue',
                    content=[{'type':'text','text':marker + '-' + preset}])
        prompt('TOOL')
        logged('result-TOOL-' + preset)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if 'Verified TOOL-' + preset in json.dumps(request(page, 'session/projections', **opts)):
                break
            page.wait_for_timeout(100)
        else:
            raise AssertionError('Native assistant response missing for ' + preset)
        prompt('STOP')
        logged('STOP-' + preset)
        request(page, 'session/cancel', **opts)
        logged('aborted-STOP-' + preset)
        print('PASS', preset, 'existing chat, tool result, streamed reply and cancellation', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', nargs=2, type=Path)
    args = parser.parse_args()
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    original = desktop.environment
    desktop.environment = lambda home: {**original(home), 'PYTHONDONTWRITEBYTECODE': '1'}
    with fresh_test_home('alcor-plan-') as fixture, sync_playwright() as playwright:
        home = Path(fixture)
        docs = profile(home, fixture)
        desktop.native_cli(home, 'plan-install', 'add', '--ignore-scripts', *(str(p.resolve(strict=True)) for p in args.archives))
        with desktop.desktop(playwright, home, 'subscription') as (page, errors):
            assert not request(page, 'alcorChatgpt/accountState')['accounts']
            check_login_control(page)
            sessions = {preset: request(page, 'session/create', cwd=str(docs), agentPreset=preset)['sessionId']
                        for preset in ['standard','alcor']}
            login(page, home)
            catalog = model_catalog(page)
            assert 'plan-test' in catalog and 'hidden-test' not in catalog
            conversations(page, home, sessions)
            assert not errors, errors
        with desktop.desktop(playwright, home, 'subscription-restart') as (page, errors):
            state = request(page, 'alcorChatgpt/accountState')
            assert state['activeId'] and 'plan-test' in model_catalog(page)
            assert not request(page, 'alcorChatgpt/signOut', accountId=state['activeId'])['activeId']
            assert 'plan-test' not in model_catalog(page)
            assert not errors, errors
        print('PASS signed Desktop login, account model catalog, restart and logout', flush=True)


if __name__ == '__main__':
    main()
