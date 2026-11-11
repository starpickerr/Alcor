"""Inspect the real Electron window, its native caption and the plugin's top button."""
import ctypes
import json
import time

import win32api
import win32con
import win32gui
import win32process
from playwright.sync_api import expect


def check_caption(page, pid, home, mode):
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    handles = []
    def collect(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == 'Chrome_WidgetWin_1'
                and win32process.GetWindowThreadProcessId(hwnd)[1] == pid):
            handles.append(hwnd)
    win32gui.EnumWindows(collect, None)
    assert len(handles) == 1, 'Expected one native desktop window'
    hwnd = handles[0]
    saved = win32gui.GetWindowPlacement(hwnd)
    results = []
    button = page.get_by_role('button', name='切换使用模式，当前 ' + mode, exact=True)
    expect(button).to_be_visible(timeout=30000)
    dsh = mode == 'DeepSeek Harness'
    filename = 'caption.json' if dsh else 'alcor-caption.json'
    if dsh:
        expect(page.get_by_label('Alcor 秘书导航', exact=True)).to_have_count(0)
    def hit(x, y):
        return win32gui.SendMessage(hwnd, win32con.WM_NCHITTEST, 0, win32api.MAKELONG(x & 65535, y & 65535))
    try:
        for width in (1180, 720):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(hwnd, 0, 60, 60, width, 650, win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            box = button.bounding_box()
            assert box and 0 <= box['y'] < 60
            assert button.evaluate('e => getComputedStyle(e).webkitAppRegion') == 'no-drag'
            scale = page.evaluate('devicePixelRatio')
            origin = win32gui.ClientToScreen(hwnd, (0, 0))
            center = (origin[0] + round((box['x'] + box['width']/2) * scale),
                      origin[1] + round((box['y'] + box['height']/2) * scale))
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            hits = {}
            for y in range(top + 5, top + round(48*scale), 4):
                for x in range(left + 8, right - 4, 12):
                    code = hit(x, y)
                    hits.setdefault(code, (x, y))
            result = {'window': [left, top, right, bottom], 'viewport': page.evaluate('[innerWidth,innerHeight]'),
                      'switch': box, 'centerHit': hit(*center), 'frameHitExamples': hits}
            results.append(result)
            (home / filename).write_text(json.dumps(results, indent=2))
            assert hit(*center) == win32con.HTCLIENT, result
            for code in (win32con.HTCAPTION, win32con.HTMINBUTTON, win32con.HTMAXBUTTON, win32con.HTCLOSE):
                assert code in hits, ('Missing native window target', code, result)
            assert box['x'] + box['width'] < (right-left)/scale - 130, 'Switch overlaps caption controls'
            button.focus()
            button.press('ArrowDown')
            expect(page.get_by_role('menuitem', name='Alcor' if dsh else 'DeepSeek Harness', exact=True)).to_be_visible()
            page.keyboard.press('Escape')
            page.screenshot(path=home / f'{"dsh" if dsh else "alcor"}-top-{width}.png')
            if width == 720 and dsh:
                result['physicalDrag'] = 'not run: test window does not own foreground'
                # Inject input only while this disposable window owns the foreground and mouse target.
                for y in range(top+12, top+36, 4):
                    points = [(x, y) for x in range(left+100, right-180, 20) if hit(x, y) == win32con.HTCAPTION]
                    if points:
                        break
                if (points and win32gui.GetForegroundWindow() == hwnd and
                        win32gui.GetAncestor(win32gui.WindowFromPoint(points[0]), win32con.GA_ROOT) == hwnd):
                    cursor = win32api.GetCursorPos()
                    x, y = points[0]
                    try:
                        win32api.SetCursorPos((x, y))
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
                        for offset in (8, 16, 24, 32, 40):
                            win32api.SetCursorPos((x+offset, y+offset))
                            time.sleep(.04)
                    finally:
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
                        win32api.SetCursorPos(cursor)
                    after = win32gui.GetWindowRect(hwnd)
                    assert after[0] > left + 20 and after[1] > top + 20, ('Caption drag failed', after)
                    result['physicalDrag'] = [after[0]-left, after[1]-top]
                (home / filename).write_text(json.dumps(results, indent=2))
    finally:
        win32gui.SetWindowPlacement(hwnd, saved)
