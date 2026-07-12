"""LaTeX compiler wrapper with error/warning parsing."""

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


@dataclass
class CompileMessage:
    """A compiler error or warning with source location and context.

    Attributes:
        file: Source file name (relative to work directory).
        line: Line number (1-indexed), or None if unknown.
        message: Human-readable error/warning text.
        type: Either "error" or "warning".
        context: Optional list of surrounding source lines for context.
            The error line is marked with >>> ... <<< markers.
    """

    file: str
    line: int | None
    message: str
    type: Literal["error", "warning"]
    context: list[str] | None = None


@dataclass
class CompileResult:
    """Result of a TeX/Markdown compilation run.

    Attributes:
        success: Whether compilation succeeded (exit code 0 and PDF exists).
        errors: List of error messages parsed from compiler output.
        warnings: List of warning messages parsed from compiler output.
        output_file: Path to generated PDF, or None if compilation failed.
        timestamp: UTC time when compilation finished.
        duration_seconds: Wall-clock time for compilation.
        log_output: Full compiler stdout/stderr for debugging.
    """

    success: bool
    errors: list[CompileMessage] = field(default_factory=list)
    warnings: list[CompileMessage] = field(default_factory=list)
    output_file: Path | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0.0
    log_output: str = ""


# Patterns for parsing LaTeX log output
ERROR_PATTERN = re.compile(
    r"^(?:!|(?P<file>[^\s:]+):(?P<line>\d+):)\s*(?P<msg>.+?)$",
    re.MULTILINE,
)
WARNING_PATTERN = re.compile(
    r"^(?:LaTeX|Package|Class)\s+(?:\w+\s+)?Warning:\s*(?P<msg>.+?)$",
    re.MULTILINE | re.IGNORECASE,
)
LINE_WARNING_PATTERN = re.compile(
    r"(?P<msg>.+?)\s+on\s+input\s+line\s+(?P<line>\d+)",
    re.IGNORECASE,
)
UNDERFULL_OVERFULL_PATTERN = re.compile(
    r"^(?P<type>Underfull|Overfull)\s+\\[hv]box\s+.+?at\s+lines?\s+(?P<line>\d+)",
    re.MULTILINE,
)

# Whitelist of allowed compilers for security
ALLOWED_COMPILERS = frozenset({
    "auto",
    "latexmk",
    "pdflatex",
    "xelatex",
    "lualatex",
    "pandoc",
})


def _detect_compiler(main_file: Path) -> str:
    """Detect the appropriate compiler based on file extension."""
    ext = main_file.suffix.lower()
    if ext in (".md", ".markdown", ".txt"):
        return "pandoc"
    return "latexmk"


def _get_compiler_command(compiler: str, main_file: Path, work_dir: Path) -> list[str]:
    """Build the compiler command."""
    # Validate compiler against whitelist for security
    if compiler not in ALLOWED_COMPILERS:
        raise ValueError(f"Unknown compiler: {compiler}")

    if compiler == "auto":
        compiler = _detect_compiler(main_file)

    main_file_relative = main_file.relative_to(work_dir) if main_file.is_absolute() else main_file

    # Defence-in-depth: reject paths that look like flags or path-traversal.
    # A malicious .tex-mcp-web.yaml could set main: "--shell-escape paper.tex"
    # which latexmk would parse as a flag, enabling \write18 RCE via a
    # supply-chain compromise of the config file.
    rel_str = str(main_file_relative)
    if rel_str.startswith("-"):
        raise ValueError(f"main file path may not start with '-': {rel_str!r}")
    if ".." in main_file_relative.parts:
        raise ValueError(f"main file path may not contain '..': {rel_str!r}")

    if compiler == "pandoc":
        return [
            "pandoc",
            str(main_file_relative),
            "-o",
            main_file.stem + ".pdf",
        ]
    elif compiler == "latexmk":
        return [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-synctex=1",
            "-file-line-error",
            str(main_file_relative),
        ]
    elif compiler in ("pdflatex", "xelatex", "lualatex"):
        return [
            compiler,
            "-interaction=nonstopmode",
            "-synctex=1",
            "-file-line-error",
            str(main_file_relative),
        ]
    else:
        raise ValueError(f"Unknown compiler: {compiler}")


def _unwrap_log(log_output: str) -> str:
    """Rejoin log lines that TeX hard-wrapped at max_print_line (79 chars).

    Without this, messages like "... \\author macro! on\ninput line 96."
    are cut at the wrap point and lose their tail.
    """
    lines = log_output.splitlines()
    out: list[str] = []
    buf = ""
    for line in lines:
        buf += line
        if len(line) < 79:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return "\n".join(out)


def _parse_errors(log_output: str, main_file: str) -> list[CompileMessage]:
    """Parse errors from LaTeX log output."""
    errors = []

    # Look for standard LaTeX errors (! Error message)
    for match in re.finditer(r"^!\s*(.+?)$", log_output, re.MULTILINE):
        msg = match.group(1).strip()
        # Try to find the line number from context
        line = None
        context = log_output[max(0, match.start() - 200) : match.end() + 200]
        line_match = re.search(r"l\.(\d+)", context)
        if line_match:
            line = int(line_match.group(1))

        errors.append(CompileMessage(file=main_file, line=line, message=msg, type="error"))

    # Look for file:line:error format
    for match in re.finditer(r"^([^\s:]+\.tex):(\d+):\s*(.+?)$", log_output, re.MULTILINE):
        errors.append(
            CompileMessage(
                file=match.group(1),
                line=int(match.group(2)),
                message=match.group(3).strip(),
                type="error",
            )
        )

    return errors


def _parse_warnings(log_output: str, main_file: str) -> list[CompileMessage]:
    """Parse warnings from LaTeX log output."""
    warnings = []

    # Standard LaTeX/Package warnings
    for match in WARNING_PATTERN.finditer(log_output):
        msg = match.group("msg").strip()
        line = None
        line_match = LINE_WARNING_PATTERN.search(msg)
        if line_match:
            line = int(line_match.group("line"))
            msg = line_match.group("msg").strip()

        warnings.append(CompileMessage(file=main_file, line=line, message=msg, type="warning"))

    # Underfull/Overfull box warnings
    for match in UNDERFULL_OVERFULL_PATTERN.finditer(log_output):
        warnings.append(
            CompileMessage(
                file=main_file,
                line=int(match.group("line")),
                message=f"{match.group('type')} box",
                type="warning",
            )
        )

    return warnings


def _parse_pandoc_errors(stderr: str) -> list[CompileMessage]:
    """Parse errors from pandoc stderr output."""
    if not stderr.strip():
        return []
    return [CompileMessage(file="", line=None, message=stderr.strip()[:200], type="error")]


def enrich_error_context(
    messages: list[CompileMessage], work_dir: Path, window: int = 5
) -> None:
    """Attach surrounding source lines to each message that has a file and line.

    For each message, reads +/-*window* lines around the error line from the
    source file on disk and stores them in ``message.context``.  The error
    line itself is marked with ``>>> ... <<<`` markers.

    Args:
        messages: List of :class:`CompileMessage` to enrich (modified in place).
        work_dir: Working directory used to resolve relative file paths.
        window: Number of context lines above and below the error line.
    """
    # Cache file contents to avoid re-reading the same file for multiple errors
    file_cache: dict[str, list[str]] = {}

    for msg in messages:
        if not msg.file or msg.line is None:
            continue

        # Resolve the source file path
        file_path = work_dir / msg.file
        if not file_path.is_file():
            continue

        # Read and cache file lines
        cache_key = str(file_path)
        if cache_key not in file_cache:
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                file_cache[cache_key] = text.splitlines()
            except OSError:
                continue

        lines = file_cache[cache_key]
        total = len(lines)

        # msg.line is 1-based
        error_idx = msg.line - 1
        if error_idx < 0 or error_idx >= total:
            continue

        start = max(0, error_idx - window)
        end = min(total, error_idx + window + 1)

        context_lines: list[str] = []
        for i in range(start, end):
            if i == error_idx:
                context_lines.append(f">>> {lines[i]} <<<")
            else:
                context_lines.append(lines[i])

        msg.context = context_lines


async def compile_tex(
    main_file: Path,
    compiler: str = "latexmk",
    work_dir: Path | None = None,
) -> CompileResult:
    """Compile a TeX or markdown file asynchronously.

    Args:
        main_file: Path to main .tex/.md/.txt file.
        compiler: Compiler to use (auto, latexmk, pdflatex, xelatex, lualatex, pandoc).
        work_dir: Working directory for compilation. Defaults to main_file's parent.

    Returns:
        CompileResult with success status, errors, warnings, and output info.
    """
    if work_dir is None:
        work_dir = main_file.parent

    # Resolve "auto" to actual compiler
    resolved_compiler = compiler
    if compiler == "auto":
        resolved_compiler = _detect_compiler(main_file)

    # Check if compiler exists
    cmd_name = resolved_compiler.split()[0] if " " in resolved_compiler else resolved_compiler
    if shutil.which(cmd_name) is None:
        return CompileResult(
            success=False,
            errors=[
                CompileMessage(
                    file=str(main_file),
                    line=None,
                    message=f"Compiler '{resolved_compiler}' not found in PATH",
                    type="error",
                )
            ],
        )

    try:
        cmd = _get_compiler_command(compiler, main_file, work_dir)
    except ValueError as exc:
        return CompileResult(
            success=False,
            errors=[
                CompileMessage(
                    file=str(main_file),
                    line=None,
                    message=str(exc),
                    type="error",
                )
            ],
        )
    start_time = datetime.now(timezone.utc)
    is_pandoc = resolved_compiler == "pandoc"

    try:
        if is_pandoc:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            log_output = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
        else:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            log_output = stdout.decode("utf-8", errors="replace")
            stderr_text = ""

        end_time = datetime.now(timezone.utc)

        # Determine output PDF path
        pdf_name = main_file.stem + ".pdf"
        pdf_path = work_dir / pdf_name

        success = process.returncode == 0 and pdf_path.exists()

        if is_pandoc:
            errors = _parse_pandoc_errors(stderr_text) if not success else []
            warnings = []
        else:
            unwrapped = _unwrap_log(log_output)
            errors = _parse_errors(unwrapped, str(main_file.name))
            warnings = _parse_warnings(unwrapped, str(main_file.name))

        # Enrich errors and warnings with surrounding source lines
        enrich_error_context(errors, work_dir)
        enrich_error_context(warnings, work_dir)

        return CompileResult(
            success=success,
            errors=errors,
            warnings=warnings,
            output_file=pdf_path if pdf_path.exists() else None,
            timestamp=end_time,
            duration_seconds=(end_time - start_time).total_seconds(),
            log_output=log_output,
        )

    except Exception as e:
        return CompileResult(
            success=False,
            errors=[
                CompileMessage(
                    file=str(main_file),
                    line=None,
                    message=f"Compilation failed: {e}",
                    type="error",
                )
            ],
        )


def check_compiler_available(compiler: str, main_file: Path | None = None) -> bool:
    """Check if a compiler is available in PATH.

    Args:
        compiler: Compiler name or "auto".
        main_file: Main file path (needed to resolve "auto").
    """
    if compiler == "auto":
        if main_file is None:
            # Can't resolve auto without main_file; assume latexmk
            compiler = "latexmk"
        else:
            compiler = _detect_compiler(main_file)
    cmd = compiler.split()[0] if " " in compiler else compiler
    return shutil.which(cmd) is not None
