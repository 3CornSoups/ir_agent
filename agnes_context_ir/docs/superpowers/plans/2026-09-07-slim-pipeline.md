# Slim Pipeline Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Replace heavy multi-call ContextIR internals with latest_agent-style 2/3-round pipeline while keeping the public `enhance` API.

**Architecture:** Keep `pipeline.enhance` signature; swap internals to R1→R2(/R3); disable skills/routers; t2va uses text model; disable thinking; strengthen dialogue/camera prompt rules.

**Tech Stack:** Python, requests, existing AGNES_* env config, MiniMax H3 guides.

## Global Constraints

- Do not change `enhance` / `run_job` call signatures.
- Environment-only config (no YAML required for production merge).
- Chinese function-level comments on new/changed functions.
- Prefer minimal diff outside the slim path.

---

### Task 1: Prompts + core rules

- [ ] Ensure `prompts/r1_*.txt`, `r2_*.txt`, `r3_*.txt`, `_core.txt` present
- [ ] Patch `_core.txt` / R1 prompts for dialogue supplement + camera defaults

### Task 2: config + gemini

- [ ] Add `AGNES_GEMINI_TEXT_MODEL`
- [ ] Disable thinking in native/openai request bodies
- [ ] Allow `chat(..., model=...)` for text model override

### Task 3: prompts.py + postprocess.py + pipeline rewrite

- [ ] Port USER builders from latest_agent
- [ ] Port `clean_final_prompt`
- [ ] Rewrite `enhance` / keep `run_job` wrapper compatible
- [ ] Compatible return dict

### Task 4: Tests + README

- [ ] Update `test_engineering_fixes` for 2-round t2va
- [ ] Update README
- [ ] Run unittest
