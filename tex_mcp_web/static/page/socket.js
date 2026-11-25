// The page's socket to the server. createSocket takes reconnect, the milliseconds it waits before
// trying again; opened(again), told as the socket opens, with whether it had closed before;
// heard(message), told each message the server sends; and gone(gone), told as the band that
// says the server cannot be reached shows or goes. It gives connect(), open(), whether the socket
// is open now, send(message), and closes(), how many times it closed since it was last open.

import { $ } from "../shared/dom.js";

export function createSocket({ reconnect, opened, heard, gone: tellGone }) {
  // The server runs inside the agent's process, so it stops with the agent. While the page
  // cannot reach it, a band across the top says so: once a try to reconnect has failed too, so
  // a moment's break does not flash it. How many times the socket closed since it was last open.
  let socketCloses = 0;

  function showServerGone(gone) {
    const band = $("#server-alert");
    band.textContent = "The review page's server stopped. It runs inside your agent: start the agent again, "
      + "or ask it to use a tex tool, and this page reconnects by itself.";
    band.hidden = !gone;
    tellGone(gone);
  }

  // The socket to the server, which the page also tells where the editor's cursor is.
  let pageSocket = null;

  function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/ws`);
    pageSocket = socket;
    socket.onopen = () => {
      opened(socketCloses > 0);
      socketCloses = 0;
      showServerGone(false);
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      heard(message).catch((error) => console.error(error));
    };
    socket.onclose = () => {
      socketCloses += 1;
      showServerGone(socketCloses > 1);
      setTimeout(connectWebSocket, reconnect);
    };
  }

  return {
    connect: connectWebSocket,
    open: () => pageSocket?.readyState === WebSocket.OPEN,
    send: (message) => pageSocket.send(JSON.stringify(message)),
    closes: () => socketCloses,
  };
}
