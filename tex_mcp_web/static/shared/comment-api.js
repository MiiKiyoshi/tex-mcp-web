// The comment calls of the review pages. Every page's server answers them on the same routes,
// under a base the page names: GET <base>/comments[?status=], POST <base>/comments, POST
// <base>/comments/status, POST <base>/comments/<id>/<action> and DELETE <base>/comments/<id>.
// The statuses are "open", "resolved" and "archived", and a refusal is JSON { error, reason }.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

// What a refused request says: its reason in words, and the kind of reason, when the server
// names one, for the page to act on. A refusal that is not JSON is said as the status and text.
export async function refusalOf(response) {
  const text = await response.text();
  if (!text) return { error: String(response.status) };
  try {
    const { error, reason } = JSON.parse(text);
    return { error, reason };
  } catch {
    return { error: `${response.status}: ${text}` };
  }
}

// The calls, each under the base `base()` names when it is made: "" for the page's own routes,
// or null while the page shows nothing to comment on, when the list is empty and a change is
// refused. Each answers { ok: true, data }, with the JSON the server sent back or null, or
// { ok: false, error, reason, conflict }: the refusal, and whether it was a 409, refused
// because something changed since.
//
//   list(status)                  "open", "resolved", "archived" or "all": { comments }
//   create(body)                  a new comment
//   setStatus(ids, status, resolved)   comments sent to one status, `resolved` giving comments
//                                 resolved again the moments they were resolved at, by id
//   mutate(id, action, body)      "reply", "edit", "delete-entry", "apply-suggestion",
//                                 "reject-suggestion", the page's own actions, or "delete"
export function createCommentApi(base) {
  async function call(path, init) {
    const at = base();
    if (at === null) return { ok: false, error: "Nothing is shown to comment on.", conflict: false };
    const response = await fetch(`${at}${path}`, init);
    if (!response.ok) return { ok: false, conflict: response.status === 409, ...(await refusalOf(response)) };
    const json = response.headers.get("Content-Type")?.includes("application/json");
    return { ok: true, data: json ? await response.json() : null };
  }
  const post = (path, body) => call(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return {
    list: (status) => (base() === null ? Promise.resolve({ ok: true, data: { comments: [] } })
      : call(status === "all" ? "/comments" : `/comments?status=${status}`)),
    create: (body) => post("/comments", body),
    setStatus: (ids, status, resolved) => post("/comments/status", { ids, status, resolved }),
    mutate: (id, action, body) => (action === "delete" ? call(`/comments/${id}`, { method: "DELETE" })
      : post(`/comments/${id}/${action}`, body)),
  };
}
