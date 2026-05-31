"""
Browser demo UI for robot agent entrypoints.

The server intentionally uses only the Python standard library so the demo can
run in the same minimal environment as the terminal TUI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


logger = logging.getLogger(__name__)


TaskPlanBuilder = Callable[[], list[str]]


@dataclass
class DemoUIState:
    agent: Any
    guidance: str
    task_plan_builder: TaskPlanBuilder
    loop: asyncio.AbstractEventLoop
    lock: threading.Lock = field(default_factory=threading.Lock)
    is_running: bool = False
    current_task: str = ""
    last_result: str = ""
    last_error: str = ""
    events: list[dict[str, str]] = field(default_factory=list)

    def add_event(self, level: str, message: str) -> None:
        with self.lock:
            self.events.append(
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "level": level,
                    "message": message,
                }
            )
            self.events = self.events[-80:]

    def start_task(self, task: str) -> bool:
        with self.lock:
            if self.is_running:
                return False
            self.is_running = True
            self.current_task = task
            self.last_result = ""
            self.last_error = ""
        self.add_event("info", f"Task submitted: {task}")
        asyncio.run_coroutine_threadsafe(self._run_task(task), self.loop)
        return True

    async def _run_task(self, task: str) -> None:
        try:
            self.agent.start_task(
                task_brief=task,
                action_guidance=self.guidance,
                task_plan=self.task_plan_builder(),
            )
            result = await self.agent.run_once(task)
            with self.lock:
                self.last_result = result
            self.add_event("success", result)
        except Exception as exc:
            logger.exception("[DemoUI] Agent task failed")
            with self.lock:
                self.last_error = str(exc)
            self.add_event("error", str(exc))
        finally:
            with self.lock:
                self.is_running = False

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            events = list(self.events)
            is_running = self.is_running
            current_task = self.current_task
            last_result = self.last_result
            last_error = self.last_error

        trajectory = self.agent.get_trajectory()
        return {
            "is_running": is_running,
            "current_task": current_task,
            "last_result": last_result,
            "last_error": last_error,
            "state": self.agent.state_machine.current_state.name,
            "progress": self.agent.memory_manager.get_current_task_progress(),
            "progress_summary": self.agent.memory_manager.get_progress_summary(),
            "trajectory_path": self.agent.last_trajectory_path,
            "tools": self.agent.tool_registry.list_tools(),
            "events": events,
            "trajectory_tail": trajectory[-12:],
        }


def build_handler(state: DemoUIState) -> type[BaseHTTPRequestHandler]:
    class DemoUIHandler(BaseHTTPRequestHandler):
        server_version = "RobotClawDemoUI/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("[DemoUI] " + format, *args)

        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                self._send_html(DEMO_HTML)
                return
            if self.path == "/api/status":
                self._send_json(state.snapshot())
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path != "/api/task":
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                task = str(payload.get("task", "")).strip()
            except Exception:
                self._send_json({"ok": False, "error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
                return

            if not task:
                self._send_json({"ok": False, "error": "Task is required"}, HTTPStatus.BAD_REQUEST)
                return
            if not state.start_task(task):
                self._send_json({"ok": False, "error": "Agent is already running"}, HTTPStatus.CONFLICT)
                return

            self._send_json({"ok": True})

        def _send_html(self, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return DemoUIHandler


async def run_demo_ui(
    args: argparse.Namespace,
    *,
    agent: Any,
    guidance: str,
    task_plan_builder: TaskPlanBuilder,
) -> int:
    loop = asyncio.get_running_loop()
    state = DemoUIState(
        agent=agent,
        guidance=guidance,
        task_plan_builder=task_plan_builder,
        loop=loop,
    )
    server = ThreadingHTTPServer((args.host, args.port), build_handler(state))
    host, port = server.server_address
    url_host = "127.0.0.1" if host in {"", "0.0.0.0"} else host
    print(f"RobotClaw demo UI: http://{url_host}:{port}")
    print("Press Ctrl+C to stop the UI server.")

    server_task = asyncio.create_task(asyncio.to_thread(server.serve_forever, poll_interval=0.25))
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise
    finally:
        server.shutdown()
        await server_task
        server.server_close()
    return 0


DEMO_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RobotClaw Demo Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667085;
      --line: #d7dce5;
      --accent: #136f63;
      --accent-2: #2f80ed;
      --warn: #b54708;
      --error: #b42318;
      --success: #027a48;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background: var(--bg);
    }
    .shell {
      display: grid;
      grid-template-columns: minmax(340px, 0.9fr) minmax(460px, 1.1fr);
      min-height: 100vh;
    }
    .stage {
      position: relative;
      overflow: hidden;
      padding: 32px;
      background:
        linear-gradient(180deg, rgba(19, 111, 99, 0.08), rgba(47, 128, 237, 0.04)),
        #eef3f6;
      border-right: 1px solid var(--line);
    }
    .brand {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 24px;
    }
    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.1;
      letter-spacing: 0;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(255,255,255,0.78);
      font-size: 13px;
      color: var(--muted);
      white-space: nowrap;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 99px;
      background: var(--muted);
    }
    .dot.running { background: var(--accent-2); }
    .dot.ready { background: var(--success); }
    .visual {
      width: 100%;
      aspect-ratio: 1.08;
      min-height: 340px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fdfefe;
      box-shadow: 0 20px 60px rgba(33, 45, 58, 0.08);
    }
    .stage-meta {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 18px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.78);
      padding: 12px;
      min-height: 74px;
    }
    .metric-label {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .metric-value {
      font-size: 16px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .console {
      padding: 28px;
      display: flex;
      flex-direction: column;
      gap: 18px;
      min-width: 0;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 12px 40px rgba(33, 45, 58, 0.06);
    }
    .task-form {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: stretch;
    }
    label {
      display: block;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 8px;
    }
    textarea {
      width: 100%;
      min-height: 104px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      color: var(--ink);
      font: inherit;
      line-height: 1.45;
      outline: none;
    }
    textarea:focus {
      border-color: var(--accent-2);
      box-shadow: 0 0 0 3px rgba(47, 128, 237, 0.12);
    }
    button {
      align-self: end;
      height: 44px;
      border: 0;
      border-radius: 8px;
      padding: 0 18px;
      color: #fff;
      background: var(--accent);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button:disabled {
      background: #98a2b3;
      cursor: not-allowed;
    }
    .summary {
      display: grid;
      gap: 10px;
      font-size: 14px;
      color: var(--muted);
    }
    .summary strong {
      color: var(--ink);
    }
    .tabs {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      margin-bottom: 12px;
    }
    .tab {
      height: 34px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      border-radius: 6px;
      font-size: 13px;
      font-weight: 700;
    }
    .tab.active {
      color: var(--accent);
      border-color: rgba(19, 111, 99, 0.35);
      background: rgba(19, 111, 99, 0.08);
    }
    .log {
      height: 300px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
    }
    .entry {
      padding: 7px 8px;
      border-bottom: 1px solid #edf0f5;
      overflow-wrap: anywhere;
    }
    .entry:last-child { border-bottom: 0; }
    .entry .time { color: var(--muted); margin-right: 8px; }
    .entry.success { color: var(--success); }
    .entry.error { color: var(--error); }
    .tools {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .tool {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
      color: var(--muted);
      background: #fff;
      font-size: 12px;
    }
    @media (max-width: 960px) {
      .shell { grid-template-columns: 1fr; }
      .stage { border-right: 0; border-bottom: 1px solid var(--line); }
      .task-form { grid-template-columns: 1fr; }
      button { width: 100%; }
      .stage-meta { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="stage">
      <div class="brand">
        <h1>RobotClaw Demo Console</h1>
        <div class="status-pill"><span id="dot" class="dot"></span><span id="state">INIT</span></div>
      </div>
      <canvas id="robotCanvas" class="visual" width="900" height="840"></canvas>
      <div class="stage-meta">
        <div class="metric"><div class="metric-label">Progress</div><div class="metric-value" id="progress">0%</div></div>
        <div class="metric"><div class="metric-label">Current Task</div><div class="metric-value" id="task">Waiting</div></div>
        <div class="metric"><div class="metric-label">Trajectory</div><div class="metric-value" id="trajectory">None</div></div>
      </div>
    </section>
    <section class="console">
      <section class="panel">
        <label for="taskInput">Task instruction</label>
        <div class="task-form">
          <textarea id="taskInput" placeholder="例如：把tag 0放tag 1上，这是一个装配任务"></textarea>
          <button id="submitBtn">Run Task</button>
        </div>
      </section>
      <section class="panel summary">
        <div><strong>Result:</strong> <span id="result">No result yet.</span></div>
        <div><strong>Runtime:</strong> <span id="runtime">Waiting for task input.</span></div>
      </section>
      <section class="panel">
        <div class="tabs">
          <button class="tab active" data-tab="events">Events</button>
          <button class="tab" data-tab="trajectory">Trajectory</button>
          <button class="tab" data-tab="tools">Tools</button>
        </div>
        <div id="eventsView" class="log"></div>
        <div id="trajectoryView" class="log" hidden></div>
        <div id="toolsView" class="tools" hidden></div>
      </section>
    </section>
  </main>
  <script>
    const els = {
      dot: document.getElementById('dot'),
      state: document.getElementById('state'),
      progress: document.getElementById('progress'),
      task: document.getElementById('task'),
      trajectory: document.getElementById('trajectory'),
      result: document.getElementById('result'),
      runtime: document.getElementById('runtime'),
      eventsView: document.getElementById('eventsView'),
      trajectoryView: document.getElementById('trajectoryView'),
      toolsView: document.getElementById('toolsView'),
      taskInput: document.getElementById('taskInput'),
      submitBtn: document.getElementById('submitBtn'),
      canvas: document.getElementById('robotCanvas')
    };
    let latestStatus = null;

    async function submitTask() {
      const task = els.taskInput.value.trim();
      if (!task) return;
      els.submitBtn.disabled = true;
      els.result.textContent = 'Task submitted.';
      try {
        const res = await fetch('/api/task', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({task})
        });
        const data = await res.json();
        if (!data.ok) {
          els.result.textContent = data.error || 'Failed to submit task.';
        }
      } catch (err) {
        els.result.textContent = String(err);
      } finally {
        await refreshStatus();
      }
    }

    async function refreshStatus() {
      const res = await fetch('/api/status', {cache: 'no-store'});
      latestStatus = await res.json();
      renderStatus(latestStatus);
    }

    function renderStatus(data) {
      const running = data.is_running;
      els.dot.className = 'dot ' + (running ? 'running' : 'ready');
      els.state.textContent = running ? 'RUNNING' : data.state;
      els.progress.textContent = `${Number(data.progress || 0).toFixed(1)}%`;
      els.task.textContent = data.current_task || 'Waiting';
      els.trajectory.textContent = data.trajectory_path ? data.trajectory_path.split('/').pop() : 'None';
      els.result.textContent = data.last_error || data.last_result || 'No result yet.';
      els.runtime.textContent = data.progress_summary || 'Waiting for task input.';
      els.submitBtn.disabled = running;

      els.eventsView.innerHTML = (data.events || []).slice().reverse().map(e =>
        `<div class="entry ${escapeHtml(e.level)}"><span class="time">${escapeHtml(e.time)}</span>${escapeHtml(e.message)}</div>`
      ).join('') || '<div class="entry">No events yet.</div>';

      els.trajectoryView.innerHTML = (data.trajectory_tail || []).slice().reverse().map(t => {
        const title = `${t.step || '-'} ${t.type || ''}`;
        return `<div class="entry"><span class="time">${escapeHtml(title)}</span>${escapeHtml(JSON.stringify(t.detail || {}))}</div>`;
      }).join('') || '<div class="entry">No trajectory yet.</div>';

      els.toolsView.innerHTML = (data.tools || []).map(t => `<span class="tool">${escapeHtml(t)}</span>`).join('');
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }

    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const active = tab.dataset.tab;
        els.eventsView.hidden = active !== 'events';
        els.trajectoryView.hidden = active !== 'trajectory';
        els.toolsView.hidden = active !== 'tools';
      });
    });
    els.submitBtn.addEventListener('click', submitTask);
    els.taskInput.addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') submitTask();
    });

    const ctx = els.canvas.getContext('2d');
    function drawRobot(time) {
      const c = els.canvas;
      const w = c.width;
      const h = c.height;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = '#fdfefe';
      ctx.fillRect(0, 0, w, h);

      ctx.strokeStyle = '#d7dce5';
      ctx.lineWidth = 2;
      for (let x = 80; x < w; x += 80) {
        ctx.beginPath(); ctx.moveTo(x, 90); ctx.lineTo(x, h - 150); ctx.stroke();
      }
      for (let y = 120; y < h - 130; y += 70) {
        ctx.beginPath(); ctx.moveTo(70, y); ctx.lineTo(w - 70, y); ctx.stroke();
      }

      ctx.fillStyle = '#e7edf3';
      roundRect(115, h - 175, w - 230, 80, 8);
      ctx.fill();
      ctx.fillStyle = '#136f63';
      roundRect(230, h - 226, 118, 78, 6);
      ctx.fill();
      ctx.fillStyle = '#2f80ed';
      roundRect(w - 350, h - 226, 118, 78, 6);
      ctx.fill();
      ctx.fillStyle = '#ffffff';
      ctx.font = '700 28px system-ui';
      ctx.fillText('TAG 0', 247, h - 178);
      ctx.fillText('TAG 1', w - 333, h - 178);

      const running = latestStatus && latestStatus.is_running;
      const phase = running ? time / 680 : 0.8;
      const baseX = w / 2;
      const baseY = h - 190;
      const shoulder = Math.sin(phase) * 0.45 - 1.15;
      const elbow = Math.cos(phase * 1.22) * 0.5 + 1.0;
      const l1 = 190;
      const l2 = 160;
      const x1 = baseX + Math.cos(shoulder) * l1;
      const y1 = baseY + Math.sin(shoulder) * l1;
      const x2 = x1 + Math.cos(shoulder + elbow) * l2;
      const y2 = y1 + Math.sin(shoulder + elbow) * l2;

      ctx.lineCap = 'round';
      ctx.lineWidth = 26;
      ctx.strokeStyle = '#31465a';
      ctx.beginPath(); ctx.moveTo(baseX, baseY); ctx.lineTo(x1, y1); ctx.stroke();
      ctx.strokeStyle = '#49657f';
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();

      ctx.fillStyle = '#17202a';
      circle(baseX, baseY, 28);
      circle(x1, y1, 24);
      ctx.fillStyle = running ? '#2f80ed' : '#667085';
      circle(x2, y2, 20);

      ctx.strokeStyle = '#17202a';
      ctx.lineWidth = 9;
      ctx.beginPath(); ctx.moveTo(x2 - 18, y2 + 16); ctx.lineTo(x2 - 52, y2 + 48); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x2 + 18, y2 + 16); ctx.lineTo(x2 + 52, y2 + 48); ctx.stroke();

      ctx.fillStyle = '#667085';
      ctx.font = '600 24px system-ui';
      ctx.fillText(running ? 'Executing batched robot task' : 'Ready for task instruction', 72, 64);
      requestAnimationFrame(drawRobot);
    }
    function circle(x, y, r) {
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    }
    function roundRect(x, y, width, height, radius) {
      ctx.beginPath();
      ctx.moveTo(x + radius, y);
      ctx.arcTo(x + width, y, x + width, y + height, radius);
      ctx.arcTo(x + width, y + height, x, y + height, radius);
      ctx.arcTo(x, y + height, x, y, radius);
      ctx.arcTo(x, y, x + width, y, radius);
      ctx.closePath();
    }
    refreshStatus();
    setInterval(refreshStatus, 1000);
    requestAnimationFrame(drawRobot);
  </script>
</body>
</html>
"""
