# Set up a folder for tex review

The user said "do tex init", or `listen` found no config. Set up review for the current MCP
session. Read setup paths from `setup_info()`, not from the shell's current directory. Its
startup directory is where the config goes. Configuration discovery searches that directory
and its parents, never its subfolders. The discovered config and an existing binding may
differ. A successful binding is retained until the MCP process is restarted.

Resolve `dir` relative to the config's directory, and `main` relative to `dir`, or to the
config's directory when `dir` is unset. Init creates the config, not the paper. Confirm only
choices the user has not already authorized. For an explicitly requested setup for another
session, use that session's intended startup directory and report separately whether its
connection has been verified.

## 1. Inspect (write nothing)

- Call `setup_info()` before connecting or writing, and read its startup directory,
  discovered config, and existing binding.
- Run `tex-mcp-web config` without arguments from the startup directory and inspect the
  selected path and values. If the config serves the intended project, reuse it and edit only
  the settings the request requires. If a parent config serves another project, leave it
  unchanged and propose a new config in the startup directory. If a config already exists in
  the startup directory, edit that file rather than replacing it with init, unless it serves
  another paper, which the next point is for. A missing main
  file alone does not establish that a config belongs to another project.
- A folder reviews one paper at a time. When the config in the startup directory serves
  another paper than the one the user names, say so in one line and offer both ways: start
  the agent in another folder, the new paper's or any that gets a config of its own, which
  reviews both side by side and is the
  one to propose when the first paper has open threads, or move this folder to the new paper
  with `config dir`. Moved, the first paper's comments stay with it and come back when the
  folder is moved back. A session keeps the paper it started with: `setup_info()`'s
  `bound_dir` names it, and the move holds once tex-mcp reconnects (`/mcp` in Claude Code) or the agent starts again.
- Main file: `grep -l '\\documentclass' *.tex` in the paper's folder. One hit is the proposal.
  Several are asked. No hit in the startup directory: ask where the paper is, and that folder
  becomes `dir`.
- Port: the first free one from 8765 (`ss -ltn`, `lsof -iTCP -sTCP:LISTEN -P -n` on macOS, `netstat -ano -p tcp` on Windows), also skipping the port in a
  `.html-mcp-web.yaml` in this folder, since both tools default to 8765.
- Compiler: `command -v latexmk`. Missing: tell the user, and do not install a TeX distribution.
- References: a folder the user names that holds the papers theirs builds on, converted to
  Markdown. Unnamed: leave it unset.
- Guideline: `setup_info()` lists the user's guidelines folder, with its absolute path and
  entries. Mark an entry without GUIDELINE.md, which cannot be chosen. None is the default.
- Page token: off is the default. Every account on a machine can reach the page's port, so on
  a machine other people log in to, such as a lab server, the page token keeps them out.

## 2. Confirm

Decide what you can yourself, and show the user only what is theirs to decide, in their
language, as short as this:

    paper      : paper2/main.tex
    page       : http://localhost:8765
    page token : off  (on: the page opens only from its link, for a machine others log in to)

Add a line only for what the user named or has to choose: `references` when they named a
folder, `guideline` when they have any, and whatever you could not settle, as which of
several main files. The rest, config path, watch, ignore and compiler, takes its default
unsaid. Under guideline, list the user's
guidelines and none, each with one line on what it is for, read from its first lines, with
none as the default. Under page token, ask whether other people can log in to this machine:
on, the page opens only from a link with a token only this account can read, once per
browser, after which its plain address works as a bookmark there. Off, the plain address
opens it for anyone on the machine, as it does on a computer of one's own. Ask once. A choice
the user has already approved is not asked again.

## 3. Write

Run init and subsequent config commands with the MCP startup directory as their working
directory. `tex-mcp-web` below is the path `setup_info()` gives as `cli`: the shell need not
have it on its PATH. Changing the shell's working directory does not retarget this MCP process.

    tex-mcp-web --port <port> init --main <file>
    tex-mcp-web config dir <folder>      # right after init, when the paper is in another folder
    tex-mcp-web config <compiler|watch|ignore|references|guideline|page_token> <value>   # only for changed fields

Continue only if init succeeds and the expected local config exists. Before changing any
setting, run `tex-mcp-web config` without arguments and confirm that it selects the intended
file. Stop if it selects another file. Inspect the resulting values after the changes.

In this setup, use `init --force` only after confirming that the existing config is in a
parent directory and creating a local config is authorized. It creates the local config
without changing the parent. Do not use it to replace an existing local config.

## 4. Verify

Before reporting completion, confirm that configuration discovery selects the intended file,
`dir` resolves to the intended paper directory, and `main` resolves to an existing paper file.

Then check the current connection without a build. Call `guide()`, which connects this session
to its project, read every file in its read list in full, and then call `setup_info()`. Its
`bound_config_path` must be the intended config and `bound_dir` the paper's folder. A review server is shared only by the same config file, so this is the config the
review page serves. If it is bound to another config or folder, restart the MCP server process through
the client and check again. A successful init or CLI config check alone does not establish
this.

Report setup as complete only after both the files and the current connection have been
verified. Otherwise report what was prepared and what remains unverified.

## 5. Tell the user

Once the current MCP connection passes the check, call `listen()` and follow its how, so
that the user's first Call agent reaches you with nothing more said. Then tell the user one
thing: the page's address to open, or with the page token on the link `tex-mcp-web link`
prints in the paper's folder, to open once in each browser. Leave the checks you ran unsaid.
If restarting the MCP process remains necessary, say that one action and what it is for.
To follow an Overleaf project, the user presses Connect Overleaf in the page's top bar (in
the ⋯ menu on a phone) and follows what it asks. Tell them so in one line.
With the plugin, Claude Code started in the folder later listens on its own.
