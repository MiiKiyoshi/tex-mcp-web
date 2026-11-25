// A review page's socket to its server, opened again a moment after it closes, with the band
// across the top that says the server cannot be reached. createSocket takes `path`, the socket's
// route; `reconnect`, the milliseconds it waits before trying again; `band`, what the band
// (#server-alert) says while the server cannot be reached, or null for a page without one;
// opened(again), told as the socket opens, with whether it had closed before; heard(message),
// told each message the server sends, which may answer a promise; gone(gone), told as the server
// comes to be counted gone, or back; and closed(), told each time the socket closes. It gives
// connect(), open(), whether the socket is open now, send(message), and closes(), how many times
// it closed since it was last open.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $ } from "./dom.js";

export function createSocket({
  path = "/ws", reconnect, band = null, opened = () => {}, heard, gone = () => {}, closed = () => {},
}) {
  // The server is counted gone once a try to reconnect has failed too, so a moment's break does
  // not flash the band. How many times the socket closed since it was last open, and the socket.
  let closes = 0;
  let socket = null;

  function showGone(away) {
    if (band !== null) {
      const alert = $("#server-alert");
      alert.textContent = band;
      alert.hidden = !away;
    }
    gone(away);
  }

  function connect() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const opening = new WebSocket(`${protocol}//${location.host}${path}`);
    socket = opening;
    opening.onopen = () => {
      opened(closes > 0);
      closes = 0;
      showGone(false);
    };
    opening.onmessage = (event) => {
      Promise.resolve(heard(JSON.parse(event.data))).catch((error) => console.error(error));
    };
    opening.onclose = () => {
      closes += 1;
      closed();
      showGone(closes > 1);
      setTimeout(connect, reconnect);
    };
  }

  return {
    connect,
    open: () => socket?.readyState === WebSocket.OPEN,
    send: (message) => socket.send(JSON.stringify(message)),
    closes: () => closes,
  };
}
