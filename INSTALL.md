# Installing tex-mcp-web

This file is for the agent the user asked to install tex-mcp-web. Fetch it with
`curl -fsSL`, because a fetch tool that summarizes pages drops the commands. Inspect first,
show the user one summary, and install once they agree. On a machine that already has
tex-mcp-web, the same steps update it.

The commands are written for a POSIX shell. On Windows, run them in Git Bash, which is the
shell Claude Code itself uses there, and write `Scripts` where the commands say `bin`. In
PowerShell or cmd, run the equivalent commands for that shell.

## 1. Inspect (change nothing)

- Python: check `python3 --version`. It must be 3.10 or newer, since the `mcp` package has
  no release for older versions. If it is older, look for a newer one with
  `command -v python3.13 python3.12 python3.11 python3.10`, or with `py -0p` on Windows. A
  Python newer than the dependencies' prebuilt packages does not work either: if the install
  in step 3 has to build a dependency and the build fails, use Python 3.13. If no suitable
  Python is installed, name the command that installs one on this system, such as
  `py install 3.13` on Windows, and install it only when the user says to. Use the Python you
  found wherever the commands below say `python3`. On Windows, use `py -3.13` from the start
  when `py -0p` lists it, rather than a newer default. If there is no `py` launcher, use the
  full path of a Python from python.org or winget, not the `python3` in a `WindowsApps`
  folder, which only opens the Microsoft Store.
- LaTeX: run `command -v latexmk` and record its directory. On Windows, record it as Windows
  names it, from `cygpath -w "$(dirname "$(command -v latexmk)")"`, because step 4 writes it
  into a Windows PATH. If it is missing, name the TeX
  distribution that suits this system and the command that installs it (TeX Live on Linux,
  MacTeX on macOS, MiKTeX or TeX Live on Windows), and install it only when the user says to.
  Continue once `latexmk` runs, using its full path if this shell's PATH does not include it
  yet. With MiKTeX, set it to install missing packages without asking
  (`initexmf --set-config-value "[MPM]AutoInstall=1"`), because no one is there to answer
  during a build.
- Install directory: `$HOME/.local/share/tex-mcp-web`, unless the user named another. Note
  whether it already contains a checkout.
- Agents: run `command -v claude` and `command -v codex`, and register with each one found.
- Existing registration: run `claude mcp get tex-mcp` and `codex mcp get tex-mcp`, and note
  any command path that differs from the one below. `claude plugin list` shows whether the `tex-mcp`
  plugin is already installed; if it is, skip the plugin install in step 4.

## 2. Confirm

Ask the user once, in their language and in a line or two, to confirm where it will be
installed (new install or update) and which agents it will be registered with. Mention Python and
latexmk only if something is wrong with them, and an existing registration only if it will
be replaced. Registration is user-level, so it applies in every folder. Do not ask about
scope.

## 3. Install

On Windows, if step 1 found a registration that runs `tex-mcp.exe`, do step 4 first, ask the
user to restart the agent, and come back to this step after the restart. Windows locks a
program's file while it runs, the running server is that program, and this step replaces
its file.

    DIR="$HOME/.local/share/tex-mcp-web"
    git clone https://github.com/MiiKiyoshi/tex-mcp-web.git "$DIR"   # update: git -C "$DIR" pull --ff-only
    python3 -m venv "$DIR/.venv"
    "$DIR/.venv/bin/python" -m pip install -q -U pip                    # editable installs need a recent pip
    "$DIR/.venv/bin/python" -m pip install -e "$DIR"
    "$DIR/.venv/bin/tex-mcp-web" mcp --check                          # lists the tools

## 4. Register

Remove any registration the user agreed to replace (`claude mcp remove --scope user tex-mcp`,
`codex mcp remove tex-mcp`), then run:

    BIN="$DIR/.venv/bin"
    P="$BIN:<latexmk directory>:<translation command directory>:/usr/bin:/bin"   # the directory of the command translation runs, if you use one
    claude mcp add --scope user tex-mcp -e PATH="$P" -- "$BIN/python" -m tex_mcp_web.cli mcp
    codex mcp add tex-mcp --env PATH="$P" -- "$BIN/python" -m tex_mcp_web.cli mcp

The server is registered as a Python module, not as the `tex-mcp` program in the same
folder, so that an update can replace that program's file while the server runs.

On Windows, join the PATH entries with `;` and use Windows folders: the directories above,
then `C:\Windows\System32` and `C:\Windows`, the folder of `git`, which syncing with Overleaf
by git needs, and the folder of `perl`. MiKTeX's latexmk needs Perl and does not include one;
Git for Windows has one in its `usr\bin`. latexmk run from Git Bash finds that Perl by
itself, so run latexmk once with only the PATH above to confirm that the server will find
Perl too.

For Claude Code, also install the plugin from the checkout. The plugin blocks tex-mcp's tools
other than `guide()`, `setup_info()` and `image()` until the current context has read the
files that `guide()` lists, and does so again after each compaction. It blocks an edit of a
`.tex` or `.bib` file of the paper in the same way, so an agent that edits the paper without
calling `guide()` reads them too, and `tex-mcp-web terms` prints that list. Its monitor makes a
session started in the paper's folder listen for Call agent for the whole session, unless
another session already listens to that paper, so a press reaches the agent without a request
to listen, however long the review page stays idle. The plugin loads from the checkout in
place, so updating the checkout also updates the plugin.

    claude plugin marketplace add "$DIR"
    claude plugin install tex-mcp@tex-mcp-web

## 5. Tell the user

The server loads when an agent session starts, so this session cannot use it yet. Tell the
user the one thing left to do: start the agent again with the command that continues this
conversation (`claude --continue`, `codex resume --last`) and say "do tex init". Say nothing
else; that step sets up the paper, opens the review page and starts listening. After an
update there is nothing to set up: tell the user only to start the agent again, and to
reload the review page.
