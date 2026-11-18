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
- Translation: `setup_info()` reports it under `translation`. It is off while `language` is
  unset. Do not ask about it here. Set it up only when the user asks for it, as the last
  section says.

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
If translation is off, say so in one line, and say that they can ask you to turn it on, for
example: "set up translation to Korean".
With the plugin, Claude Code started in the folder later listens on its own.

## Set up translation

Follow these steps when the user asks for translation, during init or at any other time,
for example "set up translation to Korean". The page then shows each paragraph of the
source in that language, beside the text.

1. Call `setup_info()`. Read its `translation` part: `language`, `command`, and
   `user_config`, the path of the user's own config file.
2. Open the paper's config file, `discovered_config_path`, with your file tools. If it is
   null, set up review first with the sections above, then come back here. Under
   `translation:`, write `language:` with the language the user named. If the user gave
   rules for the translation, such as terms to keep in English, also write `instructions:`,
   one rule per line. `tex-mcp-web config` does not set these keys.
3. If `command` is already set, go to step 8.
4. Find an LLM command on this machine. Prefer the command of the agent you run in. If there
   is none, tell the user in one line that translation stays off, and stop.
5. Build a command line for that tool. The line must do all of this:
   - Run once and exit, without asking anything.
   - Read the text on standard input. If the tool takes the text only as an argument, put
     `{prompt}` in that argument. The server puts the text there.
   - Print only the answer on standard output.
   - Name its model. Choose a mid-sized model, not the largest one.
   - Start nothing it does not need: no tools, no MCP servers, no saved session. A run that
     starts the user's MCP servers starts tex-mcp again.

   Read the tool's help first: `<tool> --help`, and the help of the subcommand that runs
   once. Take each flag from this help, under the name this version uses. Take the model
   name from the help or from the tool's list of models. Start the line with the tool's
   full path, from `command -v <tool>`. The server runs with a short PATH of its own.
6. Test the line once. Send a one-line request on standard input, for example
   `echo 'Translate into Korean: Good morning.' | <the line>`. If the line has `{prompt}`,
   put the request there instead. The output must be the answer and nothing else. If it is
   not, change the line and test it again.
7. Show the user the exact line. Say in a few words that a larger model costs more, because a
   paper has hundreds of paragraphs. Ask one short question, with this line as the default.
   When the user agrees, write the line to the user's own file, `user_config`, as `command:`
   under `translation:`. Create the file if it does not exist. A command there serves every
   paper. If the line holds `#` or `: `, write it as a YAML list instead, one word per item.
8. Tell the user which line translates and which file holds it. Say that they can change
   its model there. Then tell them to reload the page.

The server reads the paper's config file again each time it is saved. It reads the user's
file each time the page asks for a translation. Neither needs a restart.
