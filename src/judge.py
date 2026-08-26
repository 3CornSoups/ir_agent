"""本地独立裁判：把短意图 + Gemini 库存 + 最终提示词打包，按 18 维打分。"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import judge_settings, load_prompt
from .eval_dimensions import DIMENSIONS, dimension_catalog_text, empty_score_skeleton

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


def build_judge_system() -> str:
    """组装裁判 SYSTEM：模板 + 维度目录。"""
    template = load_prompt("judge_dimensions")
    catalog = dimension_catalog_text()
    if "DIMENSION_CATALOG_PLACEHOLDER" in template:
        return template.replace("DIMENSION_CATALOG_PLACEHOLDER", catalog)
    return template.rstrip() + "\n\n" + catalog + "\n"


def build_judge_user(package: dict[str, Any]) -> str:
    """把评估包拼成 USER 文本。"""
    intent = str(package.get("intent") or "").strip()
    inventory = str(package.get("inventory") or "").strip()
    prompt = str(package.get("prompt") or "").strip()
    mode = str(package.get("mode") or "").strip()
    duration = package.get("duration")
    routing = {
        "style_skills": package.get("style_skills") or [],
        "style_skill_source": package.get("style_skill_source"),
        "style_skill_scores": package.get("style_skill_scores") or {},
        "style_skill_threshold": package.get("style_skill_threshold"),
        "mechanisms": package.get("mechanisms") or [],
        "mechanism_source": package.get("mechanism_source"),
    }
    lines = [
        f"MODE={mode or 'unknown'}",
        f"DURATION_HINT={duration if duration is not None else 'unknown'}",
        "",
        "=== USER_SHORT_INTENT ===",
        intent or "(empty)",
        "",
        "=== GEMINI_MULTIMODAL_INVENTORY ===",
        inventory or "(none — text-only / t2va)",
        "",
        "=== FINAL_OPTIMIZED_PROMPT ===",
        prompt or "(empty)",
        "",
        "=== ROUTING_META ===",
        json.dumps(routing, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型回复中抽出 JSON 对象。"""
    raw = (text or "").strip()
    if not raw:
        return None
    fence = _JSON_FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(raw[start : end + 1])
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None


def _coerce_score(value: Any) -> int | None:
    """把分值规范成 1–5 整数，或 None（N/A）。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "n/a", "na", "null", "none", "不适用"}:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:  # NaN
        return None
    rounded = int(round(score))
    return max(1, min(5, rounded))


def parse_judge_response(text: str) -> dict[str, Any]:
    """解析裁判 JSON，补齐缺失维度并重算 overall。"""
    payload = _extract_json_object(text) or {}
    raw_scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    scores = empty_score_skeleton()
    for dim in DIMENSIONS:
        key = dim["id"]
        if key in raw_scores:
            scores[key] = _coerce_score(raw_scores[key])
        else:
            # 兼容中文名 / 序号键
            for alt in (dim["name"], key.split("_", 1)[-1]):
                if alt in raw_scores:
                    scores[key] = _coerce_score(raw_scores[alt])
                    break

    numeric = [v for v in scores.values() if isinstance(v, int)]
    overall = round(sum(numeric) / len(numeric), 1) if numeric else None
    if payload.get("overall") is not None:
        try:
            overall = round(float(payload["overall"]), 1)
        except (TypeError, ValueError):
            pass

    strengths = payload.get("strengths") if isinstance(payload.get("strengths"), list) else []
    weaknesses = payload.get("weaknesses") if isinstance(payload.get("weaknesses"), list) else []
    issue_tags = payload.get("issue_tags") if isinstance(payload.get("issue_tags"), list) else []
    summary = str(payload.get("summary") or "").strip()

    return {
        "scores": scores,
        "overall": overall,
        "strengths": [str(x).strip() for x in strengths if str(x).strip()],
        "weaknesses": [str(x).strip() for x in weaknesses if str(x).strip()],
        "issue_tags": [str(x).strip() for x in issue_tags if str(x).strip()],
        "summary": summary,
        "raw_response": text,
    }


def chat_judge(system: str, user: str) -> str:
    """一次裁判推理：默认 Gemini API；backend=openai 时走本地兼容接口。"""
    cfg = judge_settings()
    if cfg.get("backend") == "gemini":
        from .gemini import chat as gemini_chat

        return gemini_chat(system, user, stage="judge")

    url = f"{cfg['base_url']}/v1/chat/completions"
    body: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": cfg["temperature"],
        "max_tokens": cfg["max_tokens"],
        "stream": False,
    }
    kwargs = cfg.get("chat_template_kwargs")
    if isinstance(kwargs, dict) and kwargs:
        body["chat_template_kwargs"] = kwargs
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    retries = int(cfg["max_retries"])
    timeout = float(cfg["timeout_sec"])
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError(f"裁判模型空回复: {data!r}")
            return content.strip()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt + 1 < retries:
                time.sleep(1.5**attempt)
    raise RuntimeError(f"裁判请求失败（{retries} 次）: {last_err}") from last_err


def package_from_run_record(record: dict[str, Any]) -> dict[str, Any]:
    """从 enhance 的 run.json 记录构造评估包。"""
    return {
        "mode": record.get("mode"),
        "intent": record.get("intent") or "",
        "duration": record.get("duration"),
        "inventory": record.get("inventory") or "",
        "prompt": record.get("prompt") or "",
        "style_skills": record.get("style_skills") or [],
        "style_skill_source": record.get("style_skill_source"),
        "style_skill_scores": record.get("style_skill_scores") or {},
        "style_skill_threshold": record.get("style_skill_threshold"),
        "mechanisms": record.get("mechanisms") or [],
        "mechanism_source": record.get("mechanism_source"),
    }


def load_run_dir(run_dir: Path) -> dict[str, Any]:
    """读取 runs 目录：优先 run.json，缺字段时用旁路 txt 补齐。"""
    run_dir = Path(run_dir)
    record: dict[str, Any] = {}
    run_json = run_dir / "run.json"
    if run_json.is_file():
        data = json.loads(run_json.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            record = data
    if not record.get("prompt"):
        prompt_path = run_dir / "prompt.txt"
        if prompt_path.is_file():
            record["prompt"] = prompt_path.read_text(encoding="utf-8")
    if not record.get("inventory"):
        inv_path = run_dir / "inventory.txt"
        if inv_path.is_file():
            record["inventory"] = inv_path.read_text(encoding="utf-8")
    if not record.get("intent"):
        intent_path = run_dir / "intent.txt"
        if intent_path.is_file():
            record["intent"] = intent_path.read_text(encoding="utf-8").strip()
    if not record.get("prompt"):
        raise FileNotFoundError(f"缺少最终提示词: {run_dir}/prompt.txt 或 run.json")
    if not record.get("intent"):
        raise ValueError(f"缺少短意图: 请在 run.json 写入 intent，或提供 {run_dir}/intent.txt")
    return record


def evaluate_package(package: dict[str, Any], *, chat_fn=None) -> dict[str, Any]:
    """对单个评估包调用裁判并返回结构化结果。"""
    system = build_judge_system()
    user = build_judge_user(package)
    caller = chat_fn or chat_judge
    raw = caller(system, user)
    parsed = parse_judge_response(raw)
    cfg = judge_settings()
    return {
        "package": {
            "mode": package.get("mode"),
            "intent": package.get("intent"),
            "duration": package.get("duration"),
            "has_inventory": bool(str(package.get("inventory") or "").strip()),
            "prompt_chars": len(str(package.get("prompt") or "")),
            "style_skills": package.get("style_skills") or [],
            "style_skill_scores": package.get("style_skill_scores") or {},
        },
        "judge": {
            "backend": cfg.get("backend"),
            "model": cfg["model"] if cfg.get("backend") != "gemini" else "gemini(configs/gemini.yaml)",
            "base_url": cfg["base_url"] if cfg.get("backend") != "gemini" else "gemini-api",
            "scored_at": datetime.now(timezone.utc).isoformat(),
        },
        "scores": parsed["scores"],
        "overall": parsed["overall"],
        "strengths": parsed["strengths"],
        "weaknesses": parsed["weaknesses"],
        "issue_tags": parsed["issue_tags"],
        "summary": parsed["summary"],
        "raw_response": parsed["raw_response"],
    }


def render_eval_markdown(result: dict[str, Any]) -> str:
    """把评估结果渲染成可读 Markdown。"""
    name_by_id = {d["id"]: d["name"] for d in DIMENSIONS}
    lines = [
        "# 十八维提示词评估",
        "",
        f"- overall: **{result.get('overall')}**",
        f"- model: `{((result.get('judge') or {}).get('model'))}`",
        f"- mode: `{(result.get('package') or {}).get('mode')}`",
        f"- scored_at: `{(result.get('judge') or {}).get('scored_at')}`",
        "",
        "## 分数",
        "",
        "| 维度 | 分 |",
        "| --- | --- |",
    ]
    scores = result.get("scores") or {}
    for dim in DIMENSIONS:
        val = scores.get(dim["id"])
        cell = "N/A" if val is None else str(val)
        lines.append(f"| {name_by_id[dim['id']]} (`{dim['id']}`) | {cell} |")
    lines.extend(["", "## 摘要", "", str(result.get("summary") or "(无)"), ""])
    if result.get("strengths"):
        lines.append("## 优点")
        lines.append("")
        for item in result["strengths"]:
            lines.append(f"- {item}")
        lines.append("")
    if result.get("weaknesses"):
        lines.append("## 不足")
        lines.append("")
        for item in result["weaknesses"]:
            lines.append(f"- {item}")
        lines.append("")
    if result.get("issue_tags"):
        lines.append("## 问题标签")
        lines.append("")
        lines.append(", ".join(f"`{t}`" for t in result["issue_tags"]))
        lines.append("")
    return "\n".join(lines)


def write_eval_artifacts(out_dir: Path, result: dict[str, Any]) -> None:
    """写入 eval.json / eval.md；raw 单独落盘便于排查。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in result.items() if k != "raw_response"}
    (out_dir / "eval.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "eval.md").write_text(render_eval_markdown(result), encoding="utf-8")
    if result.get("raw_response"):
        (out_dir / "eval_raw.txt").write_text(
            str(result["raw_response"]).rstrip() + "\n",
            encoding="utf-8",
        )


def evaluate_run_dir(run_dir: Path, *, chat_fn=None, write: bool = True) -> dict[str, Any]:
    """评估单个 run 目录并可选写回产物。"""
    record = load_run_dir(run_dir)
    package = package_from_run_record(record)
    result = evaluate_package(package, chat_fn=chat_fn)
    if write:
        write_eval_artifacts(Path(run_dir), result)
    return result


def aggregate_eval_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """批量汇总：各维均分、总体均分、常见 issue_tags。"""
    per_dim: dict[str, list[int]] = {d["id"]: [] for d in DIMENSIONS}
    overalls: list[float] = []
    tag_counts: dict[str, int] = {}
    for res in results:
        scores = res.get("scores") or {}
        for dim_id, val in scores.items():
            if isinstance(val, int) and dim_id in per_dim:
                per_dim[dim_id].append(val)
        if isinstance(res.get("overall"), (int, float)):
            overalls.append(float(res["overall"]))
        for tag in res.get("issue_tags") or []:
            key = str(tag).strip()
            if key:
                tag_counts[key] = tag_counts.get(key, 0) + 1
    dim_means = {
        dim_id: (round(sum(vals) / len(vals), 2) if vals else None)
        for dim_id, vals in per_dim.items()
    }
    return {
        "n_cases": len(results),
        "overall_mean": round(sum(overalls) / len(overalls), 2) if overalls else None,
        "dimension_means": dim_means,
        "issue_tag_counts": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
