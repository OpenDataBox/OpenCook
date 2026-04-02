# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from pathlib import Path

from code_agent.utils.legacy_config import LegacyConfig
from code_agent.memory.config import MemoryConfig

# ---------------------------------------------------------------------------
# Database configuration
# ---------------------------------------------------------------------------

@dataclass
class DatabaseConfig:
    """Per-database configuration loaded from the 'databases' section of opencook_config.yaml."""
    install_folder: str = ""
    data_folder: str | None = None
    port: int | None = None          # only for server-based DBs (postgresql, clickhouse)
    user: str | None = None          # postgresql-specific
    bash_path: str | None = None     # postgresql-specific
    cpu_num: int | None = None       # postgresql / sqlite only


def _load_yaml_file(config_file: str) -> dict[str, Any]:
    """Load a YAML config file using UTF-8 so Windows locale defaults do not interfere."""
    with open(config_file, "r", encoding="utf-8-sig") as f:
        data = yaml.safe_load(f)
    return data or {}


def get_current_database(config_file: str | None = None) -> str:
    """Return the value of databases.default from opencook_config.yaml."""
    if config_file is None:
        config_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "opencook_config.yaml",
        )
    try:
        return _load_yaml_file(config_file).get("databases", {}).get("default", "sqlite")
    except Exception:
        return "sqlite"


def get_database_config(db: str, config_file: str | None = None) -> DatabaseConfig:
    """Load and return the DatabaseConfig for *db* from opencook_config.yaml.

    Falls back to the file next to the project root when *config_file* is not given.
    """
    if config_file is None:
        # code_agent/utils/config.py → project root is three levels up
        config_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "opencook_config.yaml",
        )
    try:
        raw: dict[str, Any] = _load_yaml_file(config_file).get("databases", {}).get(db, {})
    except Exception:
        raw = {}
    return DatabaseConfig(**{k: v for k, v in raw.items() if k in DatabaseConfig.__dataclass_fields__})


class ConfigError(Exception):
    pass


_REMOVED_AGENT_CONFIG_KEYS: frozenset[str] = frozenset({"enable_lakeview"})


def _normalize_agent_config(agent_name: str, agent_config: dict[str, Any]) -> dict[str, Any]:
    """Normalize per-agent YAML before constructing AgentRunConfig.

    Legacy keys for removed features are silently dropped so older configs keep
    working. Other unknown keys still raise a clear ConfigError.
    """
    valid_agent_keys = set(AgentRunConfig.__dataclass_fields__.keys())
    unknown_keys = set(agent_config) - valid_agent_keys - _REMOVED_AGENT_CONFIG_KEYS
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ConfigError(f"Unknown settings for {agent_name}: {unknown}")
    return {k: v for k, v in agent_config.items() if k in valid_agent_keys}


# ---------------------------------------------------------------------------
# Skills configuration
# ---------------------------------------------------------------------------

@dataclass
class SkillsConfig:
    """Configuration for the skill discovery system.

    standalone_project_roots: bare relative suffixes (e.g. ".opencook/skills")
        appended to each ancestor directory during walk-up discovery.
        Must NOT be resolved at parse time — stored verbatim.

    All other path fields are concrete paths resolved to absolute at parse time
    (expanduser + relative-to-config-dir for non-absolute paths).
    """

    enabled: bool = True

    # Relative suffixes walked up from project_path (stored verbatim, never resolved)
    standalone_project_roots: list[str] = field(
        default_factory=lambda: [".opencook/skills"]
    )

    # User-global roots scanned once (resolved to absolute at parse time)
    standalone_user_roots: list[str] = field(
        default_factory=lambda: ["~/.opencook/skills"]
    )

    # Extra root dirs — each child directory is one standalone skill package.
    # Each path points to a directory whose immediate children are skill packages.
    # Example: ["~/.claude/skills", "~/.agents/skills"] to also load skills
    # installed by Claude Code or opencode from their user-global directories.
    extra_standalone_paths: list[str] = field(default_factory=list)

    # Extra direct package paths — each path IS a skill root containing SKILL.md.
    # Example: ["~/.codex/skills/babysit-pr"] to load a single skill from Codex.
    extra_standalone_packages: list[str] = field(default_factory=list)

    # Maximum number of resource files (non-SKILL.md) indexed per skill package
    # at discovery time.  Indexed paths are listed in the skill tool output so
    # the model can locate and read bundled scripts and references via bash.
    # When the limit is hit, the tool output notes that the list is truncated.
    resource_limit: int = 50


@dataclass
class ModelProvider:
    """
    Model provider configuration. For official model providers such as OpenAI and Anthropic,
    the base_url is optional. api_version is required for Azure.
    """

    api_key: str
    provider: str
    base_url: str | None = None
    api_version: str | None = None


@dataclass
class ModelConfig:
    """
    Model configuration.
    """

    model: str
    model_provider: ModelProvider
    temperature: float
    top_p: float
    top_k: int
    parallel_tool_calls: bool
    max_retries: int
    max_tokens: int | None = None  # Legacy max_tokens parameter, optional
    context_window: int = 128_000  # Model context window size in tokens; used to tune compaction thresholds
    supports_tool_calling: bool = True
    candidate_count: int | None = None  # Gemini specific field
    stop_sequences: list[str] | None = None
    max_completion_tokens: int | None = None  # Azure OpenAI specific field

    def get_max_tokens_param(self) -> int:
        """Get the maximum tokens parameter value.Prioritizes max_completion_tokens, falls back to max_tokens if not available."""
        if self.max_completion_tokens is not None:
            return self.max_completion_tokens
        elif self.max_tokens is not None:
            return self.max_tokens
        else:
            # Return default value if neither is set
            return 4096

    def should_use_max_completion_tokens(self) -> bool:
        """Determine whether to use the max_completion_tokens parameter.Primarily used for Azure OpenAI's newer models (e.g., gpt-5)."""
        return (
                self.max_completion_tokens is not None
                and self.model_provider.provider == "azure"
                and ("gpt-5" in self.model or "o3" in self.model or "o4-mini" in self.model)
        )

    def resolve_config_values(
            self,
            *,
            model_providers: dict[str, ModelProvider] | None = None,
            provider: str | None = None,
            model: str | None = None,
            model_base_url: str | None = None,
            api_key: str | None = None,
    ):
        """
        When some config values are provided through CLI or environment variables,
        they will override the values in the config file.
        """
        self.model = str(resolve_config_value(cli_value=model, config_value=self.model))

        # If the user wants to change the model provider, they should either:
        # * Make sure the provider name is available in the model_providers dict;
        # * If not, base url and api key should be provided to register a new model provider.
        if provider:
            if model_providers and provider in model_providers:
                self.model_provider = model_providers[provider]
            elif api_key is None:
                raise ConfigError("To register a new model provider, an api_key should be provided")
            else:
                self.model_provider = ModelProvider(
                    api_key=api_key,
                    provider=provider,
                    base_url=model_base_url,
                )

        # Map providers to their environment variable names
        env_var_api_key = str(self.model_provider.provider).upper() + "_API_KEY"
        env_var_api_base_url = str(self.model_provider.provider).upper() + "_BASE_URL"

        resolved_api_key = resolve_config_value(
            cli_value=api_key,
            config_value=self.model_provider.api_key,
            env_var=env_var_api_key,
        )

        resolved_api_base_url = resolve_config_value(
            cli_value=model_base_url,
            config_value=self.model_provider.base_url,
            env_var=env_var_api_base_url,
        )

        if resolved_api_key:
            self.model_provider.api_key = str(resolved_api_key)

        if resolved_api_base_url:
            self.model_provider.base_url = str(resolved_api_base_url)


@dataclass
class EmbeddingConfig:
    """
    Embedding configuration.
    """

    model: str
    model_provider: ModelProvider


@dataclass
class MCPServerConfig:
    # For stdio transport
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None

    # For sse transport
    url: str | None = None

    # For streamable http transport
    http_url: str | None = None
    headers: dict[str, str] | None = None

    # For websocket transport
    tcp: str | None = None

    # Common
    timeout: int | None = None
    trust: bool | None = None

    # Metadata
    description: str | None = None


@dataclass
class AgentConfig:
    """
    Base class for agent configurations.
    """

    allow_mcp_servers: list[str]
    mcp_servers_config: dict[str, MCPServerConfig]
    max_steps: int
    run_steps: int
    model: ModelConfig
    tools: list[str]
    bash_timeout: int = 60  # seconds; auto-restart occurs on timeout


@dataclass
class AgentRunConfig(AgentConfig):
    """
    Trae agent configuration.
    """

    skills: SkillsConfig = field(default_factory=SkillsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tools: list[str] = field(
        default_factory=lambda: [
            "bash",
            "str_replace_based_edit_tool",
            "sequentialthinking",
            "task_done",
        ]
    )

    def resolve_config_values(
            self,
            *,
            max_steps: int | None = None,
    ):
        resolved_value = resolve_config_value(cli_value=max_steps, config_value=self.max_steps)
        if resolved_value:
            self.max_steps = int(resolved_value)



@dataclass
class VectorDatabaseConfig:
    """
    VectorDatabase configuration.
    """

    persist_directory: str


@dataclass
class Config:
    """
    Configuration class for agents, models and model providers.
    """
    db_name: dict = field(default_factory=lambda: {
        "postgresql": "PostgreSQL", "sqlite": "SQLite", "duckdb": "DuckDB"
    })
    embeddings: EmbeddingConfig | None = None
    vector_database: VectorDatabaseConfig | None = None

    model_providers: dict[str, ModelProvider] | None = None
    models: dict[str, ModelConfig] | None = None

    plan_agent: AgentRunConfig | None = None
    code_agent: AgentRunConfig | None = None
    test_agent: AgentRunConfig | None = None

    @classmethod
    def create(
            cls,
            *,
            config_file: str | None = None,
            config_string: str | None = None,
    ) -> "Config":
        if config_file and config_string:
            raise ConfigError("Only one of config_file or config_string should be provided")

        # Parse YAML config from file or string
        try:
            if config_file is not None:
                if config_file.endswith(".json"):
                    return cls.create_from_legacy_config(config_file=config_file)
                yaml_config = _load_yaml_file(config_file)
            elif config_string is not None:
                yaml_config = yaml.safe_load(config_string) or {}
            else:
                raise ConfigError("No config file or config string provided")
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError(f"Error reading config file: {e}") from e
        except yaml.YAMLError as e:
            raise ConfigError(f"Error parsing YAML config: {e}") from e

        config = cls()

        # Parse vector database configurations
        # config_vector_database = VectorDatabaseConfig(**yaml_config.get("vector_database", None))
        # config.vector_database = config_vector_database


        # Parse model providers
        model_providers = yaml_config.get("model_providers", None)
        if model_providers is not None and len(model_providers.keys()) > 0:
            config_model_providers: dict[str, ModelProvider] = {}
            for model_provider_name, model_provider_config in model_providers.items():
                config_model_providers[model_provider_name] = ModelProvider(**model_provider_config)
            config.model_providers = config_model_providers
        else:
            raise ConfigError("No model providers provided")

        # Parse models and populate model_provider fields
        models = yaml_config.get("models", None)
        if models is not None and len(models.keys()) > 0:
            config_models: dict[str, ModelConfig] = {}
            for model_name, model_config in models.items():
                if model_config["model_provider"] not in config_model_providers:
                    raise ConfigError(f"Model provider {model_config['model_provider']} not found")
                config_models[model_name] = ModelConfig(**model_config)
                config_models[model_name].model_provider = config_model_providers[
                    model_config["model_provider"]
                ]
            config.models = config_models
        else:
            raise ConfigError("No models provided")

        # Parse embedding configurations
        # config_embedding = EmbeddingConfig(**yaml_config.get("embeddings", None))
        # config_embedding.model_provider = config_model_providers[config_embedding.model_provider]
        # config.embeddings = config_embedding

        mcp_servers_config = {
            k: MCPServerConfig(**v) for k, v in yaml_config.get("mcp_servers", {}).items()
        }
        allow_mcp_servers = yaml_config.get("allow_mcp_servers", [])

        # Parse top-level skills config (not per-agent; injected into each agent after creation).
        # standalone_project_roots are stored verbatim (relative suffixes used in walk-up).
        # All other path fields are resolved to absolute at parse time.
        _config_dir = Path(config_file).parent if config_file else Path.cwd()

        def _resolve_path(p: str) -> str:
            path = Path(p).expanduser()
            if not path.is_absolute():
                path = (_config_dir / path).resolve()
            return str(path)

        _concrete_path_keys = {
            "standalone_user_roots",
            "extra_standalone_paths",
            "extra_standalone_packages",
        }

        skills_raw = yaml_config.get("skills", {})
        if isinstance(skills_raw, dict):
            valid_keys = set(SkillsConfig.__dataclass_fields__.keys())
            normalized: dict = {}
            for k, v in skills_raw.items():
                if k not in valid_keys:
                    continue
                if k in _concrete_path_keys and isinstance(v, list):
                    normalized[k] = [_resolve_path(p) for p in v]
                else:
                    normalized[k] = v  # standalone_project_roots: stored verbatim
            skills_config = SkillsConfig(**normalized)
        else:
            skills_config = SkillsConfig()

        # Parse top-level memory config (not per-agent; injected into each agent after creation).
        memory_raw = yaml_config.get("memory", {})
        if isinstance(memory_raw, dict):
            valid_memory_keys = set(MemoryConfig.__dataclass_fields__.keys())
            memory_config = MemoryConfig(**{
                k: v for k, v in memory_raw.items() if k in valid_memory_keys
            })
        else:
            memory_config = MemoryConfig()

        # Parse agents
        agents = yaml_config.get("agents", None)
        if agents is not None and len(agents.keys()) > 0:
            for agent_name, agent_config in agents.items():
                normalized_agent_config = _normalize_agent_config(agent_name, agent_config)
                agent_model_name = agent_config.get("model", None)
                if agent_model_name is None:
                    raise ConfigError(f"No model provided for {agent_name}")
                try:
                    agent_model = config_models[agent_model_name]
                except KeyError as e:
                    raise ConfigError(f"Model {agent_model_name} not found") from e

                match agent_name:
                    case "plan_agent":
                        agent_run_config = AgentRunConfig(
                            **normalized_agent_config,
                            mcp_servers_config=mcp_servers_config,
                            allow_mcp_servers=allow_mcp_servers,
                        )
                        agent_run_config.model = agent_model
                        config.plan_agent = agent_run_config

                    case "code_agent":
                        agent_run_config = AgentRunConfig(
                            **normalized_agent_config,
                            mcp_servers_config=mcp_servers_config,
                            allow_mcp_servers=allow_mcp_servers,
                        )
                        agent_run_config.model = agent_model
                        config.code_agent = agent_run_config

                    case "test_agent":
                        agent_run_config = AgentRunConfig(
                            **normalized_agent_config,
                            mcp_servers_config=mcp_servers_config,
                            allow_mcp_servers=allow_mcp_servers,
                        )
                        agent_run_config.model = agent_model
                        config.test_agent = agent_run_config

                    case _:
                        raise ConfigError(f"Unknown agent: {agent_name}")

                # Inject the top-level skills and memory configs into every agent.
                # Both are parsed at the top level (not per-agent) so that
                # AgentRunConfig(**agent_config) does not see a raw dict for
                # these fields — it always gets properly typed config objects.
                agent_run_config.skills = skills_config
                agent_run_config.memory = memory_config
        else:
            raise ConfigError("No agent configs provided")
        return config

    def resolve_config_values(
            self,
            *,
            provider: str | None = None,
            model: str | None = None,
            model_base_url: str | None = None,
            api_key: str | None = None,
            max_steps: int | None = None,
    ):
        if self.plan_agent:
            self.plan_agent.resolve_config_values(
                max_steps=max_steps,
            )
            self.plan_agent.model.resolve_config_values(
                model_providers=self.model_providers,
                provider=provider,
                model=model,
                model_base_url=model_base_url,
                api_key=api_key,
            )

        if self.code_agent:
            self.code_agent.resolve_config_values(
                max_steps=max_steps,
            )
            self.code_agent.model.resolve_config_values(
                model_providers=self.model_providers,
                provider=provider,
                model=model,
                model_base_url=model_base_url,
                api_key=api_key,
            )

        if self.test_agent:
            self.test_agent.resolve_config_values(
                max_steps=max_steps,
            )
            self.test_agent.model.resolve_config_values(
                model_providers=self.model_providers,
                provider=provider,
                model=model,
                model_base_url=model_base_url,
                api_key=api_key,
            )

        return self

    @classmethod
    def create_from_legacy_config(
            cls,
            *,
            legacy_config: LegacyConfig | None = None,
            config_file: str | None = None,
    ) -> "Config":
        if legacy_config and config_file:
            raise ConfigError("Only one of legacy_config or config_file should be provided")

        if config_file:
            legacy_config = LegacyConfig(config_file)
        elif not legacy_config:
            raise ConfigError("No legacy_config or config_file provided")

        model_provider = ModelProvider(
            api_key=legacy_config.model_providers[legacy_config.default_provider].api_key,
            base_url=legacy_config.model_providers[legacy_config.default_provider].base_url,
            api_version=legacy_config.model_providers[legacy_config.default_provider].api_version,
            provider=legacy_config.default_provider,
        )

        model_config = ModelConfig(
            model=legacy_config.model_providers[legacy_config.default_provider].model,
            model_provider=model_provider,
            max_tokens=legacy_config.model_providers[legacy_config.default_provider].max_tokens,
            temperature=legacy_config.model_providers[legacy_config.default_provider].temperature,
            top_p=legacy_config.model_providers[legacy_config.default_provider].top_p,
            top_k=legacy_config.model_providers[legacy_config.default_provider].top_k,
            parallel_tool_calls=legacy_config.model_providers[
                legacy_config.default_provider
            ].parallel_tool_calls,
            max_retries=legacy_config.model_providers[legacy_config.default_provider].max_retries,
            candidate_count=legacy_config.model_providers[
                legacy_config.default_provider
            ].candidate_count,
            stop_sequences=legacy_config.model_providers[
                legacy_config.default_provider
            ].stop_sequences,
        )
        mcp_servers_config = {
            k: MCPServerConfig(**vars(v)) for k, v in legacy_config.mcp_servers.items()
        }
        agent_run_config = AgentRunConfig(
            max_steps=legacy_config.max_steps,
            run_steps=legacy_config.run_steps,
            model=model_config,
            allow_mcp_servers=legacy_config.allow_mcp_servers,
            mcp_servers_config=mcp_servers_config,
        )

        return cls(
            plan_agent=agent_run_config,
            code_agent=agent_run_config,
            test_agent=agent_run_config,
            model_providers={
                legacy_config.default_provider: model_provider,
            },
            models={
                "default_model": model_config,
            },
        )


def resolve_config_value(
        *,
        cli_value: int | str | float | None,
        config_value: int | str | float | None,
        env_var: str | None = None,
) -> int | str | float | None:
    """Resolve configuration value with priority: CLI > ENV > Config > Default."""
    if cli_value is not None:
        return cli_value

    if env_var and os.getenv(env_var):
        return os.getenv(env_var)

    if config_value is not None:
        return config_value

    return None
