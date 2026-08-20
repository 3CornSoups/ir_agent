"""runlog 运行级模型调用日志的单元测试（不调用任何模型）。"""

from __future__ import annotations

from pathlib import Path

from src.runlog import (
    LOG_ROOT,
    activate,
    active_dir,
    deactivate,
    log_http_call,
    log_model_call,
    write_meta,
)


def test_log_model_call_writes_pair(tmp_path: Path) -> None:
    """激活日志目录后，一次模型调用应成对写出 request/response 文件。"""
    activate(tmp_path / "run_1")
    try:
        log_model_call(
            stage="expand",
            system="SYS",
            user="USER 内容",
            response="RESP 内容",
        )
        files = sorted(p.name for p in (tmp_path / "run_1").iterdir())
        assert files == ["01_expand_request.txt", "01_expand_response.txt"]
        req = (tmp_path / "run_1" / "01_expand_request.txt").read_text(encoding="utf-8")
        resp = (tmp_path / "run_1" / "01_expand_response.txt").read_text(encoding="utf-8")
        assert "SYS" in req
        assert "USER 内容" in req
        assert "RESP 内容" in resp
        assert "status=ok" in resp
    finally:
        deactivate()


def test_log_model_call_stage_seq_increases(tmp_path: Path) -> None:
    """同一 stage 多次调用应按 01/02/… 递增编号。"""
    activate(tmp_path / "run_seq")
    try:
        log_model_call(stage="perceive", system="S", user="u1", response="r1")
        log_model_call(stage="perceive", system="S", user="u2", response="r2")
        log_model_call(stage="expand", system="S", user="u3", response="r3")
        names = sorted(p.name for p in (tmp_path / "run_seq").iterdir())
        assert names == [
            "01_expand_request.txt",
            "01_expand_response.txt",
            "01_perceive_request.txt",
            "01_perceive_response.txt",
            "02_perceive_request.txt",
            "02_perceive_response.txt",
        ]
    finally:
        deactivate()


def test_deactivate_stops_logging(tmp_path: Path) -> None:
    """deactivate 之后不应再写日志文件，active_dir 返回 None。"""
    activate(tmp_path / "run_off")
    deactivate()
    assert active_dir() is None
    log_model_call(stage="expand", system="S", user="u", response="r")
    files = list((tmp_path / "run_off").iterdir()) if (tmp_path / "run_off").exists() else []
    assert files == []


def test_log_model_call_error_status(tmp_path: Path) -> None:
    """ok=False 时 response 文件应标记 status=error。"""
    activate(tmp_path / "run_err")
    try:
        log_model_call(stage="format", system="S", user="u", response="boom", ok=False)
        resp = (tmp_path / "run_err" / "01_format_response.txt").read_text(encoding="utf-8")
        assert "status=error" in resp
    finally:
        deactivate()


def test_user_data_uri_truncated(tmp_path: Path) -> None:
    """多模态 user 里的 data URI 超长时应截断，避免日志膨胀。"""
    activate(tmp_path / "run_uri")
    try:
        big = "data:image/png;base64," + "A" * 2000
        log_model_call(
            stage="perceive",
            system="S",
            user=[{"type": "image_url", "image_url": {"url": big}}],
            response="ok",
        )
        req = (tmp_path / "run_uri" / "01_perceive_request.txt").read_text(encoding="utf-8")
        assert "已截断" in req
        assert "A" * 2000 not in req
    finally:
        deactivate()


def test_log_http_call_and_write_meta(tmp_path: Path) -> None:
    """H3 类 HTTP 调用应成对落盘；write_meta 写 meta.json。"""
    activate(tmp_path / "run_h3")
    try:
        log_http_call(name="h3_create", request={"model": "MiniMax-H3", "content": ["text"]}, response="{}")
        assert (tmp_path / "run_h3" / "01_h3_create_request.txt").is_file()
        assert (tmp_path / "run_h3" / "01_h3_create_response.txt").is_file()
        write_meta({"run_id": "run_x", "mode": "t2va"})
        meta = (tmp_path / "run_h3" / "meta.json").read_text(encoding="utf-8")
        assert '"run_id": "run_x"' in meta
    finally:
        deactivate()


def test_log_root_defaults_to_project_log() -> None:
    """LOG_ROOT 应指向仓库根目录的 log/。"""
    assert LOG_ROOT.name == "log"
