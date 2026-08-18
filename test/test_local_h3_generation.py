from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


H3_LOCAL_ROOT = Path("/kwkj-k8s/zwb/应用/H3/本地推理")
H3_ASSETS_DIR = Path("/kwkj-k8s/MiniMax-H3/assets")

I2VA_FIRST_FRAME = H3_ASSETS_DIR / "fl2va-clay-fox-reference.png"
R2VA_REF_IMAGE = H3_ASSETS_DIR / "character-action-reference.png"
R2VA_REF_VIDEO = H3_ASSETS_DIR / "action-reference.mov"


def _require_file(path: Path) -> None:
    """断言本机文件存在，否则本地出片测试直接失败。"""
    assert path.is_file(), f"缺少本机素材：{path}"


def _env_enabled() -> bool:
    """读取环境变量：默认不启用重型本地出片测试。"""
    return os.getenv("RUN_LOCAL_H3_MEDIA_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}


def _run_bash(cmd: list[str], *, timeout_sec: int) -> None:
    """运行 bash 命令并在失败时抛出更明确的错误。"""
    # SLA 本地推理运行时需要 sparse_linear_attention；源码在 MiniMax-H3/third_party/SLA。
    # 由于该 venv 里未必安装了 pip 包，这里用 PYTHONPATH 方式临时补齐 import。
    sla_path = "/kwkj-k8s/MiniMax-H3/third_party/SLA"
    existing = os.environ.get("PYTHONPATH", "").strip()
    pythonpath = sla_path + (f":{existing}" if existing else "")
    run_env = {**os.environ, "PYTHONPATH": pythonpath, "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_sec,
        env=run_env,
    )
    if proc.returncode != 0:
        # 显存不足属于“环境不可用”，测试层面跳过而不是失败。
        if "CUDA out of memory" in proc.stdout:
            pytest.skip("本地显存不足：CUDA out of memory")
        raise RuntimeError(f"命令失败（rc={proc.returncode}）：{' '.join(cmd)}\n输出：\n{proc.stdout}")


@pytest.mark.parametrize(
    "mode,script,extra_args",
    [
        ("t2va", "gen_t2va_fast.sh", []),
        ("i2va", "gen_fl2va.sh", ["--image", str(I2VA_FIRST_FRAME)]),
        ("r2va", "gen_ref2va.sh", ["--image", str(R2VA_REF_IMAGE), "--video", str(R2VA_REF_VIDEO)]),
    ],
)
def test_local_h3_generate_mp4_if_enabled(tmp_path: Path, mode: str, script: str, extra_args: list[str]) -> None:
    """
    可选：启用时会调用 MiniMax-H3 的本地脚本生成 mp4。

    启用方式：
    `RUN_LOCAL_H3_MEDIA_TESTS=1 pytest -q test/test_local_h3_generation.py -k local_h3`
    """
    if not _env_enabled():
        pytest.skip("未启用：设置 RUN_LOCAL_H3_MEDIA_TESTS=1 才会跑本地出片")

    _require_file(I2VA_FIRST_FRAME)
    _require_file(R2VA_REF_IMAGE)
    _require_file(R2VA_REF_VIDEO)
    assert H3_LOCAL_ROOT.is_dir(), f"找不到本地推理根目录：{H3_LOCAL_ROOT}"

    out_path = tmp_path / f"{mode}.mp4"
    prompt = {
        "t2va": "雨夜涩谷，红色巴士穿过路口，氛围灯光，环境音，无对白",
        "i2va": "人物向前走，镜头缓慢推进，电影光影，自然动作",
        "r2va": "保持角色人设，在参考视频动作基础上完成流畅移动，电影光影",
    }[mode]

    # 允许用环境变量覆盖 GPU 选择，避免不同机器 GPU 编号不一致导致失败。
    # 默认优先选空闲卡，尽量避开常驻的 0-3。
    if mode == "t2va":
        gpus = os.getenv("LOCAL_H3_T2VA_GPUS", "5,6")
        cmd = [
            "bash",
            str(H3_LOCAL_ROOT / script),
            "--gpus",
            gpus,
            "--seed",
            "7",
            "--out",
            str(out_path),
            "--prompt",
            prompt,
            "--steps",
            os.getenv("LOCAL_H3_T2VA_STEPS", "20"),
        ]
    elif mode == "i2va":
        gpus = os.getenv("LOCAL_H3_FL2VA_GPUS", "5,6")
        cmd = [
            "bash",
            str(H3_LOCAL_ROOT / script),
            "--gpus",
            gpus,
            "--seed",
            "7",
            "--out",
            str(out_path),
            "--prompt",
            prompt,
            *extra_args,
        ]
    else:
        gpus = os.getenv("LOCAL_H3_REF2VA_GPUS", "6,7")
        cmd = [
            "bash",
            str(H3_LOCAL_ROOT / script),
            "--gpus",
            gpus,
            "--seed",
            "7",
            "--out",
            str(out_path),
            "--prompt",
            prompt,
            *extra_args,
        ]

    # 由于本地出片可能很慢，这里给一个相对宽松的超时（可在需要时通过环境变量调整）。
    timeout_sec = int(os.getenv("LOCAL_H3_TEST_TIMEOUT_SEC", "1800"))
    _run_bash(cmd, timeout_sec=timeout_sec)

    assert out_path.is_file(), f"本地脚本未生成输出：{out_path}"
    assert out_path.stat().st_size > 1024, f"输出文件过小：{out_path}（{out_path.stat().st_size} bytes）"

