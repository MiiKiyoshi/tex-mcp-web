// The ⋯ menu's Update, the version line under it, and the band across the top that says what
// is left once an update is installed. The page's server runs inside the agent's process, so
// an update changes the files on disk and the server keeps the code it runs until the agent
// connects again: the band says which step is left, the agent's, while what is on disk is not
// what runs, and then the page's own, to load what the new server serves. The server says
// what is newer (GET /update): a release with a higher version, for a tool on PyPI, or the
// commits the upstream of the tool's git clone has that the clone lacks, for one that is not.
// Its look is update.css.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { h } from "./dom.js";
import { refusalOf } from "./comment-api.js";

// Whether *version* is higher than *than*: 0.10.0 is after 0.9.1.
function higher(version, than) {
  const [a, b] = [version, than].map((each) => each.split(".").map(Number));
  const at = a.findIndex((part, index) => part !== (b[index] ?? 0));
  return at !== -1 && a[at] > (b[at] ?? 0);
}

// What the server says is newer, as the menu offers it and as the band names it, or null.
function offerOf(told) {
  if (told.latest && higher(told.latest, told.version)) {
    return { item: `Update to v${told.latest}`, out: `v${told.latest} is out` };
  }
  if (told.ahead > 0) {
    const commits = `${told.ahead} new ${told.ahead === 1 ? "commit" : "commits"}`;
    return { item: `Update (${commits})`, out: `${commits} ${told.ahead === 1 ? "is" : "are"} out` };
  }
  return null;
}

// The Update item and the version line drawn at the end of *menu*, the page's ⋯ menu, and
// the band before *before*. *name* is the tool's name, as tex-mcp-web, *server* the name the
// agent knows its MCP server by, as tex-mcp, and *how* the address of the page that says how
// to update, or null. read() asks the server what runs and what is newer: the page calls it
// each time its socket opens, since a server that comes back may be a new version's.
export function createUpdate({ name, server, how, menu, before }) {
  const button = h("button", { id: "update-btn", type: "button", role: "menuitem", hidden: "" });
  const line = h("p", { id: "version-line", class: "version-line" });
  menu.append(button, line);
  const text = h("span", { id: "update-alert-text" });
  const link = how === null ? null
    : h("a", { id: "update-alert-how", href: how, target: "_blank", rel: "noopener", text: "How to update" });
  const reload = h("button", { id: "update-reload-btn", type: "button", hidden: "", text: "Reload",
    onclick: () => location.reload() });
  const band = h("div", { id: "update-alert", class: "update-alert", role: "status", hidden: "" }, text, link, reload);
  before.before(band);
  const ask = `Ask your agent to update ${name}.`;
  // What the server ran when the page loaded, and what it offers now.
  let pageVersion = null;
  let offer = null;

  function say(words, { showHow = false, showReload = false } = {}) {
    text.textContent = words ?? "";
    if (link !== null) link.hidden = !showHow;
    reload.hidden = !showReload;
    band.hidden = !words;
  }

  async function read() {
    const response = await fetch("/update");
    if (!response.ok) return null;
    const told = await response.json();
    pageVersion ??= told.version;
    offer = offerOf(told);
    line.textContent = `${name} v${told.version}`;
    button.hidden = offer === null || told.on_disk !== told.version;
    button.textContent = offer?.item ?? "";
    button.dataset.selfUpdate = String(told.self_update);
    if (told.version !== pageVersion) {
      say(`${name} v${told.version} is now running. Reload this page to use it.`, { showReload: true });
    } else if (told.on_disk !== told.version) {
      say(`Updated to v${told.on_disk}. Reconnect ${server} in your agent to start using it.`, { showHow: true });
    }
    return told;
  }

  async function run() {
    if (button.dataset.selfUpdate !== "true") {
      say(`${offer.out}. ${ask}`, { showHow: true });
      return;
    }
    button.disabled = true;
    say(`Updating ${name}…`);
    try {
      const response = await fetch("/update", { method: "POST" });
      if (!response.ok) throw new Error((await refusalOf(response)).error);
      const told = await read();
      // The commands ran and brought nothing newer, as in a clone that follows another branch.
      if (told && told.on_disk === told.version) {
        say(`This install is still v${told.version} after the update. ${ask}`, { showHow: true });
      }
    } catch (error) {
      say(`The update failed.\n${error.message}`, { showHow: true });
    } finally {
      button.disabled = false;
    }
  }

  button.addEventListener("click", () => run());
  return { read };
}
