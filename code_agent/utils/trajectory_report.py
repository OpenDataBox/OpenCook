# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Standalone HTML reports for trajectory JSON files."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

_BLOCK_PREVIEW_CHARS = 6000
_TOP_TOOL_COUNT = 8
_STATE_COLORS = {
    "thinking": "#00D4FF",
    "calling_tool": "#4ADE80",
    "reflecting": "#FBBF24",
    "completed": "#A78BFA",
    "error": "#F87171",
}
_PALETTE = ["#00D4FF", "#4ADE80", "#FBBF24", "#F87171", "#A78BFA", "#FB7185", "#38BDF8", "#22C55E"]


def write_trajectory_report(trajectory_path: str | Path, output_path: str | Path | None = None) -> Path:
    src = Path(trajectory_path).resolve()
    dst = src.with_suffix(".html") if output_path is None else Path(output_path).resolve()
    html = build_trajectory_report_html(json.loads(src.read_text(encoding="utf-8")), src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(html, encoding="utf-8")
    return dst


def _sanitize_report_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""

    home = str(Path.home())
    db_home = str(Path.home() / ".opencook")
    replacements = [
        (db_home, ".opencook"),
        (db_home.replace("\\", "/"), ".opencook"),
        (home, "~"),
        (home.replace("\\", "/"), "~"),
    ]
    for source, target in replacements:
        if source:
            text = text.replace(source, target)
    return text


def _report_title(task: dict[str, Any], src: Path) -> str:
    task_kind = _sanitize_report_text(task.get("task_kind") or "").strip()
    database = _sanitize_report_text(task.get("database") or "").strip()
    if task_kind == "interactive_chat":
        if database:
            return f"DBCooker {database} Interactive Synthesis Report"[:120]
        return "DBCooker Interactive Synthesis Report"
    if database:
        return f"DBCooker {database} Synthesis Report"[:120]
    return "DBCooker Synthesis Report"


def build_trajectory_report_html(trajectory: dict[str, Any], trajectory_path: str | Path) -> str:
    src = Path(trajectory_path).resolve()
    task = trajectory.get("task") or {}
    steps = trajectory.get("agent_steps") or []
    interactions = trajectory.get("llm_interactions") or []

    in_tokens = 0
    out_tokens = 0
    token_series: list[int] = []
    tool_counts: Counter[str] = Counter()
    activity_counts: Counter[str] = Counter()

    for item in interactions:
        usage = ((item.get("response") or {}).get("usage") or {})
        cur_in = int(usage.get("input_tokens") or 0)
        cur_out = int(usage.get("output_tokens") or 0)
        in_tokens += cur_in
        out_tokens += cur_out
        token_series.append(cur_in + cur_out)

    for step in steps:
        activity_counts[_step_category(step)] += 1
        for tc in step.get("tool_calls") or []:
            tool_counts[str(tc.get("name") or "unknown")] += 1

    latency_items = _step_latency_items(steps)

    title = _report_title(task, src)
    subtitle = " / ".join(str(x) for x in [task.get("database"), task.get("task_kind")] if x)
    status = "SUCCESS" if trajectory.get("success") else "FAILED"
    status_class = "ok" if trajectory.get("success") else "bad"
    user_request = _sanitize_report_text(task.get("user_input") or "").strip()

    task_rows = []
    for key in ["task_kind", "database", "func_name", "directory", "category", "file_path"]:
        if isinstance(task, dict) and task.get(key):
            task_rows.append((key, str(task.get(key))))
    if isinstance(task, dict) and task.get("user_input"):
        task_rows.append(("user_input", str(task.get("user_input"))))

    cards = "".join(
        _stat_card(label, value, status_class if label == "Status" else "")
        for label, value in [
            ("Status", status),
            ("Input Tokens", _fmt_int(in_tokens)),
            ("Output Tokens", _fmt_int(out_tokens)),
        ]
    )

    step_nav = "".join(_step_nav(i, step) for i, step in enumerate(steps, 1)) or '<div class="empty">No steps recorded.</div>'
    interaction_nav = "".join(_interaction_nav(i, item) for i, item in enumerate(interactions, 1)) or '<div class="empty">No interactions recorded.</div>'
    step_blocks = "".join(_step_block(i, step) for i, step in enumerate(steps, 1)) or '<div class="empty">No steps recorded.</div>'
    interaction_blocks = "".join(_interaction_block(i, item) for i, item in enumerate(interactions, 1)) or '<div class="empty">No LLM interactions recorded.</div>'
    agent_blocks = "".join(_agent_block(k, v) for k, v in trajectory.items() if k.endswith("_agent") and isinstance(v, dict)) or '<div class="empty">No per-agent metadata recorded.</div>'
    task_overview = _render_task_overview(task)

    charts = "".join(
        [
            _chart_card("Token Flow", "Prompt/completion totals and per-call trend.", _token_chart(in_tokens, out_tokens, token_series)),
            _chart_card("Step Latency", "Slow gaps between completed steps help surface stalls and long-running checks.", _latency_chart(latency_items)),
            _chart_card("Tool Usage", "Distribution of tool calls across the run.", _donut_chart(tool_counts.most_common(_TOP_TOOL_COUNT), "Tool Calls")),
            _chart_card("Step Composition", "High-level view of what the agent spent its steps doing.", _column_chart(activity_counts.most_common(), None)),
        ]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="color-scheme" content="dark"/>
  <meta name="theme-color" content="#060912"/>
  <title>{escape(title)}</title>
  <style>
    :root{{--bg:#060912;--bg2:#0b1020;--panel:rgba(9,14,26,.9);--panel2:rgba(14,20,35,.96);--line:rgba(122,146,195,.16);--cyan:#00D4FF;--orange:#F97316;--green:#4ADE80;--red:#F87171;--amber:#FBBF24;--violet:#A78BFA;--text:#E8EEF9;--muted:#96A6C2;--sub:#70809A;--mono:"JetBrains Mono",Consolas,monospace;--sans:"DM Sans","Space Grotesk","Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}}
    *{{box-sizing:border-box}} html{{scroll-behavior:smooth}}
    body{{margin:0;background:radial-gradient(circle at top right,rgba(0,212,255,.12),transparent 24%),radial-gradient(circle at bottom left,rgba(249,115,22,.10),transparent 24%),linear-gradient(180deg,var(--bg),var(--bg2));color:var(--text);font:14px/1.6 var(--sans)}}
    a{{color:inherit;text-decoration:none}} .shell{{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}}
    .sidebar{{position:sticky;top:0;height:100vh;overflow:auto;background:linear-gradient(180deg,rgba(5,9,18,.97),rgba(8,13,24,.97));border-right:1px solid var(--line)}}
    .sidebar-inner{{padding:20px 14px 22px}} .logo{{display:inline-flex;align-items:center;gap:8px;margin-bottom:6px;font-weight:700}}
    .logo-mark{{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:10px;background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.35);color:var(--cyan);font-size:12px}}
    .logo-name{{font-size:18px}} .side-kicker{{color:var(--muted);font-size:12px;margin-bottom:12px}}
    .pills{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}} .pill{{display:inline-flex;align-items:center;gap:8px;padding:5px 10px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--muted);font-size:11px;font-weight:600}}
    .pill.cyan{{color:var(--cyan);border-color:rgba(0,212,255,.35);background:rgba(0,212,255,.08)}} .pill-dot{{width:8px;height:8px;border-radius:999px;background:currentColor;box-shadow:0 0 14px currentColor}}
    .side-status.ok{{color:var(--green);border-color:rgba(74,222,128,.32);background:rgba(74,222,128,.08)}} .side-status.bad{{color:var(--red);border-color:rgba(248,113,113,.32);background:rgba(248,113,113,.08)}}
    .nav{{display:grid;gap:8px;margin-bottom:16px}} .nav a{{padding:7px 10px;border-radius:10px;color:var(--muted);font-size:13px}} .nav a:hover{{background:rgba(255,255,255,.05);color:var(--text)}}
    .nav-group{{display:grid;gap:6px}} .nav-group summary{{list-style:none}} .nav-group summary::-webkit-details-marker{{display:none}}
    .nav-section{{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 10px;border-radius:10px;color:var(--text);font-weight:700;background:rgba(255,255,255,.03);border:1px solid var(--line);cursor:pointer}}
    .nav-section:hover{{border-color:rgba(0,212,255,.28);background:rgba(255,255,255,.05)}}
    .nav-caret{{color:var(--muted);font-size:11px;transition:transform .18s ease}}
    .nav-group[open] .nav-caret{{transform:rotate(90deg)}}
    .nav-sublist{{display:grid;gap:5px;padding-left:10px;padding-top:2px}}
    .step-link,.interaction-link{{display:block;padding:6px 8px;border:1px solid var(--line);border-radius:10px;background:rgba(255,255,255,.025);font-weight:700}} .step-link:hover,.interaction-link:hover{{border-color:rgba(0,212,255,.35);background:rgba(0,212,255,.06);transform:translateX(1px)}}
    .nav-item-line{{display:grid;grid-template-columns:auto auto 1fr auto;gap:7px;align-items:center;min-width:0}}
    .step-link-num,.interaction-link-num{{color:var(--cyan);font:11px/1.2 var(--mono);font-weight:700}}
    .step-link-main,.interaction-link-main{{font-size:12px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:700}}
    .step-link-sub,.interaction-link-sub{{color:var(--muted);font-size:11px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:700}}
    .step-link-state,.interaction-link-state{{font-size:10px;color:var(--muted);text-transform:uppercase;white-space:nowrap;font-weight:700}}
    .empty{{color:var(--muted)}}
    .content{{padding:28px 30px 60px}} .hero,.section{{background:var(--panel);border:1px solid var(--line);border-radius:22px;box-shadow:0 18px 42px rgba(0,0,0,.32)}}
    .hero{{position:relative;overflow:hidden;padding:28px 30px 26px;margin-bottom:18px;background:linear-gradient(135deg,rgba(0,212,255,.08),transparent 38%),linear-gradient(180deg,rgba(249,115,22,.06),transparent 44%),var(--panel)}}
    .hero:after{{content:"";position:absolute;inset:0;background:radial-gradient(circle at 84% 12%,rgba(0,212,255,.12),transparent 22%),radial-gradient(circle at 10% 90%,rgba(249,115,22,.10),transparent 26%);pointer-events:none}}
    .title{{position:relative;z-index:1;margin:0 0 8px;font:700 34px/1.08 "Space Grotesk",var(--sans);letter-spacing:-.03em}} .text-grad{{background:linear-gradient(90deg,#fff 0%,#8fe9ff 34%,#4ADE80 100%);-webkit-background-clip:text;background-clip:text;color:transparent}}
    .hero-request{{position:relative;z-index:1;max-width:980px;margin:0 0 12px;padding:14px 16px;border-radius:16px;border:1px solid rgba(0,212,255,.16);background:rgba(255,255,255,.04)}}
    .hero-request-label{{color:var(--sub);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}}
    .hero-request .rich{{gap:8px}} .hero-request .rich p{{color:var(--text)}} .hero-request .rich code{{background:rgba(0,212,255,.12)}}
    .subtitle{{position:relative;z-index:1;color:var(--muted);max-width:800px;margin-bottom:10px}} .meta{{position:relative;z-index:1;color:var(--sub);font-size:13px;word-break:break-all}} .meta a{{color:var(--cyan)}}
    .cards{{position:relative;z-index:1;display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:18px}} .card{{padding:14px;border-radius:16px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04)}}
    .card .label{{color:var(--sub);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}} .card .value{{font:700 24px/1 "Space Grotesk",var(--sans)}} .ok{{color:var(--green)}} .bad{{color:var(--red)}}
    .section{{padding:20px 22px;margin-bottom:16px}} .head{{display:flex;align-items:end;justify-content:space-between;gap:14px;margin-bottom:16px}} .head h2{{margin:0;font:700 22px/1.15 "Space Grotesk",var(--sans);letter-spacing:-.02em}} .head p{{margin:0;color:var(--muted);font-size:13px}}
    .charts{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}} .chart{{display:flex;flex-direction:column;min-height:388px;background:var(--panel2);border:1px solid var(--line);border-radius:18px;padding:16px;overflow:hidden}}
    .chart h3{{margin:0 0 4px;font:700 16px/1.2 "Space Grotesk",var(--sans)}} .chart p{{margin:0 0 12px;color:var(--muted);font-size:12px}}
    .chart-copy{{min-height:42px}} .chart-body{{display:flex;flex-direction:column;gap:12px;flex:1;min-height:0}}
    .token-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px}} .token-chip{{padding:12px;border-radius:14px;border:1px solid var(--line);background:rgba(255,255,255,.03)}} .token-chip .n{{font:700 22px/1 "Space Grotesk",var(--sans)}} .token-chip .l{{color:var(--muted);font-size:12px;margin-top:6px}}
    .spark{{width:100%;height:84px;display:block;border-radius:14px;background:rgba(0,0,0,.16);border:1px solid var(--line)}} .hint{{color:var(--sub);font-size:12px;margin-top:8px}}
    .bar-list{{display:grid;gap:10px}} .bar-row{{display:grid;grid-template-columns:minmax(90px,180px) 1fr auto;gap:10px;align-items:center}} .bar-label{{font-size:13px;word-break:break-word}}
    .bar-track{{position:relative;height:10px;border-radius:999px;background:rgba(255,255,255,.06);overflow:hidden}} .bar-fill{{position:absolute;inset:0 auto 0 0;border-radius:999px}} .bar-value{{color:var(--muted);font:12px/1.2 var(--mono)}}
    .column-chart{{display:flex;gap:12px;align-items:flex-end;flex-wrap:nowrap;overflow-x:auto;min-height:252px;padding:10px 4px 6px 0}}
    .column-card{{display:grid;gap:8px;align-items:end;flex:0 0 96px;min-width:96px}}
    .column-shell{{height:180px;display:flex;align-items:flex-end}}
    .column-bar{{width:100%;min-height:18px;border-radius:14px 14px 8px 8px;border:1px solid rgba(255,255,255,.08);box-shadow:inset 0 1px 0 rgba(255,255,255,.12)}}
    .column-value{{color:var(--text);font:700 20px/1 "Space Grotesk",var(--sans)}}
    .column-label{{color:var(--muted);font-size:12px;line-height:1.25;word-break:break-word}}
    .donut-wrap{{display:grid;grid-template-columns:180px 1fr;gap:14px;align-items:center}} .donut-svg{{width:180px;height:180px;display:block;margin:0 auto}}
    .legend{{display:grid;gap:8px}} .legend-row{{display:grid;grid-template-columns:14px 1fr auto auto;gap:8px;align-items:center}} .legend-dot{{width:10px;height:10px;border-radius:999px}}
    .legend-label{{font-size:13px;word-break:break-word}} .legend-val,.legend-pct{{color:var(--muted);font:12px/1.2 var(--mono)}}
    .overview-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}} .overview-card{{padding:14px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.03)}}
    .overview-card h3{{margin:0 0 8px;font:700 15px/1.2 "Space Grotesk",var(--sans)}} .overview-card p{{margin:0;color:var(--muted)}}
    .kv{{display:grid;grid-template-columns:minmax(96px,160px) 1fr;gap:6px 10px;align-items:start}} .kv .k{{color:var(--sub);font-size:11px;text-transform:uppercase;letter-spacing:.06em}} .kv .v{{word-break:break-word}}
    .metric-row{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px}} .metric-chip{{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--muted);font-size:12px}}
    .metric-chip strong{{color:var(--text);font-weight:700}} .stack{{display:grid;gap:12px}} .inline-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
    .tool-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}} .tool-card{{padding:14px;border-radius:16px;border:1px solid var(--line);background:rgba(255,255,255,.035)}}
    .message-stack{{display:grid;gap:12px}}
    .tool-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}} .tool-name{{font-weight:700;color:var(--text)}} .tool-meta{{color:var(--muted);font:12px/1.3 var(--mono);word-break:break-all}}
    .tool-body{{display:grid;gap:10px}} .status-ok{{color:var(--green)}} .status-bad{{color:var(--red)}} .mono{{font-family:var(--mono)}} .block-caption{{color:var(--sub);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}}
    .rich{{display:grid;gap:12px}} .rich p,.rich ul,.rich ol{{margin:0}} .rich ul,.rich ol{{padding-left:20px}} .rich li+li{{margin-top:6px}} .rich h1,.rich h2,.rich h3,.rich h4{{margin:0;font-family:"Space Grotesk",var(--sans);letter-spacing:-.02em}}
    .rich h1{{font-size:24px}} .rich h2{{font-size:20px}} .rich h3{{font-size:17px}} .rich code{{padding:2px 6px;border-radius:8px;background:rgba(0,212,255,.10);border:1px solid rgba(0,212,255,.16);color:#9fefff;font:12px/1.4 var(--mono)}}
    .rich table{{width:100%;border-collapse:collapse;border-spacing:0;overflow:hidden;border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.02)}} .rich th,.rich td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
    .rich th{{background:rgba(0,212,255,.07);color:var(--text);font-size:12px;text-transform:uppercase;letter-spacing:.06em}} .rich tr:last-child td{{border-bottom:none}}
    .rich a{{color:var(--cyan)}} .tool-xml{{border-left:3px solid rgba(0,212,255,.4);padding-left:12px}}
    details{{background:var(--panel2);border:1px solid var(--line);border-radius:18px;margin-bottom:12px;overflow:hidden}} summary{{list-style:none;cursor:pointer;padding:14px 16px;background:linear-gradient(180deg,rgba(255,255,255,.02),transparent);display:flex;align-items:center;justify-content:space-between;gap:12px}} summary::-webkit-details-marker{{display:none}}
    .smain{{display:flex;flex-direction:column;gap:4px;min-width:0}} .stitle{{font-weight:700;display:flex;align-items:center;gap:8px;flex-wrap:wrap}} .ssub{{color:var(--muted);font-size:12px;word-break:break-word}} .sbadges{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:end}}
    .badge{{display:inline-flex;align-items:center;padding:3px 9px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}}
    .details-body{{padding:0 16px 16px}} .block-label{{margin:14px 0 8px;color:var(--sub);font-size:12px;text-transform:uppercase;letter-spacing:.08em}}
    pre{{margin:0;padding:14px;background:#07101c;border:1px solid rgba(120,150,210,.12);border-radius:14px;overflow:auto;white-space:pre-wrap;word-break:break-word;color:#dce6f2;font:12px/1.55 var(--mono)}}
    @media (max-width:1100px){{.shell{{grid-template-columns:1fr}}.sidebar{{position:static;height:auto;border-right:none;border-bottom:1px solid var(--line)}}.charts{{grid-template-columns:1fr}}.donut-wrap{{grid-template-columns:1fr}}}}
    @media (max-width:720px){{.content{{padding:18px 14px 40px}}.hero,.section{{padding:18px 16px;border-radius:18px}}.token-grid,.cards,.overview-grid{{grid-template-columns:1fr 1fr}}.bar-row{{grid-template-columns:1fr}}.sbadges{{justify-content:start}}}}
  </style>
</head>
<body>
  <div class="shell" id="top">
    <aside class="sidebar"><div class="sidebar-inner">
      <a class="logo" href="#top"><span class="logo-mark">DB</span><span class="logo-name">DBCooker</span></a>
      <div class="side-kicker">Trajectory Lab</div>
      <div class="pills"><span class="pill side-status {status_class}">{escape(status)}</span><span class="pill">{escape(str(task.get("database") or "unknown"))}</span></div>
      <nav class="nav" aria-label="Trajectory sections">
        <a href="#overview"><b>Overview</b></a><a href="#task">Task</a><a href="#final-result">Final Result</a><a href="#agents">Agent Runs</a><a href="#debug-trace">Debug Trace</a>
        <details class="nav-group"><summary class="nav-section"><span>Steps</span><span class="nav-caret">&#9656;</span></summary><div class="nav-sublist">{step_nav}</div></details>
        <details class="nav-group"><summary class="nav-section"><span>Debug Trace</span><span class="nav-caret">&#9656;</span></summary><div class="nav-sublist">{interaction_nav}</div></details>
      </nav>
    </div></aside>
    <main class="content">
      <section class="hero" id="overview">
        <div class="pills"><span class="pill cyan"><span class="pill-dot"></span>Trajectory / Live</span><span class="pill">{escape(str(task.get("database") or "unknown"))} / {escape(str(task.get("task_kind") or "task"))}</span></div>
        <h1 class="title">{escape(title)}</h1>
        {_render_hero_request(user_request)}
        <div class="subtitle">Turn-by-turn execution audit for DBCooker. Summary first, analytics second, anchored step details after that.</div>
        <div class="meta">Source trajectory: <a href="{escape(src.name)}">{escape(_sanitize_report_text(src))}</a></div>
        <div class="cards">{cards}</div>
      </section>
      <section class="section"><div class="head"><div><h2>Execution Analytics</h2><p>Top-level charts before the detailed step log.</p></div></div><div class="charts">{charts}</div></section>
      <section class="section" id="task"><div class="head"><div><h2>Task Overview</h2><p>Resolved task payload and prompt-facing metadata.</p></div></div>{task_overview}</section>
      <section class="section" id="final-result"><div class="head"><div><h2>Final Result</h2><p>Task-level completion text captured at the end of execution.</p></div></div>{_render_rich_text(trajectory.get("final_result"), prefer_markdown=True)}</section>
      <section class="section" id="agents"><div class="head"><div><h2>Agent Runs</h2><p>Per-agent metadata recorded by the trajectory recorder.</p></div></div>{agent_blocks}</section>
      <section class="section" id="steps"><div class="head"><div><h2>Step Trace</h2><p>Anchored step cards with direct sidebar navigation.</p></div></div>{step_blocks}</section>
      <section class="section" id="debug-trace"><div class="head"><div><h2>Debug Trace</h2><p>Low-level provider envelopes and prompt inputs, tucked below the main execution narrative.</p></div></div>{_details_block("LLM Interactions", '<div class="details-body">' + interaction_blocks + '</div>', open_by_default=False)}</section>
    </main>
  </div>
</body></html>"""


def _stat_card(label: str, value: str, css: str = "") -> str:
    return f'<div class="card"><div class="label">{escape(label)}</div><div class="value {css}">{escape(value)}</div></div>'


def _render_hero_request(user_request: str) -> str:
    if not user_request:
        return ""
    return (
        '<div class="hero-request">'
        '<div class="hero-request-label">User Request</div>'
        f"{_render_rich_text(user_request, prefer_markdown=True)}"
        "</div>"
    )


def _chart_card(title: str, subtitle: str, body: str) -> str:
    return f'<div class="chart"><div class="chart-copy"><h3>{escape(title)}</h3><p>{escape(subtitle)}</p></div><div class="chart-body">{body}</div></div>'


def _token_chart(in_tokens: int, out_tokens: int, series: list[int]) -> str:
    return (
        '<div class="token-grid">'
        f'<div class="token-chip"><div class="n" style="color:#00D4FF">{escape(_fmt_int(in_tokens))}</div><div class="l">Prompt tokens</div></div>'
        f'<div class="token-chip"><div class="n" style="color:#4ADE80">{escape(_fmt_int(out_tokens))}</div><div class="l">Completion tokens</div></div>'
        '</div>' + _sparkline_svg(series) + '<div class="hint">Sparkline shows total tokens per LLM interaction.</div>'
    )


def _donut_chart(items: list[tuple[str, int]], center_label: str) -> str:
    if not items:
        return '<div class="empty">No data available.</div>'
    total = sum(value for _, value in items) or 1
    radius = 54
    circumference = 2 * 3.141592653589793 * radius
    offset = 0.0
    segments: list[str] = []
    legends: list[str] = []
    for idx, (label, value) in enumerate(items):
        color = _PALETTE[idx % len(_PALETTE)]
        length = (value / total) * circumference
        segments.append(
            f'<circle cx="90" cy="90" r="{radius}" fill="none" stroke="{color}" stroke-width="18" '
            f'stroke-dasharray="{length:.2f} {circumference:.2f}" stroke-dashoffset="{-offset:.2f}" '
            'stroke-linecap="butt" transform="rotate(-90 90 90)"/>'
        )
        legends.append(
            '<div class="legend-row">'
            f'<span class="legend-dot" style="background:{color}"></span>'
            f'<span class="legend-label">{escape(label)}</span>'
            f'<span class="legend-val">{escape(_fmt_int(value))}</span>'
            f'<span class="legend-pct">{(value / total) * 100:.0f}%</span>'
            '</div>'
        )
        offset += length
    svg = (
        '<svg class="donut-svg" viewBox="0 0 180 180" xmlns="http://www.w3.org/2000/svg">'
        f'<circle cx="90" cy="90" r="{radius}" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="18"/>'
        + "".join(segments)
        + f'<text x="90" y="84" text-anchor="middle" fill="#96A6C2" font-size="11" font-family="Segoe UI">{escape(center_label)}</text>'
        + f'<text x="90" y="104" text-anchor="middle" fill="#E8EEF9" font-size="24" font-weight="700" font-family="Space Grotesk, Segoe UI">{escape(_fmt_int(total))}</text>'
        + '</svg>'
    )
    return f'<div class="donut-wrap">{svg}<div class="legend">{"".join(legends)}</div></div>'


def _latency_chart(items: list[tuple[str, float]]) -> str:
    if not items:
        return '<div class="empty">No step latency data available.</div>'
    values = [value for _, value in items]
    return _sparkline_svg([int(round(value)) for value in values]) + '<div class="hint">Approximate wall time between completed steps.</div>'


def _bar_chart(items: list[tuple[str, int]], color_map: dict[str, str] | None) -> str:
    if not items:
        return '<div class="empty">No data available.</div>'
    max_value = max(v for _, v in items) or 1
    rows = []
    for idx, (label, value) in enumerate(items):
        width = max(6, int(round((value / max_value) * 100)))
        color = (color_map or {}).get(label) or _PALETTE[idx % len(_PALETTE)]
        rows.append(
            '<div class="bar-row">'
            f'<div class="bar-label">{escape(label)}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{width}%;background:linear-gradient(90deg,{color},rgba(255,255,255,.18));"></div></div>'
            f'<div class="bar-value">{escape(_fmt_int(value))}</div></div>'
        )
    return '<div class="bar-list">' + "".join(rows) + "</div>"


def _column_chart(items: list[tuple[str, int]], color_map: dict[str, str] | None) -> str:
    if not items:
        return '<div class="empty">No data available.</div>'
    max_value = max(v for _, v in items) or 1
    columns = []
    for idx, (label, value) in enumerate(items):
        height = max(18, int(round((value / max_value) * 100)))
        color = (color_map or {}).get(label) or _PALETTE[idx % len(_PALETTE)]
        columns.append(
            '<div class="column-card">'
            f'<div class="column-value">{escape(_fmt_int(value))}</div>'
            f'<div class="column-shell"><div class="column-bar" style="height:{height}%;background:linear-gradient(180deg,{color},rgba(255,255,255,.18));"></div></div>'
            f'<div class="column-label">{escape(label)}</div>'
            '</div>'
        )
    return '<div class="column-chart">' + "".join(columns) + "</div>"


def _sparkline_svg(values: list[int]) -> str:
    w, h, px, py = 420, 84, 12, 10
    if not values:
        return f'<svg class="spark" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg"><text x="{w/2:.0f}" y="{h/2:.0f}" text-anchor="middle" fill="#96A6C2" font-size="12">No token series available</text></svg>'
    if len(values) == 1:
        points = [(w / 2, h / 2)]
    else:
        min_v, max_v = min(values), max(values) or 1
        span = max(max_v - min_v, 1)
        uw, uh = w - px * 2, h - py * 2
        points = [(px + uw * (i / (len(values) - 1)), py + uh * (1 - ((v - min_v) / span))) for i, v in enumerate(values)]
    line = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    area = f"{px:.2f},{h-py:.2f} " + line + f" {w-px:.2f},{h-py:.2f}"
    grid = "".join(f'<line x1="{px}" y1="{y}" x2="{w-px}" y2="{y}" stroke="rgba(255,255,255,.06)" stroke-width="1"/>' for y in (py, h / 2, h - py))
    dots = "".join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.5" fill="#00D4FF"/>' for x, y in points)
    return f'<svg class="spark" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">{grid}<polygon points="{area}" fill="rgba(0,212,255,.14)"/><polyline points="{line}" fill="none" stroke="#00D4FF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>{dots}</svg>'


def _step_nav(index: int, step: dict[str, Any]) -> str:
    state = str(step.get("state") or "-")
    agent = str(step.get("agent_type") or "-")
    tool_names = [str(tc.get("name") or "") for tc in (step.get("tool_calls") or []) if tc.get("name")]
    tool_hint = ", ".join(tool_names[:2]) if tool_names else "no tools"
    anchor = f"step-{index}"
    return (
        f'<a class="step-link" href="#{anchor}"><div class="nav-item-line">'
        f'<span class="step-link-num">#{index}</span>'
        f'<span class="step-link-main">{escape(agent)}</span>'
        f'<span class="step-link-sub">{escape(tool_hint)}</span>'
        f'<span class="step-link-state">{escape(state)}</span>'
        '</div></a>'
    )


def _interaction_nav(index: int, item: dict[str, Any]) -> str:
    resp = item.get("response") or {}
    agent = str(item.get("agent_type") or "-")
    tool_names = [str(tc.get("name") or "") for tc in (resp.get("tool_calls") or []) if tc.get("name")]
    if tool_names:
        hint = ", ".join(tool_names[:2])
    else:
        hint = str(resp.get("finish_reason") or "-")
    return (
        f'<a class="interaction-link" href="#interaction-{index}"><div class="nav-item-line">'
        f'<span class="interaction-link-num">#{index}</span>'
        f'<span class="interaction-link-main">{escape(agent)}</span>'
        f'<span class="interaction-link-sub">{escape(hint)}</span>'
        f'<span class="interaction-link-state">{escape(str(resp.get("finish_reason") or "-"))}</span>'
        '</div></a>'
    )


def _step_block(index: int, step: dict[str, Any]) -> str:
    number = step.get("step_number", "?")
    state = str(step.get("state") or "-")
    agent = str(step.get("agent_type") or "-")
    stamp = str(step.get("timestamp") or "-")
    llm_resp = step.get("llm_response") or {}
    usage = llm_resp.get("usage") or {}
    token_hint = f'in {usage.get("input_tokens") or 0} / out {usage.get("output_tokens") or 0}' if usage else ""
    tool_names = [str(tc.get("name") or "") for tc in (step.get("tool_calls") or []) if tc.get("name")]
    tool_hint = ", ".join(tool_names[:3]) if tool_names else "No tool calls"
    category = _step_category(step)
    llm_has_tool_intent = bool(llm_resp.get("tool_calls")) or "<tool_code>" in str(llm_resp.get("content") or "")
    token_badge = f'<span class="badge">{escape(token_hint)}</span>' if token_hint else ""
    summary = (
        f'<div class="smain"><div class="stitle">Step {escape(str(number))} <span class="badge">{escape(agent)}</span></div><div class="ssub">{escape(tool_hint)}</div></div>'
        f'<div class="sbadges"><span class="badge">{escape(state)}</span><span class="badge">{escape(category)}</span>{token_badge}</div>'
    )
    body = '<div class="metric-row">' + "".join(
        [
            _metric_chip("Index", str(index)),
            _metric_chip("Step", str(number)),
            _metric_chip("Agent", agent),
            _metric_chip("State", state),
            _metric_chip("Mode", category),
            _metric_chip("Time", stamp),
        ]
    ) + "</div>"
    if llm_resp:
        body += '<div class="block-label">LLM Response</div>' + _render_llm_response(llm_resp)
    if step.get("tool_calls") and not llm_has_tool_intent:
        body += '<div class="block-label">Tool Calls</div>' + _render_tool_calls(step.get("tool_calls"))
    if step.get("tool_results"):
        body += '<div class="block-label">Tool Results</div>' + _render_tool_results(step.get("tool_results"))
    if step.get("reflection"):
        body += '<div class="block-label">Reflection</div>' + _render_rich_text(step.get("reflection"), prefer_markdown=True)
    if step.get("error"):
        body += '<div class="block-label">Error</div>' + _render_rich_text(step.get("error"), prefer_markdown=True)
    return f'<div id="step-{index}">' + _details_block(summary, '<div class="details-body">' + body + '</div>', open_by_default=(index <= 2), raw_title=True) + "</div>"


def _interaction_block(index: int, item: dict[str, Any]) -> str:
    resp = item.get("response") or {}
    usage = resp.get("usage") or {}
    title = (
        f'<div class="smain"><div class="stitle">Interaction {index} <span class="badge">{escape(str(item.get("agent_type") or "-"))}</span></div>'
        f'<div class="ssub">Prompt envelope and provider metadata</div></div>'
        f'<div class="sbadges"><span class="badge">in {escape(str(usage.get("input_tokens") or 0))}</span><span class="badge">out {escape(str(usage.get("output_tokens") or 0))}</span></div>'
    )
    body = '<div class="metric-row">' + "".join(
        [
            _metric_chip("Index", str(index)),
            _metric_chip("Agent", str(item.get("agent_type") or "-")),
            _metric_chip("Provider", str(item.get("provider") or "-")),
            _metric_chip("Finish", str(resp.get("finish_reason") or "-")),
            _metric_chip("Input", str(usage.get("input_tokens") or 0)),
            _metric_chip("Output", str(usage.get("output_tokens") or 0)),
        ]
    ) + "</div>"
    if item.get("tools_available"):
        body += '<div class="block-label">Tools Available</div>' + _render_badge_list(item.get("tools_available") or [])
    if item.get("input_messages"):
        body += '<div class="block-label">Input Messages</div>' + _render_messages(item.get("input_messages"))
    return f'<div id="interaction-{index}">' + _details_block(title, '<div class="details-body">' + body + '</div>', open_by_default=False, raw_title=True) + '</div>'


def _agent_block(name: str, value: dict[str, Any]) -> str:
    body = '<div class="metric-row">' + "".join(
        [
            _metric_chip("Provider", str(value.get("provider") or "-")),
            _metric_chip("Model", str(value.get("model") or "-")),
            _metric_chip("Max Steps", str(value.get("max_steps") or "-")),
            _metric_chip("Success", str(value.get("success")) if "success" in value else "-"),
        ]
    ) + "</div>"
    body += _kv_table([("Start", str(value.get("start_time") or "-")), ("End", str(value.get("end_time") or "-"))])
    return _details_block(f"Agent Run: {escape(name)}", '<div class="details-body">' + body + '</div>', open_by_default=False)


def _details_block(title: str, body: str, *, open_by_default: bool = False, raw_title: bool = False) -> str:
    open_attr = " open" if open_by_default else ""
    return f"<details{open_attr}><summary>{title if raw_title else escape(title)}</summary>{body}</details>"


def _kv_table(rows: list[tuple[str, str]]) -> str:
    return (
        '<div class="kv">'
        + "".join(
            f'<div class="k">{escape(_sanitize_report_text(k))}</div>'
            f'<div class="v">{escape(_sanitize_report_text(v))}</div>'
            for k, v in rows
        )
        + "</div>"
    ) if rows else '<div class="empty">-</div>'


def _json_block(data: Any) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        text = repr(data)
    return _render_text_block(text, css_class="json")


def _render_llm_response(resp: dict[str, Any]) -> str:
    usage = resp.get("usage") or {}
    parts = ['<div class="stack">']
    parts.append(
        '<div class="metric-row">' + "".join(
            [
                _metric_chip("Finish", str(resp.get("finish_reason") or "-")),
                _metric_chip("Model", str(resp.get("model") or "-")),
                _metric_chip("Input", str(usage.get("input_tokens") or 0)),
                _metric_chip("Output", str(usage.get("output_tokens") or 0)),
            ]
        ) + "</div>"
    )
    if resp.get("content"):
        parts.append(_render_rich_text(resp.get("content"), prefer_markdown=True))
    if resp.get("tool_calls"):
        parts.append('<div class="block-caption">Structured tool calls</div>' + _render_tool_calls(resp.get("tool_calls")))
    parts.append("</div>")
    return "".join(parts)


def _render_messages(messages: Any) -> str:
    items = messages or []
    if not items:
        return '<div class="empty">No input messages recorded.</div>'
    cards = []
    for idx, message in enumerate(items, 1):
        if not isinstance(message, dict):
            cards.append(f'<div class="tool-card"><div class="tool-body">{_render_rich_text(message)}</div></div>')
            continue
        role = str(message.get("role") or f"message {idx}")
        meta_parts = []
        for key in ["name", "tool_call_id"]:
            if message.get(key):
                meta_parts.append(f"{key}={message.get(key)}")
        meta = " | ".join(meta_parts) if meta_parts else "-"
        body_parts = []
        if message.get("tool_result"):
            body_parts.append('<div><div class="block-caption">Tool Result</div>' + _render_tool_results([message.get("tool_result")]) + '</div>')
        content = message.get("content")
        if content not in (None, ""):
            rendered_content = _render_rich_text(content, prefer_markdown=True) if isinstance(content, str) else _json_block(content)
            body_parts.append('<div><div class="block-caption">Content</div>' + rendered_content + '</div>')
        extra_keys = [k for k in message.keys() if k not in {"role", "content", "name", "tool_call_id", "tool_result"}]
        if extra_keys:
            body_parts.append('<div><div class="block-caption">Extra</div>' + _json_block({k: message.get(k) for k in extra_keys}) + '</div>')
        cards.append(
            '<div class="tool-card"><div class="tool-head">'
            f'<div class="tool-name">{escape(role)}</div>'
            f'<div class="tool-meta">{escape(meta)}</div>'
            '</div><div class="tool-body">' + ("".join(body_parts) if body_parts else '<div class="empty">No content.</div>') + '</div></div>'
        )
    return '<div class="message-stack">' + "".join(cards) + "</div>"


def _render_tool_calls(calls: Any) -> str:
    items = calls or []
    if not items:
        return '<div class="empty">No tool calls recorded.</div>'
    cards = []
    for call in items:
        call = call or {}
        arguments = call.get("arguments") or {}
        arg_blocks = []
        for key, value in arguments.items():
            arg_blocks.append(f'<div><div class="block-caption">{escape(str(key))}</div>{_render_rich_text(value)}</div>')
        cards.append(
            '<div class="tool-card"><div class="tool-head">'
            f'<div class="tool-name">{escape(str(call.get("name") or "unknown"))}</div>'
            f'<div class="tool-meta">{escape(str(call.get("call_id") or call.get("id") or "-"))}</div>'
            '</div><div class="tool-body">'
            + ("".join(arg_blocks) if arg_blocks else '<div class="empty">No arguments.</div>')
            + "</div></div>"
        )
    return '<div class="tool-grid">' + "".join(cards) + "</div>"


def _render_tool_results(results: Any) -> str:
    items = results or []
    if not items:
        return '<div class="empty">No tool results recorded.</div>'
    cards = []
    for result in items:
        result = result or {}
        success = bool(result.get("success"))
        status_class = "status-ok" if success else "status-bad"
        status_text = "success" if success else "error"
        parts = [
            '<div class="tool-card"><div class="tool-head">',
            f'<div class="tool-name">{escape(str(result.get("call_id") or "tool result"))}</div>',
            f'<div class="tool-meta {status_class}">{escape(status_text)}</div>',
            '</div><div class="tool-body">',
        ]
        if result.get("result") not in (None, ""):
            parts.append('<div><div class="block-caption">Result</div>' + _render_rich_text(result.get("result")) + '</div>')
        if result.get("error") not in (None, ""):
            parts.append('<div><div class="block-caption">Error</div>' + _render_rich_text(result.get("error")) + '</div>')
        parts.append('</div></div>')
        cards.append("".join(parts))
    return '<div class="tool-grid">' + "".join(cards) + "</div>"


def _render_rich_text(value: Any, *, prefer_markdown: bool = False) -> str:
    text = _sanitize_report_text(value)
    if not text.strip():
        return '<div class="empty">No content.</div>'
    if len(text) > _BLOCK_PREVIEW_CHARS:
        text = text[:_BLOCK_PREVIEW_CHARS] + f"\n... [truncated {len(text) - _BLOCK_PREVIEW_CHARS} chars]"
    tool_markup = _render_tool_markup(text)
    if tool_markup is not None:
        return tool_markup
    if prefer_markdown or _looks_like_markdown(text):
        return _render_markdown(text)
    return _render_text_block(text)


def _render_tool_markup(text: str) -> str | None:
    if "<tool" not in text or "</tool>" not in text:
        return None
    tool_matches = list(re.finditer(r"<tool name=\"([^\"]+)\">(.*?)</tool>", text, flags=re.S))
    if not tool_matches:
        return None
    cards = []
    stripped = text
    for match in tool_matches:
        name = match.group(1)
        inner = match.group(2)
        stripped = stripped.replace(match.group(0), "")
        params = re.findall(r"<parameter name=\"([^\"]+)\">(.*?)</parameter>", inner, flags=re.S)
        body = []
        for param_name, param_value in params:
            body.append(f'<div><div class="block-caption">{escape(param_name)}</div>{_render_text_block(param_value.strip())}</div>')
        if not body:
            body.append(_render_text_block(inner.strip()))
        cards.append(
            '<div class="tool-card tool-xml"><div class="tool-head">'
            f'<div class="tool-name">Proposed tool: {escape(name)}</div>'
            '</div><div class="tool-body">' + "".join(body) + '</div></div>'
        )
    stripped = re.sub(r"</?tool_code>", "", stripped, flags=re.S).strip()
    extra = _render_markdown(stripped) if stripped else ""
    return '<div class="stack">' + (extra if extra else "") + '<div class="tool-grid">' + "".join(cards) + "</div></div>"


def _looks_like_markdown(text: str) -> bool:
    return any(
        token in text
        for token in ["```", "\n# ", "\n## ", "\n- ", "\n1. ", "|---"]
    ) or text.lstrip().startswith(("#", "- ", "1. ", "* "))


def _render_markdown(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html_parts: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            i += 1
            buf: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            lang_chip = f'<div class="block-caption">{escape(lang)}</div>' if lang else ""
            code_text = "\n".join(buf)
            html_parts.append(f'<div class="stack">{lang_chip}{_render_text_block(code_text, css_class="mono")}</div>')
            continue
        if _is_table_header(lines, i):
            headers = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines):
                row_line = lines[i].strip()
                if not row_line or "|" not in row_line:
                    break
                rows.append([cell.strip() for cell in row_line.strip("|").split("|")])
                i += 1
            header_html = "".join(f"<th>{_render_inline_markdown(cell)}</th>" for cell in headers)
            row_html = "".join(
                "<tr>" + "".join(f"<td>{_render_inline_markdown(cell)}</td>" for cell in row) + "</tr>" for row in rows
            )
            html_parts.append(f'<table><thead><tr>{header_html}</tr></thead><tbody>{row_html}</tbody></table>')
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            level = min(len(heading_match.group(1)), 4)
            html_parts.append(f"<h{level}>{_render_inline_markdown(heading_match.group(2))}</h{level}>")
            i += 1
            continue
        list_match = re.match(r"^([-*]|\d+\.)\s+(.*)$", stripped)
        if list_match:
            ordered = bool(re.match(r"^\d+\.", list_match.group(1)))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < len(lines):
                cur = lines[i].strip()
                cur_match = re.match(r"^([-*]|\d+\.)\s+(.*)$", cur)
                if not cur_match:
                    break
                items.append(f"<li>{_render_inline_markdown(cur_match.group(2))}</li>")
                i += 1
            html_parts.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        para_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith("```") or _is_table_header(lines, i) or re.match(r"^(#{1,6})\s+", nxt) or re.match(r"^([-*]|\d+\.)\s+", nxt):
                break
            para_lines.append(nxt)
            i += 1
        html_parts.append(f"<p>{_render_inline_markdown(' '.join(para_lines))}</p>")
    return '<div class="rich">' + "".join(html_parts) + "</div>"


def _is_table_header(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    header = lines[index].strip()
    divider = lines[index + 1].strip()
    if "|" not in header or "|" not in divider:
        return False
    normalized = divider.replace("|", "").replace(":", "").replace("-", "").strip()
    return normalized == ""


def _render_inline_markdown(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    rendered: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("`") and part.endswith("`"):
            rendered.append(f"<code>{escape(part[1:-1])}</code>")
            continue
        escaped = escape(part)
        escaped = re.sub(r"\*\*(.+?)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", escaped)
        escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{escape(m.group(2), quote=True)}">{m.group(1)}</a>', escaped)
        rendered.append(escaped)
    return "".join(rendered)


def _metric_chip(label: str, value: str) -> str:
    return (
        f'<span class="metric-chip"><span>{escape(_sanitize_report_text(label))}</span>'
        f'<strong>{escape(_sanitize_report_text(value))}</strong></span>'
    )


def _render_badge_list(values: list[str]) -> str:
    if not values:
        return '<div class="empty">No values.</div>'
    return '<div class="metric-row">' + "".join(f'<span class="metric-chip"><strong>{escape(str(value))}</strong></span>' for value in values) + "</div>"


def _render_task_overview(task: dict[str, Any]) -> str:
    if not isinstance(task, dict) or not task:
        return '<div class="empty">No task metadata recorded.</div>'
    core_rows = []
    for key in ["database", "func_name", "category", "task_kind", "directory", "file_path"]:
        if task.get(key):
            core_rows.append((key.replace("_", " "), _sanitize_report_text(task.get(key))))
    blocks = ['<div class="overview-grid">']
    blocks.append('<div class="overview-card"><h3>Core Metadata</h3>' + _kv_table(core_rows) + '</div>')
    if task.get("description"):
        blocks.append('<div class="overview-card"><h3>Description</h3>' + _render_rich_text(task.get("description"), prefer_markdown=True) + '</div>')
    if task.get("example"):
        blocks.append('<div class="overview-card"><h3>Example</h3>' + _render_rich_text(task.get("example"), prefer_markdown=True) + '</div>')
    if task.get("user_input"):
        blocks.append('<div class="overview-card"><h3>User Input</h3>' + _render_rich_text(task.get("user_input"), prefer_markdown=True) + '</div>')
    blocks.append('</div>')
    return "".join(blocks)


def _step_category(step: dict[str, Any]) -> str:
    tool_calls = step.get("tool_calls") or []
    tool_names = [str(tc.get("name") or "") for tc in tool_calls]
    if "task_done" in tool_names:
        return "finish"
    if any(name in {"str_replace_based_edit_tool", "json_edit_tool", "edit_file", "write_file", "create_file"} for name in tool_names):
        return "edit"
    for tc in tool_calls:
        if str(tc.get("name") or "") == "bash":
            command = str((tc.get("arguments") or {}).get("command") or "").lower()
            if any(keyword in command for keyword in ["make test", "pytest", "ctest", "verify", "compile", "build", "cargo test", "go test"]):
                return "verify"
            return "inspect"
    content = str((step.get("llm_response") or {}).get("content") or "").strip()
    if "<tool_code>" in content:
        return "plan"
    return "reason"


def _step_latency_items(steps: list[dict[str, Any]]) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    prev_ts: datetime | None = None
    for index, step in enumerate(steps, 1):
        stamp = str(step.get("timestamp") or "")
        try:
            current = datetime.fromisoformat(stamp)
        except Exception:
            prev_ts = None
            continue
        if prev_ts is not None:
            items.append((f"Step {index}", max((current - prev_ts).total_seconds(), 0.0)))
        prev_ts = current
    return items


def _render_text_block(value: Any, *, css_class: str = "") -> str:
    text = _sanitize_report_text(value)
    if not text.strip():
        return '<div class="empty">No content.</div>'
    if len(text) > _BLOCK_PREVIEW_CHARS:
        text = text[:_BLOCK_PREVIEW_CHARS] + f"\n... [truncated {len(text) - _BLOCK_PREVIEW_CHARS} chars]"
    class_attr = f' class="{css_class}"' if css_class else ""
    return f"<pre{class_attr}>{escape(text)}</pre>"


def _fmt_seconds(value: Any) -> str:
    try:
        return f"{float(value):.1f}s"
    except Exception:
        return "-"


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)
