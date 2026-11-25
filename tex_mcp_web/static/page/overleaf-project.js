// What the page knows of the paper's Overleaf project, as the server last said at /overleaf or in
// the answer to a change of it. This module alone writes these facts; the page's other parts read
// them through the functions it gives.
//
// createOverleafProject takes nothing. It gives read(), the server's answer at /overleaf, or null
// when it gave none; apply(answer), which takes in what such an answer says, read at /overleaf or
// given by a request that changed the project; heardWaiting(count), the number of threads waiting
// as the socket tells it; heardCookie(refused), what the page heard of the cookie from Overleaf;
// changed(listener), which hands in a function called after each of these three;
// heardFollowed(files), the files the live session keeps in step, which changes nothing shown;
// and the facts: project(), cookie(), cookieRefused(), fullSync(), waiting(), remote(),
// imported(id), importedCount(), unplaced(id), suggested() and followed(path), whether the live
// session keeps a file in step with Overleaf, so that its edits there are written here.

import { createListeners } from "../shared/listeners.js";

export function createOverleafProject() {
  const changes = createListeners();
  const known = {
    // The project's link, or null, and where the cookie comes from ("file" or "environment", null
    // when there is none).
    project: null,
    cookie: null,
    // Whether Overleaf turned the cookie away, as the live session or a sync last heard it, until
    // a new cookie is saved.
    cookieRefused: false,
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
    // The paths of the files the live session keeps in step with Overleaf.
    followed: new Set(),
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
    changes.tell();
  }

  function heardWaiting(count) {
    known.waiting = count;
    changes.tell();
  }

  function heardFollowed(files) {
    known.followed = new Set(files.map((file) => file.path));
  }

  // That Overleaf turned the cookie away (true), let a session in with it (false), or nothing of
  // it (undefined), which changes nothing.
  function heardCookie(refused) {
    if (refused === undefined) return;
    known.cookieRefused = refused;
    changes.tell();
  }

  return {
    read,
    apply,
    heardWaiting,
    heardCookie,
    heardFollowed,
    changed: changes.add,
    project: () => known.project,
    cookie: () => known.cookie,
    cookieRefused: () => known.cookieRefused,
    fullSync: () => known.fullSync,
    waiting: () => known.waiting,
    remote: () => known.remote,
    imported: (id) => known.imported.has(id),
    importedCount: () => known.imported.size,
    unplaced: (id) => known.unplaced[id],
    suggested: () => known.suggested,
    followed: (path) => known.followed.has(path),
  };
}
