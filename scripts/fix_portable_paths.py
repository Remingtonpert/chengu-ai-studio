"""便携目录包首次运行路径自适应（辰鹄AI创作中心 · 打包迁移专用，纯本地操作）。

背景：Windows venv 的 python.exe 是「重定向器」，运行时读取同目录 pyvenv.cfg 里
home= 指向的基础解释器；pip 可编辑安装（vendor\\ACE-Step-1.5）也在 site-packages
里写死了绝对路径。整目录包复制到其他电脑后，这些绝对路径会失效。

本脚本在目标机首次启动时自动执行（由启动 bat 调用，幂等）：
  1) 把三个虚拟环境（.venv / .venv-image / .venv-music）的 pyvenv.cfg home
     指向包内自带的 runtime\\python311（打包时随包复制的同一份 CPython 3.11）；
  2) 把 pyvenv.cfg、site-packages 内 *.pth / __editable__*_finder.py 里出现的
     打包机路径（build_manifest.json 记录的 source_root）替换为当前包根路径；
  3) 首次创建 data\\apphome\\config.json（engines_enabled=true——包内已带权重与
     环境；models_dir/output_dir 等路径键留空走默认值，自动相对新包根计算）。
不联网、不下载、不上传任何信息；再次运行时若包根未变化则直接跳过。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

VENV_DIRS = [".venv", ".venv-image", ".venv-music"]
PACKAGED_RUNTIME = "runtime/python311"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    marker = root / ".portable_root"

    manifest_path = root / "build_manifest.json"
    if not manifest_path.is_file():
        print("缺少 build_manifest.json：这不是打包脚本产出的便携目录包？")
        return 1
    try:
        source_root = json.loads(manifest_path.read_text(encoding="utf-8"))["source_root"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"build_manifest.json 读取失败：{exc}")
        return 1

    # marker 记录「文件里目前嵌着的根路径」：首次=打包机路径，之后=上一次改写后的
    # 包根。包再次搬家时嵌入路径与新位置不符，就再改写一轮（支持反复迁移）。
    embedded = marker.read_text(encoding="utf-8").strip() if marker.is_file() else source_root
    if embedded == str(root):
        print(f"路径自适应已完成过（包根未变化）：{root}")
        return 0

    runtime_home = root / PACKAGED_RUNTIME
    if not (runtime_home / "python.exe").is_file():
        print(f"包内运行时不完整：缺少 {runtime_home / 'python.exe'}")
        return 1

    changed = 0
    for venv_name in VENV_DIRS:
        venv = root / venv_name
        cfg = venv / "pyvenv.cfg"
        if not cfg.is_file():
            continue
        lines = cfg.read_text(encoding="utf-8").splitlines()
        fixed: list[str] = []
        for line in lines:
            key = line.strip().lower()
            if key.startswith("home"):
                fixed.append(f"home = {runtime_home}")
            elif key.startswith("executable"):
                # venv 重定向器记录的打包机基础解释器：目标机不存在，改写为包内运行时。
                fixed.append(f"executable = {runtime_home / 'python.exe'}")
            else:
                fixed.append(
                    line.replace(embedded, str(root)).replace(
                        embedded.replace("\\", "\\\\"), str(root).replace("\\", "\\\\")
                    )
                )
        cfg.write_text("\n".join(fixed) + "\n", encoding="utf-8")
        changed += 1

        site_packages = venv / "Lib" / "site-packages"
        if site_packages.is_dir():
            for path in list(site_packages.glob("*.pth")) + list(site_packages.glob("__editable__*_finder.py")):
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                patched = text.replace(embedded, str(root)).replace(
                    embedded.replace("\\", "\\\\"), str(root).replace("\\", "\\\\")
                )
                if patched != text:
                    path.write_text(patched, encoding="utf-8")
                    changed += 1

    # 包内数据目录与最小配置：默认全部相对包根；已有用户配置则绝不覆盖。
    app_home = root / "data" / "apphome"
    config_path = app_home / "config.json"
    if not config_path.is_file():
        app_home.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"engines_enabled": True}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed += 1

    marker.write_text(str(root), encoding="utf-8")
    print(f"路径自适应完成（改写 {changed} 处）；包根 = {root}；改写前嵌入路径 = {embedded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
