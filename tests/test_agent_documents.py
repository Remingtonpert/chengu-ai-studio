"""「AI 智能体 → 生成文档」的离线回归测试。

三件事各自验一遍，全程不联网、不调真大模型、不碰 GPU：
① 「做个排期表 / 写份方案 / 做个 PPT」判成 document，并且不被图片、视频、音乐词抢走，
   「怎么做一份 PPT」这种问法不算生成文件；
② .docx / .xlsx / .pptx 各真写一个出来（python-docx / openpyxl / python-pptx 本机写 OOXML），
   再用同一批库读回校验标题、行列数、页数；
③ 文件落在配置里的输出目录，并按生成模块同一套规则镜像到项目内 output\\ 与自动另存目录，
   重名不覆盖。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import documents as docs
from app.agent.router import (
    ACTION_CHAT,
    ACTION_DOCUMENT,
    ACTION_GENERATE,
    HELP_TEXT,
    describe,
    parse_command,
)
from app.agent.service import AgentService

_MODEL_OUTLINE = (
    "# 新品上市方案\n"
    "这次上市分三步走，预算集中在头两周。\n"
    "## 目标人群\n"
    "- 一线城市白领\n"
    "- 高校学生\n"
    "## 渠道\n"
    "线上以短视频为主，线下进便利店。\n"
)

_MODEL_TABLE = (
    "| 阶段 | 时间 | 负责人 |\n"
    "| --- | --- | --- |\n"
    "| 筹备 | 3 月上旬 | 阿哲 |\n"
    "| 预热 | 4 月 | 小林 |\n"
    "| 上线 | 5 月 9 日 | 全员 |\n"
)


def _service(tmp_path, *, chat=None, llm_ready=True) -> AgentService:
    """造一个只带文档能力的智能体：输出目录指到临时目录，大模型用桩。"""
    service = AgentService(
        task_service=SimpleNamespace(project_output_dir="", auto_save_dir=""),
        engine_manager=None,
        config={
            "llm_endpoint": "http://127.0.0.1:11435/v1",
            "llm_model": "qwen3:8b",
            "output_dir": str(tmp_path / "Output"),
        },
    )
    if llm_ready:
        # 端点归别人管 → _ensure_local_llm 直接放行，不让测试去起 llama-server。
        service.local_llm.owns_endpoint = lambda: False  # type: ignore[method-assign]
    else:
        service.local_llm.ensure_running = lambda **_kw: {  # type: ignore[method-assign]
            "ok": False,
            "state": "missing",
            "note": "没找到自带的 llama-server",
        }
    service.llm.chat = (  # type: ignore[method-assign]
        chat if chat is not None else (lambda messages, **_kw: _MODEL_OUTLINE)
    )
    service.llm.resolve_model = lambda: "qwen3:8b"  # type: ignore[method-assign]
    return service


# ------------------------------------------------------------------ ① 意图判定
def test_document_commands_pick_the_right_format() -> None:
    xlsx = parse_command("做个新品发布会的排期表")
    assert xlsx.action == ACTION_DOCUMENT and xlsx.document_format == "xlsx"
    assert "新品发布会" in xlsx.prompt

    docx = parse_command("写份活动执行方案")
    assert docx.action == ACTION_DOCUMENT and docx.document_format == "docx"
    assert "活动执行" in docx.prompt

    pptx = parse_command("做个产品介绍 PPT")
    assert pptx.action == ACTION_DOCUMENT and pptx.document_format == "pptx"
    assert "产品介绍" in pptx.prompt

    for command in (xlsx, docx, pptx):
        assert command.is_document is True
        assert command.prompt.strip()  # 主题不能把「做个」「排期表」这类壳子留下


def test_document_topic_drops_the_shell_words() -> None:
    command = parse_command("帮我做一份竞品对照表")
    assert command.action == ACTION_DOCUMENT and command.document_format == "xlsx"
    assert command.prompt == "竞品"


def test_generation_words_cannot_steal_a_document_request() -> None:
    """「画一张海报，主题是新品的上市方案」还是文生图：媒体词在前就该归媒体。"""
    command = parse_command("画一张海报，主题是新品的上市方案")
    assert command.action == ACTION_GENERATE and command.mode == "t2i"

    video = parse_command("生成一段发布会开场视频，主题是新品方案")
    assert video.action == ACTION_GENERATE and video.mode == "t2v"


def test_image_to_image_request_is_not_a_document() -> None:
    command = parse_command("把这张图改成水彩风", has_ref_image=True)
    assert command.action != ACTION_DOCUMENT


def test_how_to_questions_stay_as_chat() -> None:
    for text in ("怎么做一份PPT", "PPT怎么做", "Excel表格怎么冻结首行"):
        command = parse_command(text)
        assert command.action != ACTION_DOCUMENT, text
        assert command.action in (ACTION_CHAT, "help"), text


def test_loose_char_biao_alone_never_triggers_a_spreadsheet() -> None:
    command = parse_command("写一段文案，表现品牌的温度")
    assert command.action != ACTION_DOCUMENT


def test_describe_and_help_mention_the_three_formats() -> None:
    assert "Excel" in describe(parse_command("做个排期表"))
    assert "Word" in describe(parse_command("写份说明书"))
    assert ".pptx" in describe(parse_command("做个汇报 PPT")) or "PPT" in describe(
        parse_command("做个汇报 PPT")
    )
    for token in ("docx", "xlsx", "pptx"):
        assert token in HELP_TEXT


# ------------------------------------------------------------- ② 三种格式落盘
def test_build_docx_then_read_it_back(tmp_path: Path) -> None:
    from docx import Document

    plan = docs.parse_outline(_MODEL_OUTLINE, fallback_title="新品上市方案")
    assert plan.title == "新品上市方案"
    assert [section.heading for section in plan.sections] == ["目标人群", "渠道"]

    path = docs.new_document_path(tmp_path, plan.title, docs.FORMAT_DOCX)
    info = docs.build_document(plan, path, docs.FORMAT_DOCX)
    assert path.suffix == ".docx" and path.exists() and path.stat().st_size > 5000
    assert info["sections"] == 2

    document = Document(str(path))
    texts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    assert texts[0] == "新品上市方案"
    assert "一线城市白领" in texts
    assert "线上以短视频为主，线下进便利店。" in texts


def test_build_xlsx_then_read_it_back(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    table = docs.parse_table(_MODEL_TABLE, fallback_title="新品排期")
    assert table.headers == ["阶段", "时间", "负责人"]
    assert len(table.rows) == 3

    path = docs.new_document_path(tmp_path, table.title, docs.FORMAT_XLSX)
    info = docs.build_document(table, path, docs.FORMAT_XLSX)
    assert path.suffix == ".xlsx" and path.exists() and path.stat().st_size > 3000
    assert info["rows"] == 3 and info["columns"] == 3

    sheet = load_workbook(str(path)).active
    assert [cell.value for cell in sheet[1]] == ["阶段", "时间", "负责人"]
    assert sheet["A4"].value == "上线"
    assert sheet.freeze_panes == "A2"  # 表头冻住，滚下去也认得列


def test_prose_for_a_table_falls_back_to_two_columns(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    table = docs.parse_table("阶段：3 月预热\n上线：5 月 9 日", fallback_title="排期")
    path = docs.new_document_path(tmp_path, table.title, docs.FORMAT_XLSX)
    docs.build_document(table, path, docs.FORMAT_XLSX)
    sheet = load_workbook(str(path)).active
    assert sheet.max_column == 2
    assert sheet.max_row >= 3


def test_build_pptx_then_read_it_back(tmp_path: Path) -> None:
    from pptx import Presentation

    plan = docs.parse_outline(_MODEL_OUTLINE, fallback_title="新品上市方案")
    path = docs.new_document_path(tmp_path, plan.title, docs.FORMAT_PPTX)
    info = docs.build_document(plan, path, docs.FORMAT_PPTX)
    assert path.suffix == ".pptx" and path.exists() and path.stat().st_size > 10000
    # 封面 1 页 + 两个小节各 1 页，导语折到封面上，不多出一张空标题页
    assert info["slides"] == 3

    slides = list(Presentation(str(path)).slides)
    assert len(slides) == 3
    assert slides[0].shapes.title.text == "新品上市方案"
    assert slides[1].shapes.title.text == "目标人群"


def test_skeleton_plan_marks_everything_to_fill_in(tmp_path: Path) -> None:
    plan = docs.skeleton_plan("年度预算", docs.FORMAT_XLSX)
    path = docs.new_document_path(tmp_path, plan.title, docs.FORMAT_XLSX)
    info = docs.build_document(plan, path, docs.FORMAT_XLSX)
    assert info["rows"] == 8 and info["columns"] == 5

    from openpyxl import load_workbook

    sheet = load_workbook(str(path)).active
    assert sheet["B2"].value == "（待补充）"
    assert any(str(cell.value or "").startswith("说明：") for cell in sheet["A"])


def test_same_title_does_not_overwrite(tmp_path: Path) -> None:
    stamp = 1_700_000_000.0  # 钉死时间戳，确保两次要的是同一个名字
    first = docs.new_document_path(tmp_path, "排期表", docs.FORMAT_DOCX, now=stamp)
    docs.build_document(docs.skeleton_plan("排期表", docs.FORMAT_DOCX), first, docs.FORMAT_DOCX)
    # 第一份已经落在盘上了：同名再要一次，不能把刚写好的盖掉
    second = docs.new_document_path(tmp_path, "排期表", docs.FORMAT_DOCX, now=stamp)
    assert first != second
    docs.build_document(docs.skeleton_plan("排期表", docs.FORMAT_DOCX), second, docs.FORMAT_DOCX)
    assert first.exists() and second.exists()
    assert re.search(r"-1\.docx$", second.name)


def test_documents_module_never_touches_the_network() -> None:
    """这条是「全程离线」的硬护栏：写文档只准用本地库，不许偷偷联网。"""
    source = Path(__file__).resolve().parents[1].joinpath("app", "agent", "documents.py").read_text(
        encoding="utf-8"
    )
    for banned in ("urllib", "requests", "socket", "http.client", "httpx", "ftp"):
        assert not re.search(rf"\bimport\s+{re.escape(banned)}\b", source), banned
        assert not re.search(rf"\bfrom\s+{re.escape(banned)}\b", source), banned
    assert "http://" not in source and "https://" not in source


# --------------------------------------------------------- ③ 服务层：落点与镜像
def test_agent_writes_a_spreadsheet_with_real_model_content(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    project_output = tmp_path / "project-output"
    auto_save = tmp_path / "D-drive-Output"
    service = _service(
        tmp_path,
        chat=lambda messages, **_kw: _MODEL_TABLE,
    )
    service.task_service = SimpleNamespace(
        project_output_dir=str(project_output), auto_save_dir=str(auto_save)
    )

    result = service.handle("做个新品上市的排期表")
    assert result["action"] == ACTION_DOCUMENT
    assert result["error"] == "" and result["degraded"] is False
    assert len(result["documents"]) == 1

    entry = result["documents"][0]
    path = Path(entry["path"])
    assert path.parent == tmp_path / "Output"  # 与配置里的输出目录一致
    assert entry["format"] == "xlsx" and entry["bytes"] > 3000
    assert entry["url"].startswith("/outputs/") and entry["label"].startswith("Excel")

    sheet = load_workbook(str(path)).active
    assert sheet["A1"].value == "阶段"
    assert "5 月 9 日" in [cell.value for cell in sheet[4]]

    # 与图片/视频同一套镜像规则：项目内 output\ 和自动另存目录各多一份
    mirrors = {Path(target).parent for target in entry["mirrors"]}
    assert mirrors == {project_output, auto_save}
    for target in entry["mirrors"]:
        assert Path(target).exists()
    assert str(path.parent) in result["reply"] and "同一份也放到了" in result["reply"]
    assert result["model"] == "qwen3:8b"
    assert any("qwen3:8b" in note for note in result["notes"])


def test_agent_falls_back_to_a_skeleton_when_the_llm_is_not_ready(tmp_path: Path) -> None:
    service = _service(tmp_path, llm_ready=False)
    result = service.handle("写份年会策划方案")
    assert result["action"] == ACTION_DOCUMENT
    assert len(result["documents"]) == 1
    assert result["degraded"] is True
    assert result["documents"][0]["format"] == "docx"
    assert Path(result["documents"][0]["path"]).exists()
    assert any("骨架" in note for note in result["notes"])

    from docx import Document

    texts = [p.text for p in Document(result["documents"][0]["path"]).paragraphs if p.text.strip()]
    assert any("（待补充）" in text for text in texts)


def test_agent_degrades_when_the_model_answers_empty(tmp_path: Path) -> None:
    service = _service(tmp_path, chat=lambda messages, **_kw: "")
    result = service.handle("做个项目复盘 PPT")
    assert result["degraded"] is True
    assert result["documents"][0]["format"] == "pptx"
    assert Path(result["documents"][0]["path"]).exists()
    assert any("没给出可用内容" in note for note in result["notes"])


def test_agent_mirrors_into_project_output_when_task_service_is_absent(
    tmp_path: Path, monkeypatch
) -> None:
    """独立跑智能体（没有 TaskService 的槽位）时，也要保证项目内 output\\ 有一份。"""
    import app.agent.service as agent_service

    stand_in = tmp_path / "project-output"
    monkeypatch.setattr(
        agent_service.AppPaths,
        "from_environment",
        classmethod(
            lambda cls, **_kw: SimpleNamespace(
                project_output_dir=stand_in,
                output_dir=tmp_path / "Output",
                models_dir=tmp_path / "models",
            )
        ),
    )
    service = _service(tmp_path)
    service.task_service = None
    result = service.handle("写份使用说明文档")
    entry = result["documents"][0]
    assert entry["mirrors"], entry
    assert Path(entry["mirrors"][0]).parent == stand_in


# ------------------------------------------------------------- 网页端发文件
def test_web_console_serves_office_files_with_the_right_type() -> None:
    from app.web.server import _MEDIA_TYPES, _OUTPUT_PATH

    expected = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    for suffix, mime in expected.items():
        assert _MEDIA_TYPES.get(suffix) == mime
    assert _OUTPUT_PATH.match("/outputs/%E6%96%B9%E6%A1%88-20261107-2015.docx")
