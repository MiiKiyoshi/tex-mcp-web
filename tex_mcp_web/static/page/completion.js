// The editor offers what fits where the cursor is, as an editor for LaTeX does: the paper's
// labels after \ref and its kin, its bib entries after \cite and its kin (a title's words
// find them too), environments after \begin and \end, its .tex files after \input, its
// pictures after \includegraphics, and commands after a backslash, the paper's own among
// them. The lists come from the server and are read again after a save or a compile;
// labels in the open file come from the editor, so one just typed is offered at once.
//
// createCompletion takes sourcePath(), the open file. It gives attach(editor) and
// schedule(delay), which reads the lists again.


const COMPLETION_WORD = /[^\\{},\s]/;
const ARGUMENT_COMMANDS = {
  ref: /^(ref|cref|Cref|eqref|autoref|pageref|nameref|vref|labelcref|crefrange|Crefrange)$/,
  cite: /^(cite|citep|citet|citealp|citealt|citeauthor|citeyear|parencite|textcite|autocite|footcite|nocite)$/,
  environment: /^(begin|end)$/,
  input: /^(input|include|subfile|includeonly)$/,
  graphics: /^includegraphics$/,
  bibliography: /^(bibliography|addbibresource)$/,
};

const ENVIRONMENTS = [
  "figure", "figure*", "table", "table*", "tabular", "tabularx", "itemize", "enumerate",
  "description", "equation", "equation*", "align", "align*", "gather", "gather*", "multline",
  "split", "cases", "array", "matrix", "bmatrix", "pmatrix", "center", "minipage", "subfigure",
  "algorithm", "algorithmic", "abstract", "quote", "verbatim", "lstlisting", "theorem", "lemma",
  "proof", "definition", "IEEEkeywords", "thebibliography",
];

// Name and what follows it, in Ace's snippet syntax: $1 is where the cursor goes.
const COMMANDS = [
  ["textbf", "{$1}"], ["textit", "{$1}"], ["emph", "{$1}"], ["texttt", "{$1}"], ["textsc", "{$1}"],
  ["underline", "{$1}"], ["textsuperscript", "{$1}"], ["textsubscript", "{$1}"],
  ["section", "{$1}"], ["section*", "{$1}"], ["subsection", "{$1}"], ["subsubsection", "{$1}"],
  ["paragraph", "{$1}"], ["label", "{$1}"], ["ref", "{$1}"], ["cref", "{$1}"], ["Cref", "{$1}"],
  ["eqref", "{$1}"], ["autoref", "{$1}"], ["cite", "{$1}"], ["citep", "{$1}"], ["citet", "{$1}"],
  ["caption", "{$1}"], ["includegraphics", "[width=\\\\linewidth]{$1}"], ["input", "{$1}"],
  ["include", "{$1}"], ["begin", "{$1}"], ["end", "{$1}"], ["item", ""], ["centering", ""],
  ["footnote", "{$1}"], ["url", "{$1}"], ["href", "{$1}{$2}"], ["hline", ""], ["toprule", ""],
  ["midrule", ""], ["bottomrule", ""], ["cline", "{$1}"], ["multicolumn", "{$1}{$2}{$3}"],
  ["multirow", "{$1}{$2}{$3}"], ["vspace", "{$1}"], ["hspace", "{$1}"], ["noindent", ""],
  ["newline", ""], ["newpage", ""], ["clearpage", ""], ["small", ""], ["footnotesize", ""],
  ["scriptsize", ""], ["normalsize", ""], ["large", ""], ["linewidth", ""], ["columnwidth", ""],
  ["textwidth", ""], ["resizebox", "{$1}{$2}{$3}"], ["frac", "{$1}{$2}"], ["sqrt", "{$1}"],
  ["mathrm", "{$1}"], ["mathbf", "{$1}"], ["mathcal", "{$1}"], ["text", "{$1}"],
  ["operatorname", "{$1}"], ["left", ""], ["right", ""], ["cdot", ""], ["times", ""],
  ["ldots", ""], ["approx", ""], ["leq", ""], ["geq", ""], ["neq", ""], ["sum", ""], ["infty", ""],
  ["alpha", ""], ["beta", ""], ["gamma", ""], ["delta", ""], ["lambda", ""], ["mu", ""],
  ["sigma", ""], ["theta", ""], ["usepackage", "{$1}"], ["newcommand", "{\\\\$1}{$2}"],
  ["bibliography", "{$1}"], ["bibliographystyle", "{$1}"],
];
// Commands whose argument has its own list: the list opens once the command is in.
const REOPENS = new Set(["ref", "cref", "Cref", "eqref", "autoref", "cite", "citep", "citet",
  "includegraphics", "input", "include", "begin", "end", "bibliography"]);

// What the cursor is in: a command's argument (the kind and the command), or a command name.
function completionContext(before) {
  const argument = before.match(/\\([A-Za-z]+)\*?(?:\[[^\]]*\])*\{([^{}]*)$/);
  if (argument) {
    for (const [kind, pattern] of Object.entries(ARGUMENT_COMMANDS)) {
      if (pattern.test(argument[1])) return { kind, command: argument[1] };
    }
    return null;
  }
  return /\\[A-Za-z@]*\*?$/.test(before) ? { kind: "command" } : null;
}

// Environments begun above the cursor and not yet ended, innermost last.
function openEnvironments(editor, row) {
  const open = [];
  const text = editor.session.getLines(0, row).join("\n").replace(/(^|[^\\])%.*$/gm, "$1");
  for (const match of text.matchAll(/\\(begin|end)\{([^{}]+)\}/g)) {
    if (match[1] === "begin") open.push(match[2]);
    else if (open.at(-1) === match[2]) open.pop();
  }
  return open;
}

export function createCompletion({ sourcePath }) {
  const completion = { data: null, timer: null };

  function scheduleCompletions(delay = 800) {
    clearTimeout(completion.timer);
    completion.timer = setTimeout(() => loadCompletions().catch((error) => console.error(error)), delay);
  }

  async function loadCompletions() {
    const response = await fetch("/completions");
    if (response.ok) completion.data = await response.json();
  }


  function bufferLabels(editor) {
    const found = [];
    editor.session.getDocument().getAllLines().forEach((line, row) => {
      for (const match of line.replace(/(^|[^\\])%.*$/, "$1").matchAll(/\\label\s*\{([^{}]*)\}/g)) {
        if (match[1].trim()) found.push({ name: match[1].trim(), file: sourcePath(), line: row + 1 });
      }
    });
    return found;
  }

  function completionItems(editor, context, row) {
    const data = completion.data ?? {};
    const item = (fields) => ({ completer: texCompleter, kind: context.kind, ...fields });
    switch (context.kind) {
      case "ref": {
        const labels = (data.labels ?? []).filter((label) => label.file !== sourcePath())
          .concat(bufferLabels(editor));
        return labels.map((label) => item({
          caption: label.name, value: label.name, meta: `${label.file.split("/").pop()}:${label.line}`,
          score: label.file === sourcePath() ? 2 : 1,
        }));
      }
      case "cite":
        return (data.citations ?? []).map((entry) => item({
          caption: entry.title ? `${entry.key}  ${entry.title.slice(0, 48)}` : entry.key,
          value: entry.key, meta: [entry.author, entry.year].filter(Boolean).join(" "),
          docText: [entry.title, [entry.author, entry.year].filter(Boolean).join(", ")].filter(Boolean).join("\n"),
          score: 1,
        }));
      case "environment": {
        const names = [...new Set([...(data.environments ?? []), ...ENVIRONMENTS])];
        const open = context.command === "end" ? openEnvironments(editor, row) : [];
        return names.map((name) => item({
          caption: name, value: name, meta: "environment", begin: context.command === "begin",
          score: open.length && name === open.at(-1) ? 3 : 1,
        }));
      }
      case "input":
        return (data.tex_files ?? []).map((path) => item({
          caption: path.replace(/\.tex$/, ""), value: path.replace(/\.tex$/, ""), meta: "file", score: 1,
        }));
      case "graphics":
        return (data.graphics ?? []).map((path) => item({ caption: path, value: path, meta: "picture", score: 1 }));
      case "bibliography":
        return (data.bib_files ?? []).map((path) => item({
          caption: path.replace(/\.bib$/, ""), value: path.replace(/\.bib$/, ""), meta: "bib", score: 1,
        }));
      case "command": {
        const own = (data.commands ?? []).map((command) => [command.name,
          Array.from({ length: command.args }, (_, index) => `{$${index + 1}}`).join(""), "paper"]);
        const seen = new Set(own.map(([name]) => name));
        return own.concat(COMMANDS.filter(([name]) => !seen.has(name)).map(([name, rest]) => [name, rest, "command"]))
          .map(([name, rest, meta]) => item({
            caption: `\\${name}`, value: name, rest, meta, score: meta === "paper" ? 2 : 1,
          }));
      }
      default:
        return [];
    }
  }

  const texCompleter = {
    id: "tex",
    identifierRegexps: [COMPLETION_WORD],
    triggerCharacters: ["\\", "{", ","],
    getCompletions(editor, session, pos, prefix, callback) {
      const context = completionContext(session.getLine(pos.row).slice(0, pos.column));
      callback(null, context === null ? [] : completionItems(editor, context, pos.row));
    },
    // The typed part of the word goes, and so does the rest of the word after the cursor.
    // An argument gets its closing brace unless one follows, and \begin{name} gets its
    // \end{name} with the cursor on the line between.
    insertMatch(editor, data) {
      const { row, column } = editor.getCursorPosition();
      const line = editor.session.getLine(row);
      let start = column;
      while (start > 0 && COMPLETION_WORD.test(line[start - 1])) start -= 1;
      let end = column;
      while (end < line.length && COMPLETION_WORD.test(line[end])) end += 1;
      const Range = window.ace.require("ace/range").Range;
      const { snippetManager } = window.ace.require("ace/snippets");
      const escape = (text) => text.replace(/[\\$]/g, "\\$&");   // a bare } is plain text here
      const closed = line[end] === "}";
      if (data.kind === "environment" && data.begin && closed) end += 1;
      editor.session.remove(new Range(row, start, row, end));
      editor.moveCursorTo(row, start);
      let snippet;
      if (data.kind === "command") snippet = escape(data.value) + data.rest;
      else if (data.kind === "environment" && data.begin) {
        snippet = `${escape(data.value)}}\n\t$0\n\\\\end{${escape(data.value)}}`;
      } else snippet = escape(data.value) + (closed || line[end] === "," ? "" : "}");
      snippetManager.insertSnippet(editor, snippet);
      if (data.kind === "command" && REOPENS.has(data.value)) {
        setTimeout(() => editor.execCommand("startAutocomplete"), 0);
      }
    },
  };

  function attachCompletion(editor) {
    window.ace.require("ace/ext/language_tools");
    editor.completers = [texCompleter];
    editor.setOptions({ enableBasicAutocompletion: [texCompleter], enableLiveAutocompletion: [texCompleter] });
    // Enter takes a choice only when it changes the text: after \item typed whole, Enter
    // starts the next line.
    const { Autocomplete } = window.ace.require("ace/autocomplete");
    const completer = Autocomplete.for(editor);
    completer.keyboardHandler.bindKey("Return", (target) => {
      const chosen = completer.popup?.getData(completer.popup.getRow());
      if (!chosen || chosen.value === (completer.completions?.filterText ?? "")) {
        completer.detach();
        return false;
      }
      return completer.insertMatch();
    });
    // A brace, comma or backslash starts a new list where one is open: Ace would only
    // narrow the open one, \label's for instance, by "label{" and close it.
    editor.commands.on("afterExec", (event) => {
      if (event.command.name !== "insertstring" || !texCompleter.triggerCharacters.includes(event.args)) return;
      if (!completer.activated) return;
      completer.detach();
      completer.autoShown = true;
      completer.showPopup(editor);
    });
    scheduleCompletions(0);
  }

  return { attach: attachCompletion, schedule: scheduleCompletions };
}
