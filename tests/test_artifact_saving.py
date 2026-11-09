r"""产物保存：项目内 output\ 那份始终保留，再按用户自由选择复制一份到别处。

用户的要求是「生成好的内容在工具里显示，同时保存工具内 output\ 或保存到本地硬盘（可以自由
选择）」。这一组测试盯住三件事：复制规则（重名绝不覆盖、失败不牵连任务）、任务完成后的自动
另存（并把副本路径记进结果，两端界面都能显示）、以及两端共用的设置接口（改完立刻生效，不必
重启；桌面端「设置」页与网页端「产物保存」面板写的是同一份 config.json）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.artifacts import copy_artifacts, unique_target
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskRequest, TaskResult, TaskStatus
from app.tasks.service import TaskService
from app.web.server import HttpError, WebApplication, _record_to_dict


# ------------------------------------------------------------------ 复制规则
def test_unique_target_never_overwrites_existing_file(tmp_path: Path) -> None:
    first = tmp_path / "art.png"
    first.write_bytes(b"keep me")
    assert unique_target(tmp_path, "art.png") == tmp_path / "art-1.png"
    (tmp_path / "art-1.png").write_bytes(b"x")
    assert unique_target(tmp_path, "art-1.png") == tmp_path / "art-1-1.png"
    # 没有扩展名的产物名也要能继续排下去，而不是把已有文件顶掉
    (tmp_path / "frame").write_bytes(b"y")
    assert unique_target(tmp_path, "frame") == tmp_path / "frame-1"
    assert first.read_bytes() == b"keep me"


def test_copy_artifacts_creates_dir_and_skips_missing_files(tmp_path: Path) -> None:
    source = tmp_path / "output" / "art.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png bytes")
    dest = tmp_path / "E盘" / "作品"
    # 没配置自动另存目录＝什么都不做（产物只在项目内 output\ 里）
    assert copy_artifacts([str(source)], "") == []
    assert not dest.exists()

    saved = copy_artifacts([str(source), str(tmp_path / "output" / "gone.png")], dest)
    assert len(saved) == 1  # 缺失的产物只跳过，不抛出
    assert saved[0]["source"] == str(source)
    copied = Path(saved[0]["target"])
    assert copied.parent == dest and copied.is_file()
    assert copied.read_bytes() == b"png bytes"
    # 同一个产物再生成一次：副本排到 art-1.png，前一份不被覆盖
    again = copy_artifacts([str(source)], dest)
    assert Path(again[0]["target"]).name == "art-1.png"
    assert copied.read_bytes() == b"png bytes"


def test_copy_artifacts_skips_when_file_already_lives_in_target_dir(tmp_path: Path) -> None:
    # output_dir 就指着项目内 output\ 时，镜像不能把产物变成 art-1.png 这种重复文件
    source = tmp_path / "output" / "art.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png bytes")
    assert copy_artifacts([str(source)], source.parent) == []
    assert copy_artifacts([str(source)], str(source.parent) + "\\") == []
    assert sorted(p.name for p in source.parent.iterdir()) == ["art.png"]


def test_project_output_mirror_and_auto_save_dir_each_get_one_copy(tmp_path: Path) -> None:
    service = TaskService(tmp_path / "studio.sqlite3", EngineManager(), task_timeout_seconds=5.0)
    produced = tmp_path / "ChenguAI" / "Output"
    produced.mkdir(parents=True)
    png = produced / "art.png"
    png.write_bytes(b"png bytes")
    project_output = tmp_path / "Third Project" / "output"
    extra = tmp_path / "移动硬盘" / "作品"
    service.set_project_output_dir(str(project_output))
    service.set_auto_save_dir(str(extra))

    saved = service._copy_to_extra_dirs([str(png)])
    assert {Path(item["target"]).parent for item in saved} == {project_output, extra}
    assert all(Path(item["target"]).is_file() for item in saved)
    assert png.is_file()  # 原始那一份（工具里即时显示的）永远不动

    # 两处配成同一个目录（大小写/结尾分隔符不同也算同一个）只复制一次，不会多存一份
    fresh = produced / "fresh.png"
    fresh.write_bytes(b"other bytes")
    service.set_auto_save_dir(str(project_output) + "\\")
    once = service._copy_to_extra_dirs([str(fresh)])
    assert len(once) == 1 and Path(once[0]["target"]).name == "fresh.png"


# ---------------------------------------------------- 任务完成后的自动另存
class ArtifactTestAdapter:
    """test-only 适配器：往「项目内 output\\」写一个假产物，只看复制逻辑。"""

    engine_id = "test-only-artifacts"
    task_types = frozenset({"image"})
    test_only = True

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    async def health_check(self):
        return True, "test adapter only; no media generation"

    async def validate_request(self, request):
        if request.task_type not in self.task_types:
            raise ValueError("unsupported test task")

    async def generate(self, request):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        png = self.output_dir / f"{request.parameters['name']}.png"
        png.write_bytes(b"\x89PNG fake artifact")
        return TaskResult(
            output_files=[str(png)],
            metadata={"test_only": True},
            test_only=True,
        )

    async def shutdown(self):
        return None


def test_completed_task_copies_artifact_to_auto_save_dir(tmp_path: Path) -> None:
    async def scenario():
        project_output = tmp_path / "output"
        dest = tmp_path / "本地硬盘" / "我的作品"
        manager = EngineManager()
        manager.register_factory(
            ArtifactTestAdapter.engine_id,
            lambda: ArtifactTestAdapter(project_output),
        )
        service = TaskService(
            tmp_path / "studio.sqlite3", manager, task_timeout_seconds=5.0
        )
        service.set_auto_save_dir(str(dest))
        first = service.submit_task(
            TaskRequest(
                task_type="image",
                engine_id=ArtifactTestAdapter.engine_id,
                prompt="自动另存样例",
                parameters={"test_only": True, "name": "art"},
            )
        )
        await service.start()
        await service.wait_until_idle()

        record = service.get_task(first.task_id)
        assert record.status is TaskStatus.COMPLETED
        original = Path(record.output_files[0])
        # 工具里即时显示、归队列管的那一份始终在项目内 output\ 里
        assert original.parent == project_output and original.is_file()
        copies = record.result_metadata["saved_copies"]
        assert copies, "副本路径要记进结果，桌面端和网页端才显示得出「已另存到 …」"
        copied = Path(copies[0])
        assert copied.parent == dest and copied.is_file()
        assert copied.read_bytes() == original.read_bytes()
        payload = _record_to_dict(record, project_output)
        assert payload["saved_copies"] == [str(copied)]

        # 留空＝关闭自动另存：只存项目内 output\，不再往别处复制
        service.set_auto_save_dir("")
        second = service.submit_task(
            TaskRequest(
                task_type="image",
                engine_id=ArtifactTestAdapter.engine_id,
                prompt="关闭自动另存",
                parameters={"test_only": True, "name": "art2"},
            )
        )
        await service.wait_until_idle()
        second_record = service.get_task(second.task_id)
        assert second_record.status is TaskStatus.COMPLETED
        assert not second_record.result_metadata.get("saved_copies")
        assert not (dest / "art2.png").exists()
        assert Path(second_record.output_files[0]).parent == project_output
        await service.shutdown()

    asyncio.run(scenario())


# -------------------------------------------------- 两端共用的设置接口
class _StubSettingsApp:
    """只为设置接口准备的最小宿主：不为了两个方法去起整个 HTTP 服务。"""

    output_dir = Path("output")
    logger = logging.getLogger("test.artifact.settings")
    _settings_payload = WebApplication._settings_payload
    _update_settings = WebApplication._update_settings


class _RecordingStore:
    def __init__(self) -> None:
        self.saved: dict | None = None

    def save(self, config: dict) -> None:
        self.saved = dict(config)


def _stub_app() -> _StubSettingsApp:
    app = _StubSettingsApp()
    app.services = SimpleNamespace(
        config={"auto_save_dir": "", "llm_model": "qwen3:8b"},
        config_store=_RecordingStore(),
        paths=SimpleNamespace(project_output_dir=Path("ThirdProject/output")),
        task_service=SimpleNamespace(applied=[], set_auto_save_dir=None),
    )
    applied: list[str] = []
    app.services.task_service.set_auto_save_dir = applied.append
    app.services.applied = applied
    return app


def test_auto_save_dir_setting_validates_and_applies_immediately(
    tmp_path: Path,
) -> None:
    app = _stub_app()
    payload = app._settings_payload()
    assert payload["auto_save_dir"] == ""
    assert payload["project_output_dir"] == str(Path("ThirdProject/output"))

    with pytest.raises(HttpError):  # 缺字段：宁缺勿猜，免得把产物复制到猜出来的目录
        app._update_settings({})
    with pytest.raises(HttpError):  # 相对路径必须拒绝（会落到进程工作目录，谁也找不到）
        app._update_settings({"auto_save_dir": "output副本"})
    with pytest.raises(HttpError):
        app._update_settings({"auto_save_dir": 7})

    dest = tmp_path / "作品副本"
    result = app._update_settings({"auto_save_dir": f'  "{dest}"  '})
    assert result["status"] == "ok"
    assert dest.is_dir()  # 填的时候就建好，别等生成完十几分钟才发现写不进去
    assert app.services.applied == [str(dest)]  # 本进程的运行期实例立刻生效
    assert app.services.config["auto_save_dir"] == str(dest)
    saved = app.services.config_store.saved
    assert saved is not None and saved["auto_save_dir"] == str(dest)
    assert saved["llm_model"] == "qwen3:8b"  # 只改这一项，其它配置原样保留

    cleared = app._update_settings({"auto_save_dir": ""})
    assert cleared["auto_save_dir"] == ""
    assert app.services.applied[-1] == ""
    assert app.services.config_store.saved["auto_save_dir"] == ""
