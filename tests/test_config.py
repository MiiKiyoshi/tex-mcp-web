"""Tests for config module."""

import tempfile
from pathlib import Path

import pytest
import yaml

from tex_mcp_web.config import (
    Config,
    create_config,
    find_config,
    get_main_file,
    get_watch_dir,
    load_config,
)


class TestConfig:
    """Tests for Config dataclass."""

    def test_from_dict_defaults(self):
        """Test Config.from_dict with minimal data."""
        config = Config.from_dict({"main": "test.tex"})
        assert config.main == "test.tex"
        assert config.watch == ["*.tex", "*.bib", "*.md", "*.txt"]
        assert config.ignore == []
        assert config.compiler == "auto"
        assert config.port == 8765
        assert config.page_limit is None

    def test_from_dict_full(self):
        """Test Config.from_dict with full data."""
        data = {
            "main": "document.tex",
            "watch": ["*.tex", "chapters/*.tex"],
            "ignore": ["*_old.tex"],
            "compiler": "xelatex",
            "port": 9000,
        }
        config = Config.from_dict(data)
        assert config.main == "document.tex"
        assert config.watch == ["*.tex", "chapters/*.tex"]
        assert config.ignore == ["*_old.tex"]
        assert config.compiler == "xelatex"
        assert config.port == 9000

    def test_to_dict(self):
        """Test Config.to_dict roundtrip."""
        original = {
            "main": "main.tex",
            "watch": ["*.tex"],
            "ignore": [],
            "compiler": "latexmk",
            "port": 8765,
        }
        config = Config.from_dict(original)
        result = config.to_dict()
        assert result == original

    def test_default_compiler_is_auto(self):
        """Test that default compiler is 'auto'."""
        config = Config(main="test.tex")
        assert config.compiler == "auto"

    def test_default_watch_includes_md(self):
        """Test that default watch patterns include markdown."""
        config = Config(main="test.tex")
        assert "*.md" in config.watch
        assert "*.txt" in config.watch
        assert "*.tex" in config.watch

    def test_default_watch_includes_bib(self):
        """Test that default watch patterns include .bib files."""
        config = Config(main="test.tex")
        assert "*.bib" in config.watch

    def test_page_limit_default_none(self):
        """Test that page_limit defaults to None."""
        config = Config(main="test.tex")
        assert config.page_limit is None

    def test_page_limit_roundtrip_with_value(self):
        """Test page_limit serialization round-trip with a value."""
        data = {"main": "test.tex", "page_limit": 50}
        config = Config.from_dict(data)
        assert config.page_limit == 50

        result = config.to_dict()
        assert result["page_limit"] == 50

        # Round-trip back
        config2 = Config.from_dict(result)
        assert config2.page_limit == 50

    def test_page_limit_roundtrip_none(self):
        """Test page_limit serialization round-trip with None."""
        data = {"main": "test.tex"}
        config = Config.from_dict(data)
        assert config.page_limit is None

        result = config.to_dict()
        assert "page_limit" not in result

        # Round-trip: from_dict without page_limit should give None
        config2 = Config.from_dict(result)
        assert config2.page_limit is None

    def test_page_limit_omitted_from_to_dict_when_none(self):
        """Test that to_dict omits page_limit when it is None."""
        config = Config(main="test.tex")
        d = config.to_dict()
        assert "page_limit" not in d

    def test_page_limit_included_in_to_dict_when_set(self):
        """Test that to_dict includes page_limit when it is set."""
        config = Config(main="test.tex", page_limit=25)
        d = config.to_dict()
        assert d["page_limit"] == 25


class TestFindConfig:
    """Tests for find_config function."""

    def test_find_in_current_dir(self, tmp_path):
        """Test finding config in current directory."""
        config_path = tmp_path / ".tex-mcp-web.yaml"
        config_path.write_text("main: test.tex\n")

        result = find_config(tmp_path)
        assert result == config_path

    def test_find_in_parent_dir(self, tmp_path):
        """Test finding config in parent directory."""
        config_path = tmp_path / ".tex-mcp-web.yaml"
        config_path.write_text("main: test.tex\n")

        subdir = tmp_path / "subdir"
        subdir.mkdir()

        result = find_config(subdir)
        assert result == config_path

    def test_not_found(self, tmp_path):
        """Test when no config exists."""
        result = find_config(tmp_path)
        assert result is None


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_existing_config(self, tmp_path):
        """Test loading existing config file."""
        config_path = tmp_path / ".tex-mcp-web.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "main": "thesis.tex",
                    "compiler": "lualatex",
                    "port": 9999,
                }
            )
        )

        config = load_config(config_path)
        assert config.main == "thesis.tex"
        assert config.compiler == "lualatex"
        assert config.port == 9999
        assert config.config_path == config_path

    def test_load_with_cli_override(self, tmp_path):
        """Test CLI argument overrides config file."""
        config_path = tmp_path / ".tex-mcp-web.yaml"
        config_path.write_text(yaml.dump({"main": "config.tex"}))

        config = load_config(config_path, main_file="cli.tex")
        assert config.main == "cli.tex"

    def test_load_missing_config(self, tmp_path, monkeypatch):
        """Test loading when no config exists."""
        monkeypatch.chdir(tmp_path)
        config = load_config()
        assert config.main == "main.tex"
        assert config.config_path is None


class TestCreateConfig:
    """Tests for create_config function."""

    def test_create_default(self, tmp_path):
        """Test creating config with defaults."""
        output_path = tmp_path / ".tex-mcp-web.yaml"
        result = create_config(output_path=output_path)

        assert result == output_path
        assert output_path.exists()

        with open(output_path) as f:
            data = yaml.safe_load(f)

        assert data["main"] == "main.tex"
        assert "*.tex" in data["watch"]
        assert "*.bib" in data["watch"]
        assert "*.md" in data["watch"]
        assert "*.txt" in data["watch"]
        assert data["compiler"] == "auto"
        assert data["port"] == 8765

    def test_create_custom(self, tmp_path):
        """Test creating config with custom values."""
        output_path = tmp_path / ".tex-mcp-web.yaml"
        create_config(
            main="thesis.tex",
            watch=["*.tex", "chapters/**/*.tex"],
            ignore=["old/*"],
            compiler="xelatex",
            port=9000,
            output_path=output_path,
        )

        with open(output_path) as f:
            data = yaml.safe_load(f)

        assert data["main"] == "thesis.tex"
        assert data["watch"] == ["*.tex", "chapters/**/*.tex"]
        assert data["ignore"] == ["old/*"]
        assert data["compiler"] == "xelatex"
        assert data["port"] == 9000


class TestConfigSnippets:
    def test_config_has_snippets_default(self):
        """Config.snippets defaults to empty dict."""
        config = Config(main="main.tex")
        assert config.snippets == {}

    def test_config_from_dict_with_snippets(self):
        """Config.from_dict parses snippets."""
        data = {
            "main": "paper.tex",
            "snippets": {
                "thm": "\\begin{theorem}\n$1\n\\end{theorem}",
                "prf": "\\begin{proof}\n$1\n\\end{proof}",
            },
        }
        config = Config.from_dict(data)
        assert config.snippets == data["snippets"]

    def test_config_to_dict_includes_snippets(self):
        """Config.to_dict includes non-empty snippets."""
        config = Config(main="main.tex", snippets={"thm": "\\begin{theorem}\n$1\n\\end{theorem}"})
        d = config.to_dict()
        assert "snippets" in d
        assert d["snippets"]["thm"] == "\\begin{theorem}\n$1\n\\end{theorem}"

    def test_config_to_dict_omits_empty_snippets(self):
        """Config.to_dict omits snippets when empty."""
        config = Config(main="main.tex")
        d = config.to_dict()
        assert "snippets" not in d


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_watch_dir_with_config_path(self, tmp_path):
        """Test get_watch_dir with config_path set."""
        config = Config(main="test.tex", config_path=tmp_path / ".tex-mcp-web.yaml")
        assert get_watch_dir(config) == tmp_path

    def test_get_watch_dir_without_config_path(self):
        """Test get_watch_dir without config_path."""
        config = Config(main="test.tex")
        assert get_watch_dir(config) == Path.cwd()

    def test_get_main_file(self, tmp_path):
        """Test get_main_file returns absolute path."""
        config = Config(main="document.tex", config_path=tmp_path / ".tex-mcp-web.yaml")
        result = get_main_file(config)
        assert result == tmp_path / "document.tex"
