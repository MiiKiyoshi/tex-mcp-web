# Installing tex-mcp-web

This file is for the agent the user asked to install tex-mcp-web. Fetch it with
`curl -fsSL`, since a summarizing fetch drops the commands. Inspect first, show one summary,
install after the user agrees. On a machine that already has it, the same steps update it.

## 1. Inspect (change nothing)

- Python 3.10 or newer (the `mcp` package has no release for older ones): `python3 --version`.
  When older, `command -v python3.13 python3.12 python3.11 python3.10`. None: stop and tell
  the user. Use the one found as `python3` below.
- LaTeX: `command -v latexmk`, and record its directory. Missing: tell the user a TeX
  distribution is needed and stop. Do not install one.
- Install directory: `$HOME/.local/share/tex-mcp-web`, unless the user named another.
  Note whether it already holds a checkout.
- Agents: `command -v claude` and `command -v codex`. Register with each one found.
- Existing registration: `claude mcp get tex-mcp`, `codex mcp get tex-mcp`. Note a
  command path that differs from the one below.

## 2. Confirm

Show one summary, in the user's language: install directory (new or update), Python,
latexmk's directory, the agents to register with, and any existing registration that will
be replaced. Registration is user-level, available in every folder. Do not ask about
scope. Ask once.

## 3. Install

    DIR="$HOME/.local/share/tex-mcp-web"
    git clone https://github.com/MiiKiyoshi/tex-mcp-web.git "$DIR"   # update: git -C "$DIR" pull --ff-only
    python3 -m venv "$DIR/.venv"
    "$DIR/.venv/bin/pip" install -q -U pip                              # editable installs need a recent pip
    "$DIR/.venv/bin/pip" install -e "${DIR}[mcp]"
    "$DIR/.venv/bin/tex-mcp-web" mcp --check                          # lists the tools

## 4. Register

Remove a registration the user agreed to replace (`claude mcp remove --scope user tex-mcp`,
`codex mcp remove tex-mcp`), then:

    BIN="$DIR/.venv/bin"
    P="$BIN:<latexmk directory>:/usr/bin:/bin"
    claude mcp add --scope user tex-mcp -e PATH="$P" -- "$BIN/tex-mcp"
    codex mcp add tex-mcp --env PATH="$P" -- "$BIN/tex-mcp"

## 5. Tell the user

The server loads when an agent session starts, so this session cannot use it yet. Start
the agent again in the paper's folder, then say "do tex init" once per paper and
"do tex listen" to open the review page.
