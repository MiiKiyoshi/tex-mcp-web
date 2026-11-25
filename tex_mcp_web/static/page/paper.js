// The paper as the server last described it at /paper: its main file, named in the top bar, its
// sections, its translation language and whether a build runs, and the digest of the last
// build's PDF, which names the PDF the Preview loads and the one a comment on it is placed on.
// createPaper takes read(paper), told each time the paper is read. It gives refresh(), current(),
// the paper as last read, digest(), and setDigest(digest), for a build that ends with a new PDF.

import { $ } from "../shared/dom.js";
import { responseError } from "../shared/comment-api.js";

export function createPaper({ read }) {
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
    read(state.paper);
  }

  return {
    refresh: refreshPaper,
    current: () => state.paper,
    digest: () => state.pdfDigest,
    setDigest: (digest) => { state.pdfDigest = digest; },
  };
}
