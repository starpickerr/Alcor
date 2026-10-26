"""Security Center shows product modes and Full Access limits."""

import json
import os
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
DEFINITIONS = [
    {'id': 'read-only', 'name': {'zh': '只读', 'en': 'Read Only'},
     'description': {'zh': '读取', 'en': 'Read'}, 'rules': {}},
    {'id': 'workspace-write', 'name': {'zh': '工作区写入', 'en': 'Workspace Write'},
     'description': {'zh': '写入', 'en': 'Write'}, 'rules': {}},
    {'id': 'full-access', 'name': {'zh': '完全访问', 'en': 'Full Access'},
     'description': {'zh': '放宽', 'en': 'Relax'}, 'rules': {}},
    {'id': 'custom', 'name': {'zh': '自定义', 'en': 'Custom'},
     'description': {'zh': '调整', 'en': 'Adjust'}, 'rules': {}},
]


saved = []
secret_saves = []
connection_saves = []
runtime_rebuilds = []
runtime_resumes = []
pending_rebuild = []
runtime_rows = [
    {'kind': 'plugin', 'id': 'stella.example-rebuild', 'mode': 'stopped', 'running': False,
     'reason': 'Worker has not started.', 'can_rebuild': True,
     'environment': {'id': 'old', 'base': {'version': '3.12.8'}, 'state': 'ready'}},
    {'kind': 'mcp', 'id': 'remote-example', 'mode': 'remote', 'reason': 'Remote connection.'},
    {'kind': 'plugin', 'id': 'stella.interrupted', 'mode': 'stopped', 'can_rebuild': False,
     'reason': 'Worker has not started.', 'rebuild_operation': 'resume-own'},
    {'kind': 'mcp', 'id': 'local-mcp', 'mode': 'stopped', 'can_rebuild': True,
     'reason': 'Server has not started.', 'environment': {'id': 'old-mcp', 'base': {'version': '3.12.8'}, 'state': 'ready'}},
    {'kind': 'mcp', 'id': 'interrupted-mcp', 'mode': 'stopped', 'can_rebuild': False,
     'reason': 'Server has not started.', 'rebuild_operation': 'resume-mcp'},
]
connection = {'id': 'service-test', 'name': 'Test service', 'endpoint': 'https://service.invalid', 'owners': ['mcp:test'], 'enabled': True, 'revision': 0, 'active_requests': 0}
project_chat = [False]
project_mode = ["full-access"]
policies = {name: {'filesystem_mode': 'workspace-write', 'capabilities': {'plugin:stella.tasks': {'read': 'allow', 'write': 'deny'}}, 'policy_revision': 1} for name in ('default', 'project-chat')}
policies['project-chat']['filesystem_mode'] = 'full-access'
policies['default']['filesystem_mode'] = 'read-only'


def backend(route):
    path = urlparse(route.request.url).path
    data = {'status': 'success'}
    if path == '/api/security/run-policy':
        if parse_qs(urlparse(route.request.url).query).get('view') == ['inventory']:
            data['subjects'] = [dict(agent='default', kind='session', subject=key, policy=value, source='independent', migration_status='complete', original_url='/') for key,value in policies.items() if key != 'default']
            route.fulfill(json=data, headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true'})
            return
        if route.request.method == 'POST':
            body = route.request.post_data_json
            saved.append(body)
            policies[body['subject']] = {**body['policy'], 'policy_revision': policies[body['subject']]['policy_revision'] + 1}
            subject = body['subject']
        else:
            subject = parse_qs(urlparse(route.request.url).query)['subject'][0]
        data.update(policy=policies.setdefault(subject, dict(policies['default'])), capabilities=[{'id': 'plugin:stella.tasks', 'name': '事务', 'source': 'plugin', 'installed': True, 'enabled': True}], migration_status='complete', compatibility_restrictions={})
    elif path == '/api/security/sensitive-resources':
        data.update(credentials=[{'id': f'model-test-{index}', 'owners': ['model:test'], 'account': f'Test {index}', 'kind': 'api-key', 'configured': True, 'configure_url': '/models?credential=OPENAI_API_KEY', 'reason': ''} for index in range(30)], folder_protection={'protected': False, 'status': 'unavailable', 'code': 'application_guard_not_verified', 'reason': 'Not deployed'})
    elif path == '/api/security/connections':
        if route.request.method == 'POST':
            body = route.request.post_data_json
            connection_saves.append(body)
            connection.update(enabled=body['enabled'], revision=connection['revision'] + 1)
        data['connections'] = [connection]
    elif path in ('/api/security/runtime-trust', '/api/security/runtime'):
        if route.request.method == 'POST':
            body = route.request.post_data_json
            if body['action'] == 'resume':
                runtime_resumes.append(body)
                selected_runtime = next(row for row in runtime_rows if row['id'] == body['id'])
                selected_runtime.pop('rebuild_operation')
                selected_runtime['can_rebuild'] = True
            else:
                runtime_rebuilds.append(body)
                if len(runtime_rebuilds) == 1:
                    pending_rebuild.append(route)
                    return
                next(row for row in runtime_rows if row['id'] == body['id'])['environment']['id'] = 'rebuilt'
        data.update(runtimes=runtime_rows, isolation={'status': 'enabled', 'reason': 'OS isolation'}, application_isolation={'protected': False, 'status': 'unavailable', 'reason': 'Not deployed', 'unprotected': ['core', 'desktop-main', 'desktop-helpers']})
    elif path == '/api/security/migration':
        data.update(verification={'status': 'applied', 'valid': True}, preview={'pending': 0, 'needs_confirmation': 0, 'fingerprint': 'test'})
    if path == '/api/health':
        data = {'status': 'ok'}
    elif path == '/auth/check':
        data.update(auth_required=False, authenticated=True)
    elif path == '/api/models':
        blank = {'current_provider': '', 'current_model': '', 'providers': []}
        data.update(providers=[{'id': 'test', 'label': 'Test', 'configured': True, 'api_key_field': 'open_ai_api_key', 'api_key_masked': '********', 'models': ['test'], 'api_base_field': 'open_ai_api_base', 'api_base': 'https://api.example.invalid/v1'}], capabilities={
            'chat': {'current_provider': 'test', 'current_model': 'test', 'providers': ['test']},
            'vision': blank, 'image': blank, 'asr': blank,
            'tts': {**blank, 'current_voice': '', 'reply_mode': 'off'},
            'embedding': {**blank, 'current_dim': 0},
            'search': {'strategy': 'auto', 'providers': [], 'fixed_provider': '', 'current_provider': ''},
        })
    elif path in ('/api/sessions', '/api/history', '/api/projects'):
        data.update(sessions=[], history=[], messages=[], total=0, page=1, page_size=20,
                    has_more=False, projects=[], current=None)
    elif path == '/api/security/center':
        data.update(
            global_policy={**policies['default'], 'preset': policies['default'].get('permission_mode', policies['default']['filesystem_mode'])},
            effective_policy={'preset': 'workspace-write', 'rules': {}, 'connections': {}},
            effective_mode='workspace-write', effective_rules={}, policy_source='global',
            mode_definitions=DEFINITIONS, authorized_roots=['/isolated/work'],
            process_isolation={'status': 'enabled', 'reason': 'Seatbelt is active. 完全访问仍拦截其他会话的私有工作区。'},
            connections=[
                {'id': 'email', 'name': 'Email', 'connected': True, 'allowed': True, 'scopes': [], 'last_access': None},
                {'id': 'mcp:github', 'name': 'GitHub MCP', 'connected': True, 'allowed': False, 'scopes': ['read'], 'last_access': None},
                {'id': 'github', 'name': 'GitHub', 'connected': False, 'allowed': True, 'scopes': [], 'last_access': None},
            ], secrets=[
                {'name': 'EMPTY_TOKEN', 'source': 'credential store', 'configured': False},
                {'name': 'UNAVAILABLE_TOKEN', 'source': 'credential store', 'configured': None},
                {'name': 'DEEPSEEK_API_KEY', 'source': 'model configuration', 'configured': True},
                {'name': 'CUSTOM:6891955f', 'display_name': 'packyapi',
                 'source': 'custom model provider', 'configured': True},
                {'name': 'CUSTOM:c483bd9c', 'display_name': '灵算',
                 'source': 'custom model provider', 'configured': True},
                {'name': 'CUSTOM:unnamed', 'source': 'custom model provider', 'configured': True},
            ], pending=[], background_tasks=[{'id': 'task-one', 'name': '每日整理', 'owner_agent_id': 'default', 'enabled': True, 'permission_source': 'independent', 'effective_mode': 'read-only'}],
            privacy={'available': True, 'categories': [
                {'id': 'conversations', 'name': {'zh': '对话记录', 'en': 'Conversations'}, 'bytes': 204800,
                 'path': '/isolated/conversations', 'retention': {'zh': '保留至删除', 'en': 'Until deleted'}, 'actions': []},
                {'id': 'activity', 'name': {'zh': '安全活动', 'en': 'Security activity'}, 'bytes': 32768,
                 'path': '/isolated/activity', 'retention': {'zh': '保留至清理', 'en': 'Until cleared'}, 'actions': []},
            ], 'external_transfers': []},
            summary={'status': 'normal', 'app_password_set': False, 'connection_count': 2,
                     'secret_count': 4, 'recent_high_risk': [], 'recent_denials': [],
                     'activity_counts': {'auto_allow': 0, 'user_authorized': 0, 'denied': 0,
                                         'uncertain': False, 'since': 0, 'until': 1},
                     'background_tasks': {'total': 0, 'enabled': 0, 'paused': 0,
                                          'modes': {'read-only': 0, 'workspace-write': 0,
                                                    'full-access': 0, 'custom': 0},
                                          'unknown': 0, 'available': True}},
            project=None, project_policy={}, projects=[], conversation_policy={},
            data_access={'resources': [], 'external_transfers': [], 'inherited_grants': [
                {'id': 'legacy-course', 'server': 'elearning', 'consumer': 'stella.briefing',
                 'revoked': False, 'scoped': True, 'capabilities': ['elearning.read'], 'methods': ['fetch'],
                 'resources': [{'kind': 'course', 'id': 'account:42'}], 'purposes': ['interactive'],
                 'valid_until': None, 'revision': 1}]},
        )
    elif path.endswith('/settings') and path.startswith('/api/sessions/'):
        if route.request.method == 'POST':
            body = route.request.post_data_json
            if body.get('security_policy'):
                project_mode[0] = body['security_policy']['preset']
        data.update(model={'model': 'test', 'provider': 'test', 'source': 'global',
                           'global': {'model': 'test', 'provider': 'test'}, 'providers': []},
                    permission={'mode': project_mode[0], 'preset': project_mode[0], 'source': 'project',
                                'base_preset': 'workspace-write', 'rules': {},
                                'policy': {'preset': project_mode[0], 'rules': {}, 'connections': {}},
                                'project': '/isolated/project', 'authorized_roots': ['/isolated/project'],
                                'mode_definitions': DEFINITIONS})
    elif path == '/api/security/secrets' and route.request.method == 'POST':
        secret_saves.append(route.request.post_data_json)
    elif path == '/api/security/confirmations':
        data['pending'] = []
    elif path == '/api/security/activity':
        data['records'] = []
        data['counts'] = {'auto_allow': 0, 'user_authorized': 0, 'denied': 0,
                          'uncertain': False, 'since': 0, 'until': 1}
    elif path == '/api/tools':
        data['tools'] = []
    elif path == '/api/security/privacy':
        data.update(available=True, categories=[], external_transfers=[])
    if project_chat[0] and path == '/api/security/center':
        policy = {'preset': project_mode[0], 'rules': {}, 'connections': {}}
        data.update(effective_policy=policy, effective_mode=project_mode[0], policy_source='project',
                    project='/isolated/project', project_policy=policy,
                    projects=[{'path': '/isolated/project', 'name': 'Test project', 'policy': policy}])
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data),
                  headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true',
                           'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                           'Access-Control-Allow-Headers': 'Content-Type'})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script("""localStorage.setItem('stella_lang','zh'); window.electronAPI = {platform:'darwin', getBackendPort:async()=>9876, onBackendStatus:()=>()=>{}, getBackendError:async()=>null};""")
        page.route('http://127.0.0.1:9876/**', backend)
        page.goto(url + '/#/security', wait_until='networkidle')
        nav = page.get_by_role('navigation', name='安全中心')
        expect(nav.get_by_role('button')).to_have_count(5)
        for title in ('总览', 'Agent 权限', '运行隔离', '外部连接', '敏感资源保护'):
            expect(nav.get_by_role('button', name=title, exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name='安全活动', exact=True)).to_be_visible()
        nav.get_by_role('button', name='Agent 权限', exact=True).click()
        expect(page.get_by_role('radio', name='只读', exact=True)).to_be_checked()
        page.get_by_role('radio', name='自定义', exact=True).check()
        page.get_by_label('访问网络', exact=True).select_option('deny')
        page.get_by_role('button', name='保存权限', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('权限已保存')
        assert saved[-1]['policy']['rules']['network'] == 'deny'
        page.reload(wait_until='networkidle')
        expect(page.get_by_label('访问网络', exact=True)).to_have_value('deny')
        nav.get_by_role('button', name='外部连接', exact=True).click()
        toggle = page.get_by_role('switch', name='连接 Test service')
        expect(toggle).to_be_checked()
        toggle.click()
        expect(toggle).not_to_be_checked()
        assert connection_saves[-1] == {'id':'service-test', 'enabled':False, 'revision':0}
        nav.get_by_role('button', name='运行隔离', exact=True).click()
        colors = page.get_by_role('heading', name='执行环境', exact=True).evaluate('''node => ({
          foreground: getComputedStyle(node).color,
          background: getComputedStyle(node.closest('.bg-surface')).backgroundColor
        })''')
        def luminance(value):
            channels = [float(channel) / 255 for channel in re.findall(r'[\d.]+', value)[:3]]
            linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
            return sum(channel * weight for channel, weight in zip(linear, [0.2126, 0.7152, 0.0722]))
        light, dark = sorted([luminance(colors['foreground']), luminance(colors['background'])], reverse=True)
        assert (light + 0.05) / (dark + 0.05) >= 4.5, colors
        expect(page.get_by_text('应用级保护未就绪。Core 和 Electron 不在下述子进程沙箱的保护范围内。', exact=True)).to_be_visible()
        expect(page.get_by_role('checkbox')).to_have_count(0)
        rebuild = page.get_by_role('button', name='重建 stella.example-rebuild 的依赖环境', exact=True)
        expect(rebuild).to_be_visible()
        expect(page.get_by_role('button', name='重建 remote-example 的依赖环境', exact=True)).to_have_count(0)
        rebuild.click()
        expect(rebuild).to_be_disabled()
        assert runtime_rebuilds == [{'action': 'rebuild', 'kind': 'plugin', 'id': 'stella.example-rebuild'}]
        pending_rebuild.pop().fulfill(json={'status': 'error', 'message': 'Candidate validation failed; previous environment retained'},
            headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true'})
        expect(page.get_by_role('alert')).to_contain_text('previous environment retained')
        expect(rebuild).to_be_enabled()
        rebuild.click()
        expect(page.get_by_role('status').filter(has_text='依赖环境已重建')).to_be_visible()
        assert len(runtime_rebuilds) == 2
        resume = page.get_by_role('button', name='继续重建 stella.interrupted 的依赖环境', exact=True)
        expect(resume).to_be_visible()
        resume.click()
        expect(resume).to_have_count(0)
        assert runtime_resumes == [{'action': 'resume', 'kind': 'plugin', 'id': 'stella.interrupted', 'operationId': 'resume-own'}]
        mcp_rebuild = page.get_by_role('button', name='重建 local-mcp 的依赖环境', exact=True)
        expect(mcp_rebuild).to_be_visible()
        mcp_rebuild.click()
        expect(page.get_by_role('status').filter(has_text='依赖环境已重建')).to_be_visible()
        assert runtime_rebuilds[-1] == {'action': 'rebuild', 'kind': 'mcp', 'id': 'local-mcp'}
        mcp_resume = page.get_by_role('button', name='继续重建 interrupted-mcp 的依赖环境', exact=True)
        mcp_resume.click()
        expect(mcp_resume).to_have_count(0)
        assert runtime_resumes[-1] == {'action': 'resume', 'kind': 'mcp', 'id': 'interrupted-mcp', 'operationId': 'resume-mcp'}
        if os.environ.get('STELLA_UI_SCREENSHOTS'):
            page.screenshot(path=os.environ['STELLA_UI_SCREENSHOTS'] + '/security-runtime-mcp-desktop.png', full_page=True)
        page.set_viewport_size({'width': 320, 'height': 800})
        page.get_by_role('button', name='收起侧栏').click()
        expect(rebuild).to_be_visible()
        rebuild.scroll_into_view_if_needed()
        expect(rebuild).to_be_in_viewport()
        mcp_rebuild.scroll_into_view_if_needed()
        expect(mcp_rebuild).to_be_in_viewport()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        if os.environ.get('STELLA_UI_SCREENSHOTS'):
            page.screenshot(path=os.environ['STELLA_UI_SCREENSHOTS'] + '/security-runtime-rebuild-narrow.png', full_page=True)
        page.set_viewport_size({'width': 1280, 'height': 900})
        page.get_by_role('button', name='展开侧栏').click()
        nav.get_by_role('button', name='敏感资源保护', exact=True).click()
        expect(page.get_by_role('heading', name='凭据目录', exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name='受保护文件夹', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='添加受保护文件夹', exact=True)).to_be_disabled()
        if os.environ.get('STELLA_UI_SCREENSHOTS'):
            page.screenshot(path=os.environ['STELLA_UI_SCREENSHOTS'] + '/security-resources-desktop.png', full_page=True)
        expect(page.locator('input[type=password]')).to_have_count(0)
        owner_link = page.get_by_role('link', name='前往配置', exact=True).last
        owner_link.scroll_into_view_if_needed()
        source_scroll = page.locator('[data-stella-scroll]').evaluate('(node) => node.scrollTop')
        assert source_scroll > 500
        owner_link.click()
        expect(page).to_have_url(url + '/#/models?credential=OPENAI_API_KEY')
        modal = page.get_by_role('dialog')
        modal.get_by_role('button', name='配置凭据', exact=True).click()
        field = modal.get_by_label('密钥或密码', exact=True)
        expect(field).to_have_value('')
        field.fill('synthetic-browser-key')
        modal.get_by_role('button', name='保存凭据', exact=True).click()
        expect(field).to_have_value('')
        assert secret_saves[-1] == {'name':'OPENAI_API_KEY','value':'synthetic-browser-key'}
        modal.get_by_role('button').filter(has_text='×').click()
        page.evaluate('''() => {
          const original = window.fetch.bind(window)
          window.fetch = async (...args) => {
            const response = await original(...args)
            if (String(args[0]).includes('/api/security/sensitive-resources'))
              await new Promise(resolve => { window.releaseCredentialDirectory = resolve })
            return response
          }
        }''')
        page.get_by_role('button', name='返回原页面', exact=True).click()
        expect(page).to_have_url(url + '/#/security?tab=resources')
        page.wait_for_function('typeof window.releaseCredentialDirectory === "function"')
        page.evaluate('window.releaseCredentialDirectory()')
        expect(page.get_by_role('link', name='前往配置', exact=True)).to_have_count(30)
        expect(page.locator('[data-stella-scroll]')).to_have_js_property("scrollTop", source_scroll, timeout=3000)
        for old, target in [('secrets','resources'),('plugins','runtime'),('activity','overview'),('permissions','agent-permissions')]:
            page.goto(url + '/#/security?tab=' + old, wait_until='networkidle')
            expect(page).to_have_url(url + '/#/security' + ('' if target == 'overview' else '?tab=' + target))
        page.goto(url + '/#/security?tab=agent-permissions&kind=task&subject=task-one&returnTo=%2Ftasks', wait_until='networkidle')
        expect(page.get_by_role('link', name='返回原页面')).to_have_attribute('href', '#/tasks')
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button', name='收起侧栏').click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if os.environ.get('STELLA_UI_SCREENSHOTS'):
            page.screenshot(path=os.environ['STELLA_UI_SCREENSHOTS'] + '/security-five-pages-mobile.png', full_page=True)
        project_chat[0] = True
        page.goto(url + '/#/security?tab=permissions', wait_until='networkidle')
        page.evaluate("""async () => {
          const { mountPermissionSelector } = await import('/src/__test__/PermissionSelectorHarness.tsx')
          await mountPermissionSelector('project-chat')
        }""")
        chip = page.locator('#permission-selector-chip')
        expect(chip.locator('.composer-chip-label')).to_have_text('完全访问')
        chip.get_by_role('button').first.click()
        expect(chip.get_by_role('button', name='自定义', exact=True)).to_be_visible()
        expect(chip.get_by_text('仅作用于此对话。项目内对话共享文件，各自保存权限。', exact=True)).to_be_visible()
        chip.get_by_role('button', name='设置对话权限', exact=True).click()
        chip.get_by_role('radio', name='工作区写入', exact=True).check()
        chip.get_by_role('button', name='保存权限', exact=True).click()
        expect(chip.get_by_role('status')).to_contain_text('权限已保存')
        chip.get_by_role('button', name='对话权限', exact=True).click()
        chip.get_by_role('button').first.click()
        # A save finishing after switching chats must refresh the new chat.
        race = page.evaluate("""async () => {
          const {default: api} = await import('/src/api/client.ts')
          const {useSessionSettingsStore: store} = await import('/src/store/sessionSettingsStore.ts')
          const {mountPermissionSelector} = await import('/src/__test__/PermissionSelectorHarness.tsx')
          const originalGet = api.getSessionSettings, originalSave = api.updateSessionSettings
          const previous = await originalGet.call(api, 'project-chat')
          const next = {...previous, permission: {...previous.permission, mode: 'read-only', preset: 'read-only', policy: {...previous.permission.policy, filesystem_mode: 'read-only'}}}
          let finishSave, finishLoad
          const trace = []; const stopTrace = store.subscribe(state => trace.push({session: state.sessionId, loading: state.loading}))
          const saving = new Promise(resolve => {finishSave = resolve})
          const loading = new Promise(resolve => {finishLoad = resolve})
          api.updateSessionSettings = () => saving
          api.getSessionSettings = id => id === 'next-chat' ? loading : Promise.resolve(previous)
          try {
            const apply = store.getState().apply('project-chat', {security_policy: {preset: 'workspace-write'}})
            const mount = mountPermissionSelector('next-chat')
            finishSave(previous)
            await apply
            finishLoad(next)
            await mount
            await Promise.resolve()
            return {session: store.getState().sessionId, trace}
          } finally {stopTrace(); api.getSessionSettings = originalGet; api.updateSessionSettings = originalSave}
        }""")
        assert race['session'] == 'next-chat', race
        expect(chip.locator('.composer-chip-label')).to_have_text('只读')
        # Clearing a conversation must also invalidate an in-flight response.
        cleared = page.evaluate("""async () => {
          const {default: api} = await import('/src/api/client.ts')
          const {useSessionSettingsStore: store} = await import('/src/store/sessionSettingsStore.ts')
          const original = api.getSessionSettings, response = await original.call(api, 'next-chat')
          let finish
          api.getSessionSettings = () => new Promise(resolve => {finish = resolve})
          try {
            const loading = store.getState().refresh('old-chat')
            store.getState().reset()
            finish(response)
            await loading
            const state = store.getState()
            return {session: state.sessionId, cfg: state.cfg, loading: state.loading}
          } finally {api.getSessionSettings = original}
        }""")
        assert cleared == {'session': None, 'cfg': None, 'loading': False}, cleared
        # Exact owner metadata, including unavailable encrypted references,
        # must drive the editor rather than the mere presence of a list item.
        page.goto(url + '/#/security?tab=resources&credential=EMPTY_TOKEN', wait_until='networkidle')
        expect(page.get_by_text('尚未配置凭据', exact=True)).to_be_visible()
        page.goto(url + '/#/security?tab=resources&credential=UNAVAILABLE_TOKEN', wait_until='networkidle')
        expect(page.get_by_text('凭据状态不可用', exact=True)).to_be_visible()
        page.get_by_role('button', name='配置凭据', exact=True).click()
        expect(page.get_by_label('密钥或密码', exact=True)).to_have_value('')

        # A credential request for the previous owner cannot alter the new form.
        page.evaluate("""async () => {
          const {default: api} = await import('/src/api/client.ts')
          window.originalSecretSave = api.saveSecuritySecret
          api.saveSecuritySecret = () => new Promise(resolve => {window.finishSecretSave=resolve})
          const {mountCredentialEditor} = await import('/src/__test__/CredentialEditorHarness.tsx')
          mountCredentialEditor('OWNER_A_KEY')
        }""")
        editor = page.locator('#credential-test-root')
        editor.get_by_role('button', name='配置凭据', exact=True).click()
        editor.get_by_label('密钥或密码', exact=True).fill('synthetic-old-draft')
        editor.get_by_role('button', name='保存凭据', exact=True).click()
        page.evaluate("""async () => { const {mountCredentialEditor}=await import('/src/__test__/CredentialEditorHarness.tsx'); mountCredentialEditor('OWNER_B_KEY') }""")
        editor.get_by_role('button', name='配置凭据', exact=True).click()
        expect(editor.get_by_label('密钥或密码', exact=True)).to_be_enabled()
        editor.get_by_label('密钥或密码', exact=True).fill('synthetic-new-draft')
        page.evaluate("window.finishSecretSave({status:'success'})")
        expect(editor.get_by_label('密钥或密码', exact=True)).to_have_value('synthetic-new-draft')
        expect(editor.get_by_role('status')).to_have_count(0)
        page.evaluate("""async () => { const {default:api}=await import('/src/api/client.ts'); api.saveSecuritySecret=window.originalSecretSave }""")
        assert not errors, errors
        print('PASS: five security pages, owner credential configuration and return, defaults/custom, connection switches, truthful isolation, legacy links and narrow layout')
    finally:
        browser.close()
