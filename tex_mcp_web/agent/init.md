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
  the startup directory, edit that file rather than replacing it with init. A missing main
  file alone does not establish that a config belongs to another project.
- Main file: `grep -l '\\documentclass' *.tex` in the paper's folder. One hit is the proposal.
  Several are asked. No hit in the startup directory: ask where the paper is, and that folder
  becomes `dir`.
- Port: the first free one from 8765 (`ss -ltn`, or `lsof -iTCP -sTCP:LISTEN -P -n` on macOS), also skipping the port in a
  `.html-mcp-web.yaml` in this folder, since both tools default to 8765.
- Compiler: `command -v latexmk`. Missing: tell the user, and do not install a TeX distribution.

## 2. Confirm

Show one summary, in the user's language, like:

    config   : <startup directory>/.tex-mcp-web.yaml
    dir      : paper2  (the paper's folder, relative to the config)
    main     : main.tex  (in dir, the only file with \documentclass)
    port     : 8765
    watch    : *.tex *.bib -> 9 files (main.tex, sections/*.tex, refs.bib)
    ignore   : *_backup.tex
    compiler : auto (latexmk)

Leave out `dir` when the paper is in the startup directory. Ask once. A choice the user has
already approved is not asked again.

## 3. Write

Run init and subsequent config commands with the MCP startup directory as their working
directory, with the command that `listen()`'s missing-config error names. Changing the shell's
working directory does not retarget this MCP process.

    tex-mcp-web --port <port> init --main <file>
    tex-mcp-web config <dir|compiler|watch|ignore> <value>   # only for changed fields

Continue only if init succeeds and the expected local config exists. Before changing any
setting, run `tex-mcp-web config` without arguments and confirm that it selects the intended
file. Stop if it selects another file. Inspect the resulting values after the changes.

In this setup, use `init --force` only after confirming that the existing config is in a
parent directory and creating a local config is authorized. It creates the local config
without changing the parent. Do not use it to replace an existing local config.

## 4. Verify

Before reporting completion, confirm that configuration discovery selects the intended file,
`dir` resolves to the intended paper directory, and `main` resolves to an existing paper file.

Then check the current connection without a build. Call `read_comments()`, which connects this
session to its project, and then `setup_info()`. Its `bound_config_path` must be the intended
config. A review server is shared only by the same config file, so this is the config the
review page serves. If it is bound to another config, restart the MCP server process through
the client and check again. A successful init or CLI config check alone does not establish
this.

Report setup as complete only after both the files and the current connection have been
verified. Otherwise report what was prepared and what remains unverified.

## 5. Tell the user

Report setup as ready only after the current MCP connection passes the check. If restarting
the MCP process remains necessary, say that the files are prepared but this session is not
ready yet, and state the remaining action. Saying "do tex listen" starts the review page.
Start listening only when the user asks for it.
