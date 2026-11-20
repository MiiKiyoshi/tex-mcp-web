"""Configuration loading and validation for tex_mcp_web."""

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import shared_file

DEFAULT_PORT = 8765

# The sources a paper is written in. A paper written in Markdown or plain text adds its
# own kind; a LaTeX paper does not, because the notes and summaries that live beside it
# are not its source and listing them as editable only gets in the reviewer's way.
DEFAULT_WATCH = ("*.tex", "*.bib")


# The files of a paper a new file here may be, which Send adds to the Overleaf project: its
# sources and its figures. A file of a kind the project already holds is added too, and the
# paper's own compiled output never is.
DEFAULT_OVERLEAF_ADD = ("*.tex", "*.bib", "*.sty", "*.cls", "*.bst",
                        "*.png", "*.jpg", "*.jpeg", "*.pdf", "*.eps", "*.svg")


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
            name under the user's guidelines folder. Unset means none.
        translation_language: Language the page's translation panel shows the source in,
            from ``translation: {language: ...}``. Unset means no translation panel.
        translation_instructions: Extra rules the translator follows, such as a field's
            terms to keep in English, from ``translation: {instructions: ...}``.
        translation_command: The command line that translates, from ``translation:
            {command: ...}``. Unset means the one in the user's own config file, if any:
            see translation_command().
        overleaf_sync: Keep the paper's files and that project's the same, both ways.
        overleaf_picked: Call agent carries what was written on Overleaf only in the threads
            the reviewer picked for that press; what the reviewer writes on the page goes
            as always. Off means every thread from Overleaf asks the agent.
        overleaf: The paper's Overleaf project, by its link, whose comments the page
            brings in with the reviewer's own session cookie. Unset means none.
        overleaf_add: Glob patterns for the files only here that Send adds to the project,
            in a folder that follows it without git.
        update_check: The page asks the package index for the newest release, to offer
            Update in its menu.
        page_token: The page answers only a browser that opened its link with the
            reviewer's token, for a machine other people log in to.
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
    translation_command: list[str] | None = None
    overleaf: str | None = None
    overleaf_sync: bool = False
    overleaf_picked: bool = False
    overleaf_add: list[str] = field(default_factory=lambda: list(DEFAULT_OVERLEAF_ADD))
    page_token: bool = False
    update_check: bool = True
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
            translation_command=_translation(data).get("command"),
            overleaf=_overleaf(data),
            overleaf_sync=_overleaf_flag(data, "overleaf_sync"),
            overleaf_picked=_overleaf_flag(data, "overleaf_picked"),
            overleaf_add=_patterns(data, "overleaf_add", DEFAULT_OVERLEAF_ADD),
            page_token=_switch(data, "page_token"),
            update_check=_switch(data, "update_check", True),
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
            if self.translation_command is not None:
                d["translation"]["command"] = self.translation_command
        if self.overleaf is not None:
            d["overleaf"] = self.overleaf
        if self.overleaf_sync:
            d["overleaf_sync"] = True
        if self.overleaf_picked:
            d["overleaf_picked"] = True
        if self.overleaf_add != list(DEFAULT_OVERLEAF_ADD):
            d["overleaf_add"] = self.overleaf_add
        if self.page_token:
            d["page_token"] = True
        if not self.update_check:
            d["update_check"] = False
        return d


def _patterns(data: dict[str, Any], key: str, default: tuple[str, ...]) -> list[str]:
    """A list of glob patterns, or the default when unset."""
    raw = data.get(key)
    if raw is None:
        return list(default)
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ValueError(f"{key} must be a list of patterns such as ['*.tex', '*.png']")
    return [item.strip() for item in raw]


def _guideline(data: dict[str, Any]) -> str | None:
    """``guideline:`` as a guideline folder's name."""
    raw = data.get("guideline")
    if raw is None:
        return None
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        raise ValueError("guideline must name a folder under guidelines/")
    return raw


def _overleaf(data: dict[str, Any]) -> str | None:
    """``overleaf:`` as a project's link, checked the way the fetch reads it."""
    raw = data.get("overleaf")
    if raw is None or not str(raw).strip():
        return None
    from .overleaf import project_of

    project_of(str(raw))
    return str(raw).strip()


def _translation(data: dict[str, Any]) -> dict[str, Any]:
    """``translation:`` as {language, instructions, command}: the first two a non-empty
    string when set, the command the list of its words."""
    raw = data.get("translation")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("translation must be a mapping such as {language: Korean}")
    out: dict[str, Any] = {}
    for key in ("language", "instructions"):
        value = raw.get(key)
        if value is not None and str(value).strip():
            out[key] = str(value).strip()
    command = _command(raw.get("command"))
    if command is not None:
        out["command"] = command
    for key in ("instructions", "command"):
        if key in out and "language" not in out:
            raise ValueError(f"translation.{key} needs translation.language")
    return out


def _command(raw: Any, windows: bool = os.name == "nt") -> list[str] | None:
    """``translation.command`` as its words: a string split the way a shell splits it, or a
    list of the words. Empty means unset. On Windows a backslash is part of a path and not
    an escape, so ``C:\\Tools\\llm.exe`` stays whole, and quotes around a word are taken off."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            words = shlex.split(raw, posix=not windows)
        except ValueError as error:
            raise ValueError(f"translation.command: {error}") from None
        if windows:
            words = [word[1:-1] if len(word) > 1 and word[0] == word[-1] and word[0] in "\"'" else word
                     for word in words]
    elif isinstance(raw, list) and all(isinstance(word, str) for word in raw):
        words = list(raw)
    else:
        raise ValueError("translation.command must be a command line or a list of its words")
    return words or None


def user_config_file() -> Path:
    """The reviewer's own config file, for every paper. Only ``translation: {command: ...}``
    is read from it."""
    return user_config_dir() / "config.yaml"


def translation_command(config: Config) -> list[str] | None:
    """The command that translates: the paper's ``translation.command``, else the one in
    the user's own config file, else None. The user's file is read on every call, so a
    change to it applies without a restart."""
    if config.translation_command is not None:
        return config.translation_command
    path = user_config_file()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        translation = (data.get("translation") or {}) if isinstance(data, dict) else None
        if not isinstance(translation, dict):
            raise ValueError("translation must be a mapping such as {command: ...}")
        return _command(translation.get("command"))
    except FileNotFoundError:
        return None
    except (ValueError, yaml.YAMLError) as error:
        raise ValueError(f"{path}: {error}") from None


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
        # The page's server rewrites the file when its settings change.
        data = yaml.safe_load(shared_file.read_text(config_path)) or {}

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

    with open(output_path, "w", encoding="utf-8") as f:
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


def _switch(data: dict[str, Any], key: str, unset: bool = False) -> bool:
    """A switch: *unset* unless it says true or false."""
    raw = data.get(key, unset)
    if not isinstance(raw, bool):
        raise ValueError(f"{key} must be true or false, not {raw!r}")
    return raw


def _overleaf_flag(data: dict[str, Any], key: str) -> bool:
    """A switch that sends to the Overleaf project: off unless it says true."""
    raw = _switch(data, key)
    if raw and _overleaf(data) is None:
        raise ValueError(f"{key} needs overleaf: to name the project")
    return raw


def user_config_dir() -> Path:
    """The reviewer's own tex-mcp-web folder, one for every paper: their Overleaf cookie and
    their guidelines are kept there."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tex-mcp-web"


def guidelines_dir() -> Path:
    """The folder of the user's guidelines, one folder each, holding its GUIDELINE.md."""
    return user_config_dir() / "guidelines"


def list_guidelines() -> dict[str, Any]:
    """The guidelines folder with its entries (None when the folder is missing) and the
    entries without a GUIDELINE.md."""
    root = guidelines_dir()
    entries = (sorted(path.name for path in root.iterdir()
                      if path.is_dir() and not path.name.startswith((".", "_")))
               if root.is_dir() else None)
    return {"dir": str(root), "entries": entries,
            "incomplete": [name for name in entries or [] if not (root / name / "GUIDELINE.md").is_file()]}


def _guideline_file(name: str) -> Path:
    return (guidelines_dir() / name / "GUIDELINE.md").resolve()


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
    # A Claude skill's SKILL.md opens with its own name and description, and a guideline
    # folder may hold a link to one: keys other than extends are left alone.
    if not isinstance(front, dict):
        raise ValueError(f"{path}: front matter must be keys and values")
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
