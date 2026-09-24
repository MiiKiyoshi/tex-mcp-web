"""Configuration loading and validation for tex_mcp_web."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PORT = 8765

# The sources a paper is written in. A paper written in Markdown or plain text adds its
# own kind; a LaTeX paper does not, because the notes and summaries that live beside it
# are not its source and listing them as editable only gets in the reviewer's way.
DEFAULT_WATCH = ("*.tex", "*.bib")


def default_watch(main: str) -> list[str]:
    """The watch patterns for a paper whose top-level source is *main*."""
    patterns = list(DEFAULT_WATCH)
    own = Path(main).suffix
    if own and f"*{own}" not in patterns:
        patterns.append(f"*{own}")
    return patterns


@dataclass
class Config:
    """Runtime configuration for a tex-mcp-web project.

    Loaded from .tex-mcp-web.yaml or constructed programmatically.
    Used by the server and compiler to determine watch behavior.

    Attributes:
        main: Main file to compile (relative to project directory).
        dir: Project directory, relative to the config file's folder or absolute.
            Unset means the config file's own folder.
        watch: Glob patterns for the source files the page offers and follows.
        ignore: Glob patterns for files to exclude from watching.
        compiler: Compiler command ("auto", "latexmk", "pdflatex", etc.).
        port: HTTP server port.
        config_path: Path to .tex-mcp-web.yaml file (used to resolve watch_dir).
    """

    main: str
    dir: str | None = None
    watch: list[str] = field(default_factory=lambda: list(DEFAULT_WATCH))
    ignore: list[str] = field(default_factory=list)
    compiler: str = "auto"
    port: int = DEFAULT_PORT
    config_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_path: Path | None = None) -> "Config":
        """Create Config from dictionary."""
        return cls(
            main=data.get("main", "main.tex"),
            dir=data.get("dir"),
            watch=data.get("watch", default_watch(data["main"])),
            ignore=data.get("ignore", []),
            compiler=data.get("compiler", "auto"),
            port=data.get("port", DEFAULT_PORT),
            config_path=config_path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary (for API responses)."""
        d: dict[str, Any] = {
            "main": self.main,
            "watch": self.watch,
            "ignore": self.ignore,
            "compiler": self.compiler,
            "port": self.port,
        }
        if self.dir is not None:
            d["dir"] = self.dir
        return d


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
        "watch": watch or default_watch(main),
        "ignore": ignore or ["*_backup.tex"],
        "compiler": compiler,
        "port": port,
    }

    with open(output_path, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

    return output_path


def get_watch_dir(config: Config) -> Path:
    """Get the directory to watch based on config."""
    if config.config_path:
        base = config.config_path.parent
        return (base / config.dir).resolve() if config.dir else base
    return Path.cwd()


def get_main_file(config: Config) -> Path:
    """Get the absolute path to the main TeX file."""
    watch_dir = get_watch_dir(config)
    return watch_dir / config.main
