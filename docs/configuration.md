# Configuration

`do tex init` writes `.tex-mcp-web.yaml` in the folder where the agent was started. Changes
you save to the file take effect immediately, except `port` and `dir`, which take effect when
the agent restarts.

```yaml
main: main.tex
watch:
- '*.tex'
- '*.bib'
ignore:
- '*_backup.tex'
compiler: auto
port: 8765
guideline: my-field
translation:
  language: Korean
  instructions: Keep technical terms in English.
overleaf: https://www.overleaf.com/project/0123456789abcdef01234567
```

| Field | Default | Description |
|---|---|---|
| `main` | `main.tex` | The top-level source file that is compiled into the PDF, relative to the paper's folder. |
| `dir` | This file's folder | The paper's folder, relative to this file or absolute. |
| `watch` | `*.tex`, `*.bib` and the main file's type | Patterns for the source files that the Source tab lists and the page watches for changes. |
| `ignore` | None | Patterns for files to leave out even when they match `watch`. |
| `compiler` | `auto` | `auto` (latexmk for LaTeX, pandoc for Markdown or plain text), `latexmk`, `pdflatex`, `xelatex`, `lualatex` or `pandoc`. The PDF is always built in the paper's folder and named after the main file, so an `$out_dir` set in a `.latexmkrc` is not used. |
| `port` | `8765` | The port of this paper's review page. Any free port works, and `do tex init` picks the first free one from 8765 up. |
| `guideline` | None | The writing guideline the agent reads before it edits: the name of a folder under `~/.config/tex-mcp-web/guidelines/` that contains a `GUIDELINE.md`. If that file begins with the front matter `extends: <name>`, the agent reads the named guideline first. |
| `references` | None | A folder, relative to this file or absolute, that contains only the papers yours builds on, converted to Markdown. The agent reads the terms those papers use. |
| `translation` | None | Translation of the source. See below. |
| `overleaf` | None | The link to the paper's Overleaf project, which `do tex init` or **Connect** in Settings writes for you. |
| `overleaf_sync` | `false` | `true` is **Live** and `false` is **By hand** under **How files sync with Overleaf** in Settings. [Keeping the files in step](overleaf.md#keeping-the-files-in-step) describes both. |
| `overleaf_picked` | `false` | `true` makes **Call agent** carry what was written on Overleaf only in the threads you picked for that press. What you write on the page reaches the agent as always. With `false`, every thread from Overleaf with new words asks the agent. |
| `overleaf_add` | Sources and figures | Patterns for files that exist only here and are added to the Overleaf project, by **Send** or automatically under **Live**, in a folder that is not a git clone. The default is `*.tex`, `*.bib`, `*.sty`, `*.cls`, `*.bst`, `*.png`, `*.jpg`, `*.jpeg`, `*.pdf`, `*.eps` and `*.svg`. Files of a type the project already has are added too, but never the compiled PDF. |
| `page_token` | `false` | `true` makes the page answer only the agent's tools and browsers that opened its link, for a machine other people log in to. `tex-mcp-web link` prints the link, with a token kept in `~/.config/tex-mcp-web/page-token` that only you can read. After the first visit the browser keeps the token, so the plain address works there and can be bookmarked. |
| `update_check` | `true` | Whether the page checks once a day for a newer release, which the ⋯ menu then offers as **Update to v…**. |
| `agents` | None | Agents the bell can call beside the session that is listening. See [Calling a second agent](reviewing.md#calling-a-second-agent). |

`translation` has three keys:

- `language` is the language the translation pane shows the source in.
- `command` is the command line that translates, as one string, split the way a shell splits
  it, or as a list of its words. It must run an LLM tool once without asking anything, read
  the request on standard input, and print only the answer. If the tool takes the request only
  as an argument, put `{prompt}` in that argument. The command should name its model,
  preferably a mid-sized one, because a paper has hundreds of paragraphs, and should start no
  tools or servers of its own.
- `instructions` adds rules for the translator, one per line.

You can also set `command` for every paper in `~/.config/tex-mcp-web/config.yaml`, under the
same `translation: {command: ...}` key. Nothing else is read from that file, and a `command`
in the paper's own file takes precedence. In the paper's file, `instructions` and `command`
need `language`.

Nothing is translated until both `language` and `command` are set. If you ask your agent to
set up translation, it builds the command from the installed tool's own `--help`, tests it
once, and asks you before saving it. Each paragraph is translated once for each version of its
text and each language, and of a figure or table only the caption is translated.
