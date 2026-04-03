<div align="center">
  <img src="./data/images/opencook_logo.png" width="140px" alt="OpenCook Logo"/><br/>
  <h1>OpenCook</h1>
  <p><b>Project-Specific Personalization for Coding Agents</b></p>
  <p><i>Start with a generic project. End with a perfectly tailored solution.</i></p>
</div>

<div align="center">

  [![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
  [![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
  [![Multi-Agent](https://img.shields.io/badge/Multi--Agent-Plan·Code·Test-F97316?style=flat-square)]()
  [![Memory](https://img.shields.io/badge/Memory-4%20Layers-7C3AED?style=flat-square)]()
  [![LLM](https://img.shields.io/badge/LLM-Any%20Provider-00D4FF?style=flat-square)]()

</div>

<div align="center">

  <a href="#-news">News</a> &nbsp;•&nbsp;
  <a href="#-introduction">Introduction</a> &nbsp;•&nbsp;
  <a href="#-demo">Demo</a> &nbsp;•&nbsp;
  <a href="#-quick-start">Quick Start</a> &nbsp;•&nbsp;
  <a href="#-how-it-works">How It Works</a> &nbsp;•&nbsp;
  <a href="#-extend-opencook">Extend</a> &nbsp;•&nbsp;
  <a href="#-comparison">Comparison</a> &nbsp;•&nbsp;
  <a href="#-faq">FAQ</a> &nbsp;•&nbsp;
  <a href="#-citation">Citation</a>

</div>

<div align="center">
  <b>English</b> &nbsp;|&nbsp; 简体中文
  <br/><br/>
  <b>⭐ Star us on GitHub and motivate us to cook more features!</b>
</div>


## 🗞️ News

> - **\[04/2026\]** 🎉 OpenCook **v1.0** is live, the first project-specific personalization layer purpose-built for coding agents. Built-in recipe library **`open-cookbook/`** released. Community recipes are welcome!



## ✨ Introduction

Coding agents are powerful but generic. They can navigate your codebase, but they struggle to **deeply** personalize it: injecting a production-ready feature that respects internal conventions, passes the build system, clears regression tests, and ships as a mergeable patch, all without hand-holding.

**OpenCook is the missing layer.** It wraps any coding agent with three project-local primitives:

- **Recipes**: step-by-step domain guides that teach the agent *exactly* how to implement a feature in your specific codebase
- **Rules**: per-project constraint files that encode conventions, style, and invariants the agent must respect
- **Memory**: a 4-layer stack (Working → Episodic → Project → Long-Term) that keeps the agent coherent across long sessions

### Why OpenCook?

Generic coding agents (Claude Code, Codex, OpenCode) treat every project the same. Personalization demands more. OpenCook addresses five requirements they leave unmet:

| What personalization requires | Claude Code / Codex / OpenCode | OpenCook |
|---|---|---|
| **Know your project's rules** | Agent re-infers conventions and entry points from scratch each session | Recipes + Rules encode exact conventions, registration patterns, and constraints, all injected before the first line is written |
| **Know precisely what to change** | Edits broadly, often missing required dependencies or touching unrelated code | PlanAgent scopes exactly which files and entry points need to change before coding begins |
| **Verify the result fits** | Generates code and stops; you compile and test manually | Plan → Code → Test loop runs until the patch compiles and all tests pass; the session does not end until the change works |
| **Remember what it learned** | Context resets each run; the same project knowledge is re-explained every time | 4-layer persistent memory retains decisions and discoveries across sessions, so the agent improves with each run |
| **Produce a mergeable artifact** | Output is a chat response; turning it into a commit requires manual work | Every run produces a file diff, a trajectory record, and a structured report, ready to review and merge |


For a full feature-by-feature breakdown, see [Comparison](#-comparison).



## 🎬 Demo

<div align="center">
  <i> 👉 Full walkthrough video: <a href="https://vimeo.com/1178451557" target="_blank">watch on Vimeo</a></i>
</div>

<br/>

[![CLI Overview](./opencook-preview.png)](https://vimeo.com/1178451557)



## 🕹 Quick Start

**Prerequisites:** Python 3.10+, an LLM API key, and the source tree of your target project.

### Step 1: Install

```bash
git clone https://github.com/weAIDB/OpenCook.git && cd OpenCook
uv venv --python 3.10 && source .venv/bin/activate
uv pip install -e .
```

> **Windows:** use `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

This installs OpenCook and its runtime dependencies, then exposes the
`opencook` command for all CLI workflows.

### Step 2: Update After Local Changes

OpenCook is a pure Python CLI project. In most day-to-day development, you do **not**
need to rebuild or reinstall.

**When you do NOT need to reinstall**

- You changed Python source files under `code_agent/`
- You edited prompts, markdown docs, or skill files under `open-cookbook/`
- You changed YAML config files such as `opencook_config.yaml`

In these cases, just run the command again:

```bash
opencook --help
opencook interactive --config-file opencook_config.yaml
```

**When you SHOULD reinstall**

- You changed `pyproject.toml`
- You changed package dependencies
- You changed the CLI entrypoint or script name
- You want to refresh the installed `opencook` launcher after packaging changes

Reinstall with:

```bash
uv pip install -e .
```

To force a clean reinstall:

```bash
uv pip uninstall opencook
uv pip install -e .
```

If `opencook` is not found after reinstalling, use the module entrypoint as a fallback:

```bash
python -m code_agent --help
```

### Step 3: Configure

Use the bundled `opencook_config.yaml` as your starting point, or copy it before editing:

```bash
cp opencook_config.yaml my_opencook_config.yaml
```

Then update the database paths, model providers, and agent settings for your environment.
See `opencook_config.yaml` in the repository root for the full supported structure.

You can also point the CLI at a config file globally:

```bash
export OPENCOOK_CONFIG_FILE=my_opencook_config.yaml
```

### Step 4: Cook

```bash
# Interactive TUI (recommended)
opencook interactive --config-file my_opencook_config.yaml

# Headless single task
opencook run "Implement the BOOL_AND aggregate function for SQLite" --config-file my_opencook_config.yaml

# Start an interactive PostgreSQL session
opencook interactive --database postgresql --config-file my_opencook_config.yaml
```



## 📚 Features

<table>
<tr>
<td width="50%">
**🧩 Project-Local Personalization**  
Per-project recipe roots and rule files are auto-discovered and injected. Every agent knows the local conventions before writing a single line.

</td>
<td width="50%">

**🔄 Built-in Delivery Loop**  
Fixed Plan → Code → Test cycle with self-correcting subagents. Iterates until the patch compiles and tests pass.

</td>
</tr>
<tr>
<td>

**🧠 4-Layer Memory Stack**  
Working → Episodic → Project → Long-Term. Agents stay coherent across hours-long personalization sessions.

</td>
<td>

**📖 Dynamic Recipe System**  
Drop a recipe package with a `SKILL.md` entry file anywhere on the discovery path. Auto-loaded at runtime, injected at the right agent stage.

</td>
</tr>
<tr>
<td>

**🔎 Context-Aware Injection**  
Understands cross-unit dependencies and build conventions. Features land exactly where they belong.

</td>
<td>

**📋 Patch-Oriented Traceability**  
Every run emits a trajectory record, a file diff, and a structured report. Full reproducibility out of the box.

</td>
</tr>
</table>



## 🏗️ How It Works

OpenCook runs a deterministic **Plan → Code → Test** pipeline through three specialized agents:

| Agent | Role | Key Behavior |
|---|---|---|
| **CodeAgent** | Orchestrator | Writes code, coordinates subagents, self-corrects on failure |
| **PlanAgent** | Read-only Scoper | Decomposes the task; locates files, entry points, and conventions |
| **TestAgent** | Validator | Compiles, runs test suite, reports failures back to CodeAgent |



## 🗄️ Reference Case: Database Functions

> **Why database functions?**  
> Implementing a C/C++ function inside a production database, subject to its memory model, type system, and build infrastructure, is one of the hardest personalization tasks imaginable. It is the strongest proof that the approach works.

The table below shows where each database's extension entry points live:

| Database | Language | Entry Point |
|---|---|---|
| **SQLite** | C | `FuncDef aBuiltinFunc[]` in `func.c` |
| **PostgreSQL** | C | `builtins.h` + `.c` implementation |
| **DuckDB** | C++ | `ScalarFunctionSet` / `FunctionFactory` |
| **ClickHouse** | C++ | `FunctionFactory::instance().registerFunction<>()` |

> The same Plan → Code → Test loop applies to any codebase domain. Database engines are just the hardest kitchen to cook in.



## 🔌 Compatibility

**Works alongside any coding agent**

| Agent | Integration Path |
|---|---|
| Claude Code | Recipes via `CLAUDE.md` or recipe packages on the skills path |
| Codex | `AGENTS.md` context + MCP |
| OpenCode / OpenClaw | Plugin package path |
| TRAE | Project-local context |
| Any agent | Serialize recipes into the system prompt |

**Supports any LLM provider**

| Provider | Example Models |
|---|---|
| Anthropic | Claude Sonnet 4.6, Claude Opus 4.6 |
| OpenAI | GPT-4o, GPT-5 |
| Google | Gemini 2.5 Pro |
| DeepSeek | DeepSeek-V3, DeepSeek-Coder |
| Zhipu / Qwen / DouBao | GLM-5, Qwen3-Coder |
| Azure / OpenRouter | Any deployed endpoint |
| Ollama | Any local model (fully offline) |

Each agent role (Plan / Code / Test) can use a **different model and provider** independently.



## 🔪 Extend OpenCook

### Write a Recipe

OpenCook ships a built-in recipe library at `open-cookbook/` in the repository root. User-created recipes go in `.opencook/skills/` inside your project directory and are auto-discovered at runtime. Both locations use `SKILL.md` as the entry file:

```
.opencook/skills/
└── my-feature-recipe/
    ├── SKILL.md        ← trigger · context · steps
    └── references/     ← optional supporting docs
```

```markdown
---
name: my-feature-recipe
description: Teaches agents how to implement X in codebase Y
triggers: [implement X, add X feature]
---

## Context
[Conventions, pitfalls, and patterns the agent must know]

## Steps
1. Locate the entry point by searching for ...
2. Follow the registration pattern at ...
3. Verify with ...
```

### Add a new domain

| Step | What to implement |
|---|---|
| 1 | **Template**: code scaffolding for the target language/framework |
| 2 | **Test harness**: domain-specific build and execution runner |
| 3 | **Extraction utils**: symbol/schema extraction helpers |
| 4 | **Recipes**: domain knowledge in `SKILL.md` recipe packages |

### Add an LLM provider

Implement `BaseClient` in `code_agent/utils/llm_clients/` and register it in `LLMClient`. No other changes needed.



## 🆚 Comparison

How OpenCook compares on each of the five personalization requirements covered above.

| Feature | **OpenCook** | Claude Code | Codex | OpenCode | OpenClaw |
|---|:---:|:---:|:---:|:---:|:---:|
| Project rules injection | ✦ | ✦ | ~ | ✦ | ✦ |
| Targeted change scoping | ✦ | ~ | ~ | ~ | ~ |
| Built-in delivery loop (Plan→Code→Test) | ✦ | ~ | ~ | ~ | ~ |
| Persistent multi-layer memory | ✦ | ~ | ~ | ~ | ~ |
| Structured patch output | ✦ | ~ | ~ | ~ | ~ |

<sub>✦ Clearly present &nbsp; ~ Present but narrower or less structured &nbsp; ✗ Not observed in inspected source</sub>



## 🤔 FAQ

<details>
<summary><b>Is OpenCook only for databases?</b></summary>
<br/>
No. Database function implementation is the reference benchmark because it demands deep C/C++ internals knowledge, making it the hardest test of our personalization thesis. The Plan → Code → Test loop and recipe system are fully domain-agnostic. We are actively expanding to other codebase domains.
</details>

<details>
<summary><b>How is this different from just prompting Claude Code or Codex?</b></summary>
<br/>
A generic prompt gives breadth, not depth. OpenCook's recipe system encodes the <em>exact</em> project conventions, registration patterns, pitfalls, and verification steps for your domain. Combined with a fixed delivery loop and multi-layer memory, the agent is far less likely to hallucinate conventions or leave the patch broken.
</details>

<details>
<summary><b>Which LLM gives the best results?</b></summary>
<br/>
For complex C/C++ internals: Claude Sonnet/Opus and GPT-4o class models. For cost-effective alternatives: DeepSeek-Coder and Qwen3-Coder. Models below ~32B may struggle with deep project conventions.
</details>

<details>
<summary><b>How does the self-correction loop work?</b></summary>
<br/>
TestAgent captures compiler output and test failures as structured tool results and feeds them back to CodeAgent. CodeAgent patches iteratively until all checks pass or the step budget is exhausted.
</details>


## 📋 Roadmap

- [ ] **Open Benchmark**: public leaderboard of personalization tasks across databases and domains
- [ ] **Broader Domain Support**: kernel modules, language runtimes, compiler backends
- [ ] **Parallel Cooking**: concurrent PlanAgent/TestAgent pairs for batch personalization
- [ ] **Web UI**: browser-based session dashboard
- [ ] **Fine-Tuned Models**: domain-specific models trained on successful trajectories
- [ ] **MCP Server**: expose the recipe + memory system as an MCP endpoint for any agent



## 👫 Community

We welcome contributions of all kinds: new recipes, domain backends, LLM clients, bug reports, and ideas.

- **GitHub**: [github.com/weAIDB/OpenCook](https://github.com/weAIDB/OpenCook)
- **Issues / PRs**: open an issue or pull request
- **Discussions**: [GitHub Discussions](https://github.com/weAIDB/OpenCook/discussions)



## 📒 Citation

```bibtex
@article{zhou2026dbcooker,
  author       = {Wei Zhou and Xuanhe Zhou and Qikang He and Guoliang Li and Bingsheng He and Quanqing Xu and Fan Wu},
  title        = {Automating
Database-Native Function Code Synthesis with LLMs},
  journal      = {Proc. {ACM} Manag. Data},
  volume       = {3},
  number       = {4},
  pages        = {141:1--141:26},
  year         = {2026}
}
```



## 📝 License

MIT License. See [LICENSE](LICENSE) for details.
