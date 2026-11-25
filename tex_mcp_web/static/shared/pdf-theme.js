// The look of a review page's PDF viewer (EmbedPDF): its colours for each of the page's themes
// (viewerTheme), and the rules the page adds inside the viewer (viewerStyle), for its toolbar, a
// selection and the Area tool, the comments' badges, a badge whose words are gone, its scroll
// bars, and touch.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

// The viewer's colours for each of the page's themes. Warm, the restful one, is drawn on
// the viewer's light scheme with its own warm paper colours.
const VIEWER_COLORS = {
  light: {
    accent: {
      primary: "#647b95",
      primaryHover: "#536b86",
      primaryActive: "#455c75",
      primaryLight: "#e5e9ed",
      primaryForeground: "#fffdf8",
    },
    background: {
      app: "#faf8f2",
      surface: "#fffdf8",
      surfaceAlt: "#f4f1e8",
      elevated: "#fffdf8",
      input: "#fffdf8",
    },
    foreground: {
      primary: "#302e29",
      secondary: "#6f6a61",
      muted: "#8a8479",
      disabled: "#aaa396",
      onAccent: "#fffdf8",
    },
    interactive: {
      hover: "#f0ece2",
      active: "#e7e1d4",
      selected: "#e5e9ed",
      focus: "#647b95",
    },
    border: {
      default: "#ddd7ca",
      subtle: "#ebe6db",
      strong: "#bdb5a5",
    },
  },
  dark: {
    accent: {
      primary: "#8ea5bf",
      primaryHover: "#a2b6cc",
      primaryActive: "#b6c6d8",
      primaryLight: "#5b636d",
      primaryForeground: "#4a4944",
    },
    background: {
      app: "#403f3b",
      surface: "#54524d",
      surfaceAlt: "#4a4944",
      elevated: "#5c5a54",
      input: "#4a4944",
    },
    foreground: {
      primary: "#f5f2eb",
      secondary: "#d8d2c6",
      muted: "#c2bbaf",
      disabled: "#77726a",
      onAccent: "#4a4944",
    },
    interactive: {
      hover: "#5d5b55",
      active: "#68655e",
      selected: "#5b636d",
      focus: "#8ea5bf",
    },
    border: {
      default: "#7a766e",
      subtle: "#5d5b55",
      strong: "#8e8a80",
    },
  },
  warm: {
    accent: {
      primary: "#9b6a3d",
      primaryHover: "#8a5c33",
      primaryActive: "#774f2b",
      primaryLight: "#ded6c6",
      primaryForeground: "#fbf3e4",
    },
    background: {
      app: "#d9d2c5",
      surface: "#ece6dc",
      surfaceAlt: "#e2dccf",
      elevated: "#efebe2",
      input: "#f4f0e9",
    },
    foreground: {
      primary: "#3a2f22",
      secondary: "#6c5c46",
      muted: "#8a7860",
      disabled: "#b2a186",
      onAccent: "#fbf3e4",
    },
    interactive: {
      hover: "#e3dccf",
      active: "#d8cfc0",
      selected: "#ded6c6",
      focus: "#9b6a3d",
    },
    border: {
      default: "#ccc3b3",
      subtle: "#dcd6c9",
      strong: "#b3a896",
    },
  },
};

export function viewerTheme(theme) {
  return {
    preference: theme === "dark" ? "dark" : "light",
    light: theme === "warm" ? VIEWER_COLORS.warm : VIEWER_COLORS.light,
    dark: VIEWER_COLORS.dark,
  };
}

// The rules the page adds in the viewer's shadow root, on its own classes and attributes there,
// each named after `prefix`: .<prefix>-comment-badge, which .stale marks, and the host's
// data-<prefix>-selecting, data-<prefix>-area and data-<prefix>-theme.
export function viewerStyle(prefix) {
  return `
    [data-epdf-i="main-toolbar"] {
      gap: 4px !important;
      padding: 0 6px !important;
    }
    [data-epdf-i="main-toolbar"] button {
      height: 26px !important;
      min-width: 26px !important;
      padding: 3px !important;
    }
    [data-epdf-i="main-toolbar"] button svg {
      height: 16px !important;
      width: 16px !important;
    }
    :host([data-${prefix}-selecting]) [data-no-interaction] *,
    :host([data-${prefix}-area]) [data-no-interaction] * {
      pointer-events: none !important;
    }
    :host([data-${prefix}-area]) * {
      cursor: crosshair !important;
      touch-action: none !important;
    }
    .${prefix}-comment-badge {
      position: absolute;
      min-width: 18px;
      height: 18px;
      padding: 0 4px;
      border: 1px solid #8b7413;
      border-radius: 9px;
      color: #302900;
      background: #ffe77a;
      font: 11px/16px system-ui, sans-serif;
      text-align: center;
      transform-origin: top left;
      pointer-events: auto;
      cursor: pointer;
      z-index: 2;
    }
    .${prefix}-comment-badge.stale {
      border-style: dashed;
      color: #6d6650;
      background: #f6f1e2;
    }
    :host([data-${prefix}-theme="dark"]) * { color-scheme: dark; scrollbar-color: #8a867c #403f3b; }
    :host([data-${prefix}-theme="warm"]) * { scrollbar-color: #b3a896 #d9d2c5; }
    /* On a touch screen a finger dragged on the pages scrolls them in the pointer mode too,
       as a page does, and a finger held still selects first. The viewer turns touch
       scrolling off in that mode so that every drag selects. */
    @media (pointer: coarse) {
      [style*="touch-action: none"]:not([data-epdf-handle]):not([data-epdf-vertex]) {
        touch-action: pan-x pan-y !important;
      }
    }
  `;
}
