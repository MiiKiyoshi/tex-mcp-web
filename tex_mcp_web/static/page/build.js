// The build: Compile, also Ctrl+Enter (Cmd+Enter) and :make with Vim keys, what the top bar says
// of the build, and what the Preview says while there is no PDF to show. While a build runs, the
// Preview shows the PDF of the build before it.
//
// createBuild takes paper (digest(), setDigest(digest), refresh(), current()), pdf (view(),
// open(view), shown()), overleaf, the page's knowledge of the Overleaf project
// (overleaf-project.js), of which it reads project(), cookie() and fullSync(), source's dirty()
// and save(), refreshComments() and showTab(name). It gives attach() and attachCompileKey(),
// compile() and compileFromKeys(), start(), showProblems(), failed(), whether the last build
// failed, renderNoPaper(), applied(listener), which hands in a function called with each build's
// result once the page shows it, and what the socket hears of builds: heardCompiling,
// heardRequested, heardCompiled, heardSourceChanged, configReloaded, configFailed and heardState,
// each given the message.

import { $, h, eventOrigin } from "../shared/dom.js";
import { responseError } from "../shared/comment-api.js";
import { setSidebarCollapsed } from "../shared/sidebar.js";
import { createListeners } from "../shared/listeners.js";

export function createBuild({ paper, pdf, overleaf, source, refreshComments, showTab }) {
  const applied = createListeners();
  const state = {
    appliedCompileTimestamp: null,
    compileRefreshPromise: null,
    // What the top bar says of the build: how the last one went or that one runs, what a
    // request to compile made while it runs leads to, whether a source file changed since,
    // and a config the server could not take, until it takes one or a build begins or ends.
    compileStatus: "idle",
    compileNote: "",
    sourceAhead: false,
    configError: "",
    // Whether the server runs a build, as it last said.
    serverCompiling: false,
    // Whether the last build failed, which the Preview and the translation say while there is
    // no PDF.
    buildFailed: false,
    // The main file's name while it is not in the folder, so that nothing was built.
    noMain: "",
  };

  // A folder without its paper yet, as when the paper is only on Overleaf: the Preview says what
  // brings it, which changes once the project is connected.
  function renderNoPaper() {
    if (!state.noMain || pdf.shown()) return;
    const next = !overleaf.project() ? "If the paper is on Overleaf, Connect Overleaf brings it here."
      : !overleaf.cookie() ? "Save your Overleaf cookie in Settings to bring the paper from Overleaf."
      : overleaf.fullSync() ? "The paper is coming from Overleaf." : "Get brings the paper from Overleaf.";
    $("#pdf-viewer").replaceChildren(h("p", { class: "placeholder" }, `${state.noMain} is not in this folder. ${next}`));
  }

  function applyCompileResult(result) {
    const duration = typeof result.duration_seconds === "number"
      ? ` · ${result.duration_seconds.toFixed(1)}s`
      : "";
    state.compileStatus = (result.success ? "✓ ok" : "✗ failed") + duration;
    state.compileNote = "";
    state.configError = "";
    renderCompileStatus();
    state.buildFailed = !result.success;
    // Until a build makes a PDF, the Preview says why there is none and where to read it, and
    // so does the translation, which follows the PDF's pages.
    if (!result.success && !pdf.shown()) {
      state.noMain = result.no_main ? result.errors[0].file : "";
      if (state.noMain) renderNoPaper();
      else $("#pdf-viewer").replaceChildren(h("p", { class: "placeholder" }, "The build failed. See Problems for details. ",
        h("button", { type: "button", onclick: showProblems }, "Show Problems")));
    }
    applied.tell(result);
  }

  function showProblems() {
    setSidebarCollapsed(false);
    showTab("compile");
  }

  function showCompileButton(compiling) {
    const button = $("#compile-btn");
    button.disabled = compiling;
    button.querySelector(".wide-label").textContent = compiling ? "Compiling…" : "Compile";
  }

  function applyCompiling(compiling) {
    showCompileButton(compiling);
    if (compiling) {
      // A build that starts now reads the sources as they are. Until it ends, the Preview
      // shows the PDF of the build before it, when there is one.
      state.compileStatus = paper.digest() ? "compiling… · showing the previous build" : "compiling…";
      state.compileNote = "";
      state.sourceAhead = false;
      state.configError = "";
      renderCompileStatus();
    }
  }

  function renderCompileStatus() {
    $("#compile-status").textContent = state.configError ? `config error: ${state.configError}`
      : state.compileStatus + (state.compileNote ? ` · ${state.compileNote}` : "")
        + (state.sourceAhead ? " · source changed, Compile to update" : "");
  }

  async function applyCompletedCompile(result, pdfDigest) {
    const timestamp = result.timestamp;
    if (timestamp && timestamp === state.appliedCompileTimestamp) {
      if (state.compileRefreshPromise) await state.compileRefreshPromise;
      showCompileButton(state.serverCompiling);
      return;
    }

    state.appliedCompileTimestamp = timestamp;
    const refresh = (async () => {
      applyCompileResult(result);
      if (result.success) {
        const pdfView = pdf.view();
        if (pdfDigest === undefined) await paper.refresh();
        else paper.setDigest(pdfDigest);
        await pdf.open(pdfView);
        await refreshComments();
      }
      if (pdfDigest !== undefined || !result.success) await paper.refresh();
    })();
    state.compileRefreshPromise = refresh;
    try {
      await refresh;
    } finally {
      if (state.compileRefreshPromise === refresh) state.compileRefreshPromise = null;
      // The build that follows this one may have begun while its PDF loaded.
      showCompileButton(state.serverCompiling);
    }
  }

  // Ctrl+Enter (Cmd+Enter), or :make with Vim keys, compiles as the button does, saving the
  // editor's changes first so that the PDF shows them. While a build runs, the server answers
  // with that build, or with one after it when a source changed since the build began, and
  // the top bar says which. A build that cannot start says why.
  async function compileFromKeys() {
    try {
      if (source.dirty()) await source.save();
      // Edits the file did not take, since it changed on disk, as the editor's status says,
      // are not what a build would show.
      if (source.dirty()) return;
      await compile();
    } catch (error) {
      alert(`Could not compile: ${error.message}`);
    }
  }

  async function compile() {
    applyCompiling(true);
    try {
      const response = await fetch("/compile", { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response));
      await applyCompletedCompile(await response.json());
    } catch (error) {
      applyCompiling(false);
      alert(`Could not compile: ${error.message}`);
    }
  }

  // What the socket hears of builds.
  function heardCompiling(message) {
    state.serverCompiling = Boolean(message.status);
    if (message.status) applyCompiling(true);
  }

  function heardRequested(message) {
    state.compileNote = message.follows
      ? "another build will follow to include the latest changes"
      : "this build already includes the latest changes";
    renderCompileStatus();
  }

  async function heardCompiled(message) {
    await applyCompletedCompile(message.result, message.pdf_digest);
    // A source saved while the build ran, which no build follows to take.
    if (message.sources_ahead && !state.serverCompiling) {
      state.sourceAhead = true;
      renderCompileStatus();
    }
  }

  function heardSourceChanged(message) {
    // The PDF shows the sources as the last build read them, and the server says whether
    // the change is one it lacks: a file that build read as it is now is not, however late
    // the change is heard of.
    if (message.ahead) {
      state.sourceAhead = true;
      renderCompileStatus();
    }
  }

  function configReloaded() {
    state.configError = "";
    renderCompileStatus();
  }

  function configFailed(message) {
    state.configError = message.error;
    renderCompileStatus();
  }

  function heardState(message) {
    if (message.result) applyCompileResult(message.result);
    // A build that ended while the page could not hear of it leaves Compile free again.
    state.serverCompiling = Boolean(message.compiling);
    applyCompiling(state.serverCompiling);
  }

  function start() {
    // A build already running says so from the start, before the page's socket connects.
    state.serverCompiling = Boolean(paper.current().compiling);
    applyCompiling(state.serverCompiling);
  }

  function attach() {
    $("#compile-btn").addEventListener("click", () => compile());
  }

  function attachCompileKey() {
    // Ctrl+Enter (Cmd+Enter) compiles from anywhere but a field being written in, where it
    // sends what is written, and an open dialog.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
      const inEditor = event.target.closest?.("#source-editor");
      if (document.querySelector("dialog[open]")
        || (!inEditor && eventOrigin(event).matches?.("textarea, input, select, [contenteditable]"))) return;
      event.preventDefault();
      event.stopPropagation();
      compileFromKeys();
    }, true);
  }

  return {
    attach, attachCompileKey, compile, compileFromKeys, start, showProblems, renderNoPaper,
    failed: () => state.buildFailed,
    applied: applied.add,
    heardCompiling, heardRequested, heardCompiled, heardSourceChanged, configReloaded, configFailed, heardState,
  };
}
