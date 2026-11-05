"""Six local apps against the real packaged host; all data and model are synthetic."""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from playwright.sync_api import expect
from web_check import rpc, requests, open_secretary, start_secretary_chat


def check_app_theme(page):
    actions = [
        ('Agent 协作', '发送并执行', 'agents'), ('事务', '新建事务', 'tasks'),
        ('日程', '新建日程', 'calendar'), ('简报', '生成今日简报', 'briefing'),
        ('账单', '记账', 'bills'),
    ]
    for theme in ['dark', 'light']:
        page.emulate_media(color_scheme=theme)
        page.wait_for_function('dark => document.body.hasAttribute("data-ds-dark-theme") === dark', arg=theme == 'dark')
        for label, action, slug in actions:
            page.get_by_role('button', name=label, exact=True).click()
            page.get_by_role('heading', name=label, exact=True).wait_for()
            button = page.get_by_role('button', name=action, exact=True)
            expect(button).to_be_visible()
            assert button.evaluate('''button => {
                const probe = document.createElement('span')
                probe.style.backgroundColor = 'var(--dsw-alias-button-primary-fill)'
                probe.style.borderRadius = 'var(--dsw-radius-md)'
                document.body.append(probe)
                const expected = getComputedStyle(probe)
                const actual = getComputedStyle(button)
                const matches = actual.backgroundColor === expected.backgroundColor
                    && actual.borderRadius === expected.borderRadius
                    && actual.height === '36px'
                probe.remove()
                return matches
            }'''), (theme, label)
            if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                page.screenshot(path=str(Path(directory) / f'dsh-{slug}-{theme}.png'), animations='disabled')


def check_hub(page, read):
    page.get_by_role('button', name='Agent 协作', exact=True).click()
    expect(page.get_by_role('heading', name='Agent 协作', exact=True)).to_be_visible()
    page.locator('.stella-hub-task-list button').filter(has_text='Synthetic collaboration').click()
    expect(page.get_by_role('heading', name='Synthetic collaboration', exact=True)).to_be_visible()
    for label in ['过程', 'Agents', '流程']:
        page.get_by_role('tab', name=label, exact=True).click()
        expect(page.locator('.stella-hub-detail-content')).to_be_visible()
    page.get_by_role('button', name='管理接入', exact=True).click()
    dialog = page.get_by_role('dialog', name='管理接入', exact=True)
    expect(dialog.get_by_role('heading', name='Stella', exact=True)).to_be_visible()
    expect(dialog.get_by_text('尚未接入', exact=True)).to_have_count(3)
    page.keyboard.press('Escape')
    expect(page.get_by_role('button', name='管理接入', exact=True)).to_be_focused()
    count_before = len(requests)
    page.get_by_role('button', name='新建协作任务', exact=True).click()
    page.get_by_label('任务标题', exact=True).fill('UI hub task')
    page.get_by_label('目标', exact=True).fill('Only this task. UI_HUB_SENTINEL')
    page.get_by_role('button', name='创建任务', exact=True).click()
    composer = page.get_by_role('textbox', name='协作任务内容', exact=True)
    expect(composer).to_have_value('Only this task. UI_HUB_SENTINEL')
    expect(page.get_by_role('button', name='发送', exact=True)).to_be_disabled()
    page.get_by_role('tab', name='过程', exact=True).click()
    page.get_by_text('任务尚未启动，暂无执行记录。', exact=True).wait_for()
    open_secretary(page)
    page.get_by_role('button', name='Agent 协作', exact=True).click()
    expect(composer).to_have_value('Only this task. UI_HUB_SENTINEL')
    page.reload()
    page.get_by_role('button', name='Agent 协作', exact=True).click()
    expect(composer).to_have_value('Only this task. UI_HUB_SENTINEL')
    assert len(requests) == count_before, 'Saving or viewing a draft must not call a model'
    for theme in ['dark', 'light']:
        page.emulate_media(color_scheme=theme)
        page.wait_for_function('dark => document.body.hasAttribute("data-ds-dark-theme") === dark', arg=theme == 'dark')
        if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
            page.screenshot(path=str(Path(directory) / f'hub-draft-{theme}.png'), animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    for label in ['任务', '详情', '交流']:
        page.get_by_role('tab', name=label, exact=True).click()
        assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
        if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
            page.screenshot(path=str(Path(directory) / f'hub-mobile-{label}.png'), animations='disabled')
    expect(composer).to_have_value('Only this task. UI_HUB_SENTINEL')
    page.get_by_role('button', name='发送并执行', exact=True).scroll_into_view_if_needed()
    expect(page.get_by_role('button', name='发送并执行', exact=True)).to_be_in_viewport()
    page.set_viewport_size({'width': 1280, 'height': 850})
    page.get_by_role('button', name='发送并执行', exact=True).click()
    page.locator('.stella-hub-message').get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=25000)
    expect(page.get_by_role('button', name='发送并执行', exact=True)).to_be_disabled()
    assert page.evaluate('sessionStorage.getItem("stella.agent-hub.draft.v1")') is None
    job = next(item for item in read()['jobs'] if item['title'] == 'UI hub task')
    assert job['status'] == 'completed' and job['output'] and job['prompt'] == 'Only this task. UI_HUB_SENTINEL'
    delegated = [request for request in requests if any('UI_HUB_SENTINEL' in str(message.get('content', '')) for message in request.get('messages', []))]
    assert delegated and 'WORKSPACE_USER_SENTINEL' not in json.dumps(delegated), 'Private identity must not enter delegated jobs'
    page.get_by_role('tab', name='过程', exact=True).click()
    expect(page.get_by_role('button', name='查看原生会话', exact=True)).to_be_visible()
    page.get_by_role('button', name='历史委托', exact=True).click()
    page.get_by_role('dialog', name='历史委托', exact=True).get_by_role('button').filter(has_text='Synthetic collaboration').click()
    expect(page.get_by_role('heading', name='Synthetic collaboration', exact=True)).to_be_visible()
    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
        page.screenshot(path=str(Path(directory) / 'hub-completed.png'), animations='disabled')


def check_apps(page, url, compatible):
    def call(method, args=None):
        return rpc(page, url, 'stellaOrganizer/' + method, {'request': args or {}})
    def rejects(method, args):
        response = page.request.post(url.split('/?')[0] + '/api/stellaOrganizer/' + method, data={
            'type': 'client-request', 'rpcId': str(uuid4()), 'method': 'stellaOrganizer/' + method, 'payload': {'args': {'request': args}}})
        assert not response.ok or not response.json()['result']['ok']
    initial = call('read')
    assert initial == {'records': [], 'jobs': [], 'schedules': []}, initial
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    base = {'id': str(uuid4()), 'revision': 1, 'createdAt': now, 'updatedAt': now, 'archived': False, 'title': 'Synthetic task sentinel', 'details': 'PRIVATE_BUSINESS_SENTINEL'}
    task = call('write', {'record': {**base, 'kind': 'task', 'due': '2026-11-04', 'status': 'open'}, 'expectedRevision': 0})
    rejects('write', {'record': {**task, 'title': 'Lost concurrent write'}, 'expectedRevision': 0})
    rejects('write', {'record': {**task, 'unexpected': True}, 'expectedRevision': 1})
    if compatible:
        open_secretary(page)
        page.get_by_role('heading', name=task['title'], exact=True).wait_for()
        page.get_by_role('button', name='完成事务', exact=True).click()
        page.get_by_text('事务状态已更新', exact=True).wait_for()
        current = next(item for item in call('read')['records'] if item['id'] == task['id'])
        call('archive', {'id': task['id'], 'expectedRevision': current['revision'], 'archived': True})
        page.get_by_role('button', name='刷新', exact=True).click()
        expect(page.get_by_role('heading', name=task['title'], exact=True)).not_to_be_visible()
        archived_task = next(item for item in call('read')['records'] if item['id'] == task['id'])
        assert archived_task['archived']
        call('archive', {'id': task['id'], 'expectedRevision': archived_task['revision'], 'archived': False})
        page.get_by_role('button', name='刷新', exact=True).click()
        page.get_by_role('button', name='重新打开', exact=True).click()
        page.get_by_text('事务状态已更新', exact=True).wait_for()
        page.get_by_role('button', name=task['title'], exact=True).click()
        page.get_by_role('button', name='编辑详情', exact=True).click()
        page.get_by_label('标题', exact=True).fill('Edited synthetic task')
        page.get_by_role('button', name='保存事务', exact=True).click()
        page.get_by_role('heading', name='Edited synthetic task', exact=True).wait_for()
        page.get_by_role('button', name='返回事务树', exact=True).click()
    event = {**base, 'id': str(uuid4()), 'kind': 'event', 'title': 'Synthetic event', 'start': '2026-11-04', 'end': '2026-11-05', 'allDay': True, 'timeZone': 'Asia/Shanghai'}
    call('write', {'record': event, 'expectedRevision': 0})
    rejects('write', {'record': {**event, 'id': str(uuid4()), 'end': event['start']}, 'expectedRevision': 0})
    for amount, currency in [('0.1', 'CNY'), ('0.2', 'CNY'), ('3', 'JPY')]:
        call('write', {'record': {**base, 'id': str(uuid4()), 'title': f'Bill {currency} {amount}', 'kind': 'bill', 'amount': amount, 'currency': currency, 'date': '2026-11-04', 'direction': 'expense', 'status': 'paid'}, 'expectedRevision': 0})
    rejects('write', {'record': {**base, 'id': str(uuid4()), 'kind': 'bill', 'amount': '0.001', 'currency': 'CNY', 'date': '2026-11-04', 'direction': 'expense', 'status': 'paid'}, 'expectedRevision': 0})
    report_request = {'date': '2026-11-04', 'notes': 'Synthetic report material', 'requestId': str(uuid4())}
    report = call('briefing', report_request)
    assert call('briefing', report_request) == report
    rejects('briefing', {**report_request, 'notes': 'Different request content'})
    assert len(report['sources']) == 2 and 'Synthetic event' in report['body'], report
    rejects('write', {'record': report, 'expectedRevision': 1})
    if compatible:
        check_app_theme(page)
        open_secretary(page)
        page.get_by_label('搜索事务', exact=True).fill('no matching task')
        page.get_by_role('heading', name='没有符合条件的事务', exact=True).wait_for()
        page.get_by_label('搜索事务', exact=True).fill('Edited synthetic')
        page.get_by_role('heading', name='Edited synthetic task', exact=True).wait_for()
        page.get_by_role('tab', name='已完成 0', exact=True).click()
        page.get_by_role('heading', name='没有符合条件的事务', exact=True).wait_for()
        page.get_by_role('button', name='日程', exact=True).click()
        for label in ['日', '周', '年', '列表', '月']:
            page.get_by_role('tab', name=label, exact=True).click()
            expect(page.get_by_role('tabpanel')).to_be_visible()
        day = datetime.now().strftime('%Y-%m-') + '06'
        page.get_by_role('button', name=day, exact=True).click()
        page.get_by_role('button', name='为选中日期新建日程', exact=True).click()
        expect(page.get_by_label('开始', exact=True)).to_have_value(day + 'T09:00')
        page.get_by_role('button', name='取消编辑', exact=True).click()
        expect(page.get_by_role('button', name='为选中日期新建日程', exact=True)).to_be_focused()
        page.get_by_role('button', name='新建日程', exact=True).click()
        page.get_by_label('标题', exact=True).fill('UI calendar event')
        page.get_by_label('全天日程', exact=False).check()
        page.get_by_label('开始', exact=True).fill('2026-11-06')
        page.get_by_label('结束日期（不含当天）', exact=True).fill('2026-11-07')
        page.get_by_role('button', name='保存日程', exact=True).click()
        page.get_by_role('tab', name='列表', exact=True).click()
        page.get_by_role('heading', name='UI calendar event', exact=True).wait_for()
        page.get_by_role('button', name='账单', exact=True).click()
        expect(page.locator('.stella-bill-totals-cards')).to_contain_text('¥0.30')
        for label, period in [('月账单', '2026年11月'), ('年账单', '2026年')]:
            page.get_by_role('tab', name=label, exact=True).click()
            page.get_by_role('button', name=f'查看{period}账单', exact=True).click()
            expect(page.locator('.stella-bill-report .stella-bill-totals')).to_contain_text('¥0.30')
            expect(page.get_by_role('heading', name='分类构成', exact=True)).to_be_visible()
        page.get_by_role('tab', name='明细', exact=True).click()
        page.get_by_role('button', name='编辑账单：Bill CNY 0.1', exact=True).wait_for()
        with page.expect_download() as csv_download:
            page.get_by_role('button', name='导出 CSV', exact=True).click()
        csv = Path(csv_download.value.path()).read_text(encoding='utf-8-sig')
        assert '"0.1"' in csv and '"CNY"' in csv and '"0.2"' in csv
        assert '"JPY"' not in csv
        page.get_by_label('账单月份', exact=True).select_option('01')
        page.get_by_role('heading', name='这个月还没有流水', exact=True).wait_for()
        expect(page.get_by_role('button', name='导出 CSV', exact=True)).to_be_disabled()
        page.get_by_label('账单月份', exact=True).select_option('11')
        page.get_by_role('button', name='记账', exact=True).click()
        page.get_by_label('标题', exact=True).fill('UI bill')
        page.get_by_label('金额', exact=True).fill('12.34')
        page.get_by_role('button', name='保存账单', exact=True).click()
        page.get_by_role('button', name='编辑账单：UI bill', exact=True).wait_for()
        page.get_by_role('button', name='简报', exact=True).click()
        page.get_by_text('补充材料', exact=True).click()
        page.get_by_label('本次补充材料', exact=True).fill('UI report material')
        page.get_by_role('button', name='生成今日简报', exact=True).or_(page.get_by_role('button', name='更新今日简报', exact=True)).click()
        expect(page.locator('.stella-report')).to_contain_text('Synthetic structured briefing saved through DSH.', timeout=30000)
        page.get_by_role('button', name='更新今日简报', exact=True).wait_for()
        page.locator('.stella-briefing-history > button').last.click()
        expect(page.locator('.stella-report')).to_contain_text('Synthetic report material')
        for label in ['事务', '日程', '简报', '账单', '自动化']:
            page.get_by_role('button', name=label, exact=True).click()
            expect(page.get_by_text('更多操作', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='导出本机业务记录', exact=True)).to_have_count(0)
            expect(page.get_by_label('查看归档', exact=True)).to_have_count(0)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth'), label
            if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                page.screenshot(path=str(Path(directory) / f'layout-mobile-{label}.png'), animations='disabled')
            page.set_viewport_size({'width': 1280, 'height': 850})
        if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
            page.screenshot(path=str(Path(directory) / 'apps-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('[data-sidebar-opener]').wait_for()
            page.locator('[data-sidebar-opener]').click()
            page.get_by_role('dialog', name='Stella 秘书导航', exact=True).get_by_role('button', name='事务', exact=True).click()
            expect(page.locator('[data-sidebar-drawer]')).to_have_attribute('data-sidebar-collapsed', 'true')
            page.get_by_role('button', name='新建事务', exact=True).click()
            page.get_by_label('标题', exact=True).fill('Mobile synthetic task')
            page.get_by_role('button', name='保存事务', exact=True).click()
            page.get_by_role('heading', name='Mobile synthetic task', exact=True).wait_for()
            assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
            page.screenshot(path=str(Path(directory) / 'apps-mobile.png'), animations='disabled')
            page.set_viewport_size({'width': 1280, 'height': 850})
    open_secretary(page)
    start_secretary_chat(page)
    page.locator('[data-composer-input][contenteditable="true"]').wait_for()
    parent = page.evaluate('JSON.parse(localStorage.getItem("dsh.sessions.current")).sessionId')
    generated = call('generateBriefing', {'date': datetime.now().date().isoformat(), 'notes': 'Synthetic structured integration', 'requestId': str(uuid4()), 'parentSessionId': parent, 'sources': []})
    assert generated['body'] == 'Synthetic structured briefing saved through DSH.', generated
    assert generated['business']['snapshot']['briefing']['status'] == 'completed'
    job = call('startJob', {'id': str(uuid4()), 'parentSessionId': parent, 'title': 'Synthetic collaboration', 'prompt': 'Return a short independent analysis. CHILD_TASK_SENTINEL'})
    deadline = __import__('time').monotonic() + 30
    while __import__('time').monotonic() < deadline:
        saved = next(item for item in call('read')['jobs'] if item['id'] == job['id'])
        if saved['status'] not in ['starting', 'running']: break
        page.wait_for_timeout(100)
    assert saved['status'] == 'completed' and saved['childId'] and saved['output'], saved
    if compatible: check_hub(page, lambda: call('read'))
    child_requests = [item for item in requests if 'CHILD_TASK_SENTINEL' in json.dumps(item)]
    assert child_requests and all('PRIVATE_BUSINESS_SENTINEL' not in json.dumps(item) for item in child_requests)
    assert 'WORKSPACE_USER_SENTINEL' not in json.dumps(child_requests)
    assert all(set(tool['function']['name'] for tool in item.get('tools', [])) == {'stella_overview', 'stella_business', 'stella_generate_briefing', 'stella_review_tasks'} for item in child_requests if item.get('tools'))
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    schedule = call('createSchedule', {'title': 'Synthetic native schedule', 'prompt': 'Synthetic schedule delivery', 'timing': {'kind': 'at', 'at': future}})
    call('editSchedule', {'id': schedule['id'], 'action': 'pause', 'expectedUpdatedAt': schedule['updatedAt']})
    paused = next(item for item in call('read')['schedules'] if item['id'] == schedule['id'])
    assert paused['status'] == 'paused', paused
    rejects('editSchedule', {'id': 'unowned-id', 'action': 'delete', 'expectedUpdatedAt': None})
    if compatible:
        page.get_by_role('button', name='自动化', exact=True).click()
        page.get_by_test_id('task-manager-page').wait_for()
        page.get_by_role('button', name='Paused', exact=True).click()
        page.get_by_role('button', name='Synthetic native schedule', exact=True).click()
        page.get_by_role('button', name='Resume', exact=True).click()
        page.get_by_role('button', name='Pause', exact=True).click()
        paused = next(item for item in call('read')['schedules'] if item['id'] == schedule['id'])
        assert paused['status'] == 'paused'
        if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
            page.screenshot(path=str(Path(directory) / 'native-automation-paused.png'), animations='disabled')
    call('editSchedule', {'id': schedule['id'], 'action': 'resume', 'expectedUpdatedAt': paused['updatedAt']})
    call('editSchedule', {'id': schedule['id'], 'action': 'run', 'expectedUpdatedAt': None})
    deadline = __import__('time').monotonic() + 20
    while __import__('time').monotonic() < deadline:
        schedule = next(item for item in call('read')['schedules'] if item['id'] == schedule['id'])
        if schedule['deliveredAt']: break
        page.wait_for_timeout(100)
    assert schedule['deliveredAt'] and schedule['targetSessionId'], schedule
    deadline = __import__('time').monotonic() + 20
    scheduled_requests = []
    while __import__('time').monotonic() < deadline:
        scheduled_requests = [item for item in requests if 'Synthetic schedule delivery' in json.dumps(item)]
        if scheduled_requests: break
        page.wait_for_timeout(100)
    assert scheduled_requests and 'WORKSPACE_USER_SENTINEL' not in json.dumps(scheduled_requests), 'Private identity must not enter scheduled jobs'
    open_secretary(page)
    return {'recordId': task['id'], 'scheduleId': schedule['id']}
