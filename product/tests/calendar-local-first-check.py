"""Check first paint, in-flight navigation and background refresh on an isolated official Desktop."""
import json
import tarfile
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from web_check import open_secretary


def event(page, title):
    day = date.today().isoformat()
    desktop.rpc(page, 'alcorOrganizer/business', {'request': {
        'domain': 'calendar', 'method': 'create_event', 'arguments': {
            'title': title, 'start_at': day + 'T09:00:00+08:00', 'end_at': day + 'T10:00:00+08:00',
            'repeat_timezone': 'Asia/Shanghai'}}})


def fake_apple(home):
    fixture = home / 'calendar-sync-fixture'
    fixture.mkdir()
    (fixture / 'package.json').write_text(json.dumps({'name':'calendar-sync-fixture','version':'1.0.0',
        'type':'module', 'exports':'./index.js','dsh':{'bundle':{'patch':'./cordis.patch.yml'}}}))
    (fixture / 'cordis.patch.yml').write_text('- insert:\n    - id: calendar-sync-fixture\n      name: calendar-sync-fixture\n')
    (fixture / 'index.js').write_text("""import {appendFileSync} from 'node:fs'
export const inject = ['alcorOrganizer']
export function apply(ctx) {
  const log = line => appendFileSync(new URL('calls',import.meta.url), line+'\\n')
  const worker = ctx.alcorOrganizer.businessWorker
  const original = worker.call
  worker.call = function(action, input) {
    if (action === 'invoke' && input?.method === 'list_events' && input.arguments.start) log('read')
    return original.call(this,action,input)
  }
  ctx.effect(() => () => {worker.call = original})
  let count = 0
  ctx.provide('alcorAppleSync',{async invoke({method}) {
    if (method !== 'sync_range') return {}
    const n = ++count
    log('sync')
    if (n === 1) {
      await new Promise(resolve => setTimeout(resolve, 5000))
      const date = new Date().toLocaleDateString('en-CA', {timeZone:'Asia/Shanghai'})
      await ctx.alcorOrganizer.business({domain:'calendar',method:'create_event',
        arguments:{title:'同步新增会议',start_at:date+'T09:00:00+08:00',end_at:date+'T10:00:00+08:00'}})
      return {status:'success'}
    }
    if (n === 2) {
      await new Promise(resolve => setTimeout(resolve, 2000))
      log('stale-finished')
      return {status:'error', reason:'过期月份的错误'}
    }
    if (n === 3) return {status:'cached', reason:'本月同步已暂停'}
    throw new Error('测试日历离线')
  }})
}
""")
    pack = home / 'fixture.tgz'
    with tarfile.open(pack, 'w:gz') as tar:
        tar.add(fixture, arcname='package')
    return pack


def main():
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with fresh_test_home('alcor-calendar-first-') as temp, sync_playwright() as playwright:
        home = Path(temp)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        initialize_fresh(temp, home / 'dsh', profile)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name':'calendar-first-check','private':True,
            'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}))
        desktop.native_cli(home, 'calendar-local-install', 'add', str(desktop.PACK), str(fake_apple(home)), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'calendar-local-first') as (page, errors):
            open_secretary(page)
            event(page, '本地测试会议')
            log = profile / 'node_modules/calendar-sync-fixture/calls'
            def calls():
                return log.read_text().splitlines() if log.exists() else []

            page.get_by_role('button', name='日程', exact=True).evaluate("""button => {
              const t = performance.now()
              const observer = new MutationObserver(() => {
                if ([...document.querySelectorAll('.alcor-calendar-surface button')].some(b => b.textContent.includes('本地测试会议'))) {
                  window.calendarPaintMs = performance.now() - t
                  observer.disconnect()
                }
              })
              observer.observe(document.body, {childList:true, subtree:true})
              button.click()
            }""")
            local = page.get_by_role('button', name='本地测试会议', exact=False)
            expect(local).to_be_visible()
            latency = page.evaluate('window.calendarPaintMs')
            assert latency is not None and latency < 500, latency
            page.wait_for_timeout(4200)
            expect(local).to_be_visible()
            assert calls() == ['read','sync'], calls()
            expect(page.get_by_role('button', name='同步新增会议', exact=False)).to_be_visible(timeout=1700)
            assert calls().count('sync') == 1

            page.get_by_role('button', name='下一期').click()
            first_empty = page.get_by_text('正在从 Apple 同步此范围的日程…')
            expect(first_empty).to_be_visible()
            page.get_by_role('button', name='下一期').click()
            expect(page.get_by_text('本月同步已暂停')).to_be_visible()
            deadline = time.monotonic() + 5
            while 'stale-finished' not in calls() and time.monotonic() < deadline:
                page.wait_for_timeout(30)
            assert calls().count('sync') == 3 and 'stale-finished' in calls(), calls()
            expect(page.get_by_text('本月同步已暂停')).to_be_visible()
            expect(page.get_by_text('过期月份的错误')).to_have_count(0)
            expect(local).to_have_count(0)
            page.get_by_role('button', name='本月', exact=True).click()
            expect(page.get_by_text('测试日历离线')).to_be_visible()
            expect(local).to_be_visible()
            print(f'PASS: local event visible in {latency:.0f} ms; single initial read and sync, '
                  '5 s delayed refresh, empty month and stale result handling.')


if __name__ == '__main__':
    main()
