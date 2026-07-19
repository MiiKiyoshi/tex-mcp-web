"""Configuration loading and validation for tex_mcp_web."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PORT = 8765


@dataclass
class Config:
    """Runtime configuration for a tex-mcp-web project.

    Loaded from .tex-mcp-web.yaml or constructed programmatically.
    Used by the server and compiler to determine watch behavior.

    Attributes:
        main: Main file to compile (relative to project directory).
        watch: Glob patterns for files that trigger recompilation.
        ignore: Glob patterns for files to exclude from watching.
        compiler: Compiler command ("auto", "latexmk", "pdflatex", etc.).
        auto_compile: Whether watched source changes trigger compilation.
        port: HTTP server port.
        config_path: Path to .tex-mcp-web.yaml file (used to resolve watch_dir).
    """

    main: str
    watch: list[str] = field(default_factory=lambda: ["*.tex", "*.bib", "*.md", "*.txt"])
    ignore: list[str] = field(default_factory=list)
    compiler: str = "auto"
    auto_compile: bool = False
    port: int = DEFAULT_PORT
    config_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_path: Path | None = None) -> "Config":
        """Create Config from dictionary."""
        if "auto_compile" not in data:
            data["auto_compile"] = False
        if not isinstance(data["auto_compile"], bool):
            raise ValueError("auto_compile must be true or false")
        return cls(
            main=data.get("main", "main.tex"),
            watch=data.get("watch", ["*.tex", "*.bib", "*.md", "*.txt"]),
            ignore=data.get("ignore", []),
            compiler=data.get("compiler", "auto"),
            auto_compile=data["auto_compile"],
            port=data.get("port", DEFAULT_PORT),
            config_path=config_path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary (for API responses)."""
        return {
            "main": self.main,
            "watch": self.watch,
            "ignore": self.ignore,
            "compiler": self.compiler,
            "auto_compile": self.auto_compile,
            "port": self.port,
        }


DEFAULT_CONFIG_NAME = ".tex-mcp-web.yaml"


def find_config(start_dir: Path | None = None) -> Path | None:
    """Find .tex-mcp-web.yaml in current or parent directories."""
    if start_dir is None:
        start_dir = Path.cwd()

    current = start_dir.resolve()
    while current != current.parent:
        config_path = current / DEFAULT_CONFIG_NAME
        if config_path.exists():
            return config_path
        current = current.parent

    return None


def load_config(path: Path | None = None, main_file: str | None = None) -> Config:
    """Load configuration from file or create default.

    Args:
        path: Explicit path to config file. If None, searches for .tex-mcp-web.yaml.
        main_file: Override main file from CLI argument.

    Returns:
        Config instance.
    """
    config_path = path
    data: dict[str, Any] = {}

    if config_path is None:
        config_path = find_config()

    if config_path and config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

    # CLI argument overrides config file
    if main_file:
        data["main"] = main_file

    # Default main file if not specified
    if "main" not in data:
        data["main"] = "main.tex"

    return Config.from_dict(data, config_path=config_path)


def create_config(
    main: str = "main.tex",
    watch: list[str] | None = None,
    ignore: list[str] | None = None,
    compiler: str = "auto",
    auto_compile: bool = False,
    port: int = DEFAULT_PORT,
    output_path: Path | None = None,
) -> Path:
    """Create a new .tex-mcp-web.yaml configuration file.

    Args:
        main: Main TeX file.
        watch: List of glob patterns to watch.
        ignore: List of glob patterns to ignore.
        compiler: Compiler to use.
        port: Server port.
        output_path: Where to write config. Defaults to ./.tex-mcp-web.yaml.

    Returns:
        Path to created config file.
    """
    if output_path is None:
        output_path = Path.cwd() / DEFAULT_CONFIG_NAME

    config_data = {
        "main": main,
        "watch": watch or ["*.tex", "*.bib", "*.md", "*.txt"],
        "ignore": ignore or ["*_backup.tex"],
        "compiler": compiler,
        "auto_compile": auto_compile,
        "port": port,
    }

    with open(output_path, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

    return output_path


def write_auto_compile(config_path: Path, enabled: bool) -> None:
    """Persist the automatic compilation mode without dropping other settings."""
    text = config_path.read_text()
    pattern = re.compile(
        r"^(auto_compile\s*:\s*)([^\s#]+)(\s*(?:#.*)?)$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) > 1:
        raise ValueError(f"{config_path} contains multiple auto_compile fields")
    value = "true" if enabled else "false"
    if matches:
        updated = pattern.sub(
            lambda match: f"{match.group(1)}{value}{match.group(3)}",
            text,
            count=1,
        )
    else:
        separator = "" if not text or text.endswith("\n") else "\n"
        updated = f"{text}{separator}auto_compile: {value}\n"
    config_path.write_text(updated)


def get_watch_dir(config: Config) -> Path:
    """Get the directory to watch based on config."""
    if config.config_path:
        return config.config_path.parent
    return Path.cwd()


def get_main_file(config: Config) -> Path:
    """Get the absolute path to the main TeX file."""
    watch_dir = get_watch_dir(config)
    return watch_dir / config.main
