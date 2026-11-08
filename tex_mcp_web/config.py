"""Configuration loading and validation for tex_mcp_web."""

import os
import re
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
        references: Folder of reference papers converted to Markdown, relative to the
            config file's folder or absolute. Unset means no list of their terms.
        guideline: The writing guideline the agent reads before editing, by its folder's
            name under the user's guidelines or the installed ones. Unset means none.
        translation_language: Language the page's translation panel shows the source in,
            from ``translation: {language: ...}``. Unset means no translation panel.
        translation_instructions: Extra rules the translator follows, such as a field's
            terms to keep in English, from ``translation: {instructions: ...}``.
        overleaf_sync: Keep the paper's files and that project's the same, both ways.
        overleaf: The paper's Overleaf project, by its link, whose comments the page
            brings in with the reviewer's own session cookie. Unset means none.
        config_path: Path to .tex-mcp-web.yaml file (used to resolve watch_dir).
    """

    main: str
    dir: str | None = None
    watch: list[str] = field(default_factory=lambda: list(DEFAULT_WATCH))
    ignore: list[str] = field(default_factory=list)
    compiler: str = "auto"
    port: int = DEFAULT_PORT
    references: str | None = None
    guideline: str | None = None
    translation_language: str | None = None
    translation_instructions: str | None = None
    overleaf: str | None = None
    overleaf_sync: bool = False
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
            references=data.get("references"),
            guideline=_guideline(data),
            translation_language=_translation(data).get("language"),
            translation_instructions=_translation(data).get("instructions"),
            overleaf=_overleaf(data),
            overleaf_sync=_overleaf_flag(data, "overleaf_sync"),
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
        if self.references is not None:
            d["references"] = self.references
        if self.guideline is not None:
            d["guideline"] = self.guideline
        if self.translation_language is not None:
            d["translation"] = {"language": self.translation_language}
            if self.translation_instructions is not None:
                d["translation"]["instructions"] = self.translation_instructions
        if self.overleaf is not None:
            d["overleaf"] = self.overleaf
        if self.overleaf_sync:
            d["overleaf_sync"] = True
        return d


def _guideline(data: dict[str, Any]) -> str | None:
    """``guideline:`` as a guideline folder's name."""
    raw = data.get("guideline")
    if raw is None:
        return None
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        raise ValueError("guideline must name a folder under guidelines/, such as neutral-paper")
    return raw


def _overleaf(data: dict[str, Any]) -> str | None:
    """``overleaf:`` as a project's link, checked the way the fetch reads it."""
    raw = data.get("overleaf")
    if raw is None or not str(raw).strip():
        return None
    from .overleaf import project_of

    project_of(str(raw))
    return str(raw).strip()


def _translation(data: dict[str, Any]) -> dict[str, str]:
    """``translation:`` as {language, instructions}, each a non-empty string when set."""
    raw = data.get("translation")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("translation must be a mapping such as {language: Korean}")
    out = {}
    for key in ("language", "instructions"):
        value = raw.get(key)
        if value is not None and str(value).strip():
            out[key] = str(value).strip()
    if "instructions" in out and "language" not in out:
        raise ValueError("translation.instructions needs translation.language")
    return out


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
        "guideline": "neutral-paper",
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


def get_references_dir(config: Config) -> Path | None:
    """The folder of reference papers in Markdown, or None when none is configured."""
    if config.references is None:
        return None
    base = config.config_path.parent if config.config_path else Path.cwd()
    return (base / config.references).resolve()


def get_main_file(config: Config) -> Path:
    """Get the absolute path to the main TeX file."""
    watch_dir = get_watch_dir(config)
    return watch_dir / config.main


def _overleaf_flag(data: dict[str, Any], key: str) -> bool:
    """A switch that sends to the Overleaf project: off unless it says true."""
    raw = data.get(key, False)
    if not isinstance(raw, bool):
        raise ValueError(f"{key} must be true or false, not {raw!r}")
    if raw and _overleaf(data) is None:
        raise ValueError(f"{key} needs overleaf: to name the project")
    return raw


def user_config_dir() -> Path:
    """The reviewer's own tex-mcp-web folder, one for every paper: their Overleaf cookie and
    their guidelines are kept there."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tex-mcp-web"


def guideline_roots() -> dict[str, Path]:
    """The folders guidelines come from. A name in the user's folder is used instead of the
    installed one of the same name."""
    return {"built_in": Path(__file__).resolve().parent / "guidelines", "user": user_config_dir() / "guidelines"}


def list_guidelines() -> dict[str, Any]:
    """Each guideline folder with its entries (None when the folder is missing), the folder
    each name is read from, and entries without a GUIDELINE.md."""
    choices: dict[str, Any] = {}
    used: dict[str, str] = {}
    incomplete: list[str] = []
    for source, root in guideline_roots().items():
        entries = (sorted(path.name for path in root.iterdir()
                          if path.is_dir() and not path.name.startswith((".", "_")))
                   if root.is_dir() else None)
        choices[source] = {"dir": str(root), "entries": entries}
        for name in entries or []:
            used[name] = source          # the user's folder comes second and wins, as in lookup
            if not (root / name / "GUIDELINE.md").is_file():
                incomplete.append(f"{source}/{name}")
    return {**choices, "used": used, "incomplete": incomplete}


def _guideline_file(name: str) -> Path:
    roots = guideline_roots()
    if (roots["user"] / name).is_dir():
        return (roots["user"] / name / "GUIDELINE.md").resolve()
    return roots["built_in"] / name / "GUIDELINE.md"


def _extended_name(path: Path) -> str | None:
    """The guideline a file extends, from front matter it opens with: a ``---`` line,
    ``extends: <name>``, and a closing ``---`` line."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end < 0:
        raise ValueError(f"{path}: front matter has no closing ---")
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict) or set(front) - {"extends"}:
        raise ValueError(f"{path}: front matter takes only extends")
    name = front.get("extends")
    if name is not None and not (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name)):
        raise ValueError(f"{path}: extends must name a guideline folder")
    return name


def get_guideline_files(config: Config) -> list[Path]:
    """The configured guideline and every guideline it extends, base first, so the agent
    reads the general rules before the ones that add to or override them. A file that is
    missing raises."""
    files: list[Path] = []
    name = config.guideline
    while name is not None:
        path = _guideline_file(name)
        if path in files:
            raise ValueError(f"guideline {name} extends itself")
        if not path.is_file():
            raise FileNotFoundError(f"guideline not found: {path}")
        files.insert(0, path)
        name = _extended_name(path)
    return files
