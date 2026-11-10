"""Real iframe and Core/plugin workers; synthetic CLI, isolated state only."""
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.plugins.page_host import PluginPageHost
from core.services import agent_hub_gateway as gateway

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'stella-plugins-workspace/plugins/agent-hub'
os.environ['STELLA_AGENT_HUB_PLUGIN'] = '1'
os.environ['STELLA_AGENT_HUB_PLUGIN_ROOT'] = str(PACKAGE)
URL = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
API = 'http://127.0.0.1:9876'
host = PluginPageHost()
channel = host.bridges.open(plugin_id='stella.agent-hub', user_id='desktop', instance_id='browser', generation=1, page_id='agent-hub.main')
identity = dict(pluginId=channel.plugin_id, pageId=channel.page_id, channelNonce=channel.channel_nonce, generation=1)
fail_save = [False]


def serve(route):
    path = urlparse(route.request.url).path
    if path == '/api/plugins/pages/open':
        query = '&'.join(f'{key}={value}' for key, value in identity.items())
        value = {'status': 'ok', 'result': {**identity, 'contentUrl': '/hub/index.html?' + query}}
    elif path.startswith('/hub/'):
        file = PACKAGE / 'ui/pages/agent-hub.main' / Path(path).name
        route.fulfill(status=200, content_type='text/html' if file.suffix == '.html' else 'application/javascript', body=file.read_text())
        return
    elif path == '/api/plugins/bridge/invoke':
        body = route.request.post_data_json
        try:
            if fail_save[0] and body['method'] == 'update_participant':
                raise ValueError('模拟保存失败')
            result = host.invoke(channel_nonce=body['channelNonce'], plugin_id=body['pluginId'], page_id=body['pageId'], generation=body['generation'], method=body['method'], input=body['input'])
            value = {'status': 'ok', 'value': result}
        except Exception as exc:
            value = {'status': 'error', 'message': str(exc)}
    else:
        raise AssertionError(path)
    route.fulfill(status=200, content_type='application/json', body=json.dumps(value), headers={'Access-Control-Allow-Origin': URL, 'Access-Control-Allow-Credentials': 'true'})


with tempfile.TemporaryDirectory(prefix='hub-cli-') as directory:
    binary = Path(directory) / 'codex'
    binary.write_text(f'''#!{sys.executable}
import json,sys
if '--version' in sys.argv: print('synthetic 1')
else:
 print(json.dumps({{'type':'tool_call','text':'only in process'}}))
 print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':'浏览器正式结果 <script>unsafe()</script>'}}}}))
''')
    binary.chmod(0o755)
    configured_provider = host.invoke(channel_nonce=channel.channel_nonce, plugin_id=channel.plugin_id, page_id=channel.page_id, generation=1, method='upsert_provider', input={'provider': {'providerLocalId': 'codex-cli', 'binaryPath': str(binary)}})
    renderer = ROOT / 'desktop/src/renderer'
    with tempfile.NamedTemporaryFile(mode='w', dir=renderer, suffix='.html', prefix='hub-check-', delete=False) as file:
        file.write('''<!doctype html><html style="--bg-base:#f9fafb;--bg-surface:#ffffff;--text-primary:#1e293b;color-scheme:light"><style>html,body,#root{margin:0;height:100%}#root{display:flex}iframe{flex:1;width:100%;border:0}</style><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import PluginPageHost from './src/pages/plugins/PluginPageHost.tsx';
createRoot(document.getElementById('root')).render(React.createElement(PluginPageHost,{baseUrl:'http://127.0.0.1:9876',pluginId:'stella.agent-hub',pageId:'agent-hub.main',title:'Agent 协作'}));
</script></html>''')
        probe = Path(file.name)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1360, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: print('CONSOLE:',msg.text) if msg.type=='error' else None)
            page.route(API + '/**', serve)
            page.goto(URL + '/' + probe.name)
            frame = page.frame_locator('iframe')
            frame.locator('#new-task').click()
            frame.get_by_label('任务标题', exact=True).fill('浏览器协作验收')
            frame.get_by_label('目标', exact=True).fill('完成小任务')
            frame.get_by_label('Codex · CLI exec', exact=False).check()
            frame.get_by_role('button', name='创建任务', exact=True).click()
            try:
                expect(frame.locator('#task-title')).to_have_text('浏览器协作验收')
            except AssertionError:
                print('CREATE ERROR:', frame.locator('#create-error').text_content(), frame.locator('#notice').text_content(), errors)
                raise
            frame.get_by_role('button', name='Agents', exact=True).click()
            frame.get_by_label('任务内人设', exact=True).fill('先检查边界')
            frame.get_by_role('combobox', name='Codex · CLI exec 模型选择', exact=True).select_option('explicit')
            frame.get_by_role('textbox', name='Codex · CLI exec 模型 ID', exact=True).fill('test-model')
            fail_save[0] = True
            frame.get_by_role('button', name='保存设定', exact=True).click()
            expect(frame.get_by_role('status')).to_contain_text('模拟保存失败')
            expect(frame.get_by_label('任务内人设', exact=True)).to_have_value('先检查边界')
            fail_save[0] = False
            frame.get_by_role('button', name='保存设定', exact=True).click()
            expect(frame.get_by_role('status')).to_contain_text('设定已保存')
            participant_id = frame.locator('#recipient option').nth(1).get_attribute('value')
            frame.get_by_role('combobox', name='消息接收者', exact=True).select_option(participant_id)
            frame.get_by_role('textbox', name='输入消息', exact=True).fill('人设：对话设定')
            assert frame.get_by_role('textbox', name='输入消息', exact=True).evaluate(
                "node => node.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',ctrlKey:true,isComposing:true,bubbles:true,cancelable:true}))")
            expect(frame.get_by_role('textbox', name='输入消息', exact=True)).to_have_value('人设：对话设定')
            frame.get_by_role('button', name='发送', exact=True).click()
            expect(frame.get_by_label('任务内人设', exact=True)).to_have_value('对话设定')
            frame.get_by_role('combobox', name='加入参与者', exact=True).select_option(configured_provider['providerConfigId'])
            frame.get_by_role('button', name='加入任务', exact=True).click()
            expect(frame.get_by_label('任务内人设', exact=True)).to_have_count(2)
            frame.get_by_role('combobox', name='任务负责人', exact=True).select_option(index=1)
            expect(frame.get_by_role('combobox', name='任务负责人', exact=True)).not_to_have_value(participant_id)
            frame.get_by_role('button', name='编辑流程', exact=True).click()
            frame.get_by_role('button', name='＋ 添加步骤', exact=True).click()
            frame.get_by_role('textbox', name='步骤目标', exact=True).fill('完成并答复')
            frame.get_by_role('button', name='保存流程', exact=True).click()
            frame.get_by_role('button', name='启动', exact=True).click()
            expect(frame.locator('#messages')).to_contain_text('浏览器正式结果', timeout=15000)
            expect(frame.locator('#messages')).not_to_contain_text('only in process')
            assert frame.locator('#messages script').count() == 0
            frame.get_by_role('button', name='查看过程', exact=True).click()
            expect(frame.get_by_role('log')).to_contain_text('only in process')
            frame.get_by_role('button', name='流程', exact=True).click()
            expect(frame.get_by_role('img', name='步骤依赖图；下方步骤列表可操作')).to_be_visible()
            page.screenshot(path='/tmp/stella-hub-desktop-light.png', full_page=True)
            page.evaluate("document.documentElement.style.cssText='--bg-base:#111827;--bg-surface:#18212f;--bg-surface-2:#243041;--bg-inset:#243041;--text-primary:#f3f4f6;--text-secondary:#cbd5e1;--text-tertiary:#94a3b8;--border-default:#334155;--accent:#4abe6e;--accent-soft:#203b2b;--accent-contrast:#102117;color-scheme:dark'")
            expect(frame.locator('html')).to_have_css('color-scheme', 'dark')
            page.screenshot(path='/tmp/stella-hub-desktop-dark.png', full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            frame.get_by_role('button', name='对话', exact=True).click()
            expect(frame.get_by_role('textbox', name='输入消息', exact=True)).to_be_visible()
            frame.get_by_role('textbox', name='输入消息', exact=True).fill('草稿保留')
            frame.get_by_role('button', name='详情', exact=True).click()
            frame.get_by_role('button', name='Agents', exact=True).click()
            frame.get_by_label('任务内人设', exact=True).first.fill('未保存人设')
            frame.get_by_role('button', name='对话', exact=True).click()
            expect(frame.get_by_role('textbox', name='输入消息', exact=True)).to_have_value('草稿保留')
            frame.get_by_role('button', name='详情', exact=True).click()
            expect(frame.get_by_label('任务内人设', exact=True).first).to_have_value('未保存人设')
            assert page.frames[1].evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path='/tmp/stella-hub-narrow-dark.png', full_page=True)
            assert not errors, errors
            browser.close()
            print('PASS: real host iframe, create/configure/workflow/execute/process, save failure preserves input, output escaping, graph, theme and narrow layout')
    finally:
        probe.unlink(missing_ok=True)
        gateway.shutdown_agent_hub_session()
