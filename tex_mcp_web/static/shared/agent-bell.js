// The bell, which calls the agent to the comments. It ends the comment panel's row: green while
// an agent listens, and red while a press would be queued until one does, with the word a press
// leaves beside it for a moment, Called, Queued or Failed. Ctrl+Alt+Enter presses it from
// anywhere on the page but over a dialog. A note over the list says that a call waits for an
// agent to listen, or, once a comment or a reply is posted while comments wait for the agent,
// that they reach it at the bell. When the page's server names agents beside the session
// listening, lists beside the bell pick the agent, and the model and effort that agent lists.
// A page that offers more than one way to call has a button beside the bell that names the way
// chosen; a press on it chooses the next.
//
// The comment panel makes it from the page's `bell` hooks (createCommentPanel), draws it into
// its row and over its Undo line, fits the row to it, and tells it what the list holds. The page
// tells it what the page hears with panel.bell.show. Its look is agent-bell.css.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, keyNames } from "./dom.js";

// The key that presses the bell, as this keyboard names it.
const KEY = keyNames("Ctrl+Alt+Enter");

// The hooks the page gives, each with what the bell does without it. Either `call` or `ways` is
// required. A name the bell does not know is refused.
const DEFAULTS = {
  // call(lane), a press: it posts the press to the page's server and answers what the server
  // answers, `delivered` false when the call is queued until an agent listens. An answer with
  // `said` shows that word after the press in place of Called or Queued. `lane` is the agent
  // picked beside the bell, { agent, model, effort }, or null for the session listening. A press
  // the server refuses throws.
  call: null,
  // The ways to call, for a page that offers more than one, in place of `call`, each as WAY
  // lists. The first is chosen until the reader chooses another, which the browser keeps.
  ways: null,
  // The page's own words: `queued`, the note after a press no agent heard, and `gone`, the
  // bell's title while the page cannot reach its server.
  words: { queued: "No agent is listening yet, so the call is queued until one listens.",
           gone: "The page's server cannot be reached" },
  // How long the word a press leaves stays, in milliseconds.
  wordStays: 2000,
};

// A way to call: `name`, its word on the button that chooses it; `does`, what a press does by
// it, as "start the command", which that button's title says; `call(lane)`, the press, as
// above; `listens`, false for a way that needs no session listening, whose bell is green while
// the page reaches its server and whose title is `does`; and `note()`, what the note over the
// list says while the way is chosen, or "" for the bell's own words. The bell reads `note`
// each time it is drawn, and panel.bell.show() with nothing draws it again.
const WAY = { name: null, does: null, call: null, listens: true, note: () => "" };

// How the bell's parts give way as the row does (ROW_STEPS in comment-panel.js), each step after
// the panel's step it names: the word a press leaves goes once the sort list has shrunk, before
// Resolve's; the model and effort lists wait behind the dots once the sort list does; and the
// bell, with the agent list and the way, once the view list gives up its room. A page's own
// steps may come in after these too.
export const BELL_STEPS = {
  "sort-shrunk": [["wordless"]],
  narrow: [["unchosen", "#agent-model-select, #agent-effort-select"]],
  squeezed: [["uncalled", "#call-by-btn, #call-agent-select, #call-agent-btn"]],
};

// The model and effort lists: their element, what a lane lists for them, and what the browser
// keeps the one last picked for each agent under, after the page's prefix.
const LANE_CHOICES = [["#agent-model-select", "models", "AgentModel"], ["#agent-effort-select", "efforts", "AgentEffort"]];

function refuseUnknown(given, known, where) {
  for (const name of Object.keys(given)) {
    if (!(name in known)) throw new Error(`createAgentBell: no hook ${where}${name}`);
  }
}

// The bell drawn at the end of *row* with its note before *noteBefore*, given the page's hooks
// over DEFAULTS. *prefix* is what the browser keeps its choices under, and fit() fits the row
// again after the bell changes width. attach() sets it listening.
export function createAgentBell(given, { prefix, row, noteBefore, fit }) {
  refuseUnknown(given, DEFAULTS, "");
  refuseUnknown(given.words ?? {}, DEFAULTS.words, "words.");
  const host = { ...DEFAULTS, ...given, words: { ...DEFAULTS.words, ...given.words } };
  if ((host.call === null) === (host.ways === null)) throw new Error("createAgentBell: give call or ways");
  const ways = (host.ways ?? [{ call: host.call }]).map((way, at) => {
    refuseUnknown(way, WAY, `ways[${at}].`);
    return { ...WAY, ...way };
  });
  // Waiters parked for the session listening, and each agent the server names beside it with
  // its waiters and the models and efforts a press picks from.
  let waiters = 0;
  let lanes = {};
  // The page cannot reach its server, and a call waits for an agent to listen.
  let gone = false;
  let callWaits = false;
  // A comment or a reply was posted on what is shown, some comment waits for the agent, and the
  // bell was pressed in this visit: what the note goes by.
  let posted = false;
  let awaited = false;
  let called = false;
  let way = ways[0];
  if (ways.length > 1) {
    try { way = ways.find((each) => each.name === localStorage.getItem(`${prefix}CallBy`)) ?? way; } catch { /* the first */ }
  }

  row.insertAdjacentHTML("beforeend", `${ways.length > 1 ? `
    <!-- The way the bell calls by. Behind the dots, where a button says its label, the label
         names the way. -->
    <button id="call-by-btn" class="icon-btn" type="button"><span class="way-name"></span></button>` : ""}
    <!-- Which agent the bell calls, when the server names agents beside the session listening,
         and the model and effort a press for one of them asks for. -->
    <select id="call-agent-select" class="agent-select hidden" autocomplete="off" aria-label="Agent to call" title="Agent the bell calls"></select>
    <select id="agent-model-select" class="agent-select hidden" autocomplete="off" aria-label="Model" title="Model for this call"></select>
    <select id="agent-effort-select" class="agent-select hidden" autocomplete="off" aria-label="Effort" title="Effort for this call"></select>
    <button id="call-agent-btn" class="icon-btn agent-offline" type="button">
      <span id="agent-waiting-dot" class="agent-dot"></span>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 11V7a4 4 0 0 1 8 0v4l1.5 1.5h-11z"/><path d="M6.5 14a1.5 1.5 0 0 0 3 0"/></svg>
      <span id="call-agent-word" class="count"></span>
    </button>`);
  noteBefore.insertAdjacentHTML("beforebegin", '<p id="call-agent-why" class="panel-note" role="status" hidden></p>');

  // Green reaches a parked waiter now. Red stays pressable, as the server queues the call until
  // the next waiter connects. The bell shows the agent picked beside it.
  function renderBell() {
    const lane = lanes[$("#call-agent-select").value];
    const listening = (lane ? lane.waiters : waiters) > 0;
    if (listening) callWaits = false;
    const ready = !gone && (listening || !way.listens);
    const button = $("#call-agent-btn");
    button.classList.toggle("agent-ready", ready);
    button.classList.toggle("agent-offline", !ready);
    button.setAttribute("aria-label", ready ? "Call agent" : "Queue call for agent");
    button.title = gone ? host.words.gone
      : !way.listens ? `${way.does[0].toUpperCase()}${way.does.slice(1)} (${KEY})`
      : ready ? `Call the agent (${KEY})`
      : `No agent is listening. The call will be queued until one connects (${KEY})`;
    if (ways.length > 1) {
      const next = ways[(ways.indexOf(way) + 1) % ways.length];
      const by = $("#call-by-btn");
      by.querySelector(".way-name").textContent = way.name;
      by.setAttribute("aria-label", `The bell calls: ${way.name}`);
      by.title = `The bell is set to ${way.does}. Press to ${next.does} instead.`;
    }
    renderNote();
  }

  // The note over the list: what the way chosen says, or that a call is queued until an agent
  // listens, which takes it at once, or, after a comment or a reply is posted while comments
  // wait for the agent, that they reach it at the bell. Once the bell has been pressed in this
  // visit, that is not said again.
  function renderNote() {
    const note = $("#call-agent-why");
    const remind = !called && posted && awaited;
    note.textContent = way.note() || (way.listens && callWaits ? host.words.queued
      : remind ? `Your comments reach the agent when you press the bell (${KEY}).` : "");
    note.hidden = note.textContent === "";
  }

  // The agents the server names beside the session listening. The choice among them is shown
  // only when there is one to make, and starts on the session listening.
  function showLanes() {
    const pick = $("#call-agent-select");
    const names = Object.keys(lanes);
    const picked = names.includes(pick.value) ? pick.value : "";
    pick.replaceChildren(new Option("Agent", ""), ...names.map((name) => new Option(name[0].toUpperCase() + name.slice(1), name)));
    pick.value = picked;
    pick.classList.toggle("hidden", !names.length);
    showLaneChoice();
  }

  // A model or an effort is offered for an agent that lists them, the one last picked for it first.
  function showLaneChoice() {
    const name = $("#call-agent-select").value;
    for (const [id, listed, key] of LANE_CHOICES) {
      const box = $(id);
      const words = lanes[name]?.[listed] || [];
      if (box.dataset.lane !== name || [...box.options].map((option) => option.value).join() !== words.join()) {
        box.replaceChildren(...words.map((word) => new Option(word, word)));
        box.dataset.lane = name;
        try {
          const saved = localStorage.getItem(`${prefix}${key}:${name}`);
          if (words.includes(saved)) box.value = saved;
        } catch { /* storage blocked: the first listed */ }
      }
      box.classList.toggle("hidden", !words.length);
    }
    renderBell();
    fit();
  }

  // What a press for an agent picked beside the bell carries, or null for the session listening.
  function calledLane() {
    const agent = $("#call-agent-select").value;
    if (!agent) return null;
    const lane = { agent };
    if ($("#agent-model-select").value) lane.model = $("#agent-model-select").value;
    if ($("#agent-effort-select").value) lane.effort = $("#agent-effort-select").value;
    return lane;
  }

  async function press() {
    const button = $("#call-agent-btn");
    const word = $("#call-agent-word");
    const by = way;
    button.disabled = true;
    try {
      const reply = await by.call(calledLane());
      // Delivered went straight to a parked agent; queued is kept by the server and answers the
      // agent's next wait at once.
      word.textContent = reply.said ?? (reply.delivered ? "Called" : "Queued");
      callWaits = by.listens && !reply.delivered;
      called = true;
      renderNote();
    } catch (error) {
      word.textContent = "Failed";
      console.error(error);
    } finally {
      // The row fits itself to the word, and again once the word is gone.
      fit();
      setTimeout(() => { word.textContent = ""; button.disabled = false; fit(); }, host.wordStays);
    }
  }

  function attach() {
    $("#call-agent-btn").addEventListener("click", () => press());
    $("#call-agent-select").addEventListener("change", showLaneChoice);
    for (const [id, , key] of LANE_CHOICES) {
      $(id).addEventListener("change", () => {
        try { localStorage.setItem(`${prefix}${key}:${$("#call-agent-select").value}`, $(id).value); } catch { /* not kept */ }
      });
    }
    $("#call-by-btn")?.addEventListener("click", () => {
      way = ways[(ways.indexOf(way) + 1) % ways.length];
      try { localStorage.setItem(`${prefix}CallBy`, way.name); } catch { /* the choice lasts this visit */ }
      renderBell();
      fit();
    });
    // Ctrl+Alt+Enter (Cmd+Option+Enter) presses the bell from anywhere on the page, an editor
    // included, but not over a dialog.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !event.altKey || !(event.ctrlKey || event.metaKey)) return;
      if (document.querySelector("dialog[open]")) return;
      event.preventDefault();
      event.stopPropagation();
      if (!$("#call-agent-btn").disabled) $("#call-agent-btn").click();
    }, true);
  }

  renderBell();

  return {
    attach,
    // What the page hears: `waiters`, how many sessions listen; `lanes`, the agents the server
    // names beside them, { name: { waiters, models, efforts } }; and `gone`, whether the page
    // cannot reach its server. What is left out stays as it was.
    show({ waiters: listening, lanes: named, gone: away } = {}) {
      if (listening !== undefined) waiters = listening;
      if (away !== undefined) gone = away;
      if (named === undefined) {
        renderBell();
      } else {
        lanes = named;
        showLanes();
      }
    },
    // From the panel: whether some comment waits for the agent, as its last reading found, and
    // whether a comment or a reply was posted on what is shown, false once the panel shows
    // something else. The note goes by them from the panel's next reading.
    awaited(some) {
      awaited = some;
      renderNote();
    },
    posted(was = true) {
      posted = was;
    },
  };
}
