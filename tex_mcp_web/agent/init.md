# Set up a folder for tex review

The user said "do tex init", or `listen` found no config. Set up review for the current MCP
session. Read setup paths from `setup_info()`, not from the shell's current directory. The
config goes in the startup directory that `setup_info()` reports. Configuration discovery
searches that directory and its parents, never its subfolders. The discovered config and an
existing binding may differ. A successful binding is retained until the MCP process is
restarted.

Resolve `dir` relative to the config's directory, and `main` relative to `dir`, or to the
config's directory when `dir` is unset. Init creates the config, not the paper; a paper that
is on Overleaf is brought in as "Start from Overleaf" describes. Confirm only
choices the user has not already authorized. When the user explicitly asks you to set up
another session, use that session's intended startup directory and report separately whether
its connection has been verified.

## 1. Inspect (write nothing)

- Call `setup_info()` before connecting or writing, and read its startup directory,
  discovered config, and existing binding.
- Run `tex-mcp-web config` without arguments from the startup directory and inspect the
  selected path and values. If the config serves the intended project, reuse it and edit only
  the settings the request requires. If a parent config serves another project, leave it
  unchanged and propose a new config in the startup directory. If a config already exists in
  the startup directory, edit that file rather than replacing it with init, unless it serves
  another paper, which the next point covers. A missing main file alone does not establish
  that a config belongs to another project.
- A folder reviews one paper at a time. If the config in the startup directory serves a
  different paper from the one the user names, say so in one line and offer two options. The
  first is to start the agent in another folder, either the new paper's folder or any folder
  that gets a config of its own, so that both papers are reviewed side by side; propose this
  one when the first paper has open threads. The second is to switch this folder to the new
  paper with `config dir`. The first paper's comments stay with that paper and come back when
  the folder is switched back. A session keeps the paper it started with, which
  `setup_info()` reports as `bound_dir`, and the switch takes effect once tex-mcp reconnects
  (`/mcp` in Claude Code) or the agent starts again.
- Main file: run `grep -l '\\documentclass' *.tex` in the paper's folder. If one file
  matches, propose it, and if several match, ask which one. If nothing matches in the startup
  directory, ask where the paper is: in a folder on this machine, which becomes `dir`, on
  Overleaf, which "Start from Overleaf" covers, or not written yet. A paper not written yet
  is written first, in a folder of its own, when the user asks for it; init follows it.
- Overleaf: `setup_info()` gives `overleaf_cookie`, which is where the user's Overleaf
  cookie comes from, or null when none is saved. It never gives the cookie.
- Port: the first free port from 8765 upward (check with `ss -ltn`, with
  `lsof -iTCP -sTCP:LISTEN -P -n` on macOS, or with `netstat -ano -p tcp` on Windows). Also skip the port set in a `.html-mcp-web.yaml`
  in this folder, since both tools default to 8765.
- Compiler: run `command -v latexmk`. If it is missing, tell the user, and do not install a TeX
  distribution.
- References: a folder the user names that contains the papers their paper builds on,
  converted to Markdown. If the user names none, leave it unset.
- Guideline: `setup_info()` lists the user's guidelines folder with its absolute path and
  entries. Mark each entry that has no GUIDELINE.md, since it cannot be chosen. The default
  is none.
- Page token: off by default. Every account on a machine can reach the page's port, so on a
  machine that other people log in to, such as a lab server, the page token keeps them out.
- Translation: `setup_info()` reports it under `translation`. It is off while `language` is
  unset. Do not ask about it here; set it up only when the user asks for it, as the last
  section describes.

## 2. Confirm

Decide what you can yourself, and show the user only what is theirs to decide, in their
language. When your client has a tool that lets the user pick among options, ask every
choice with it, the default first: where the paper is, page token on or off, By hand or Live,
which guideline. Put all the choices that are known at once into one call. What the user
types, such as a project's link or a folder's path, is asked in plain chat and never as an
option to fill in. Without such a tool, show the choices as briefly as this:

    paper      : paper2/main.tex
    page       : http://localhost:8765
    page token : off  (on: the page opens only from its link, for a machine others log in to)

When the folder has a paper, add one more line that asks whether it is also an Overleaf
project, and continue with "Start from Overleaf" if the user gives its link:

    overleaf   : none  (give the project's link to see your co-authors' comments here)

Add a line only for what the user named or has to choose: `references` when they named a
folder, `guideline` when they have any guidelines, and anything you could not settle, such as
which of several main files to use. The rest (config path, watch, ignore and compiler) keeps
its default and is not shown. Under guideline, list the user's guidelines and none, each with
one line on its purpose taken from its first lines, and make none the default. Under page
token, ask whether other people can log in to this machine. With the token on, the page opens
only from a link carrying a token that only this account can read; the link is needed once
per browser, after which the plain address works there as a bookmark. With it off, the plain
address opens the page for anyone on the machine, as it does on a personal computer. Ask
once, and do not ask again about a choice the user has already approved.

## 3. Write

Run init and the config commands after it with the MCP startup directory as their working
directory. Below, `tex-mcp-web` stands for the path that `setup_info()` gives as `cli`, so
the shell does not need it on its PATH. Changing the shell's working directory does not
change this MCP process's startup directory.

    tex-mcp-web --port <port> init --main <file>
    tex-mcp-web config dir <folder>      # right after init, when the paper is in another folder
    tex-mcp-web config <compiler|watch|ignore|references|guideline|page_token> <value>   # only for changed fields

Continue only if init succeeds and the expected local config exists. Before changing any
setting, run `tex-mcp-web config` without arguments and confirm that it selects the intended
file. Stop if it selects another file. Inspect the resulting values after the changes.

In this setup, use `init --force` only after confirming that the existing config is in a
parent directory and creating a local config is authorized. It creates the local config
without changing the parent. Do not use it to replace an existing local config.

## Start from Overleaf

Follow this section when the paper is on Overleaf, with or without its files in the folder.
It needs no git and no download from Overleaf by hand.

1. Ask for the project's link in plain chat, in a message that asks nothing else and
   reports nothing else: the address the browser shows on Overleaf
   (`https://www.overleaf.com/project/…`). When the link has come, ask how the files sync,
   as a choice:

       By hand : nothing moves until you press Get (Overleaf to here) or Send (here to
                 Overleaf). Send asks first.
       Live    : while the page is open, edits here go to Overleaf at once, where your
                 co-authors see them, and theirs come here.

   By hand is the default. Choose Live only when the user picks it in answer to this
   question.

2. Choose the paper's folder. A folder that already holds the paper stays the paper's
   folder. Otherwise the paper gets a folder of its own, so that its files do not mix with
   code or data: propose `paper` under the startup directory, and use the startup directory
   itself only when it is empty.

3. Write the settings. Without a paper in the folder, run init as "Write" describes, with
   `config dir <folder>` for the paper's folder, and leave `main` as init wrote it: the page
   sets it to Overleaf's main file when the files arrive.

       tex-mcp-web config overleaf <link>
       tex-mcp-web config overleaf_sync true      # only when the user chose Live

   The command takes only a link on overleaf.com. For another Overleaf server the user
   writes the link into `.tex-mcp-web.yaml` themselves, since their cookie goes to it.

4. Verify the connection as "Verify" describes, and give the user the page's address. The
   page reads Overleaf only while a browser has it open, so ask the user to keep it open.

5. If `overleaf_cookie` is null, the user gives the cookie to the page, never to you: do
   not ask for it in chat and do not read the file that keeps it. Tell them these steps and
   nothing else in that message:

       1. Open <the page's address> and press Connect Overleaf (in the ⋯ menu on a phone).
       2. In another tab, sign in to Overleaf and open the browser's developer tools:
          Application (Chrome, Edge) or Storage (Safari, Firefox), then Cookies, then
          overleaf.com. "Where to find the cookie" on the page links each browser's guide.
       3. Copy the value of overleaf_session2, paste it into the page, and press Save.
       4. Tell me when it is saved.

   The cookie signs in as them, is kept in their own config folder for every paper, and is
   asked for once. When the user says it is saved, call `setup_info()` and check that
   `overleaf_cookie` is no longer null.

6. Bring the files in once the user has the page open. With By hand, call
   `overleaf(action="get")`; with Live, they arrive on their own. If the call says no page
   is in the project, the page is not open in a browser: ask the user to open it, and call
   again when they say it is. Files the folder and Overleaf both have but differ in
   wait for the user to choose on the page: say so, and do not choose for them.

7. Check that `main` in the config names a file that is there, set it with
   `tex-mcp-web config main <file>` if it does not, and `compile()`.

Then tell the user, in their words and without tool names, three things: the page's address,
that their co-authors' comments appear there, and how files move. With By hand, the top bar
shows Get and Send with a count when there is something to move. With Live, edits go both
ways while the page is open. In a folder that is a git clone of the project, the buttons are
git pull and commit & push instead; mention git only then. Leave out what the user chose or
did not ask about, such as the port, the page token and a cookie that was already saved.

## 4. Verify

Before reporting completion, confirm that configuration discovery selects the intended file,
`dir` resolves to the intended paper directory, and `main` resolves to an existing paper file.

Then check the current connection without a build. Call `guide()`, which connects this
session to its project, read every file in its read list in full, and then call
`setup_info()`. Its `bound_config_path` must be the intended config and its `bound_dir` the
paper's folder. Only sessions that use the same config file share a review server, so this
is the config the review page serves. If the session is bound to another config or folder,
restart the MCP server process through the client and check again. A successful init or CLI
config check alone does not establish this.

Report setup as complete only after both the files and the current connection have been
verified. Otherwise report what was prepared and what remains unverified.

## 5. Tell the user

Once the current MCP connection passes the check, call `listen()` and follow its `how`, so
that the user's first Call agent press reaches you without anything more being said. Then
tell the user one thing: the page's address to open, or, with the page token on, the link
that `tex-mcp-web link` prints in the paper's folder, which they open once in each browser.
Do not mention the checks you ran. If the MCP process still has to be restarted, tell the
user that one action and why it is needed. If the user did not connect Overleaf here, say
in one line that they can ask you to at any time, or press Connect Overleaf in the page's
top bar (in the ⋯ menu on a phone). If translation is off, say so in one line, and say that they can ask
you to turn it on, for example with "set up translation to Korean". With the plugin
installed, a Claude Code session started in this folder later listens on its own.

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
4. Find an LLM command on this machine, preferring the command of the agent you run in. If
   there is none, tell the user in one line that translation stays off, and stop.
5. Build a command line for that tool. The line must do all of this:
   - Run once and exit, without asking anything.
   - Read the text on standard input. If the tool takes the text only as an argument, put
     `{prompt}` in that argument, and the server puts the text there.
   - Print only the answer on standard output.
   - Name its model. Choose a mid-sized model, not the largest one.
   - Start nothing it does not need: no tools, no MCP servers, no saved session. A run that
     starts the user's MCP servers starts tex-mcp again.

   Read the tool's help first (`<tool> --help`, and the help of the subcommand that runs
   once), and take each flag from it under the name this version uses. Take the model name
   from the help or from the tool's list of models. Start the line with the tool's full path
   from `command -v <tool>`, because the server runs with a short PATH of its own. On
   Windows, write the path as Windows names it, with forward slashes, such as
   `C:/Users/me/.local/bin/tool.exe` (`cygpath -m "$(command -v <tool>)"` in Git Bash, or
   `where <tool>`).
6. Test the line once by sending a one-line request on standard input, for example
   `echo 'Translate into Korean: Good morning.' | <the line>`. If the line has `{prompt}`,
   put the request there instead. The output must be the answer and nothing else; if it is
   not, change the line and test it again.
7. Show the user the exact line, say in a few words that a larger model costs more because a
   paper has hundreds of paragraphs, and ask one short question with this line as the
   default. When the user agrees, write the line to the user's own file, `user_config`, as
   `command:` under `translation:`, creating the file if it does not exist. A command there
   serves every paper. If the line contains `#` or `: `, write it as a YAML list instead, one
   word per item.
8. Tell the user which line does the translation and which file contains it, and that they
   can change its model there. Then tell them to reload the page.

The server reads the paper's config file again each time it is saved, and the user's file
each time the page asks for a translation, so neither needs a restart.
