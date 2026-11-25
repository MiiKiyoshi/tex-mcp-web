// What the page knows of the paper's Overleaf project, as the server last said at /overleaf or in
// the answer to a change of it. This module alone writes these facts; the page's other parts read
// them through the functions it gives.
//
// createOverleafProject takes nothing. It gives read(), the server's answer at /overleaf, or null
// when it gave none; apply(answer), which takes in what such an answer says, read at /overleaf or
// given by a request that changed the project; heardWaiting(count), the number of threads waiting
// as the socket tells it; and the facts: project(), cookie(), fullSync(), waiting(), remote(),
// imported(id), importedCount(), unplaced(id) and suggested().

export function createOverleafProject() {
  const known = {
    // The project's link, or null, and where the cookie comes from ("file" or "environment", null
    // when there is none).
    project: null,
    cookie: null,
    // Whether the files are kept the same both ways (which brings new threads in by themselves),
    // and how many open threads on Overleaf wait to be synced.
    fullSync: false,
    waiting: 0,
    // The git remote of a folder cloned from the project (null when it is none).
    remote: null,
    // Comments brought in from Overleaf, and where on Overleaf the comments from there are whose
    // words are not in the file here yet.
    imported: new Set(),
    unplaced: {},
    // The edits made on Overleaf with track changes on, waiting to be accepted or rejected.
    suggested: [],
  };

  async function read() {
    const response = await fetch("/overleaf", { cache: "no-store" });
    return response.ok ? response.json() : null;
  }

  function apply(answer) {
    known.project = answer.project ?? null;
    known.cookie = answer.cookie ?? null;
    known.fullSync = Boolean(answer.live);
    known.waiting = answer.waiting ?? 0;
    known.remote = answer.git ?? null;
    known.imported = new Set(answer.imported ?? []);
    known.unplaced = answer.unplaced ?? {};
    known.suggested = answer.suggested ?? [];
  }

  function heardWaiting(count) {
    known.waiting = count;
  }

  return {
    read,
    apply,
    heardWaiting,
    project: () => known.project,
    cookie: () => known.cookie,
    fullSync: () => known.fullSync,
    waiting: () => known.waiting,
    remote: () => known.remote,
    imported: (id) => known.imported.has(id),
    importedCount: () => known.imported.size,
    unplaced: (id) => known.unplaced[id],
    suggested: () => known.suggested,
  };
}
