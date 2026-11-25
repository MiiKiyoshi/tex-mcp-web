// The paper as the server last described it at /paper: its main file, named in the top bar, its
// sections, its translation language and whether a build runs, and the digest of the last
// build's PDF, which names the PDF the Preview loads and the one a comment on it is placed on.
// createPaper takes nothing. It gives refresh(), current(), the paper as last read, digest(),
// setDigest(digest), for a build that ends with a new PDF, and read(listener), which hands in a
// function called with the paper each time it is read.

import { $ } from "../shared/dom.js";
import { responseError } from "../shared/comment-api.js";
import { createListeners } from "../shared/listeners.js";

export function createPaper() {
  const read = createListeners();
  const state = {
    paper: null,
    pdfDigest: null,
  };

  async function refreshPaper() {
    const response = await fetch("/paper");
    if (!response.ok) throw new Error(await responseError(response));
    state.paper = await response.json();
    state.pdfDigest = state.paper.pdf_digest;
    $("#main-file").textContent = state.paper.main_file;
    read.tell(state.paper);
  }

  return {
    refresh: refreshPaper,
    current: () => state.paper,
    digest: () => state.pdfDigest,
    setDigest: (digest) => { state.pdfDigest = digest; },
    read: read.add,
  };
}
