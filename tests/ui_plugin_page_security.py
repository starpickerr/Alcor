"""Real browser proof: scoped Bridge works; same-nonce foreign messages do not."""
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
backend = 'http://127.0.0.1:9876'
identity = dict(pluginId='stella.test', pageId='main', channelNonce='test-page-nonce', generation=1)
calls, forbidden = [], []
content = '''<!doctype html><body><script nonce="test-page-nonce">
try { document.cookie; document.body.dataset.cookieBlocked='false'; }
catch { document.body.dataset.cookieBlocked='true'; }
fetch('/api/forbidden').then(()=>document.body.dataset.fetchBlocked='false',()=>document.body.dataset.fetchBlocked='true');
addEventListener('message', event=>{if(event.data.type==='stella-bridge-result')document.body.append(event.data.value);});
parent.postMessage({type:'stella-bridge-invoke', requestId:'normal', pluginId:'stella.test',pageId:'main',channelNonce:'test-page-nonce',generation:1,method:'list_items',input:{}},'*');
</script></body>'''


def serve(route):
    path = urlparse(route.request.url).path
    if path == '/api/plugins/pages/open':
        body = dict(status='ok', result={**identity, 'contentUrl': '/plugin-content'})
    elif path == '/api/plugins/bridge/invoke':
        calls.append(route.request.post_data_json)
        body = dict(status='ok', value='normal-business-available')
    elif path == '/plugin-content':
        route.fulfill(status=200, content_type='text/html', body=content,
                      headers={'Content-Security-Policy': "default-src 'none'; script-src 'nonce-test-page-nonce'; connect-src 'none'"})
        return
    else:
        forbidden.append(path)
        body = dict(status='error')
    route.fulfill(status=200, content_type='application/json', body=json.dumps(body),
                  headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true'})


root = Path(__file__).resolve().parents[1] / 'desktop/src/renderer'
with tempfile.NamedTemporaryFile(mode='w', dir=root, suffix='.html', prefix='stella-test-plugin-', delete=False) as output:
    output.write('''<!doctype html><div id="test-plugin"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import PluginPageHost from './src/pages/plugins/PluginPageHost.tsx';
createRoot(document.getElementById('test-plugin')).render(React.createElement(PluginPageHost,{baseUrl:'http://127.0.0.1:9876',pluginId:'stella.test',pageId:'main',title:'Test plugin'}));
</script>''')
    probe_path = Path(output.name)
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.route(backend + '/**', serve)
            page.goto(url + '/' + probe_path.name, wait_until='networkidle')
            frame = page.frame_locator('iframe[title="Test plugin"]')
            expect(frame.locator('body')).to_contain_text('normal-business-available')
            expect(frame.locator('body')).to_have_attribute('data-cookie-blocked', 'true')
            expect(frame.locator('body')).to_have_attribute('data-fetch-blocked', 'true')
            assert len(calls) == 1 and not forbidden, (calls, forbidden)
            page.evaluate('payload => window.postMessage(payload,"*")', {
                **identity, 'type': 'stella-bridge-invoke', 'requestId': 'forged', 'method': 'delete_everything', 'input': {}})
            page.wait_for_timeout(150)
            assert len(calls) == 1, calls
            page.set_viewport_size({'width': 620, 'height': 900})
            expect(frame.locator('body')).to_contain_text('normal-business-available')
            print('PASS: real iframe scoped business call, opaque origin, no direct API fetch, foreign same-nonce message rejected, narrow window')
        finally:
            browser.close()
finally:
    probe_path.unlink(missing_ok=True)
