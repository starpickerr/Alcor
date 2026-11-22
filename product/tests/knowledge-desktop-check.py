"""Exercise the packed library in an isolated official macOS Desktop with a synthetic model."""

import json
import os
import re
import runpy
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright

import official_desktop_check as desktop
from web_check import open_secretary

configure = runpy.run_path(str(Path(__file__).with_name("shared-business-chat-check.py")))["configure"]


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tools = [item["function"]["name"] for item in body.get("tools", [])]
        last = body["messages"][-1]
        delta, stop = {"content": "Synthetic title"}, "stop"
        if "alcor_knowledge" in tools:
            if last["role"] == "tool":
                result = json.loads(last["content"])
                if "edges" in result:
                    names = {r["id"]: r["title"] for r in result["nodes"]}
                    assert any(names[e["source"]] == "复习提纲" and names[e["target"]] == "期末通知.png" for e in result["edges"]), result
                    delta = {"content": "复习提纲引用了期末通知。"}
                else:
                    assert any(row["title"] == "期末通知.png" for row in result["items"]), result
                    delta = {"content": "已找到考试范围原图。"}
            else:
                action = {"action": "graph"} if "查图谱" in str(last["content"]) else {"action": "search", "query": "Chapter five"}
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "library-search",
                            "type": "function",
                            "function": {
                                "name": "alcor_knowledge",
                                "arguments": json.dumps(action),
                            },
                        }
                    ]
                }
                stop = "tool_calls"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for data, finish in [({"role": "assistant"}, None), (delta, None), ({}, stop)]:
            event = {
                "id": "library",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "synthetic",
                "choices": [{"index": 0, "delta": data, "finish_reason": finish}],
            }
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


def check(page, docs):
    open_secretary(page)
    page.get_by_role("group", name="应用", exact=True).get_by_role("button", name="知识库").click()
    expect(page.get_by_role("heading", name="选择资料")).to_be_visible()
    expect(page.get_by_label("收藏范围")).to_have_count(0)
    add = page.get_by_role("button", name="导入文件", exact=True)
    dialog = page.get_by_role("dialog")
    reader = page.get_by_role("article", name="资料详情")
    tools = reader.get_by_role("group", name="资料操作")
    location = reader.locator(".alcor-library-location")
    directory = page.get_by_role("navigation", name="知识库目录")
    folder_tools = directory.get_by_role("group", name="目录操作", exact=True)
    expect(folder_tools.get_by_role("button")).to_have_count(2)
    for name, button in (
        ("课程", folder_tools.get_by_role("button", name="新建文件夹", exact=True)),
        ("数据结构", directory.get_by_role("button", name="在课程中新建文件夹", exact=True)),
    ):
        button.click()
        dialog.get_by_label("文件夹名称").fill(name)
        dialog.get_by_role("button", name="新建", exact=True).click()
        expect(directory.get_by_role("button", name=name, exact=True)).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_role("button", name="添加资料", exact=True)).to_have_count(0)
    directory.get_by_role("button", name="在数据结构中新建文件", exact=True).click()
    dialog.get_by_label("标题", exact=True).fill("教学大纲")
    expect(dialog.get_by_label("保存位置")).to_have_value("课程/数据结构")
    dialog.get_by_label("来源网址").fill("https://example.org/syllabus")
    dialog.get_by_label("文字摘录").fill("第八章为选读")
    dialog.get_by_role("button", name="保存", exact=True).click()
    page.get_by_role("button", name="网页 教学大纲").click()
    expect(folder_tools.get_by_role("button")).to_have_count(2)
    expect(reader.get_by_role("link", name="打开原网页")).to_have_attribute(
        "href", "https://example.org/syllabus"
    )
    expect(tools.get_by_role("button", name="编辑", exact=True)).to_be_visible()
    expect(tools.get_by_role("button", name="删除资料", exact=True)).to_be_visible()
    expect(tools.get_by_role("button", name="移动到…", exact=True)).to_have_count(0)
    entry = directory.get_by_role("button", name="网页 教学大纲", exact=True)
    expect(directory.get_by_text("知识库首页", exact=True)).to_have_count(0)
    top_drop = directory.get_by_text("移到顶层", exact=True)
    expect(top_drop).to_have_count(0)
    box = entry.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 12, y, steps=4)
    expect(top_drop).to_be_visible()
    drop = top_drop.bounding_box()
    x, y = drop["x"] + drop["width"] / 2, drop["y"] + drop["height"] / 2
    page.mouse.move(x, y, steps=8)
    page.mouse.move(x + 2, y)
    page.mouse.up()
    expect(top_drop).to_have_count(0)
    expect(page.get_by_role("status").filter(has_text="已移到 顶层")).to_be_visible()
    expect(reader).to_contain_text("第八章为选读")
    page.get_by_role("button", name="撤销", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="已撤销移动")).to_be_visible()
    expect(location.get_by_text("数据结构", exact=True)).to_be_visible()
    expect(location.locator("svg")).to_have_count(2)
    entry.click(button="right")
    expect(page.get_by_role("menuitem", name="移动到…", exact=True)).to_have_count(0)
    entry.drag_to(directory.get_by_role("button", name="课程", exact=True))
    expect(page.get_by_role("status").get_by_text("已移到 课程", exact=True)).to_be_visible()
    expect(location.get_by_text("课程", exact=True)).to_be_visible()
    expect(location.get_by_text("数据结构", exact=True)).to_have_count(0)
    expect(location.locator("svg")).to_have_count(1)
    entry.drag_to(directory.get_by_role("button", name="数据结构", exact=True))
    expect(page.get_by_role("status").filter(has_text="已移到 课程 / 数据结构")).to_be_visible()
    expect(location.get_by_text("数据结构", exact=True)).to_be_visible()
    assert directory.bounding_box()["x"] < reader.bounding_box()["x"]
    page.screenshot(path=str(desktop.ARTIFACTS / "knowledge-split.png"))
    folder_tools.get_by_role("button", name="新建文件夹", exact=True).click()
    expect(dialog.get_by_label("保存位置")).to_have_value("课程/数据结构")
    dialog.get_by_label("保存位置").select_option("")
    dialog.get_by_label("文件夹名称").fill("测试文件夹")
    dialog.get_by_role("button", name="新建", exact=True).click()
    expect(directory.locator(".alcor-library-tree").first.locator(":scope > li").get_by_role("button", name="测试文件夹", exact=True)).to_be_visible()
    test_folder = directory.get_by_role("button", name="测试文件夹", exact=True)
    directory.get_by_role("button", name="在测试文件夹中新建文件", exact=True).click()
    dialog.get_by_label("标题", exact=True).fill("空白文件")
    expect(dialog.get_by_label("保存位置")).to_have_value("测试文件夹")
    dialog.get_by_role("button", name="保存", exact=True).click()
    blank = directory.get_by_role("button", name="笔记 空白文件", exact=True)
    blank.focus()
    page.keyboard.press("Tab")
    delete_blank = directory.get_by_role("button", name="删除文件：空白文件", exact=True)
    expect(delete_blank).to_be_focused()
    delete_blank.press("Enter")
    dialog.get_by_role("button", name="永久删除", exact=True).click()
    expect(blank).to_have_count(0)
    test_folder.hover()
    directory.get_by_role("button", name="在测试文件夹中新建文件夹", exact=True).click()
    dialog.get_by_label("文件夹名称").fill("临时子目录")
    dialog.get_by_role("button", name="新建", exact=True).click()
    folder_tools.get_by_role("button", name="新建文件", exact=True).click()
    dialog.get_by_label("标题", exact=True).fill("待删除资料")
    dialog.get_by_role("button", name="保存", exact=True).click()
    disposable = directory.get_by_role("button", name="笔记 待删除资料", exact=True)
    expect(disposable).to_be_visible()
    test_folder.hover()
    delete_folder = directory.get_by_role("button", name="删除文件夹：测试文件夹", exact=True)
    delete_folder.click()
    expect(dialog).to_contain_text("1 份资料和 1 个子文件夹")
    dialog.get_by_role("button", name="取消", exact=True).click()
    expect(disposable).to_be_visible()
    test_folder.hover()
    delete_folder.click()
    dialog.get_by_role("button", name="永久删除", exact=True).click()
    expect(test_folder).to_have_count(0)
    expect(disposable).to_have_count(0)
    expect(directory.get_by_role("button", name="课程", exact=True)).to_be_visible()
    image = Image.new("RGB", (900, 120), "white")
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 48)
    ImageDraw.Draw(image).text((20, 20), "Chapter five is optional", font=font, fill="black")
    original = docs / "期末通知.png"
    image.save(original)
    with page.expect_file_chooser() as chooser:
        add.click()
    chooser.value.set_files(original)
    dialog.get_by_label("保存位置").select_option("")
    dialog.get_by_role("button", name="保存", exact=True).click()
    page.get_by_label("搜索知识库").fill("Chapter five")
    page.get_by_role("button", name="图片 期末通知.png").click(timeout=60000)
    expect(reader.get_by_role("img", name="期末通知.png")).to_be_visible()
    expect(tools.locator("svg")).to_have_count(3)
    assert tools.bounding_box()["x"] > reader.get_by_role("heading", name="期末通知.png").bounding_box()["x"]
    page.screenshot(path=str(desktop.ARTIFACTS / "knowledge-toolbar.png"))
    with page.expect_download() as download:
        tools.get_by_role("link", name="下载原件").click()
    assert Path(download.value.path()).read_bytes() == original.read_bytes()
    page.get_by_label("搜索知识库").clear()
    # Two scanned pages exercise page numbering, preview, and the exact download.
    pdf = docs / "课程讲义.pdf"
    second = Image.new("RGB", (900, 120), "white")
    ImageDraw.Draw(second).text((20, 20), "Binary trees: CS204X", font=font, fill="black")
    image.save(pdf, "PDF", save_all=True, append_images=[second])
    page.locator('input[type=file][accept*=".pdf"]').set_input_files(pdf)
    dialog.get_by_role("button", name="保存", exact=True).click()
    expect(dialog).not_to_be_visible(timeout=60000)
    page.get_by_label("搜索知识库").fill("CS204X")
    page.get_by_role("button", name="PDF 课程讲义.pdf").click(timeout=60000)
    expect(reader).to_contain_text("第 2 / 2 页")
    expect(reader.locator("mark")).to_contain_text("CS204X")
    expect(reader.locator("object")).to_have_attribute("data", re.compile("#page=2$"))
    page.get_by_label("搜索知识库").clear()
    for title, body in [
        ("复习计划", "先阅读 [[复习提纲]] 和 [[教学大纲]]，再完成 [[自测题]]。"),
        ("课程笔记", "整理 [[课程讲义.pdf]] 的例题和 [[概念清单]]。"),
        ("例题集", "参考 [[课程笔记]] 和 [[自测题]]。"),
        ("概念清单", "重点来自 [[教学大纲]]。"),
        ("自测题", "结合 [[课程讲义.pdf]] 与 [[概念清单]]。"),
    ]:
        desktop.rpc(page, "alcorOrganizer/business", {"request": {"domain": "knowledge", "method": "save", "arguments": {"title": title, "text": body}}})
    folder_tools.get_by_role("button", name="新建文件", exact=True).click()
    dialog.get_by_label("标题", exact=True).fill("复习提纲")
    dialog.get_by_label("笔记", exact=True).fill("参考 [[教学大纲]] 与 ")
    dialog.get_by_role("button", name="插入资料引用").click()
    dialog.get_by_label("查找要引用的资料").fill("期末通知")
    dialog.locator(".alcor-reference-picker").get_by_role("button", name=re.compile("期末通知")).click()
    dialog.get_by_role("button", name="保存", exact=True).click()
    page.get_by_role("button", name="图谱", exact=True).click()
    network = page.get_by_role("region", name="知识图谱")
    node = network.get_by_role("button", name="打开资料：复习提纲", exact=True)
    expect(node).to_be_visible()
    expect(network.locator("line")).to_have_count(12)
    transform = network.locator("svg > g")
    initial = transform.get_attribute("transform")
    network.get_by_role("button", name="放大图谱").click()
    expect(transform).not_to_have_attribute("transform", initial)
    network.get_by_role("button", name="适应窗口").click()
    expect(transform).to_have_attribute("transform", initial)
    network.screenshot(path=str(desktop.ARTIFACTS / "graph-desktop.png"))
    page.emulate_media(color_scheme="dark")
    page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
    network.screenshot(path=str(desktop.ARTIFACTS / "graph-dark.png"), animations="disabled")
    page.emulate_media(color_scheme="light")
    page.wait_for_function('!document.body.hasAttribute("data-ds-dark-theme")')
    node.focus()
    node.press("Enter")
    expect(dialog.get_by_role("region", name="引用了")).to_contain_text("期末通知.png")
    dialog.locator(".alcor-knowledge-text").get_by_role("button", name="教学大纲").click()
    expect(dialog.get_by_role("region", name="被引用")).to_contain_text("复习提纲")
    dialog.get_by_role("button", name="返回上一份资料").click()
    expect(dialog.get_by_role("region", name="引用了")).to_be_visible()
    dialog.get_by_role("button", name="关闭", exact=True).click()
    page.set_viewport_size({"width": 540, "height": 740})
    expect(network.get_by_role("button", name="适应窗口")).to_be_in_viewport()
    page.mouse.move(500, 70)
    page.keyboard.press("Escape")
    expect(page.locator("[data-sidebar-drawer]")).to_have_attribute("data-sidebar-collapsed", "true")
    page.wait_for_function("""() => {
        const el = document.querySelector('.alcor-library-graph'), box = el.getBoundingClientRect()
        return el.contains(document.elementFromPoint(box.left + 15, box.top + 15))
    }""")
    network.screenshot(path=str(desktop.ARTIFACTS / "graph-narrow.png"), animations="disabled")
    page.get_by_label("筛选图谱标题").fill("复习提纲")
    expect(network.locator("line")).to_have_count(4)
    expect(network.get_by_role("button", name="打开资料：期末通知.png")).to_be_visible()
    page.get_by_label("筛选图谱标题").fill("不存在的资料")
    expect(page.get_by_text("当前范围没有资料")).to_be_visible()
    page.set_viewport_size({"width": 1280, "height": 850})
    page.get_by_role("button", name="文档", exact=True).click()
    page.get_by_label("搜索知识库").fill("教学大纲")
    page.get_by_role("button", name="网页 教学大纲").click()
    reader.get_by_role("button", name="删除资料", exact=True).click()
    dialog.get_by_role("button", name="取消", exact=True).click()
    expect(reader).to_contain_text("第八章为选读")
    reader.get_by_role("button", name="删除资料", exact=True).click()
    dialog.get_by_role("button", name="永久删除", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role("button", name="网页 教学大纲")).to_have_count(0)
    expect(page.get_by_role("button", name=re.compile("笔记 复习提纲"))).to_be_visible()
    long_text = docs / "学期记录.txt"
    marker = b"LASTSECTION392"
    long_text.write_bytes(b" " * (5 * 1024 * 1024 - len(marker)) + marker)
    page.locator('input[type=file][accept*=".pdf"]').set_input_files(long_text)
    dialog.get_by_role("button", name="保存", exact=True).click()
    expect(dialog).not_to_be_visible(timeout=60000)
    page.get_by_label("搜索知识库").fill(marker.decode())
    page.get_by_role("button", name="文档 学期记录.txt").click()
    expect(reader.locator("mark")).to_contain_text(marker.decode())
    with page.expect_download() as downloaded:
        tools.get_by_role("link", name="下载原件").click()
    assert Path(downloaded.value.path()).read_bytes() == long_text.read_bytes()
    page.set_viewport_size({"width": 540, "height": 740})
    expect(directory).not_to_be_visible()
    expect(page.locator("[data-sidebar-drawer]")).to_have_attribute("data-sidebar-collapsed", "true")
    expect(reader).to_be_visible()
    expect(reader.get_by_role("heading", name="学期记录.txt")).to_be_in_viewport()
    page.get_by_role("button", name="返回目录", exact=True).click(trial=True)
    page.wait_for_function("""() => {
        const el = document.querySelector('.alcor-library-detail'), box = el.getBoundingClientRect();
        return el.contains(document.elementFromPoint(box.left + 12, box.top + 16));
    }""")
    tools.get_by_role("button", name="编辑", exact=True).click()
    expect(dialog.get_by_label("标题", exact=True)).to_have_value("学期记录.txt")
    dialog.get_by_role("button", name="取消", exact=True).click()
    page.screenshot(path=str(desktop.ARTIFACTS / "knowledge-reading-narrow.png"))
    page.get_by_role("button", name="返回目录", exact=True).click()
    expect(page.get_by_label("搜索知识库")).to_be_in_viewport()
    expect(add).to_be_in_viewport()
    page.set_viewport_size({"width": 1280, "height": 850})
    page.locator(".alcor-sidebar-chat").click()
    composer = page.locator('[data-composer-input][contenteditable="true"]')
    composer.fill("从知识库给我找出考试范围的原图。")
    composer.press("Enter")
    expect(page.get_by_text("已找到考试范围原图。", exact=True)).to_be_visible(timeout=60000)
    citation = page.get_by_role("button", name="期末通知.png", exact=True)
    citation.click()
    expect(dialog.get_by_role("img", name="期末通知.png")).to_be_visible()
    dialog.get_by_role("button", name="关闭", exact=True).click()
    composer.fill("查图谱，复习提纲引用了哪张通知？")
    composer.press("Enter")
    expect(page.get_by_text("复习提纲引用了期末通知。", exact=True)).to_be_visible(timeout=60000)


if __name__ == "__main__":
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    desktop.verify_release()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with (
            tempfile.TemporaryDirectory(prefix="alcor-library-desktop-") as temp,
            sync_playwright() as playwright,
        ):
            home = Path(temp)
            docs = configure(home, desktop.environment(home), server.server_port)
            pack = Path(os.environ.get("STELLA_SECRETARY_REVIEW_PACK", desktop.PACK))
            desktop.native_cli(home, "knowledge-install", "add", str(pack), "--ignore-scripts")
            with desktop.desktop(playwright, home, "knowledge") as (page, errors):
                check(page, docs)
                assert not errors, errors
            print(
                "PASS: split reader, directory actions, drag and undo, recursive deletion, OCR, original download, graph, references and narrow layout"
            )
    finally:
        server.shutdown()
        server.server_close()
