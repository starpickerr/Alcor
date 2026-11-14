"""Exercise the native Windows caption and the right-hand split in an isolated Alcor session."""
import ctypes
import json
import time
from uuid import uuid4

import win32api
import win32con
import win32gui
import win32process
from playwright.sync_api import expect


def drag(hwnd, origin, destination):
    assert win32gui.GetAncestor(win32gui.WindowFromPoint(origin), win32con.GA_ROOT) == hwnd
    assert win32gui.GetForegroundWindow() == hwnd, 'The disposable desktop must own input'
    assert not (win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
    cursor = win32api.GetCursorPos()
    try:
        win32api.SetCursorPos(origin)
        time.sleep(.08)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        for step in range(1, 13):
            win32api.SetCursorPos(tuple(round(a+(b-a)*step/12) for a, b in zip(origin, destination)))
            time.sleep(.035)
    finally:
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        time.sleep(.15)
        win32api.SetCursorPos(cursor)


def check_window_interactions(page, pid, workspace, evidence):
    """workspace must be the current session's disposable workspace directory."""
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    handles = []

    def find(hwnd, _):
        if (win32process.GetWindowThreadProcessId(hwnd)[1] == pid and win32gui.IsWindowVisible(hwnd)
                and win32gui.GetClassName(hwnd) == 'Chrome_WidgetWin_1'):
            handles.append(hwnd)

    win32gui.EnumWindows(find, None)
    assert len(handles) == 1 and page.url.startswith('dsh-app://app/')
    hwnd = handles[0]
    saved = win32gui.GetWindowPlacement(hwnd)
    was_topmost = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST
    results = {'renderer': page.url.split('?')[0], 'caption': []}
    scale = page.evaluate('devicePixelRatio')

    def resize(width):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 65, 65, width, 700, win32con.SWP_SHOWWINDOW)
        page.wait_for_function('([w,s]) => Math.abs(outerWidth*s-w) <= 2', arg=[width, scale])
        rect = win32gui.GetWindowRect(hwnd)
        box = page.evaluate('[screenX, screenY, outerWidth, outerHeight]')
        assert all(abs(css*scale-v) <= 2 for css, v in zip(box, (rect[0], rect[1], rect[2]-rect[0], rect[3]-rect[1])))
        return rect

    def dimensions():
        return {'panel': page.locator('[data-rightbar-col]').bounding_box()['width'],
                'columns': [el.bounding_box()['width'] for el in panes.all()]}

    def slide(selector, offset):
        handle = page.locator(selector)
        point = handle.evaluate("""e => {
            const r = e.getBoundingClientRect(), x = Math.round(r.x+r.width/2), y = Math.round(r.y+r.height/2);
            if (document.elementFromPoint(x,y) !== e) throw Error('Divider is obscured');
            return [x,y];
        }""")
        x, y = win32gui.ClientToScreen(hwnd, tuple(round(v*scale) for v in point))
        drag(hwnd, (x, y), (x+round(offset*scale), y))
        return dimensions()

    expect(page.get_by_role('button', name='切换使用模式，当前 Alcor', exact=True)).to_be_visible()
    try:
        for width in (1180, 720):
            left, top, right, _ = resize(width)
            # Exercise the draggable area of the reserved Alcor header.
            bar = page.locator('.alcor-mode-bar').bounding_box()
            y = win32gui.ClientToScreen(hwnd, (0, round((bar['y']+bar['height']/2)*scale)))[1]
            points = ((x, y) for x in range(left+250, right-160, 10))
            start = next(point for point in points if win32gui.SendMessage(
                hwnd, win32con.WM_NCHITTEST, 0, win32api.MAKELONG(*point)) == win32con.HTCAPTION)
            drag(hwnd, start, (start[0]+72, start[1]+48))
            after = win32gui.GetWindowRect(hwnd)
            delta = [after[0]-left, after[1]-top]
            assert delta[0] > 40 and delta[1] > 24 and after[2]-after[0] == right-left, (width, start, delta, after)
            results['caption'].append({'outer': width, 'inner': page.evaluate('innerWidth'), 'delta': delta})
        resize(1600)
        close = page.get_by_role('button', name='收起右侧边栏', exact=True)
        if close.is_visible():
            close.click()
        page.get_by_role('button', name='打开右侧边栏', exact=True).click()
        # Use two file previews so both columns must keep rendering through either drag.
        preview = f'win-pane-{uuid4().hex[:8]}'
        panes = page.locator('[data-dockkit-pane]:visible')
        for index in range(2):
            name = f'{preview}-{index}.txt'
            content = f'Synthetic Windows preview {index}: {preview}'
            (workspace/name).write_text(content, encoding='utf8')
            if index:
                page.get_by_role('button', name='分栏', exact=True).click()
            pane = panes.nth(index)
            pane.get_by_role('button', name='工作区文件 浏览会话工作区的文件 Ctrl + P').click()
            pane.get_by_text(name, exact=True).click()
            expect(pane.get_by_text(content, exact=True)).to_be_visible()
        before = dimensions()
        panel = slide('[data-side="rightbar"]', -100)
        assert panel['panel'] - before['panel'] > 65
        columns = slide('[data-dockkit-divider]', 64)
        assert abs(columns['panel']-panel['panel']) < 2
        assert columns['columns'][0]-panel['columns'][0] > 40 and panel['columns'][1]-columns['columns'][1] > 40
        for index in range(2):
            expect(panes.nth(index).get_by_text(f'Synthetic Windows preview {index}: {preview}', exact=True)).to_be_visible()
        results.update(before=before, afterPanelDrag=panel, afterColumnDrag=columns)
        page.screenshot(path=evidence/'windows-right-pane.png')
        (evidence/'windows-window-interactions.json').write_text(json.dumps(results, indent=2))
    finally:
        if not was_topmost:
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        win32gui.SetWindowPlacement(hwnd, saved)
