r"""AI 智能体的「生成文档」能力：把大模型写的结构化大纲变成真正的 .docx / .xlsx / .pptx。

设计边界（跟用户提的硬要求一一对应）：

* **全程离线**：只用三个纯 Python 写库（python-docx / openpyxl / python-pptx），它们把 OOXML
  直接写进本地文件，不联网、不调用任何在线文档服务；库已经装在项目 venv 里并随便携包
  一起带走（见 ``scripts\build_portable.ps1`` 复制 .venv 那一段），用户不用 pip install。
* **不碰五个生成模块**：这里不排队、不碰显存、不 import 任何 image/video/music 引擎，
  也不碰大模型本身和它的加载参数（只借用 app/agent/llm.py 已有的聊天通道）。
* **产物落盘沿用现有规则**：先写进配置的 ``output_dir``（工具里即时显示的那一份），再由
  调用方走 ``app.core.artifacts`` 的同一套镜像逻辑（项目内 ``output\`` 与可选 auto_save_dir），
  重名不覆盖（扩展名前加 -1、-2）。

内容来源是本地大模型：它给的文本按这里约定的极简标记（``#`` 标题、``##`` 小节、``-`` 要点、
``|`` 分隔的表格行）解析。模型输出不规整是常态，所以解析器一律**宽松接收、缺什么补什么**，
并在回执里如实说明降级了多少；模型完全不可用时只按题目搭骨架，绝不假装有内容。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_DOCX = "docx"
FORMAT_XLSX = "xlsx"
FORMAT_PPTX = "pptx"
FORMATS: tuple[str, ...] = (FORMAT_DOCX, FORMAT_XLSX, FORMAT_PPTX)
FORMAT_LABELS: dict[str, str] = {
    FORMAT_DOCX: "Word 文档（.docx）",
    FORMAT_XLSX: "Excel 表格（.xlsx）",
    FORMAT_PPTX: "PPT 演示文稿（.pptx）",
}

# 本地 8B 模型很容易话痨：给文件体量兜个上限，超出部分截掉并如实说明。
MAX_SECTIONS = 40
MAX_PARAGRAPHS_PER_SECTION = 20
MAX_BULLETS_PER_SECTION = 40
MAX_TABLE_ROWS = 400
MAX_TABLE_COLUMNS = 30
MAX_SLIDES = 60

# 解析用的极简标记：'#'/'##' 标题，'-'/'*'/'•'/'1.' 要点，其余是正文段落。
_HEADING_TOKEN = re.compile(r"^(#{1,6})\s*(.+)$")
_BULLET_TOKEN = re.compile(r"^\s*(?:[-*•·–]|\d{1,2}[.)、])\s+(.*)$")
_MD_NOISE_TOKEN = re.compile(r"\*\*|__|~~|`+")
_SEPARATOR_TOKEN = re.compile(r"^[\s\-=_*·—–]*$")
_ILLEGAL_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 模型不可用时的骨架：只按题目搭结构，内容一律标「（待补充）」，不假装写好了。
_DOCX_SKELETON = ("背景与目标", "现状与问题", "方案要点", "实施步骤与分工", "时间计划", "风险与对策")
_PPTX_SKELETON = ("为什么要做", "要解决的问题", "思路与方案", "关键步骤", "资源与预算", "预期结果")


class DocumentEngineMissing(RuntimeError):
    """写库没装（.venv 里缺 python-docx / openpyxl / python-pptx）：说清楚该补哪一个。"""


@dataclass
class Section:
    """一个小节：标题 + 正文段落 + 要点。"""

    heading: str = ""
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


@dataclass
class DocumentPlan:
    """docx / pptx 共用的文档结构（pptx 里一个小节 = 一页）。"""

    title: str = ""
    subtitle: str = ""
    sections: list[Section] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def paragraph_count(self) -> int:
        return sum(len(sec.paragraphs) + len(sec.bullets) for sec in self.sections)


@dataclass
class TablePlan:
    """xlsx 的结构：表头 + 数据行（列宽、样式由写库按内容算）。"""

    title: str = ""
    sheet_name: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- 大模型提示词
def document_system_prompt(fmt: str) -> str:
    """按格式给大模型的固定格式说明（输出的是待解析的中间格式，不是成品文件）。"""
    common = (
        "你是「辰鹄AI创作中心」内置的文档写作助手，运行在用户本机，完全离线。"
        "你只输出下面约定的纯文本结构，不要解释、不要客套、不要用代码块包裹。"
        "内容要具体、能直接用；不确定的地方写「（待补充）」而不是编造数字。"
    )
    if fmt == FORMAT_XLSX:
        return (
            common
            + "\n输出格式（严格遵守）：\n"
            "第一行：# 表格标题\n"
            "第二行：表头，列与列之间用 | 分隔，例如 名称 | 数量 | 负责人 | 完成时间\n"
            "从第三行起每行一条数据，同样用 | 分隔，列数必须和表头一致，共 8-15 行。"
            "除了这些行，不要输出任何其他文字。"
        )
    if fmt == FORMAT_PPTX:
        return (
            common
            + "\n输出格式（严格遵守）：\n"
            "# 演示文稿标题\n"
            "第二行：一句话副标题（不超过 30 字）\n"
            "然后 6-10 页，每页以 ## 开头写这一页的标题，"
            "下面用 - 开头写 2-5 条要点，每条不超过 40 字，一页最多两行要点之外的正文。"
        )
    return (
        common
        + "\n输出格式（严格遵守）：\n"
        "# 文档标题\n"
        "第二行：一句话副标题或导语\n"
        "然后 3-6 个小节，每节以 ## 开头写小节标题；"
        "小节里每行一段正文（每段 2-4 句），需要列举时用 - 开头写要点。"
        "全文 600-1200 字，层级清楚，不要输出表格。"
    )


def document_instruction(fmt: str, topic: str) -> str:
    """用户那句话 → 给大模型的写作任务（带上格式要求，防跑题）。"""
    label = FORMAT_LABELS.get(fmt, fmt)
    return (
        f"请写一份{label}，主题：{topic}\n"
        "按系统里约定的格式输出，标题、小节、要点都要齐；中文，直接给内容。"
    )


# ------------------------------------------------------------------------- 解析
def _clean_line(text: str) -> str:
    """去掉 Markdown 噪声与控制字符，压掉多余空白（模型爱写 **粗体**）。"""
    line = _MD_NOISE_TOKEN.sub("", str(text or ""))
    line = "".join(ch if ch >= " " or ch in "\t" else " " for ch in line)
    line = re.sub(r"[ \t]{2,}", " ", line).strip()
    return re.sub(r"\s+([，,。.；;：!！?？])", r"\1", line)


def _new_section(heading: str) -> Section:
    return Section(heading=heading)


def parse_outline(text: str, *, fallback_title: str = "") -> DocumentPlan:
    """把大模型输出的极简标记解析成 DocumentPlan；输出不规整也照样能落文件。

    * ``#``/``##``/``###`` → 标题与小节（``#`` 只取第一个当标题）；
    * ``-``/``*``/``•``/``1.`` → 要点；其余非空行 → 正文段落；
    * 完全没有结构时：整段文本当一个「内容」小节，标题回退到用户那句话。
    """
    plan = DocumentPlan()
    current = Section()
    for raw in str(text or "").splitlines():
        line = _clean_line(raw)
        if not line or _SEPARATOR_TOKEN.match(line):
            continue  # 空行与 ---、=== 这类分隔线都不算内容
        heading = _HEADING_TOKEN.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if level == 1 and not plan.title:
                plan.title = title[:120]
                continue
            if plan.title and not plan.subtitle and level == 2 and not current.paragraphs and not current.bullets:
                # 紧跟大标题的 ## 行往往其实是副标题（模型不分页时很常见）。
                plan.subtitle = title
                continue
            if current.heading or current.paragraphs or current.bullets:
                plan.sections.append(current)
                current = _new_section(title)
            else:
                current = _new_section(title)
            if len(plan.sections) >= MAX_SECTIONS:
                plan.notes.append(f"小节超过 {MAX_SECTIONS} 个，后面截掉了")
                break
            continue
        bullet = _BULLET_TOKEN.match(line)
        if bullet:
            if len(current.bullets) < MAX_BULLETS_PER_SECTION:
                current.bullets.append(bullet.group(1).strip())
            continue
        if len(current.paragraphs) < MAX_PARAGRAPHS_PER_SECTION:
            current.paragraphs.append(line)
    if current.heading or current.paragraphs or current.bullets:
        plan.sections.append(current)
    if not plan.sections:
        plan.sections = [_new_section("内容")]
    if not plan.title:
        plan.title = fallback_title.strip() or "未命名文档"
    # 第一个「没有小节标题」的块通常是全文导语：折叠成副标题，不然 Word 里多一段
    # 没归属的正文，PPT 里更会变成一张只有正文没有标题的页。
    if plan.sections and not plan.sections[0].heading and not plan.sections[0].bullets:
        lead = plan.sections.pop(0)
        if lead.paragraphs and len(lead.paragraphs) <= 2 and not plan.subtitle:
            plan.subtitle = " ".join(lead.paragraphs)[:200]
        elif lead.paragraphs:
            # 段落多就不算导语了：留成「导语」小节，Word 里是正文、PPT 里是一页开场白。
            plan.sections.insert(0, Section(heading="导语", paragraphs=lead.paragraphs))
    if not plan.sections:
        plan.sections = [_new_section("内容")]
    return plan


def parse_table(text: str, *, fallback_title: str = "") -> TablePlan:
    """解析 ``|`` 分隔的表格输出；模型给的是散文时退成「项目 / 内容」两列，照样有文件。"""
    title = ""
    pipe_rows: list[list[str]] = []
    prose: list[str] = []
    for raw in str(text or "").splitlines():
        line = _clean_line(raw)
        if not line:
            continue
        heading = _HEADING_TOKEN.match(line)
        if heading:
            if not title:
                title = heading.group(2).strip()
            continue
        if "|" in line or "｜" in line:
            cells = [cell.strip() for cell in re.split(r"[|｜]", line)]
            cells = [cell for index, cell in enumerate(cells) if not (index in (0, len(cells) - 1) and cell == "")]
            if all(_SEPARATOR_TOKEN.match(cell) and cell for cell in cells):
                continue  # markdown 的 --- 分隔行
            if all(cell == "" for cell in cells):
                continue
            pipe_rows.append(cells[:MAX_TABLE_COLUMNS])
            continue
        if len(prose) < MAX_TABLE_ROWS:
            prose.append(line)

    plan = TablePlan(title=title or fallback_title.strip() or "未命名表格")
    if pipe_rows:
        headers = pipe_rows[0]
        if len(headers) < 2:
            headers = (headers + ["内容"])[:2]
        plan.headers = headers
        width = len(headers)
        for row in pipe_rows[1:]:
            padded = (row + [""] * width)[:width]
            if all(not cell for cell in padded):
                continue
            plan.rows.append(padded)
            if len(plan.rows) >= MAX_TABLE_ROWS:
                break
        if not plan.rows:
            plan.rows = [["（待补充）"] * width]
            plan.notes.append("模型没有给出数据行，表格只留了表头")
    elif prose:
        plan.headers = ["项目", "内容"]
        plan.rows = [[f"条目 {index + 1}", line] for index, line in enumerate(prose)]
        plan.notes.append("模型没有按表格格式输出，已退成「项目 / 内容」两列")
    else:
        plan.headers = ["项目", "内容"]
        plan.rows = [["（待补充）", "（待补充）"]]
        plan.notes.append("模型没有给出可用内容，表格只留了空壳")
    return plan


def skeleton_plan(topic: str, fmt: str) -> DocumentPlan | TablePlan:
    """大模型不可用时的骨架：结构按题目搭，内容一律「（待补充）」。"""
    topic = (topic or "").strip() or "未命名文档"
    if fmt == FORMAT_XLSX:
        return TablePlan(
            title=topic,
            headers=["序号", "项目", "负责人", "完成时间", "备注"],
            rows=[[str(index + 1), "（待补充）", "（待补充）", "（待补充）", "（待补充）"] for index in range(8)],
            notes=["大模型不可用，这份表格只按题目搭了骨架，请直接在上头填内容。"],
        )
    headings = _PPTX_SKELETON if fmt == FORMAT_PPTX else _DOCX_SKELETON
    plan = DocumentPlan(
        title=topic,
        subtitle="（大模型没到位，本次只按题目搭了结构）",
        sections=[Section(heading=name, paragraphs=["（待补充）"]) for name in headings],
        notes=["大模型不可用，这份文档只按题目搭了骨架，内容请在 Word 里补。"],
    )
    return plan


# --------------------------------------------------------------------- 文件名与落盘
def safe_stem(text: str, fallback: str = "文档") -> str:
    """文件名片段：去掉 Windows 非法字符与控制字符，压到 60 字以内。"""
    stem = _ILLEGAL_NAME_CHARS.sub(" ", str(text or ""))
    stem = re.sub(r"\s{2,}", " ", stem).strip().strip(". ")
    if len(stem) > 60:
        stem = stem[:60].strip()
    return stem or fallback


def new_document_path(directory: Path, title: str, fmt: str, *, now: float | None = None) -> Path:
    """目标目录里的落点：``标题-20261107-2015.docx``，重名不覆盖（交给 artifacts.unique_target）。"""

    from app.core.artifacts import unique_target

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(now)) if now else time.strftime("%Y%m%d-%H%M")
    name = f"{safe_stem(title)}-{stamp}.{fmt}"
    return unique_target(directory, name)


# ------------------------------------------------------------------------- 写文件
def _require(fmt: str) -> None:
    """缺库时给出「装哪一个」的人话，而不是 ImportError 堆栈。"""
    import importlib.util

    needed = {
        FORMAT_DOCX: ("docx", "python-docx"),
        FORMAT_XLSX: ("openpyxl", "openpyxl"),
        FORMAT_PPTX: ("pptx", "python-pptx"),
    }[fmt]
    if importlib.util.find_spec(needed[0]) is None:
        raise DocumentEngineMissing(
            f"生成 {FORMAT_LABELS.get(fmt, fmt)} 需要的库 {needed[1]} 没装进运行环境"
            f"（.venv 里缺模块 {needed[0]}）。便携包自带这个库；手动环境请跑："
            f" .\\.venv\\Scripts\\python.exe -m pip install {needed[1]}"
        )


def build_docx(plan: DocumentPlan, path: Path) -> dict[str, Any]:
    """写出 .docx：大标题 + 导语 + 各小节（Heading 1 / 正文 / List Bullet）。"""
    _require(FORMAT_DOCX)
    from docx import Document  # 延迟导入：智能体其它功能不该被文档库拖住

    document = Document()
    document.add_heading(plan.title or "未命名文档", level=0)
    if plan.subtitle:
        document.add_paragraph(plan.subtitle)
    for section in plan.sections:
        if section.heading:
            document.add_heading(section.heading, level=1)
        for text in section.paragraphs:
            document.add_paragraph(text)
        for item in section.bullets:
            document.add_paragraph(item, style="List Bullet")
    for note in plan.notes:
        document.add_paragraph(f"说明：{note}")
    document.save(str(path))
    return {
        "kind": FORMAT_DOCX,
        "title": plan.title,
        "sections": len(plan.sections),
        "paragraphs": plan.paragraph_count,
    }


def build_xlsx(table: TablePlan, path: Path) -> dict[str, Any]:
    """写出 .xlsx：表头加粗冻结、列宽按内容（中文按两格宽算）、备注放最后一行下方。"""
    _require(FORMAT_XLSX)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = (table.sheet_name or table.title or "Sheet1")[:31]

    def width_of(text: str) -> int:
        # CJK 在等宽格里占两格，用 len 累加会算窄列宽。
        units = sum(2 if ord(char) > 0x2E80 else 1 for char in str(text))
        return min(40, max(8, units + 2))

    headers = table.headers or ["项目", "内容"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="left", vertical="center")
    for row in table.rows:
        sheet.append([(cell if cell else None) for cell in row])
    for index, header in enumerate(headers, start=1):
        widest = width_of(header)
        for row in table.rows:
            if index - 1 < len(row):
                widest = max(widest, width_of(row[index - 1]))
        sheet.column_dimensions[get_column_letter(index)].width = widest
    sheet.freeze_panes = "A2"
    if table.notes:
        gap = len(table.rows) + 3
        for offset, note in enumerate(table.notes):
            sheet.cell(row=gap + offset, column=1, value=f"说明：{note}")
    workbook.save(str(path))
    return {
        "kind": FORMAT_XLSX,
        "title": table.title,
        "rows": len(table.rows),
        "columns": len(headers),
    }


def build_pptx(plan: DocumentPlan, path: Path) -> dict[str, Any]:
    """写出 .pptx：标题页 + 每小节一页（标题 + 要点），说明放演讲者备注。"""
    _require(FORMAT_PPTX)
    from pptx import Presentation
    from pptx.util import Pt

    presentation = Presentation()
    cover = presentation.slides.add_slide(presentation.slide_layouts[0])
    cover.shapes.title.text = plan.title or "未命名演示文稿"
    if plan.subtitle:
        try:
            cover.placeholders[1].text = plan.subtitle
        except KeyError:  # 模板没有副标题占位符：不为此失败
            pass
    slides = 0
    for section in plan.sections[: MAX_SLIDES - 1]:
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = section.heading or f"第 {slides + 1} 页"
        body = slide.placeholders[1].text_frame
        body.word_wrap = True
        lines = [item for item in section.bullets] + [item for item in section.paragraphs]
        if not lines:
            lines = ["（待补充）"]
        for index, line in enumerate(lines[:20]):
            paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
            paragraph.text = line
            paragraph.level = 0
            paragraph.font.size = Pt(20)
        if plan.notes:
            slide.notes_slide.notes_text_frame.text = "；".join(plan.notes)
        slides += 1
    presentation.save(str(path))
    return {
        "kind": FORMAT_PPTX,
        "title": plan.title,
        "slides": slides + 1,
        "paragraphs": plan.paragraph_count,
    }


def build_document(plan: DocumentPlan | TablePlan, path: Path, fmt: str) -> dict[str, Any]:
    """按格式分发到三个写库；返回可给界面直接显示的结构概要。"""
    if fmt == FORMAT_XLSX:
        table = plan if isinstance(plan, TablePlan) else TablePlan(title=getattr(plan, "title", ""), rows=[])
        return build_xlsx(table, path)
    if fmt == FORMAT_PPTX:
        return build_pptx(plan if isinstance(plan, DocumentPlan) else DocumentPlan(title=str(plan)), path)
    return build_docx(plan if isinstance(plan, DocumentPlan) else DocumentPlan(title=str(plan)), path)


__all__ = [
    "DocumentEngineMissing",
    "DocumentPlan",
    "FORMATS",
    "FORMAT_DOCX",
    "FORMAT_LABELS",
    "FORMAT_PPTX",
    "FORMAT_XLSX",
    "Section",
    "TablePlan",
    "build_document",
    "build_docx",
    "build_pptx",
    "build_xlsx",
    "document_instruction",
    "document_system_prompt",
    "new_document_path",
    "parse_outline",
    "parse_table",
    "safe_stem",
    "skeleton_plan",
]
