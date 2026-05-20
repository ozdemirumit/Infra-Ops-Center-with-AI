"""
Workflow engine — interprets a parsed workflow dict, drives steps, persists state.

Design goals:
  - MCP-agnostic: tool steps look up the tool by name in the live registry,
    so new MCPs work without engine changes.
  - Restart-safe: state is persisted after every step.
  - Approval-aware: wait_approval steps pause the run; UI resumes it.
  - Safe templating: only {{ path.to.value }} placeholders, no Python eval.
"""

import time
import uuid
from datetime import datetime
from typing import Optional, Callable

from logging_config.logger import get_logger

from core.workflow.template import render, evaluate_when
from core.workflow.storage import save_run, get_run

logger = get_logger("workflow.engine")


STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_WAITING_APPROVAL = "waiting_approval"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


# Hooks that callers can override (e.g., the UI passes a Slack/syslog sender)
DEFAULT_NOTIFIER: Optional[Callable[[str, str, dict], None]] = None


def _now() -> str:
    return datetime.now().isoformat()


class WorkflowEngine:
    """
    Stateless interpreter — all run state lives in JSON.

    Usage:
        engine = WorkflowEngine()
        run_id = engine.start(workflow_dict, inputs={"server_name": "srv01"})
        # ... if status becomes waiting_approval ...
        engine.resume(run_id, approval=True)
    """

    def __init__(self, notifier: Optional[Callable] = None):
        self.notifier = notifier or DEFAULT_NOTIFIER

    # ── Public API ────────────────────────────────────────────────

    def start(self, workflow: dict, inputs: Optional[dict] = None,
              connections: Optional[dict] = None,
              session_id: Optional[str] = None,
              triggered_by: str = "manual",
              dry_run: bool = False) -> str:
        """
        Begin a new run. Returns run_id.

        If `dry_run` is True, every side-effecting step (tool, agent,
        metric_check, notify, close_incident, sleep) is mocked — the
        workflow's control flow runs end-to-end without touching MCPs,
        the LLM, the monitor, or any external system. Use it to verify
        a workflow's structure before letting it loose for real.
        """
        run_id = uuid.uuid4().hex[:12]
        run = {
            "id": run_id,
            "workflow_name": workflow.get("name", "unnamed"),
            "workflow_file": workflow.get("_source_file", ""),
            "workflow_steps": workflow.get("steps", []),
            "status": STATUS_PENDING,
            "triggered_by": triggered_by,
            "session_id": session_id,
            "connections": connections or {},
            "dry_run": bool(dry_run),
            "context": {
                "inputs": {**(workflow.get("inputs") or {}), **(inputs or {})},
                "steps": {},   # step_id -> result dict
            },
            "history": [],
            "current_index": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "error": None,
        }
        save_run(run)
        self._continue(run)
        return run_id

    def resume(self, run_id: str, approval: bool = True,
               approval_note: str = "") -> Optional[dict]:
        """Resume a run that was paused at a wait_approval step."""
        run = get_run(run_id)
        if not run:
            logger.warning(f"Resume failed — run not found: {run_id}")
            return None
        if run["status"] != STATUS_WAITING_APPROVAL:
            logger.warning(f"Resume skipped — run {run_id} status is {run['status']}")
            return run

        # Record approval result on the current step
        idx = run["current_index"]
        steps = run["workflow_steps"]
        if idx < len(steps):
            step = steps[idx]
            sid = step.get("id", f"step_{idx}")
            result = {
                "approved": bool(approval),
                "note": approval_note,
                "decided_at": _now(),
            }
            run["context"]["steps"][sid] = result
            run["history"].append({
                "step": sid, "type": "wait_approval",
                "status": "approved" if approval else "rejected",
                "result": result, "at": _now(),
            })
            run["current_index"] = idx + 1

        if approval:
            run["status"] = STATUS_RUNNING
            save_run(run)
            self._continue(run)
        else:
            run["status"] = STATUS_CANCELLED
            run["updated_at"] = _now()
            save_run(run)
            logger.info(f"Run {run_id} cancelled at approval step")
        return get_run(run_id)

    def cancel(self, run_id: str) -> Optional[dict]:
        run = get_run(run_id)
        if not run:
            return None
        if run["status"] in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
            return run
        run["status"] = STATUS_CANCELLED
        run["updated_at"] = _now()
        save_run(run)
        return run

    # ── Core driver ───────────────────────────────────────────────

    def _continue(self, run: dict) -> None:
        """Execute steps from current_index until completion or pause."""
        run["status"] = STATUS_RUNNING
        run["updated_at"] = _now()
        save_run(run)

        try:
            # NB: re-read workflow_steps every loop — branch steps splice new
            # entries into the list, replacing the reference.
            while run["current_index"] < len(run["workflow_steps"]):
                step = run["workflow_steps"][run["current_index"]]
                # Re-read fresh run state to catch external cancel
                latest = get_run(run["id"])
                if latest and latest.get("status") == STATUS_CANCELLED:
                    logger.info(f"Run {run['id']} cancelled externally")
                    return

                paused = self._exec_step(step, run)
                if paused:
                    # Step asked us to stop (e.g. wait_approval).
                    # Status was set by the handler; persist before returning.
                    run["updated_at"] = _now()
                    save_run(run)
                    return

                run["current_index"] += 1
                run["updated_at"] = _now()
                save_run(run)

            run["status"] = STATUS_COMPLETED
            run["updated_at"] = _now()
            save_run(run)
            self._on_complete(run)

        except Exception as e:
            logger.error(f"Run {run['id']} failed: {e}", exc_info=True)
            run["status"] = STATUS_FAILED
            run["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            run["updated_at"] = _now()
            run["history"].append({
                "step": "_engine", "type": "error",
                "status": "failed", "result": {"error": run["error"]},
                "at": _now(),
            })
            save_run(run)

    # ── Step dispatcher ───────────────────────────────────────────

    def _exec_step(self, step: dict, run: dict) -> bool:
        """Run a single step. Returns True if the engine should pause."""
        stype = step.get("type")
        sid = step.get("id", f"step_{run['current_index']}")
        ctx = run["context"]
        rendered = render(step, ctx)
        started = time.time()

        logger.info(f"[wf {run['id']}] step '{sid}' ({stype})")

        handlers = {
            "agent": self._step_agent,
            "tool": self._step_tool,
            "metric_check": self._step_metric_check,
            "wait_approval": self._step_wait_approval,
            "branch": self._step_branch,
            "notify": self._step_notify,
            "sleep": self._step_sleep,
            "set": self._step_set,
            "close_incident": self._step_close_incident,
        }
        handler = handlers.get(stype)
        if not handler:
            raise ValueError(f"unknown step type: {stype}")

        # Conditional skip — only run if `when` is truthy
        cond = step.get("when")
        if cond is not None and stype != "branch":
            if not evaluate_when(str(cond), ctx):
                self._record(run, sid, stype, "skipped", {"reason": f"when={cond}"}, started)
                return False

        try:
            paused, result = handler(rendered, run)
        except Exception as e:
            # Allow on_error: continue|fail
            on_error = step.get("on_error", "fail")
            err_msg = f"{type(e).__name__}: {e}"
            self._record(run, sid, stype, "error", {"error": err_msg}, started)
            if on_error == "continue":
                logger.warning(f"[wf {run['id']}] step '{sid}' errored but continuing: {err_msg}")
                return False
            raise

        # Store under the step id; also under store_as if provided
        store_as = step.get("store_as", sid)
        ctx["steps"][sid] = result
        if store_as != sid:
            ctx["steps"][store_as] = result

        # Backward-compat: also expose `result` directly under the id
        # (e.g. {{ steps.investigate.summary }} or {{ investigate.summary }})
        ctx[sid] = result
        if store_as != sid:
            ctx[store_as] = result

        self._record(run, sid, stype, "completed" if not paused else "waiting", result, started)
        return paused

    def _record(self, run: dict, step_id: str, stype: str, status: str,
                result: dict, started: float) -> None:
        run["history"].append({
            "step": step_id, "type": stype, "status": status,
            "result": _truncate_result(result),
            "duration_ms": int((time.time() - started) * 1000),
            "at": _now(),
        })

    # ── Step handlers ─────────────────────────────────────────────

    def _step_tool(self, step: dict, run: dict) -> tuple[bool, dict]:
        """Call any registered MCP tool by name. MCP-agnostic."""
        tool_name = step.get("tool", "")
        tool_input = step.get("input", {}) or {}
        if not isinstance(tool_input, dict):
            raise ValueError("tool step: 'input' must be a mapping")

        # MCP-agnostic: live registry membership is a warning, not a block
        in_registry = True
        try:
            from tools.registry import get_active_tools
            known = {t.get("name") for t in get_active_tools()}
            in_registry = tool_name in known
            if not in_registry:
                logger.warning(
                    f"tool '{tool_name}' is not in the live registry"
                )
        except Exception:
            pass

        if run.get("dry_run"):
            cmd = tool_input.get("command") or tool_input.get("action") or ""
            target = tool_input.get("target_host") or tool_input.get("host") or ""
            mock = (
                f"[DRY-RUN] would call MCP tool '{tool_name}'"
                + (f" on {target}" if target else "")
                + (f": {str(cmd)[:300]}" if cmd else "")
            )
            return False, {
                "tool": tool_name, "input": tool_input,
                "output": mock, "ok": True,
                "dry_run": True, "in_registry": in_registry,
            }

        from core.agent_loop import _dispatch_tool
        raw = _dispatch_tool(tool_name, tool_input, run.get("connections") or {})
        return False, {
            "tool": tool_name,
            "input": tool_input,
            "output": _truncate_str(raw, 4000),
            "ok": not str(raw).lower().startswith("❌"),
        }

    def _step_agent(self, step: dict, run: dict) -> tuple[bool, dict]:
        """Run a free-form agent turn. The agent picks tools dynamically."""
        prompt = step.get("prompt", "")
        max_steps = int(step.get("max_steps", 8))

        if run.get("dry_run"):
            return False, {
                "summary": f"[DRY-RUN] agent would run prompt: {prompt[:200]}",
                "tools_used": [],
                "turns": 0,
                "dry_run": True,
            }

        from proxy.ai_proxy import AIProxy
        from core.agent_loop import _dispatch_tool, _is_change_command
        from tools.registry import get_active_tools
        from config.settings import settings
        proxy = AIProxy()

        messages = [{"role": "user", "content": prompt}]
        system_prompt = settings.SYSTEM_PROMPT or ""
        tools_used = []
        final_text = ""

        for _ in range(max_steps):
            response = proxy.chat(
                messages=messages, tools=get_active_tools(), system=system_prompt,
            )
            assistant_msg = {"role": "assistant", "content": []}
            tool_uses = []
            for block in response.content:
                if block.type == "text":
                    final_text = block.text
                    assistant_msg["content"].append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_msg["content"].append({
                        "type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input,
                    })
                    tool_uses.append(block)
            messages.append(assistant_msg)

            if not tool_uses:
                break

            tool_results = []
            for tu in tool_uses:
                cmd = tu.input.get("command", tu.input.get("action", ""))
                if _is_change_command(cmd) and not step.get("allow_changes", False):
                    out = "⚠️ change command skipped (allow_changes=false on this step)"
                else:
                    try:
                        out = _dispatch_tool(tu.name, tu.input, run.get("connections") or {})
                    except Exception as e:
                        out = f"❌ {type(e).__name__}: {e}"
                tools_used.append({"name": tu.name, "input": tu.input,
                                   "output": _truncate_str(out, 800)})
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tu.id, "content": out,
                })
            messages.append({"role": "user", "content": tool_results})

        return False, {
            "summary": _truncate_str(final_text, 4000),
            "tools_used": tools_used,
            "turns": len(tools_used),
        }

    def _step_metric_check(self, step: dict, run: dict) -> tuple[bool, dict]:
        from core.monitor import run_check_now, get_checks_config, _compare, load_state

        metric = step.get("metric", "")
        cfg = get_checks_config().get(metric, {})

        if run.get("dry_run"):
            # Use the last cached results — no fresh check, no SSH/HTTP/etc.
            state = load_state()
            results = state.results
        else:
            # Trigger a fresh check
            results = run_check_now(metric)

        expect = step.get("expect", {}) or {}
        compare_op = expect.get("compare", cfg.get("compare", "gt"))
        threshold = expect.get("value", cfg.get("threshold", 0))

        # Pick rows for this metric only
        latest = [r for r in results if r.get("check_name") == metric]

        any_failed = False
        details = []
        for r in latest:
            v = r.get("value")
            ok = _compare(v, threshold, compare_op)
            details.append({
                "server": r.get("server_name", ""), "value": v,
                "status": r.get("status"), "expectation_met": ok,
            })
            if not ok:
                any_failed = True

        out = {
            "metric": metric, "expectation_met": (not any_failed) if details else False,
            "failed": any_failed, "results": details,
        }
        if run.get("dry_run"):
            out["dry_run"] = True
            out["note"] = "used cached results; no fresh check ran"
        return False, out

    def _step_wait_approval(self, step: dict, run: dict) -> tuple[bool, dict]:
        """Pause the run. UI shows an approval card and calls resume().

        In dry-run mode this is auto-approved with a marker, so the
        rest of the workflow can be inspected without operator action.
        """
        if run.get("dry_run"):
            return False, {
                "approved": True,
                "note": "[DRY-RUN] auto-approved for simulation",
                "prompt": step.get("prompt", "Approval required"),
                "risk": step.get("risk", "medium"),
                "dry_run": True,
            }
        run["status"] = STATUS_WAITING_APPROVAL
        return True, {
            "prompt": step.get("prompt", "Approval required"),
            "risk": step.get("risk", "medium"),
            "waiting_since": _now(),
        }

    def _step_branch(self, step: dict, run: dict) -> tuple[bool, dict]:
        """Conditional execution. Inlines `then` or `else` steps."""
        cond = step.get("when", "")
        truthy = evaluate_when(str(cond), run["context"])
        chosen = step.get("then" if truthy else "else", []) or []
        # Insert chosen steps right after this branch
        if isinstance(chosen, list) and chosen:
            idx = run["current_index"]
            run["workflow_steps"] = (
                run["workflow_steps"][: idx + 1] + chosen +
                run["workflow_steps"][idx + 1 :]
            )
        return False, {"condition": cond, "taken": "then" if truthy else "else",
                       "added_steps": len(chosen)}

    def _step_notify(self, step: dict, run: dict) -> tuple[bool, dict]:
        channel = step.get("channel", "log")
        message = step.get("message", "")
        level = step.get("level", "info")
        meta = {"workflow": run["workflow_name"], "run_id": run["id"]}

        if run.get("dry_run"):
            extra = ""
            if channel == "email":
                extra = f" to={step.get('to', '(default)')!r} subject={step.get('subject', '')!r}"
            logger.info(f"[wf-notify DRY-RUN] would send via {channel}{extra}: {message[:200]}")
            return False, {"channel": channel, "message": message,
                           "sent": False, "dry_run": True}

        if channel == "log":
            logger.log(_log_level(level), f"[wf-notify] {message}")
            return False, {"channel": channel, "message": message, "sent": True}

        if channel == "syslog":
            try:
                from logging_config.logger import get_logger as _gl
                _gl("workflow.audit").info(f"NOTIFY {message} {meta}")
            except Exception:
                logger.info(f"[wf-notify-fallback] {message}")
            return False, {"channel": channel, "message": message, "sent": True}

        if channel == "webhook":
            url = step.get("url", "")
            if not url:
                return False, {"channel": channel, "sent": False,
                               "error": "webhook step requires 'url'"}
            try:
                import httpx
                httpx.post(url, json={"message": message, **meta}, timeout=5.0)
                return False, {"channel": channel, "message": message,
                               "sent": True, "url": url}
            except Exception as e:
                logger.warning(f"webhook notify failed: {e}")
                return False, {"channel": channel, "sent": False, "error": str(e)}

        if channel == "email":
            from core.notifier import send_email
            subject = step.get("subject") or (
                f"[{run['workflow_name']}] notification"
            )
            to = step.get("to")  # list or comma-string or None (falls back to default)
            html = bool(step.get("html", False))
            # Auto-prepend a small header so recipients see context
            body = (
                f"{message}\n\n"
                f"--\nWorkflow: {run['workflow_name']}\n"
                f"Run: {run['id']}\n"
                f"Triggered by: {run.get('triggered_by', 'manual')}\n"
            ) if not html else message
            result = send_email(subject=subject, body=body, to=to, html=html)
            return False, {
                "channel": channel,
                "subject": subject,
                "recipients": result["recipients"],
                "sent": result["sent"],
                "error": result["error"],
            }

        if self.notifier:
            try:
                self.notifier(channel, message, meta)
                return False, {"channel": channel, "message": message,
                               "sent": True, "via": "custom"}
            except Exception as e:
                logger.warning(f"custom notifier failed: {e}")
                return False, {"channel": channel, "sent": False, "error": str(e)}

        return False, {"channel": channel, "message": message,
                       "sent": False, "error": f"unknown channel '{channel}'"}

    def _step_sleep(self, step: dict, run: dict) -> tuple[bool, dict]:
        seconds = float(step.get("seconds", 1))
        # Cap to 5 minutes — long sleeps belong in scheduler, not workflow
        seconds = max(0.0, min(seconds, 300.0))
        if run.get("dry_run"):
            return False, {"slept": 0, "would_sleep": seconds, "dry_run": True}
        time.sleep(seconds)
        return False, {"slept": seconds}

    def _step_set(self, step: dict, run: dict) -> tuple[bool, dict]:
        """Inject variables into the run context."""
        values = step.get("values", {}) or {}
        for k, v in values.items():
            run["context"][k] = v
        return False, {"values": values}

    def _step_close_incident(self, step: dict, run: dict) -> tuple[bool, dict]:
        """Mark the associated session as completed, if any."""
        sid = run.get("session_id")
        if not sid:
            return False, {"closed": False, "reason": "no session linked"}
        if run.get("dry_run"):
            return False, {"closed": False, "would_close": sid, "dry_run": True}
        try:
            from sessions.storage import get_session, set_session_completed
            sess = get_session(sid)
            if sess:
                set_session_completed(sid, sess.get("messages", []))
                return False, {"closed": True, "session": sid}
        except Exception as e:
            return False, {"closed": False, "error": str(e)}
        return False, {"closed": False}

    # ── Completion hook ───────────────────────────────────────────

    def _on_complete(self, run: dict) -> None:
        """Persist a runbook when the run touched real tools (skips dry-runs)."""
        if run.get("dry_run"):
            return
        try:
            tool_calls = sum(
                1 for h in run.get("history", [])
                if h.get("type") in ("tool", "agent") and h.get("status") == "completed"
            )
            if tool_calls == 0:
                return

            from pathlib import Path
            from config.settings import settings
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = "".join(c for c in run["workflow_name"] if c.isalnum() or c in "_-")[:40]
            rb_dir = Path(settings.KNOWLEDGE_BASE_DIR) / "runbooks"
            rb_dir.mkdir(parents=True, exist_ok=True)
            path = rb_dir / f"workflow_{safe}_{ts}.md"

            lines = [
                f"# Workflow runbook: {run['workflow_name']}",
                f"**Run ID:** {run['id']}",
                f"**Triggered by:** {run.get('triggered_by', 'manual')}",
                f"**Completed:** {run['updated_at']}",
                f"**Inputs:** `{run['context'].get('inputs', {})}`",
                "",
                "## Steps",
                "",
            ]
            for h in run["history"]:
                lines.append(f"### {h['step']} ({h['type']}) — {h['status']}")
                lines.append(f"_{h['at']} · {h.get('duration_ms', 0)}ms_")
                res = h.get("result", {})
                if isinstance(res, dict):
                    for k, v in res.items():
                        sv = str(v)
                        if len(sv) > 600:
                            sv = sv[:600] + "..."
                        lines.append(f"- **{k}:** {sv}")
                lines.append("")

            path.write_text("\n".join(lines), encoding="utf-8")
            # Index into RAG (best-effort)
            try:
                from core.rag_engine import RAGEngine
                RAGEngine().index_document(
                    file_path=str(path),
                    doc_id=f"workflow_run_{run['id']}",
                    source="workflow",
                )
            except Exception as e:
                logger.debug(f"RAG index skipped for runbook: {e}")
        except Exception as e:
            logger.warning(f"runbook save failed: {e}")


# ── Helpers ────────────────────────────────────────────────────────

def _truncate_str(s, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + "\n... (truncated)"


def _truncate_result(r):
    if isinstance(r, dict):
        return {k: _truncate_str(v, 2000) if isinstance(v, str) else v
                for k, v in r.items()}
    return _truncate_str(r, 2000) if isinstance(r, str) else r


def _log_level(name: str) -> int:
    import logging as _l
    return getattr(_l, name.upper(), _l.INFO)
