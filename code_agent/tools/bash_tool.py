# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import asyncio
import os
import re
import shlex
import shutil
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from code_agent.tools.base import Tool, ToolCallArguments, ToolError, ToolExecResult, ToolParameter
from code_agent.utils.encoding_utils import decode_bytes
from code_agent.utils.demo_mode import DEMO_BASH_OUTPUT_DELAY, DEMO_RECORDING_MODE


class _BashSession:
    """A session of a bash shell."""

    _started: bool
    _timed_out: bool

    command: str = "/bin/bash"
    # TEMP(demo): faster polling keeps shell output moving during screen capture.
    _output_delay: float = DEMO_BASH_OUTPUT_DELAY if DEMO_RECORDING_MODE else 0.2
    _timeout: float = 60.0  # seconds
    _sentinel: str = ",,,,bash-command-exit-__ERROR_CODE__-banner,,,,"  # `__ERROR_CODE__` will be replaced by `$?` or `!errorlevel!` later

    def __init__(self, timeout: float = _timeout) -> None:
        self._timeout = timeout
        self._started = False
        self._timed_out = False
        self._process: asyncio.subprocess.Process | None = None
        self._use_cmd_shell: bool = False
        self._use_powershell: bool = False

    @staticmethod
    def _quote_powershell(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _strip_outer_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
        return value

    @classmethod
    def _powershell_list_literal(cls, values: list[str]) -> str:
        if not values:
            return "@()"
        return "@(" + ", ".join(cls._quote_powershell(value) for value in values) + ")"

    def _translate_grep_command(self, command: str) -> str | None:
        fallback_text: str | None = None
        fallback_match = re.search(r"\s*\|\|\s*echo\s+(.+?)\s*$", command, flags=re.IGNORECASE)
        if fallback_match:
            fallback_text = self._strip_outer_quotes(fallback_match.group(1).strip())
            command = command[:fallback_match.start()].rstrip()

        head_limit: int | None = None
        head_match = re.search(r"\|\s*head\s+-([0-9]+)\s*$", command, flags=re.IGNORECASE)
        if head_match:
            head_limit = int(head_match.group(1))
            command = command[:head_match.start()].rstrip()

        command = re.sub(r"\s*2>\s*/dev/null\b", "", command, flags=re.IGNORECASE).strip()

        working_dir: str | None = None
        cd_match = re.match(
            r'^\s*cd(?:\s+/d)?\s+("[^"]+"|\'[^\']+\'|\S+)\s*(?:&&|;)\s*(.+)$',
            command,
            flags=re.IGNORECASE,
        )
        if cd_match:
            working_dir = self._strip_outer_quotes(cd_match.group(1).strip())
            command = cd_match.group(2).strip()

        if not re.match(r"^grep(?:\s|$)", command, flags=re.IGNORECASE):
            return None

        tokens = shlex.split(command, posix=False)
        if not tokens or tokens[0].lower() != "grep":
            return None

        recursive = False
        include_patterns: list[str] = []
        pattern: str | None = None
        search_paths: list[str] = []
        i = 1
        while i < len(tokens):
            token = tokens[i]
            lower = token.lower()
            if lower.startswith("--include="):
                include_patterns.append(self._strip_outer_quotes(token.split("=", 1)[1]))
            elif lower == "--include" and i + 1 < len(tokens):
                i += 1
                include_patterns.append(self._strip_outer_quotes(tokens[i]))
            elif lower == "-e" and i + 1 < len(tokens):
                i += 1
                pattern = self._strip_outer_quotes(tokens[i])
            elif token.startswith("-") and pattern is None:
                for flag in token[1:]:
                    if flag in ("r", "R"):
                        recursive = True
            elif pattern is None:
                pattern = self._strip_outer_quotes(token)
            else:
                search_paths.append(self._strip_outer_quotes(token))
            i += 1

        if not pattern:
            return None

        derived_paths: list[str] = []
        for raw_path in search_paths or ["."]:
            if any(char in raw_path for char in "*?[]"):
                base_path = os.path.dirname(raw_path) or "."
                leaf_pattern = os.path.basename(raw_path)
                derived_paths.append(base_path)
                if leaf_pattern:
                    include_patterns.append(leaf_pattern)
            else:
                derived_paths.append(raw_path)

        deduped_paths = list(dict.fromkeys(derived_paths or ["."]))
        deduped_includes = list(dict.fromkeys(include_patterns))

        file_query_parts = [
            f"Get-ChildItem -Path {self._powershell_list_literal(deduped_paths)}",
        ]
        if recursive:
            file_query_parts.append("-Recurse")
        file_query_parts.append("-File")
        if deduped_includes:
            file_query_parts.append(f"-Include {self._powershell_list_literal(deduped_includes)}")
        file_query_parts.append("-ErrorAction SilentlyContinue")

        match_pipeline = (
            " ".join(file_query_parts)
            + f" | Select-String -Pattern {self._quote_powershell(pattern)}"
        )
        format_pipeline = (
            "$__dbcooker_matches"
            + (f" | Select-Object -First {head_limit}" if head_limit else "")
            + ' | ForEach-Object { "{0}:{1}:{2}" -f $_.Path, $_.LineNumber, $_.Line.TrimEnd() }'
        )

        translated_parts: list[str] = []
        if working_dir:
            translated_parts.append(f"Set-Location {self._quote_powershell(working_dir)}")
        translated_parts.append(f"$__dbcooker_matches = @({match_pipeline})")
        if fallback_text is not None:
            translated_parts.append(
                "if ($__dbcooker_matches.Count -eq 0) "
                + "{ Write-Output "
                + self._quote_powershell(fallback_text)
                + " } else { "
                + format_pipeline
                + " }"
            )
        else:
            translated_parts.append(format_pipeline)

        return "\n".join(translated_parts)

    def _normalize_powershell_command(self, command: str) -> str:
        normalized = command.replace("\r\n", "\n").replace("\r", "\n").strip()
        translated_grep = self._translate_grep_command(normalized)
        if translated_grep:
            return translated_grep
        normalized = re.sub(r"\s*2>\s*/dev/null\b", " 2>$null", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\|\s*head\s+-([0-9]+)", r"| Select-Object -First \1", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*&&\s*", "; ", normalized)
        return normalized

    async def start(self) -> None:
        if self._started:
            return

        # Windows compatibility: os.setsid not available

        if os.name != "nt":  # Unix-like systems
            self._process = await asyncio.create_subprocess_shell(
                self.command,
                shell=True,
                bufsize=0,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=os.setsid,
            )
        else:
            powershell_path = shutil.which("powershell") or shutil.which("pwsh")
            if powershell_path:
                self._process = await asyncio.create_subprocess_exec(
                    powershell_path,
                    "-NoLogo",
                    "-NoProfile",
                    "-NoExit",
                    "-Command",
                    "-",
                    bufsize=0,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._use_powershell = True
                self._use_cmd_shell = False
            else:
                self._process = await asyncio.create_subprocess_exec(
                    "cmd.exe",
                    "/q",
                    "/v:on",  # enable delayed expansion to allow `echo !errorlevel!`
                    bufsize=0,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._use_cmd_shell = True

        self._started = True

    async def stop(self) -> None:
        """Terminate the bash shell."""
        if not self._started:
            raise ToolError("Session has not started.")
        if self._process is None:
            return

        if self._process.returncode is None:
            # Process is still running: terminate it, close stdin so its read
            # end is not blocked, then drain remaining output.
            self._process.terminate()
            if self._process.stdin and not self._process.stdin.is_closing():
                self._process.stdin.close()
            await self._process.communicate()

        # Release the underlying IOCP transport regardless of whether the
        # process was still running or had already exited on its own.
        # On Windows, asyncio's ProactorEventLoop keeps pending Overlapped
        # handles on the pipe transport even after the process exits.  If those
        # handles are not explicitly closed before the event loop garbage-
        # collects them, Python emits:
        #   RuntimeError: <_overlapped.Overlapped ...> still has pending
        #   operation at deallocation, the process may crash
        transport = getattr(self._process, "_transport", None)
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass

    async def _wait_for_sentinel(
        self,
        sentinel_before: str,
        sentinel_after: str,
    ) -> tuple[str, int]:
        """Read stdout until the synthetic exit sentinel appears.

        Uses polling because directly awaiting EOF on stdout/stderr would block
        forever for an interactive shell session.
        """
        if self._process is None or self._process.stdout is None:
            raise ToolError("Session stdout is not available.")

        while True:
            await asyncio.sleep(self._output_delay)
            output: str = decode_bytes(self._process.stdout._buffer)  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
            if sentinel_before not in output:
                continue

            output, pivot, exit_banner = output.rpartition(sentinel_before)
            assert pivot

            error_code_str, pivot, _ = exit_banner.partition(sentinel_after)
            if not pivot or not error_code_str.isdecimal():
                continue

            return output, int(error_code_str)

    async def run(self, command: str) -> ToolExecResult:
        """Execute a command in the bash shell."""
        if not self._started or self._process is None:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return ToolExecResult(
                error=f"bash has exited with returncode {self._process.returncode}. tool must be restarted.",
                error_code=-1,
            )
        if self._timed_out:
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            )

        # we know these are not None because we created the process with PIPEs
        assert self._process.stdin
        assert self._process.stdout
        assert self._process.stderr

        sentinel_before, pivot, sentinel_after = self._sentinel.partition("__ERROR_CODE__")
        assert pivot == "__ERROR_CODE__"

        # send command to the process
        if self._use_powershell:
            command = self._normalize_powershell_command(command)
            sentinel_expr = self._sentinel.replace("__ERROR_CODE__", "$($__dbcooker_exit)")
            wrapped = (
                "$global:LASTEXITCODE = 0\n"
                + command
                + "\n"
                + "$__dbcooker_exit = if ($LASTEXITCODE -ne $null) { [int]$LASTEXITCODE } "
                  "elseif ($?) { 0 } else { 1 }\n"
                + f'Write-Output "{sentinel_expr}"\n'
            )
        elif self._use_cmd_shell:
            normalized_command = " ".join(command.replace("\r", "\n").splitlines())
            wrapped = (
                f"({normalized_command})& "
                f"echo {self._sentinel.replace('__ERROR_CODE__', '!errorlevel!')}\n"
            )
        else:
            wrapped = (
                "(\n"
                + command
                + f"\n); echo {self._sentinel.replace('__ERROR_CODE__', '$?')}\n"
            )
        self._process.stdin.write(wrapped.encode())
        await self._process.stdin.drain()

        # read output from the process, until the sentinel is found
        try:
            if hasattr(asyncio, "timeout"):
                async with asyncio.timeout(self._timeout):
                    output, error_code = await self._wait_for_sentinel(
                        sentinel_before,
                        sentinel_after,
                    )
            else:
                output, error_code = await asyncio.wait_for(
                    self._wait_for_sentinel(sentinel_before, sentinel_after),
                    timeout=self._timeout,
                )
        except asyncio.TimeoutError:
            self._timed_out = True
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            ) from None

        if output.endswith("\n"):  # pyright: ignore[reportUnknownMemberType]
            output = output[:-1]  # pyright: ignore[reportUnknownVariableType]

        error: str = decode_bytes(self._process.stderr._buffer)  # type: ignore[attr-defined] # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAttributeAccessIssue]
        if error.endswith("\n"):  # pyright: ignore[reportUnknownMemberType]
            error = error[:-1]  # pyright: ignore[reportUnknownVariableType]

        # clear the buffers so that the next output can be read correctly
        self._process.stdout._buffer.clear()  # type: ignore[attr-defined] # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        self._process.stderr._buffer.clear()  # type: ignore[attr-defined] # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

        return ToolExecResult(output=output, error=error, error_code=error_code)  # pyright: ignore[reportUnknownArgumentType]


class BashTool(Tool):
    """
    A tool that allows the agent to run bash commands.
    The tool parameters are defined by Anthropic and are not editable.
    """

    def __init__(self, model_provider: str | None = None):
        super().__init__(model_provider)
        self._session: _BashSession | None = None
        self._timeout: float = _BashSession._timeout  # default; overridden by set_timeout()

    def set_timeout(self, seconds: int | float) -> None:
        self._timeout = float(seconds)

    @override
    def get_model_provider(self) -> str | None:
        return self._model_provider

    @override
    def get_name(self) -> str:
        return "bash"

    @override
    def get_description(self) -> str:
        return """Run commands in a shell
* On Windows, commands run in PowerShell and should use PowerShell syntax.
* On Unix-like systems, commands run in bash syntax.
* When invoking this tool, the contents of the "command" parameter does NOT need to be XML-escaped.
* You have access to a mirror of common linux and python packages via apt and pip.
* State is persistent across command calls and discussions with the user.
* To inspect a particular line range of a file, e.g. lines 10-25, try 'sed -n 10,25p /path/to/the/file'.
* Please avoid commands that may produce a very large amount of output.
* Please run long lived commands in the background, e.g. 'sleep 10 &' or start a server in the background.
"""

    @override
    def get_parameters(self) -> list[ToolParameter]:
        # For OpenAI models, all parameters must be required=True
        # For other providers, optional parameters can have required=False
        restart_required = self.model_provider == "openai"

        return [
            ToolParameter(
                name="command",
                type="string",
                description="The bash command to run.",
                required=True,
            ),
            ToolParameter(
                name="restart",
                type="boolean",
                description="Set to true to restart the bash session.",
                required=restart_required,
            ),
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        if arguments.get("restart"):
            if self._session:
                await self._session.stop()
            self._session = _BashSession(timeout=self._timeout)
            await self._session.start()

            return ToolExecResult(output="tool has been restarted.")

        if self._session is None:
            try:
                self._session = _BashSession(timeout=self._timeout)
                await self._session.start()
            except Exception as e:
                return ToolExecResult(error=f"Error starting bash session: {e}", error_code=-1)

        command = str(arguments["command"]) if "command" in arguments else None
        if command is None:
            return ToolExecResult(
                error=f"No command provided for the {self.get_name()} tool",
                error_code=-1,
            )
        try:
            return await self._session.run(command)
        except Exception as e:
            timed_out = self._session._timed_out if self._session else False
            timeout_secs = int(self._session._timeout) if self._session else 60
            if timed_out:
                # Auto-restart so the next command doesn't also fail immediately.
                try:
                    await self._session.stop()
                except Exception:
                    pass
                self._session = _BashSession(timeout=self._timeout)
                await self._session.start()
                return ToolExecResult(
                    error=(
                        f"Command timed out after {timeout_secs} seconds. "
                        "The bash session has been restarted automatically.\n"
                        "To avoid repeated timeouts, consider:\n"
                        "1. Breaking the command into smaller steps\n"
                        "2. Running it in the background (append `&`) and checking output separately\n"
                        "3. Targeting a smaller scope (e.g., a single file, module, or target)"
                    ),
                    error_code=-1,
                )
            return ToolExecResult(error=f"Error running bash command: {e}", error_code=-1)

    @override
    async def close(self):
        """Properly close self._process."""
        if self._session:
            ret = await self._session.stop()
            self._session = None
            return ret
