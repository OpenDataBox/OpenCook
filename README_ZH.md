<div align="center">
  <h1><img src="./OpenCook Logo.png" alt="OpenCook logo" width="120" align="middle" />&nbsp;OpenCook</h1>
  <p><b>面向 Coding Agent 的项目级个性化层</b></p>
  <p><i>"从一个通用项目出发，最终落到与你代码库严丝合缝的解决方案。"</i></p>
</div>

<div align="center">
  <a href="./README.md">English</a> &nbsp;|&nbsp; <b>简体中文</b>
  <br></br>
</div>

<div align="center">

  [![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
  [![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
  [![Multi-Agent](https://img.shields.io/badge/Multi--Agent-Plan·Code·Test-F97316?style=flat-square)]()
  [![Memory](https://img.shields.io/badge/Memory-4%20Layers-7C3AED?style=flat-square)]()
  [![LLM](https://img.shields.io/badge/LLM-Any%20Provider-00D4FF?style=flat-square)]()

</div>

<div align="center">
  <a href="#news">新闻</a> &nbsp;•&nbsp;
  <a href="#introduction">简介</a> &nbsp;•&nbsp;
  <a href="#features">特性</a> &nbsp;•&nbsp;
  <a href="#comparison">对比</a> &nbsp;•&nbsp;
  <a href="#demo">演示</a> &nbsp;•&nbsp;
  <a href="#quick-start">快速开始</a> &nbsp;•&nbsp;
  <a href="#extend-opencook">扩展 OpenCook</a> &nbsp;•&nbsp;
  <a href="#faq">常见问题</a> &nbsp;•&nbsp;
  <a href="#citation">引用</a>
</div>
<div align="center">
  <br></br>
  <b>⭐ 如果这个项目对你有帮助，欢迎在 GitHub 上 Star 支持我们！</b>
</div>



<a id="news"></a>

## 🗞️ 新闻

> - **\[04 / 2026\]** **OpenCook v1.0** 已正式发布。这是首个专为编码代理打造、面向具体项目的个性化层，演示视频见 [YouTube](https://www.youtube.com/watch?v=JmgN2mUS2Qk)。我们目前正在准备可复用的编码菜谱集，欢迎社区贡献！🔔 👫
> - **\[02 / 2026\]:** 我们的论文 "*Automating Database-Native Function Code Synthesis with LLMs*" 已被 [SIGMOD 2026](https://arxiv.org/abs/2604.06231) 接收！🎉 🎉 🎉


<a id="introduction"></a>

## ✨ 简介

Coding Agent 很强大，但它们本质上仍是通用型工具。它们能够浏览你的代码库，却很难做到**深度个性化**：在几乎无需手把手指导的情况下，注入一个符合内部约定、能够通过构建系统、通过回归测试，并且可以直接作为可合并补丁提交到生产环境的功能。

**OpenCook 正是缺失的这一层。** 它为任意编码代理补上三个项目本地原语：

- **Recipes（配方）**：按步骤编写的领域指南，精确告诉代理如何在你的特定代码库中实现某项功能
- **Rules（规则）**：项目级约束文件，用来编码代理必须遵守的约定、风格和不变量
- **Memory（记忆）**：四层记忆栈（Working → Episodic → Project → Long-Term），让代理在长会话中始终保持一致性

### 为什么需要 OpenCook？

通用编码代理（Claude Code、Codex、OpenCode）默认把每个项目都当成同一种问题处理。真正的个性化远不止如此。OpenCook 针对它们尚未满足的五项关键要求给出了解法：

| 个性化真正需要什么 | Claude Code / Codex / OpenCode | OpenCook |
|---|---|---|
| **理解你项目的规则** | 每次会话都要重新推断约定和入口点 | Recipes + Rules 在落笔前就把精确约定、注册模式和约束注入给代理 |
| **准确知道该改哪里** | 修改范围往往过宽，常漏掉必要依赖，或误碰无关代码 | PlanAgent 会在编码开始前精确界定需要修改的文件与入口点 |
| **验证结果是否真正适配** | 生成代码后即停止；编译和测试需要你手动完成 | 固定的 Plan → Code → Test 循环会一直运行，直到补丁编译成功并通过全部测试 |
| **记住它已经学到的东西** | 每次运行上下文都会重置，同一项目知识反复解释 | 四层持久化记忆会跨会话保留决策与发现，代理会随着使用不断变“更懂项目” |
| **产出可直接合并的成果** | 输出通常只是聊天回复，落地成提交仍需人工整理 | 每次运行都会产出文件 diff、轨迹记录和结构化报告，便于审阅与合并 |

<a id="features"></a>

## 📚 特性

<table>
<tr>

<td width="50%">

🧩 **项目本地个性化**  
按项目自动发现并注入 recipe 根目录与规则文件。代理在写下第一行代码之前，就已经知道本地约定。

</td>
<td width="50%">

**🔄 内置交付闭环**  
固定的 Plan → Code → Test 流程配合可自纠错的子代理，持续迭代，直到补丁编译通过并通过测试。

</td>
</tr>
<tr>
<td>

**🧠 四层记忆栈**  
Working → Episodic → Project → Long-Term。即使是持续数小时的个性化会话，代理也能保持上下文一致。

</td>
<td>

**📖 动态 Recipe 系统**  
只需在发现路径上的任意位置放入一个以 `SKILL.md` 为入口的 recipe 包，运行时即可自动加载，并在合适的代理阶段注入。

</td>
</tr>
<tr>
<td>

**🔎 上下文感知注入**  
理解跨模块依赖与构建约定，让功能精确落在它应当落入的位置。

</td>
<td>

**📋 面向补丁的可追踪性**  
每次运行都会输出轨迹记录、文件 diff 与结构化报告，开箱即具备完整复现能力。

</td>
</tr>
</table>

<a id="comparison"></a>

## 🆚 对比

下表基于上文五项个性化要求，对 OpenCook 与其他方案进行对照。

| 能力 | **OpenCook** | Claude Code | Codex | OpenCode | OpenClaw |
|---|:---:|:---:|:---:|:---:|:---:|
| 项目规则注入 | ✦ | ✦ | ~ | ✦ | ✦ |
| 精确变更范围界定 | ✦ | ~ | ~ | ~ | ~ |
| 内置交付闭环（Plan→Code→Test） | ✦ | ~ | ~ | ~ | ~ |
| 持久化多层记忆 | ✦ | ~ | ~ | ~ | ~ |
| 结构化补丁输出 | ✦ | ~ | ~ | ~ | ~ |

<sub>✦ 明确具备 &nbsp; ~ 具备但能力更窄或结构化程度较低 &nbsp; ✗ 在已检查的源码中未观察到</sub>


<a id="demo"></a>

## 🎬 演示

<div align="center">
  <i> 👉 完整演示视频：<a href="https://www.youtube.com/watch?v=JmgN2mUS2Qk" target="_blank">点击前往 YouTube 观看！</a></i>
</div>


<br/>

[![CLI Overview](./opencook-preview.png)](https://www.youtube.com/watch?v=JmgN2mUS2Qk)



<a id="quick-start"></a>

## 🕹 快速开始

**前置条件：** Python 3.10+、可用的 LLM API Key，以及目标项目的源码目录。

### 第 1 步：安装

```bash
git clone https://github.com/weAIDB/OpenCook.git && cd OpenCook
uv venv --python 3.10 && source .venv/bin/activate
uv pip install -e .
```

> **Windows：** 请将 `source .venv/bin/activate` 替换为 `.venv\Scripts\activate`。

这一步会安装 OpenCook 及其运行时依赖，并暴露统一的 CLI 命令 `opencook`。

### 第 2 步：本地修改后的更新方式

OpenCook 是一个纯 Python CLI 项目。在日常开发中，大多数情况下你**不需要**重新构建或重新安装。

**以下情况不需要重新安装**

- 你修改了 `code_agent/` 下的 Python 源码
- 你修改了 `open-cookbook/` 下的 prompts、Markdown 文档或 skill 文件
- 你修改了 `opencook_config.yaml` 等 YAML 配置文件

这些情况下，直接重新执行命令即可：

```bash
opencook --help
opencook interactive --config-file opencook_config.yaml
```

**以下情况应当重新安装**

- 你修改了 `pyproject.toml`
- 你修改了包依赖
- 你修改了 CLI 入口点或脚本名称
- 你希望在打包相关改动后刷新已安装的 `opencook` 启动器

重新安装命令如下：

```bash
uv pip install -e .
```

如需强制执行一次干净重装：

```bash
uv pip uninstall opencook
uv pip install -e .
```

如果重装后仍找不到 `opencook`，可以退回到模块入口方式：

```bash
python -m code_agent --help
```

### 第 3 步：配置

可以直接以仓库附带的 `opencook_config.yaml` 为起点，或者先复制一份再修改：

```bash
cp opencook_config.yaml my_opencook_config.yaml
```

然后根据你的运行环境更新数据库路径、模型提供商和代理设置。
完整配置结构请参见仓库根目录下的 `opencook_config.yaml`。

你也可以通过环境变量全局指定配置文件：

```bash
export OPENCOOK_CONFIG_FILE=my_opencook_config.yaml
```

### 第 4 步：开始烹饪

```bash
# 交互式 TUI（推荐）
opencook interactive --config-file my_opencook_config.yaml

# 无界面单任务模式
opencook run "Implement the BOOL_AND aggregate function for SQLite" --config-file my_opencook_config.yaml

# 启动一个交互式 PostgreSQL 会话
opencook interactive --database postgresql --config-file my_opencook_config.yaml
```


## 🏗️ 工作机制

OpenCook 通过三个专门化代理执行一个确定性的 **Plan → Code → Test** 流水线：

| 代理 | 角色 | 关键行为 |
|---|---|---|
| **CodeAgent** | 编排者 | 编写代码、协调子代理，并在失败后执行自纠错 |
| **PlanAgent** | 只读范围界定器 | 拆解任务，定位文件、入口点与项目约定 |
| **TestAgent** | 验证者 | 编译、运行测试套件，并将失败结果反馈给 CodeAgent |



## 🗄️ 参考案例：数据库函数

> **为什么选数据库函数？**  
> 在生产级数据库内部实现一个受内存模型、类型系统和构建基础设施约束的 C/C++ 函数，是最困难的个性化任务之一。这是证明该方法有效性的强力案例。

下表展示了各数据库扩展入口点所在的位置：

| 数据库 | 语言 | 入口点 |
|---|---|---|
| **SQLite** | C | `FuncDef aBuiltinFunc[]` in `func.c` |
| **PostgreSQL** | C | `builtins.h` + `.c` implementation |
| **DuckDB** | C++ | `ScalarFunctionSet` / `FunctionFactory` |
| **ClickHouse** | C++ | `FunctionFactory::instance().registerFunction<>()` |

> 同样的 Plan → Code → Test 闭环适用于任何代码库领域。数据库引擎只是最难“下厨”的厨房之一。



## 🔌 兼容性

**可与任意编码代理协同使用**

| 代理 | 集成路径 |
|---|---|
| Claude Code | 通过 `CLAUDE.md` 或 skills 路径上的 recipe 包注入 |
| Codex | `AGENTS.md` 上下文 + MCP |
| OpenCode / OpenClaw | 插件包路径 |
| TRAE | 项目本地上下文 |
| 任意代理 | 将 recipes 序列化后注入 system prompt |

**支持任意 LLM 提供商**

| 提供商 | 示例模型 |
|---|---|
| Anthropic | Claude Sonnet 4.6, Claude Opus 4.6 |
| OpenAI | GPT-4o, GPT-5 |
| Google | Gemini 2.5 Pro |
| DeepSeek | DeepSeek-V3, DeepSeek-Coder |
| Zhipu / Qwen / DouBao | GLM-5, Qwen3-Coder |
| Azure / OpenRouter | 任意已部署端点 |
| Ollama | 任意本地模型（完全离线） |

Plan / Code / Test 三个代理角色都可以独立使用**不同的模型和不同的提供商**。



<a id="extend-opencook"></a>

## 🔪 扩展 OpenCook

### 编写一个 Recipe

OpenCook 在仓库根目录自带一套内置 recipe 库，位于 `open-cookbook/`。用户自定义 recipe 则放在项目目录下的 `.opencook/skills/` 中，运行时会自动发现。两者都以 `SKILL.md` 作为入口文件：

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

### 新增一个领域

| 步骤 | 需要实现的内容 |
|---|---|
| 1 | **模板**：目标语言或框架的代码脚手架 |
| 2 | **测试 harness**：领域特定的构建与执行运行器 |
| 3 | **提取工具**：符号或模式提取辅助工具 |
| 4 | **Recipes**：以 `SKILL.md` recipe 包形式组织的领域知识 |

### 新增一个 LLM 提供商

在 `code_agent/utils/llm_clients/` 中实现 `BaseClient`，并在 `LLMClient` 中完成注册即可，无需其他改动。


<a id="faq"></a>

## 🤔 常见问题

<details>
<summary><b>OpenCook 只适用于数据库吗？</b></summary>
<br/>
不是。数据库函数实现之所以被选为参考基准，是因为它要求对 C/C++ 内部机制有很深的理解，因此能成为检验我们“个性化”论点的高强度测试。Plan → Code → Test 闭环与 recipe 系统本身是完全领域无关的。我们正在积极扩展到其他代码库领域。
</details>

<details>
<summary><b>它和直接给 Claude Code 或 Codex 写 prompt 有什么不同？</b></summary>
<br/>
通用 prompt 解决的是“广度”，不是“深度”。OpenCook 的 recipe 系统编码的是你所在领域中<em>精确</em>的项目约定、注册模式、常见陷阱和验证步骤。再配合固定交付闭环与多层记忆后，代理幻觉式地臆造约定、或者留下不可用补丁的概率会显著降低。
</details>

<details>
<summary><b>哪种 LLM 的效果最好？</b></summary>
<br/>
对于复杂的 C/C++ 内部实现任务，Claude Sonnet/Opus 和 GPT-4o 级别模型表现更稳。若考虑成本效率，DeepSeek-Coder 和 Qwen3-Coder 是不错的替代方案。参数规模低于约 32B 的模型，往往难以稳定掌握深层项目约定。
</details>

<details>
<summary><b>自纠错闭环是如何工作的？</b></summary>
<br/>
TestAgent 会把编译器输出和测试失败结果整理为结构化工具结果，再反馈给 CodeAgent。随后 CodeAgent 会持续迭代补丁，直到所有检查通过，或者用尽步骤预算。
</details>


## 📋 路线图

- [ ] **开放基准**：面向数据库与更多领域的个性化任务公开排行榜
- [ ] **更广泛的领域支持**：内核模块、语言运行时、编译器后端
- [ ] **并行烹饪**：面向批量个性化任务的并发 PlanAgent / TestAgent 组合
- [ ] **Web UI**：基于浏览器的会话仪表盘
- [ ] **微调模型**：基于成功轨迹训练的领域专用模型
- [ ] **MCP Server**：将 recipe + memory 系统以 MCP 端点形式开放给任意代理



## 👫 社区

欢迎各种形式的贡献：新的 recipes、领域后端、LLM 客户端、缺陷报告，以及任何想法。



<a id="citation"></a>

## 📒 引用

```bibtex
@article{zhou2026dbcooker,
  author       = {Wei Zhou and Xuanhe Zhou and Qikang He and Guoliang Li and Bingsheng He and Quanqing Xu and Fan Wu},
  title        = {Automating Database-Native Function Code Synthesis with LLMs},
  journal      = {Proc. {ACM} Manag. Data},
  volume       = {3},
  number       = {4},
  pages        = {141:1--141:26},
  year         = {2026}
}
```



## 📝 许可

本项目采用 MIT License。详见 [LICENSE](LICENSE)。
