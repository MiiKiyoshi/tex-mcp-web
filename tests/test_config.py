"""Tests for config module."""

from pathlib import Path

import yaml
import pytest

from tex_mcp_web.config import (
    Config,
    create_config,
    find_config,
    get_main_file,
    get_watch_dir,
    load_config,
    write_auto_compile,
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
        assert config.auto_compile is False
        assert config.port == 8765

    def test_from_dict_full(self):
        """Test Config.from_dict with full data."""
        data = {
            "main": "document.tex",
            "watch": ["*.tex", "chapters/*.tex"],
            "ignore": ["*_old.tex"],
            "compiler": "xelatex",
            "auto_compile": True,
            "port": 9000,
        }
        config = Config.from_dict(data)
        assert config.main == "document.tex"
        assert config.watch == ["*.tex", "chapters/*.tex"]
        assert config.ignore == ["*_old.tex"]
        assert config.compiler == "xelatex"
        assert config.auto_compile is True
        assert config.port == 9000

    def test_to_dict(self):
        """Test Config.to_dict roundtrip."""
        original = {
            "main": "main.tex",
            "watch": ["*.tex"],
            "ignore": [],
            "compiler": "latexmk",
            "auto_compile": False,
            "port": 8765,
        }
        config = Config.from_dict(original)
        result = config.to_dict()
        assert result == original

    def test_default_compiler_is_auto(self):
        """Test that default compiler is 'auto'."""
        config = Config(main="test.tex")
        assert config.compiler == "auto"

    def test_auto_compile_rejects_non_boolean(self):
        with pytest.raises(ValueError, match="auto_compile must be true or false"):
            Config.from_dict({"main": "test.tex", "auto_compile": "false"})

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
        assert "**/*.tex" not in data["watch"]
        assert data["compiler"] == "auto"
        assert data["auto_compile"] is False
        assert data["port"] == 8765

    def test_create_custom(self, tmp_path):
        """Test creating config with custom values."""
        output_path = tmp_path / ".tex-mcp-web.yaml"
        create_config(
            main="thesis.tex",
            watch=["*.tex", "chapters/**/*.tex"],
            ignore=["old/*"],
            compiler="xelatex",
            auto_compile=True,
            port=9000,
            output_path=output_path,
        )

        with open(output_path) as f:
            data = yaml.safe_load(f)

        assert data["main"] == "thesis.tex"
        assert data["watch"] == ["*.tex", "chapters/**/*.tex"]
        assert data["ignore"] == ["old/*"]
        assert data["compiler"] == "xelatex"
        assert data["auto_compile"] is True
        assert data["port"] == 9000

    def test_write_auto_compile_preserves_other_settings(self, tmp_path):
        output_path = tmp_path / ".tex-mcp-web.yaml"
        output_path.write_text(
            "# paper settings\n"
            "main: paper.tex\n"
            "custom: keep\n"
            "auto_compile: false  # top-bar mode\n"
        )

        write_auto_compile(output_path, True)

        assert output_path.read_text() == (
            "# paper settings\n"
            "main: paper.tex\n"
            "custom: keep\n"
            "auto_compile: true  # top-bar mode\n"
        )

    def test_write_auto_compile_appends_missing_field(self, tmp_path):
        output_path = tmp_path / ".tex-mcp-web.yaml"
        output_path.write_text("main: paper.tex")

        write_auto_compile(output_path, True)

        assert output_path.read_text() == "main: paper.tex\nauto_compile: true\n"


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
