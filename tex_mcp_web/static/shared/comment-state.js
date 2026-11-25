// What the comment panel holds of the comments it shows: the comments its list shows, in order;
// those whose cards are open; those picked, with the one a pick last started from; the one the
// ring is on; the comment tab shown; and how many times the panel was reset, letting go of what
// it held for the document it showed. The panel's own files share it, and no page holds it: a
// page reads it through the panel (comment-panel.js). A reader gives the value at the moment it
// is called, and an array as a copy of its own.
//
// createCommentState(firstTab), the tab shown first, gives:
//   listed()           the comments the list shows, in order      setListed(comments)
//   isOpen(id)         whether a comment's card is open           setOpen(id, open)
//   ringed()           the ringed comment's id, or null           ring(id)
//   picked()           the picked ids                             isPicked(id)
//   pick(ids, on)      picks each of ids, or lets each go         clearPicks()
//   pickedFrom()       the comment a pick last started from       pickFrom(id)
//   swapPicked(ids)    ids picked in place of those picked until restore(), which it gives
//   tab()              the comment tab shown                      setTab(name)
//   generation()       how many times the panel was reset         bump(), one more
//   clear()            the comments, open cards, picks and ring let go
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

export function createCommentState(firstTab) {
  let comments = [];
  const open = new Set();
  let picked = new Set();
  let from = null;
  let ringedId = null;
  let shownTab = firstTab;
  let resets = 0;

  return {
    listed: () => [...comments],
    setListed(list) {
      comments = [...list];
    },
    isOpen: (id) => open.has(id),
    setOpen(id, opened) {
      if (opened) open.add(id);
      else open.delete(id);
    },
    ringed: () => ringedId,
    ring(id) {
      ringedId = id;
    },
    picked: () => [...picked],
    isPicked: (id) => picked.has(id),
    pick(ids, on) {
      for (const id of ids) {
        if (on) picked.add(id);
        else picked.delete(id);
      }
    },
    clearPicks() {
      picked.clear();
    },
    pickedFrom: () => from,
    pickFrom(id) {
      from = id;
    },
    // A press on the row's buttons for a range reads the range as what is picked, and the picks
    // made before come back once it has read them.
    swapPicked(ids) {
      const kept = picked;
      picked = new Set(ids);
      return () => { picked = kept; };
    },
    tab: () => shownTab,
    setTab(name) {
      shownTab = name;
    },
    generation: () => resets,
    bump() {
      resets += 1;
    },
    clear() {
      comments = [];
      open.clear();
      picked.clear();
      from = null;
      ringedId = null;
    },
  };
}
