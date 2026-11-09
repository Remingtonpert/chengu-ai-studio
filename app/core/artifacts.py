r"""生成产物的另存与自动复制（桌面端与 Web 控制台共用）。

产物先写进配置的 output_dir（工具里即时显示、归队列管理的那一份），本模块只负责「再存一份
到用户要的地方」：固定镜像到项目内 output\（用户明确要求的位置），以及配置了 auto_save_dir
时的自选目录（本地硬盘任意位置，包括移动硬盘）。目标目录不存在就创建，重名绝不覆盖（在扩展
名前加 -1、-2 后缀），产物已在目标目录里就不重复复制；单个文件复制失败只记日志、不抛出——
生成结果本身不应该因为一份副本没存上就被判成失败。

配置项 auto_save_dir 留空＝只保留 output_dir 与项目内 output\ 两处；填了则每次生成完成后
再复制一份过去。界面上的手动「另存为」不受这个配置影响。
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def _same_dir(left: Path, right: Path) -> bool:
    """两个路径是否指向同一个目录（大小写、8.3、符号链接都算同一个）。"""
    try:
        return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
            os.path.realpath(right)
        )
    except OSError:  # 极端情况下（路径已消失）宁可当作不同目录，复制一份不会有害
        return False


def unique_target(dest_dir: Path, name: str) -> Path:
    """目标目录内的落点：重名时不覆盖已有文件，在扩展名前加 -1、-2 …"""
    target = dest_dir / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def copy_artifacts(files: Iterable[str], dest_dir: str | Path) -> list[dict[str, str]]:
    """把产物复制到 dest_dir，返回 [{"source": 原路径, "target": 副本路径}]。

    dest_dir 为空（未配置自动另存）时直接返回空列表；目录无法创建或某个文件复制
    失败时只写日志，成功的部分照常返回。
    """
    text = str(dest_dir or "").strip()
    if not text:
        return []
    directory = Path(text).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _LOGGER.warning("自动另存目录无法创建，本次不另存：%s（%s）", directory, exc)
        return []
    saved: list[dict[str, str]] = []
    for raw in files:
        source = Path(str(raw))
        try:
            if not source.is_file():
                _LOGGER.warning("产物文件不存在，跳过另存：%s", source)
                continue
            if _same_dir(source.parent, directory):
                # 产物本来就在目标目录里（output_dir 就指着它）：再复制只会多出
                # art-1.png 这种重复文件，直接跳过。
                continue
            target = unique_target(directory, source.name)
            shutil.copy2(source, target)
        except OSError as exc:
            _LOGGER.warning("另存产物失败，已跳过：%s → %s（%s）", source, directory, exc)
            continue
        saved.append({"source": str(source), "target": str(target)})
    if saved:
        _LOGGER.info("已另存 %d 个产物到 %s", len(saved), directory)
    return saved
