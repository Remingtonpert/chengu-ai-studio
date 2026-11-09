"""下载 SD1.5 图片引擎权重（替换 31GB 的 FLUX：新链路峰值显存压在 6GB 以内）。

三个仓库都是公开仓库（gated:false，机器上没有 HF token 也能下），许可也都能随便携包再分发：

- SG161222/Realistic_Vision_V5.1_noVAE —— SD1.5 精细微调（CreativeML-OpenRAIL-M）。仓库根目录躺着
  同一份权重的四种单文件打包（.ckpt / .safetensors × 有无 inpainting × 有无 fp16，共 23GB），
  本端走 Diffusers 组件目录（unet/ text_encoder/ vae/ ...，约 4.1GB），根目录那批一概不下；
  仓库里的权重只有 fp32，加载时用 dtype=fp16 现场转，显存里仍是半精度。
  仓库名里的 noVAE 是说自带 VAE 质量差，由下面的 stabilityai/sd-vae-ft-mse（MIT）补上。
- stabilityai/sd-vae-ft-mse —— 官方 VAE 微调版（MIT，fp16 约 80MB），出图不糊不发灰。
- latent-consistency/lcm-lora-sdv1-5 —— LCM-LoRA（OpenRAIL++，约 270MB），把 20+ 步压到 4-8 步，
  3060 上单张从 1 分半降到 10 秒上下；显存峰值基本不变。

要点（与 download_video_weights.py 同一套约束）：
- 强制走 hf-mirror：本机直连 huggingface.co 超时（WinError 10060）。
- snapshot_download 断点续传：中断后重跑，已完成的分片跳过。
- 目标目录用 huggingface_hub 标准缓存布局（blobs + models--org--name/snapshots/<sha>），
  引擎侧按快照离线加载，运行期不联网。
- 跳过 assets/、examples/、.gitattributes；保留 README/LICENSE 许可文本（便携包要再分发）。

用法（用含 huggingface_hub 的解释器，推荐 .venv-image）：
    .\\.venv-image\\Scripts\\python.exe scripts\\download_image_weights.py            # 三个都下
    .\\.venv-image\\Scripts\\python.exe scripts\\download_image_weights.py lcm-lora   # 只下某一项
    .\\.venv-image\\Scripts\\python.exe scripts\\download_image_weights.py -DryRun    # 只列清单与体积，不下
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# Xet 通路在 hf-mirror 上不稳（实测：3.3GB 的 unet 拉到 732MB 后 CAS 连接进入
# CloseWait、10 分钟零进展）。关掉它退回经典 HTTP Range 下载，断点续传可靠。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# Windows 默认控制台代码页是 GBK，打不出中文和 emoji：不兜底的话脚本会在 print 时
# 直接抛 UnicodeEncodeError 崩掉，用户只看到「跑不起来」。errors="replace" 保证永不因此崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # 被重定向、已关闭或过老的流：忽略，不影响主流程
        pass

ROOT = Path(__file__).resolve().parents[1]

# huggingface_hub 只在真要下载时才 import：-VerifyOnly 纯粹是文件系统上的快照校验，
# 桌面端那个 .venv 里没装 huggingface_hub，也必须能跑（这里以前是 import 期就炸）。
_api_singleton = None


def _api():
    global _api_singleton
    if _api_singleton is None:
        from huggingface_hub import HfApi  # 延迟到下载路径里 import

        _api_singleton = HfApi()
    return _api_singleton


def _snapshot_download(**kwargs) -> str:
    from huggingface_hub import snapshot_download  # 同上：下载时才 import

    return snapshot_download(**kwargs)
MAX_ATTEMPTS = 8
MIN_WEIGHT_BYTES = 200 * 1024 * 1024  # 小于这个数的「权重」一律不算权重（LoRA 之外没有这种东西）
# LCM-LoRA 一共才一百来 MB，用统一的 200MB 下限会把它误判成「没下全」。
MIN_WEIGHT_BYTES_BY_KEY = {"lcm-lora": 30 * 1024 * 1024}

# 键 -> (仓库, 本地缓存目录, 选择策略)
#   auto_fp16：只取 fp16 变体权重，跳过体积翻倍的 fp32
#   diffusers_dirs：只取组件目录里的权重，跳过仓库根目录的单文件打包（同一份权重的另一种格式）
#   everything：仓库里权重文件本来就小，全取
REPOS = {
    "sd15-checkpoint": (
        "SG161222/Realistic_Vision_V5.1_noVAE",
        ROOT / "models" / "sd-realvis",
        "diffusers_dirs",
    ),
    "sd15-vae": ("stabilityai/sd-vae-ft-mse", ROOT / "models" / "sd-vae-ft-mse", "auto_fp16"),
    "lcm-lora": (
        "latent-consistency/lcm-lora-sdv1-5",
        ROOT / "models" / "lcm-lora-sd15",
        "everything",
    ),
}
SKIP_NAMES = (".gitattributes", ".gitignore", "flax", "onnx")
WEIGHT_SUFFIXES = (".safetensors", ".ckpt", ".bin", ".msgpack", ".ot", ".h5")


def _name_of(entry: object) -> str:
    """huggingface_hub 不同版本里文件名字段叫 path 或 rfilename，两个都认。"""
    return str(getattr(entry, "path", None) or getattr(entry, "rfilename", ""))


def _size_of(entry: object) -> int:
    try:
        return int(getattr(entry, "size", None) or 0)
    except (TypeError, ValueError):
        return 0


def _human(size: int) -> str:
    return f"{size / 1024**2:.0f} MB" if size < 1024**3 else f"{size / 1024**3:.2f} GB"


def _select(repo_id: str, strategy: str) -> tuple[list[str], int, int]:
    """列出该仓库要下的文件（精确路径清单）与其体积；顺带报告跳过了多少。"""

    info = _api().model_info(repo_id, files_metadata=True)
    files = [(_name_of(entry), _size_of(entry)) for entry in getattr(info, "siblings", []) or []]
    files = [(path, size) for path, size in files if path and not path.startswith(".")]
    files = [
        (path, size)
        for path, size in files
        if not any(part in SKIP_NAMES for part in path.lower().split("/"))
        and not path.lower().endswith((".onnx", ".msgpack", ".ot", ".h5"))
    ]
    if not files:
        raise SystemExit(f"[image-weights] {repo_id} 列不出文件清单（网络或仓库名不对）")

    weights = [(path, size) for path, size in files if path.lower().endswith(WEIGHT_SUFFIXES)]
    if strategy == "everything":
        pool = weights
    elif strategy == "diffusers_dirs":
        # 组件目录（unet/、text_encoder/、vae/ ...）里的权重才是 Diffusers 快照要读的东西；
        # 仓库根目录那几份单文件是同一份权重的另一种打包格式，下回来纯属占地方。
        nested = [item for item in weights if "/" in item[0]]
        fp16 = [item for item in nested if "/fp16/" in item[0].lower()]
        pool = fp16 or nested
    else:  # auto_fp16
        fp16 = [item for item in weights if "/fp16/" in item[0].lower()]
        pool = fp16 or weights
    safetensors = [item for item in pool if item[0].lower().endswith(".safetensors")]
    keep_weights = safetensors or pool

    keep = [path for path, _ in keep_weights] + [path for path, _ in files if path not in dict(weights)]
    kept_bytes = sum(size for path, size in files if path in set(keep))
    skipped_bytes = sum(size for path, size in files if path not in set(keep))
    return keep, kept_bytes, skipped_bytes


def _download(repo_id: str, cache_dir: Path, allow: list[str]) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _snapshot_download(
                repo_id=repo_id,
                cache_dir=str(cache_dir),
                allow_patterns=allow,
                max_workers=3,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试，断点续传会跳过已完成分片
            last_exc = exc
            print(f"[image-weights] {repo_id} 第 {attempt} 次失败：{exc}", flush=True)
            time.sleep(10)
    raise SystemExit(f"[image-weights] 下载失败（连续 {MAX_ATTEMPTS} 次）：{repo_id}：{last_exc}")


def _verify(key: str, snapshot: Path) -> list[str]:
    """下完自己核一遍：缺哪个关键文件就直说，别让引擎在运行期才发现。"""

    floor = MIN_WEIGHT_BYTES_BY_KEY.get(key, MIN_WEIGHT_BYTES)
    problems: list[str] = []
    big = sorted(
        (f for f in snapshot.rglob("*") if f.is_file() and f.stat().st_size > floor),
        key=lambda f: -f.stat().st_size,
    )
    if not big:
        problems.append(f"没有任何大于 {floor // (1024 * 1024)}MB 的权重文件（多半没下全）")
    if key == "sd15-checkpoint":
        if not (snapshot / "model_index.json").is_file():
            problems.append("缺 model_index.json（Diffusers 快照不完整）")
        unet = [f for f in big if "/unet/" in f.as_posix().lower() or "unet" in f.parent.name.lower()]
        if not unet:
            problems.append("缺 unet 权重（文生图/图生图都跑不起来）")
        text = [f for f in big if "text_encoder" in f.parent.name.lower()]
        if not text:
            problems.append("缺 text_encoder 权重（提示词编不出来）")
    if key == "sd15-vae" and not any("vae" in f.as_posix().lower() for f in big):
        problems.append("缺 VAE 权重（出图会退回内置 VAE，颜色发灰）")
    if key == "lcm-lora" and not any(
        f.suffix == ".safetensors" and "lora" in f.name.lower() for f in big
    ):
        problems.append("缺 LoRA 权重（加速档位不可用）")
    return problems


def _snapshot_probe(key: str):
    """判定「这份快照算完整」，与引擎侧 app/engine/image/sd15.py 的 probe 一一对应。

    模型快照要 model_index.json（Diffusers pipeline 的入口）；VAE 是普通权重目录，本来就没有
    model_index.json；LCM-LoRA 认 lora_weights.safetensors。一律要求 model_index.json 会把
    已经就位的 VAE 误报成「缺快照」（这次就踩到了）。
    """
    if key == "sd15-checkpoint":
        return lambda snapshot: (snapshot / "model_index.json").is_file()
    if key == "lcm-lora":
        return lambda snapshot: bool(list(snapshot.rglob("*lora_weights*.safetensors")))
    return lambda snapshot: bool(
        [f for f in snapshot.rglob("*") if f.suffix.lower() in (".safetensors", ".bin", ".pt")]
    )


def _local_snapshot(cache: Path, probe) -> Path | None:
    """不联网的前提下找到本地已就绪的快照目录（HF hub 布局：models--org--name/snapshots/<sha>）。

    引擎侧也是这套判定，所以 -VerifyOnly 校验的就是引擎真正会读的那个目录。
    """
    candidates = [path for path in cache.glob("models--*/snapshots/*") if path.is_dir() and probe(path)]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def main() -> None:
    args = [a for a in sys.argv[1:]]
    dry_run = any(a in ("-DryRun", "--dry-run", "-n") for a in args)
    verify_only = any(a in ("-VerifyOnly", "--verify-only") for a in args)
    keys = [a for a in args if not a.startswith("-")] or list(REPOS)
    unknown = [k for k in keys if k not in REPOS]
    if unknown:
        raise SystemExit(f"未知键：{unknown}（可用：{list(REPOS)}）")

    if verify_only:
        print("[image-weights] 模式：只校验本地快照（不联网，任何 venv 都能跑）", flush=True)
    else:
        print(f"[image-weights] endpoint={os.environ['HF_ENDPOINT']}", flush=True)
    grand_total = 0
    for key in keys:
        repo, cache, strategy = REPOS[key]
        if verify_only:
            snapshot = _local_snapshot(cache, _snapshot_probe(key))
            if snapshot is None:
                print(f"[image-weights] 缺失 {key}：{cache.relative_to(ROOT)} 下没有可用快照（先不带 -VerifyOnly 跑一次下载）", flush=True)
                raise SystemExit(1)
            size = sum(f.stat().st_size for f in snapshot.rglob("*") if f.is_file())
            grand_total += size
            problems = _verify(key, snapshot)
            print(f"[image-weights] VERIFY OK {key} snapshot={snapshot.name} 快照内文件={_human(size)}", flush=True)
            for problem in problems:
                print(f"[image-weights] 警告 {key}: {problem}", flush=True)
            if problems:
                raise SystemExit(f"[image-weights] {key} 快照不完整：先重跑本脚本补齐")
            continue
        allow, kept_bytes, skipped_bytes = _select(repo, strategy)
        print(
            f"[image-weights] {key} <- {repo} 取 {len(allow)} 个文件 共 {_human(kept_bytes)}"
            f"（跳过 fp32/展示文件 {_human(skipped_bytes)}）-> {cache.relative_to(ROOT)}",
            flush=True,
        )
        for path in allow:
            if path.lower().endswith(WEIGHT_SUFFIXES):
                print(f"    权重 {path}", flush=True)
        if dry_run:
            continue
        snapshot = Path(_download(repo, cache, allow))
        size = sum(f.stat().st_size for f in snapshot.rglob("*") if f.is_file())
        grand_total += size
        problems = _verify(key, snapshot)
        print(
            f"[image-weights] DONE {key} snapshot={snapshot} 快照内文件={_human(size)}",
            flush=True,
        )
        for problem in problems:
            print(f"[image-weights] 警告 {key}: {problem}", flush=True)
        if problems:
            raise SystemExit(f"[image-weights] {key} 快照不完整，先重跑本脚本补齐再继续")
    if verify_only:
        print(f"[image-weights] 三件套本地快照全部就位，总计={_human(grand_total)}（图像模块峰值显存实测 1.9-2.1GB）", flush=True)
    elif not dry_run:
        print(
            f"[image-weights] ALL DONE models/sd-realvis + sd-vae-ft-mse + lcm-lora-sd15 "
            f"总计={_human(grand_total)}",
            flush=True,
        )


if __name__ == "__main__":
    main()
