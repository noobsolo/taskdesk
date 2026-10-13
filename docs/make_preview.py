"""Build a local example project and capture the actual Textual dashboard."""
import asyncio
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

os.environ.pop("NO_COLOR", None)

from rich.cells import cell_len

from taskdeck.core import Project, git
from taskdeck.tui import TaskDeck


async def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        git(root, "init", "-b", "main")
        git(root, "-c", "user.name=Taskdeck Demo", "-c", "user.email=demo@example.invalid", "commit", "--allow-empty", "-m", "demo baseline")
    project = Project.init(root)
    definitions = [("T001", "构建基础", []), ("T002", "数据模型", ["T001"]),
                   ("T003", "数据库迁移", ["T002"]), ("T004", "登录与鉴权", ["T001"]),
                   ("T005", "搜索接口", ["T002"]), ("T006", "搜索页面", ["T005"]),
                   ("T007", "文件导入", ["T003"]), ("T008", "集成验证", ["T004", "T006", "T007"])]
    for ident, title, deps in definitions:
        if ident not in project.scan():
            project.add(ident, title, deps)
    app = TaskDeck(project)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        screenshot = Path(__file__).parent / "taskdeck.svg"
        app.save_screenshot(screenshot.name, path=str(screenshot.parent))
        # Rich's SVG export uses codepoint lengths for textLength; restore
        # terminal cell widths so CJK glyphs render faithfully in Quick Look.
        tree = ET.parse(screenshot)
        for element in list(tree.getroot().iter()):
            text = element.text or ""
            length = element.get("textLength")
            if length and text:
                cell_width = float(length) / len(text)
                offset = float(element.get("x", "0"))
                element.attrib.pop("textLength", None)
                element.text = None
                for char in text:
                    width = cell_len(char)
                    if char in "\r\n":
                        continue
                    span = ET.SubElement(element, "{http://www.w3.org/2000/svg}tspan", {"x": str(offset)})
                    if width:
                        span.set("textLength", str(cell_width * width))
                        span.set("lengthAdjust", "spacingAndGlyphs")
                    span.text = char
                    offset += cell_width * width
        screenshot.write_text("\n".join(line.rstrip() for line in ET.tostring(tree.getroot(), encoding="unicode").splitlines()) + "\n")
        print(f"预览仓库：{root}")
        print(f"界面截图：{Path(__file__).parent / 'taskdeck.svg'}")


asyncio.run(main())
