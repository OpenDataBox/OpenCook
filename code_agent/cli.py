# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Command Line Interface for OpenCook."""

import asyncio
import logging
import os
import sys
import traceback
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from code_agent.agent import Agent
from code_agent.utils.cli import CLIConsole, ConsoleFactory, ConsoleMode, ConsoleType
from code_agent.utils.config import Config, AgentRunConfig, get_current_database

total_count = 0
TIMEOUT = 360 + 60 * 2
# TIMEOUT = 900

# Load environment variables
_ = load_dotenv()

console = Console()


def resolve_config_file(config_file: str) -> str:
    """
    Resolve config file with backward compatibility.
    First tries the specified file, then falls back to JSON if YAML doesn't exist.
    """
    if config_file.endswith(".yaml") or config_file.endswith(".yml"):
        yaml_path = Path(config_file)
        json_path = Path(config_file.replace(".yaml", ".json").replace(".yml", ".json"))
        if yaml_path.exists():
            return str(yaml_path)
        elif json_path.exists():
            console.print(f"[yellow]YAML config not found, using JSON config: {json_path}[/yellow]")
            return str(json_path)
        else:
            console.print(
                "[red]Error: Config file not found. Please specify a valid config file in the command line option --config-file[/red]"
            )
            sys.exit(1)
    else:
        return config_file


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """OpenCook - Project-specific personalization for coding agents."""
    pass


@cli.command("run")
@click.argument("task", required=False)
@click.option("--file", "-f", "file_path", help="Path to a file containing the task description.")
@click.option("--provider", "-p", help="LLM provider to use")
@click.option("--model", "-m", help="Specific model to use")
@click.option("--model-base-url", help="Base URL for the model API")
@click.option("--api-key", "-k", help="API key (or set via environment variable)")
@click.option("--max-steps", help="Maximum number of execution steps", type=int)
@click.option("--working-dir", "-w", help="Working directory for the agent")
@click.option("--must-patch", "-mp", is_flag=True, help="Whether to patch the code")
@click.option(
    "--config-file",
    help="Path to configuration file",
    default="opencook_config.yaml",
    envvar="OPENCOOK_CONFIG_FILE",
)
@click.option("--trajectory-file", "-t", help="Path to save trajectory file")
@click.option("--patch-path", "-pp", help="Path to patch file")
@click.option(
    "--console-type", "-ct", default="simple",
    type=click.Choice(["simple", "rich"], case_sensitive=False),
    help="Type of console to use (simple or rich)",
)
@click.option(
    "--agent-type", "-at",
    type=click.Choice(["code_agent", "plan_agent", "test_agent"], case_sensitive=False),
    help="Type of agent to use", default="code_agent",
)
def run_command(
        task, file_path, provider, model, model_base_url, api_key, max_steps,
        working_dir, must_patch, config_file, trajectory_file, patch_path,
        console_type, agent_type,
):
    """Run a task using the agent (CLI entry point)."""
    is_success, _, _ = run(
        task=task, file_path=file_path, patch_path=patch_path,
        provider=provider, model=model, model_base_url=model_base_url,
        api_key=api_key, max_steps=max_steps, working_dir=working_dir,
        must_patch=must_patch, config_file=config_file,
        trajectory_file=trajectory_file, console_type=console_type,
        agent_type=agent_type,
    )
    if not is_success:
        sys.exit(1)


def run(
        task: dict | None,
        file_path: str | None,
        patch_path: str,
        provider: str | None = None,
        model: str | None = None,
        model_base_url: str | None = None,
        api_key: str | None = None,
        max_steps: int | None = None,
        working_dir: str | None = None,
        must_patch: bool = False,
        config_file: str = "opencook_config.yaml",
        trajectory_file: str | None = None,
        console_type: str | None = "simple",
        agent_type: str | None = "code_agent",
):
    """
    Run the main OpenCook execution flow for a project-specific task.
    Args:
        tasks: the task that you want your agent to solve. This is required to be in the input
        model: the model expected to be use
        working_dir: the working directory of the agent. This should be set either in cli or in the config file

    Return:
        None (it is expected to be ended after calling the run function)
    """

    is_success, response, execution_time = False, "", 0.0

    # Apply backward compatibility for config file
    config_file = resolve_config_file(config_file)

    if file_path:
        if task:
            console.print(
                "[red]Error: Cannot use both a task string and the --file argument.[/red]"
            )
            # sys.exit(1)
            return is_success, response, execution_time
        try:
            task = Path(file_path).read_text()
        except FileNotFoundError:
            console.print(f"[red]Error: File not found: {file_path}[/red]")
            # sys.exit(1)
            return is_success, response, execution_time
    elif not task:
        console.print(
            "[red]Error: Must provide either a task string or use the --file argument.[/red]"
        )
        # sys.exit(1)
        return is_success, response, execution_time

    config = Config.create(
        config_file=config_file,
    ).resolve_config_values(
        provider=provider,
        model=model,
        model_base_url=model_base_url,
        api_key=api_key,
        max_steps=max_steps,
    )

    if not agent_type:
        console.print("[red]Error: agent_type is required.[/red]")
        # sys.exit(1)
        return is_success, response, execution_time

    # Create CLI Console
    if console_type is None:
        cli_console = None
        selected_console_type = None
    else:
        console_mode = ConsoleMode.RUN
        if console_type:
            selected_console_type = (
                ConsoleType.SIMPLE if console_type.lower() == "simple" else ConsoleType.RICH
            )
        else:
            selected_console_type = ConsoleFactory.get_recommended_console_type(console_mode)

        cli_console = ConsoleFactory.create_console(
            console_type=selected_console_type, mode=console_mode
        )

        # For rich console in RUN mode, set the initial task
        if (selected_console_type is not None and selected_console_type
                == ConsoleType.RICH and hasattr(cli_console, "set_initial_task")):
            cli_console.set_initial_task(task)

    # Change working directory if specified
    if working_dir:
        try:
            Path(working_dir).mkdir(parents=True, exist_ok=True)
            # os.chdir(working_dir)
            console.print(f"[blue]Changed working directory to: {working_dir}[/blue]")
            working_dir = os.path.abspath(working_dir)
        except Exception as e:
            console.print(f"[red]Error changing directory: {e}[/red]")
            # sys.exit(1)
            return is_success, response, execution_time
    else:
        working_dir = os.getcwd()
        console.print(f"[blue]Using current directory as working directory: {working_dir}[/blue]")

    # Ensure working directory is an absolute path
    if not Path(working_dir).is_absolute():
        console.print(
            f"[red]Working directory must be an absolute path: {working_dir}, it should start with `/`[/red]"
        )
        # sys.exit(1)
        return is_success, response, execution_time

    # Initialise to None so the finally can safely iterate even if Agent()
    # raises before all three wrappers are created.
    code_agent = None
    plan_agent = None
    test_agent = None

    # Outer finally ensures executor threads are released on every exit path:
    # normal return, TimeoutError, KeyboardInterrupt, and unexpected Exception.
    # Agent creation is inside the try so construction failures are also covered.
    try:
        code_agent = Agent(
            agent_type,
            config,
            trajectory_file,
            cli_console,
        )
        trajectory_recorder = code_agent.trajectory_recorder
        plan_agent = Agent("plan_agent", config, trajectory_recorder, cli_console)
        test_agent = Agent("test_agent", config, trajectory_recorder, cli_console)
        code_agent.agent.initialize_subagent(plan_agent, test_agent)

        try:
            os.chdir(working_dir)
        except Exception as e:
            console.print(f"[red]Error changing directory: {e}[/red]")
            # sys.exit(1)
            return is_success, response, execution_time

        task_args = {
            "project_path": working_dir,
            "must_patch": "true" if must_patch else "false",
            "patch_path": patch_path,
        }

        # Set up agent context for rich console if applicable
        if selected_console_type == ConsoleType.RICH and hasattr(cli_console, "set_agent_context"):
            cli_console.set_agent_context(code_agent, config.code_agent, config_file, trajectory_file)

        execution = asyncio.run(
            asyncio.wait_for(code_agent.run(task, task_args), timeout=TIMEOUT)
        )

        console.print(f"\n[green]Trajectory saved to: {code_agent.trajectory_file}[/green]")

        is_success = execution.success
        response = execution.final_result
        execution_time = execution.execution_time

        return is_success, response, execution_time

    except asyncio.TimeoutError:
        print(f"Task timed out after {TIMEOUT} seconds and was cancelled.")
        return is_success, response, execution_time

    except KeyboardInterrupt:
        console.print("\n[yellow]Task execution interrupted by user[/yellow]")
        if code_agent is not None:
            console.print(f"[blue]Partial trajectory saved to: {code_agent.trajectory_file}[/blue]")
        # sys.exit(1)
        return is_success, response, execution_time

    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        console.print(traceback.format_exc())
        if code_agent is not None:
            console.print(f"[blue]Trajectory saved to: {code_agent.trajectory_file}[/blue]")
        # sys.exit(1)
        return is_success, response, execution_time

    finally:
        for _a in (code_agent, plan_agent, test_agent):
            if _a is not None:
                _a.close()



def _has_recent_session(store: "SessionStore") -> bool:
    """Return True if there is at least one non-archived session in the store."""
    return any(not s.archived for s in store.list())


@cli.command()
@click.option("--session", "-s", "session_id", default=None, help="Resume an existing session by ID")
@click.option("--new", "force_new", is_flag=True, help="Force creation of a new session")
@click.option("--name", "-n", "title", default="", help="Session title")
@click.option("--database", "-db", default=None, help="Database type (sqlite/postgresql/...)")
@click.option("--working-dir", "-w", default=None, help="Working directory (defaults to cwd)")
@click.option("--provider", "-p", help="LLM provider to use")
@click.option("--model", "-m", help="Specific model to use")
@click.option("--model-base-url", help="Base URL for the model API")
@click.option("--api-key", "-k", help="API key (or set via environment variable)")
@click.option(
    "--config-file",
    help="Path to configuration file",
    default="opencook_config.yaml",
    envvar="OPENCOOK_CONFIG_FILE",
)
@click.option("--max-steps", help="Maximum number of execution steps", type=int, default=None)
@click.option(
    "--console-type",
    "-ct",
    type=click.Choice(["auto", "textual", "chat", "simple"], case_sensitive=False),
    default="auto",
    help="Console type: auto (default) | textual | chat | simple",
)
def interactive(
        session_id: str | None = None,
        force_new: bool = False,
        title: str = "",
        database: str | None = None,
        working_dir: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        model_base_url: str | None = None,
        api_key: str | None = None,
        config_file: str = "opencook_config.yaml",
        max_steps: int | None = None,
        console_type: str = "auto",
):
    """Start an interactive session with OpenCook."""
    import hashlib
    from code_agent.session import SessionMeta, SessionRunner, SessionStore

    config_file = resolve_config_file(config_file)
    config = Config.create(config_file=config_file).resolve_config_values(
        provider=provider,
        model=model,
        model_base_url=model_base_url,
        api_key=api_key,
        max_steps=max_steps,
    )

    if config.code_agent is None:
        console.print("[red]Error: code_agent configuration is required in the config file.[/red]")
        sys.exit(1)

    working_dir = os.path.abspath(working_dir or os.getcwd())
    try:
        os.chdir(working_dir)
    except Exception as e:
        console.print(f"[red]Error changing directory: {e}[/red]")
        sys.exit(1)
    database = database or get_current_database(config_file)

    # Global session storage, keyed by project directory hash (mirrors memory layout).
    project_hash = hashlib.md5(working_dir.encode()).hexdigest()[:12]
    store = SessionStore(root=Path.home() / ".opencook" / "sessions" / project_hash)

    # Determine which session to use.
    if session_id and not force_new:
        session = store.get(session_id)
        if session is None:
            # Global fallback: scan all project stores under ~/.opencook/sessions/
            sessions_root = Path.home() / ".opencook" / "sessions"
            for proj_dir in sorted(sessions_root.iterdir()) if sessions_root.exists() else []:
                if proj_dir.is_dir() and proj_dir != store._root:
                    candidate = SessionStore(root=proj_dir)
                    found = candidate.get(session_id)
                    if found is not None:
                        store = candidate
                        session = found
                        # chdir to the session's original working directory.
                        if not os.path.isdir(session.cwd):
                            console.print(f"[red]Session working directory no longer exists: {session.cwd}[/red]")
                            sys.exit(1)
                        os.chdir(session.cwd)
                        break
        if session is None:
            console.print(f"[red]Session {session_id} not found.[/red]")
            sys.exit(1)
        console.print(f"Resuming session: {session.session_id} ({session.title or 'untitled'})")
    elif not force_new and _has_recent_session(store):
        session = store.list()[0]
        console.print(f"Resuming session: {session.session_id} ({session.title or 'untitled'})")
    else:
        session = store.create(SessionMeta(
            cwd=working_dir,
            database=database,
            title=title,
            model=config.code_agent.model.model,
        ))
        console.print(f"New session created: {session.session_id}")

    cli_console = ConsoleFactory.create_interactive_console(
        console_type=console_type.lower(),
    )

    runner = SessionRunner(
        config=config,
        store=store,
        session=session,
        cli_console=cli_console,
    )

    if hasattr(cli_console, "run_app"):
        # Redirect all logging output to a file before the Textual TUI starts.
        # Without this, log records (LLM errors, runner exceptions, etc.) reach
        # Python's lastResort handler and are written directly to sys.stderr,
        # which bypasses the TUI and appears as raw text on the terminal —
        # especially noticeable during and after a force exit.
        _log_dir = Path.home() / ".opencook" / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(_log_dir / "session.log"),
            level=logging.WARNING,
            format="%(asctime)s %(name)s %(levelname)s: %(message)s",
            force=True,  # replace any pre-existing handlers (e.g. stderr)
        )
        # TextualConsole owns the event loop; it drives runner.run() internally.
        asyncio.run(cli_console.run_app(runner))
    else:
        asyncio.run(runner.run())


async def _run_simple_interactive_loop(
        agent: Agent,
        cli_console: CLIConsole,
        code_agent_config: AgentRunConfig,
        config_file: str,
        trajectory_file: str | None,
):
    """Run the interactive loop for simple console."""
    while True:
        try:
            task = cli_console.get_task_input()
            if task is None:
                console.print("[green]Goodbye![/green]")
                break

            if task.lower() == "help":
                console.print(
                    Panel(
                        """[bold]Available Commands:[/bold]

• Type any task description to execute it
• 'status' - Show agent status
• 'clear' - Clear the screen
• 'exit' or 'quit' - End the session""",
                        title="Help",
                        border_style="yellow",
                    )
                )
                continue

            working_dir = cli_console.get_working_dir_input()

            if task.lower() == "status":
                console.print(
                    Panel(
                        f"""[bold]Provider:[/bold] {agent.agent_config.model.model_provider.provider}
    [bold]Model:[/bold] {agent.agent_config.model.model}
    [bold]Available Tools:[/bold] {len(agent.agent.tools)}
    [bold]Config File:[/bold] {config_file}
    [bold]Working Directory:[/bold] {os.getcwd()}""",
                        title="Agent Status",
                        border_style="blue",
                    )
                )
                continue

            if task.lower() == "clear":
                console.clear()
                continue

            # Set up trajectory recording for this task
            console.print(f"[blue]Trajectory will be saved to: {trajectory_file}[/blue]")

            task_args = {
                "project_path": working_dir or os.getcwd(),
                "must_patch": "false",
                "patch_path": None,
            }

            # Wrap user input as an interactive task dict so that
            # tool-call approval is triggered (_interactive_approval flag).
            task_dict = {
                "task_kind": "interactive_chat",
                "user_input": task,
                "func_name": task[:80],  # satisfies agent.py field normalisation
                "database": "sqlite",
            }

            # Execute the task
            console.print(f"\n[blue]Executing task: {task}[/blue]")

            # Start console and execute task
            console_task = asyncio.create_task(cli_console.start())
            execution_task = asyncio.create_task(agent.run(task_dict, task_args))

            # Wait for execution to complete
            _ = await execution_task
            _ = await console_task

            console.print(f"\n[green]Trajectory saved to: {trajectory_file}[/green]")

        except KeyboardInterrupt:
            console.print("\n[yellow]Use 'exit' or 'quit' to end the session[/yellow]")
        except EOFError:
            console.print("\n[green]Goodbye![/green]")
            break
        except Exception as e:
            console.print(f"[red]Error: {escape(str(e))}[/red]")
            console.print(traceback.format_exc())


async def _run_rich_interactive_loop(
        agent: Agent,
        cli_console: CLIConsole,
        code_agent_config: AgentRunConfig,
        config_file: str,
        trajectory_file: str | None,
):
    """Run the interactive loop for rich console."""
    # Set up the agent in the rich console so it can handle task execution
    if hasattr(cli_console, "set_agent_context"):
        cli_console.set_agent_context(agent, code_agent_config, config_file, trajectory_file)

    # Start the console UI - this will handle the entire interaction
    await cli_console.start()


@cli.command()
@click.option(
    "--config-file",
    help="Path to configuration file",
    default="opencook_config.yaml",
    envvar="OPENCOOK_CONFIG_FILE",
)
@click.option("--provider", "-p", help="LLM provider to use")
@click.option("--model", "-m", help="Specific model to use")
@click.option("--model-base-url", help="Base URL for the model API")
@click.option("--api-key", "-k", help="API key (or set via environment variable)")
@click.option("--max-steps", help="Maximum number of execution steps", type=int)
def show_config(
        config_file: str,
        provider: str | None,
        model: str | None,
        model_base_url: str | None,
        api_key: str | None,
        max_steps: int | None,
):
    """Show current configuration settings."""
    # Apply backward compatibility for config file
    config_file = resolve_config_file(config_file)

    config_path = Path(config_file)
    if not config_path.exists():
        console.print(
            Panel(
                f"""[yellow]No configuration file found at: {config_file}[/yellow]

Using default settings and environment variables.""",
                title="Configuration Status",
                border_style="yellow",
            )
        )

    config = Config.create(
        config_file=config_file,
    ).resolve_config_values(
        provider=provider,
        model=model,
        model_base_url=model_base_url,
        api_key=api_key,
        max_steps=max_steps,
    )

    if config.code_agent:
        code_agent_config = config.code_agent
    else:
        console.print("[red]Error: code_agent configuration is required in the config file.[/red]")
        sys.exit(1)

    # Display general settings
    general_table = Table(title="General Settings")
    general_table.add_column("Setting", style="cyan")
    general_table.add_column("Value", style="green")

    general_table.add_row(
        "Default Provider", str(code_agent_config.model.model_provider.provider or "Not set")
    )
    general_table.add_row("Max Steps", str(code_agent_config.max_steps or "Not set"))

    console.print(general_table)

    # Display provider settings
    provider_config = code_agent_config.model.model_provider
    provider_table = Table(title=f"{provider_config.provider.title()} Configuration")
    provider_table.add_column("Setting", style="cyan")
    provider_table.add_column("Value", style="green")

    provider_table.add_row("Model", code_agent_config.model.model or "Not set")
    provider_table.add_row("Base URL", provider_config.base_url or "Not set")
    provider_table.add_row("API Version", provider_config.api_version or "Not set")
    provider_table.add_row(
        "API Key",
        f"Set ({provider_config.api_key[:4]}...{provider_config.api_key[-4:]})"
        if provider_config.api_key
        else "Not set",
    )
    provider_table.add_row("Max Tokens", str(code_agent_config.model.max_tokens))
    provider_table.add_row("Temperature", str(code_agent_config.model.temperature))
    provider_table.add_row("Top P", str(code_agent_config.model.top_p))

    if code_agent_config.model.model_provider.provider == "anthropic":
        provider_table.add_row("Top K", str(code_agent_config.model.top_k))

    console.print(provider_table)


@cli.command()
def tools():
    """Show available tools and their descriptions."""
    from .tools import tools_registry

    tools_table = Table(title="Available Tools")
    tools_table.add_column("Tool Name", style="cyan")
    tools_table.add_column("Description", style="green")

    for tool_name in tools_registry:
        try:
            tool = tools_registry[tool_name]()
            tools_table.add_row(tool.name, tool.description)
        except Exception as e:
            tools_table.add_row(tool_name, f"[red]Error loading: {e}[/red]")

    console.print(tools_table)


def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
