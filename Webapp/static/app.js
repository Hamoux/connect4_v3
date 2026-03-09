const ROWS = 9;
const COLS = 9;

// sessionStorage pour éviter le bug des deux onglets avec le même client id
let CLIENT_ID = sessionStorage.getItem("connect4_client_id");
if (!CLIENT_ID) {
  try {
    CLIENT_ID = (crypto && crypto.randomUUID) ? crypto.randomUUID() : null;
  } catch (e) {
    CLIENT_ID = null;
  }
  if (!CLIENT_ID) {
    CLIENT_ID = "cid_" + Date.now() + "_" + Math.floor(Math.random() * 1e6);
  }
  sessionStorage.setItem("connect4_client_id", CLIENT_ID);
}

window.addEventListener("error", (ev) => {
  console.error("JS ERROR", ev.error || ev.message);
  setMessageOnly("Erreur JS: " + (ev.error?.message || ev.message));
});

// ===== état global front =====
let PLAYER_R_NAME = localStorage.getItem("playerNameR") || "Joueur Rouge";
let PLAYER_J_NAME = localStorage.getItem("playerNameJ") || "IA";
let playerColor = localStorage.getItem("playerColor") || "R";

const AI_DELAY_MS = 900;

let lastState = null;
let hoverCol = null;
let lastMove = null;
let aiTimer = null;
let busy = false;
let pollTimer = null;
let GAME_ID = null;

// ===== helpers =====
function $(id) {
  return document.getElementById(id);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, m => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[m]));
}

function nameFor(letter) {
  if (letter === "R") return lastState?.player_r_name || PLAYER_R_NAME || "Joueur Rouge";
  if (letter === "J") return lastState?.player_j_name || PLAYER_J_NAME || "Joueur Jaune";
  return "?";
}

function setThinking(on) {
  const el = $("aiThinking");
  if (!el) return;
  el.hidden = !on;
}

function cancelAiTimer() {
  if (aiTimer) {
    clearTimeout(aiTimer);
    aiTimer = null;
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function showMessage(txt) {
  const historyDiv = $("history");
  if (historyDiv) {
    const item = document.createElement("div");
    item.className = "logItem system";
    item.innerHTML = `<span class="text">${escapeHtml(txt)}</span>`;
    historyDiv.appendChild(item);
    historyDiv.scrollTop = historyDiv.scrollHeight;
  }
  setMessageOnly(txt);
}

function setMessageOnly(txt) {
  const msg = $("message");
  if (msg) {
    msg.hidden = false;
    msg.innerHTML = txt;
  }
}

function hideMessage() {
  const msg = $("message");
  if (msg) {
    msg.hidden = true;
    msg.textContent = "";
  }
}

function isColumnFull(col) {
  return lastState?.board?.[0]?.[col] !== 0;
}

function findLastMove(prevBoard, newBoard) {
  if (!prevBoard || !newBoard) return null;

  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      if (prevBoard?.[r]?.[c] === 0 && (newBoard?.[r]?.[c] === "R" || newBoard?.[r]?.[c] === "J")) {
        return { r, c };
      }
    }
  }
  return null;
}

function jsFindWinningLine(r, c, board) {
  const dirs = [[0,1],[1,0],[1,1],[1,-1]];
  const player = board[r][c];

  for (const [dr, dc] of dirs) {
    let coords = [];
    for (let i = -3; i < 4; i++) {
      const nr = r + dr * i;
      const nc = c + dc * i;
      if (nr >= 0 && nr < ROWS && nc >= 0 && nc < COLS && board[nr][nc] === player) {
        coords.push([nr, nc]);
        if (coords.length === 4) return coords;
      } else {
        coords = [];
      }
    }
  }
  return null;
}

// ===== API =====
async function getState(id) {
  let url = "/api/state";
  if (id) {
    url += `?game_id=${encodeURIComponent(id)}&client_id=${encodeURIComponent(CLIENT_ID)}`;
  }

  const res = await fetch(url);
  const data = await res.json();

  if (!res.ok) {
    setMessageOnly(data.error || "Erreur récupération état");
    return null;
  }

  return data;
}

async function newGame() {
  busy = false;
  hideMessage();
  lastMove = null;
  cancelAiTimer();
  setThinking(false);

  const mode = ($("modeSelect")?.value || "IA").toUpperCase();
  const difficulty = ($("diffSelect")?.value || "medium").toLowerCase();
  const starting_player = (mode === "ONLINE")
    ? undefined
    : (($("colorSelect")?.value || playerColor || "R").toUpperCase());

  GAME_ID = null;
  history.replaceState({}, "", location.pathname);

  if (mode === "LOCAL") {
    lastState = {
      id_partie: null,
      mode: "LOCAL",
      type_partie: "HUMAIN",
      status: "EN_COURS",
      ai_enabled: false,
      ai_depth: 0,
      board: Array.from({ length: ROWS }, () => Array(COLS).fill(0)),
      current_player: starting_player,
      starting_player,
      signature: "init",
      game_over: false,
      ai_player: null,
      winning_line: null,
      player_count: 1,
      client_r: null,
      client_j: null
    };
    stopPolling();
    render(lastState);
    return;
  }

  const payload = {
    mode,
    difficulty,
    starting_player,
    client_id: CLIENT_ID,
    player_r_name: PLAYER_R_NAME,
    player_j_name: PLAYER_J_NAME
  };

  const res = await fetch("/api/new", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const state = await res.json();
  if (!res.ok) {
    setMessageOnly(state.error || "Erreur création partie");
    return;
  }

  lastState = state;

  if (state.id_partie) {
    GAME_ID = state.id_partie;
    history.replaceState({}, "", `?game_id=${GAME_ID}`);

    const linkInput = $("shareLink");
    if (linkInput) linkInput.value = window.location.href;

    if (state.mode === "WEB") {
      startPolling();
    }
  }

  render(lastState);

  if (lastState.type_partie === "IA" &&
      lastState.current_player === lastState.ai_player &&
      !lastState.game_over) {
    aiTimer = setTimeout(aiMove, AI_DELAY_MS);
  }
}

async function play(col) {
  if (busy || !lastState) return;

  // si online humain et joueur 2 pas encore là
  if (lastState.mode === "WEB" &&
      lastState.type_partie === "HUMAIN" &&
      lastState.player_count < 2) {
    setMessageOnly("⏳ En attente d'un adversaire… Partage le lien.");
    return;
  }

  // blocage si tour IA
  if (lastState.type_partie === "IA" && lastState.current_player === lastState.ai_player) {
    return;
  }

  // mode local
  if (lastState.mode === "LOCAL") {
    if (lastState.game_over) return;
    if (isColumnFull(col)) return;

    let placed = null;
    for (let r = ROWS - 1; r >= 0; r--) {
      if (lastState.board[r][col] === 0) {
        placed = r;
        lastState.board[r][col] = lastState.current_player;
        break;
      }
    }

    if (placed === null) return;

    if (String(lastState.signature).startsWith("init_")) {
      lastState.signature = "";
    }
    lastState.signature += String(col + 1);
    lastMove = { r: placed, c: col };

    const line = jsFindWinningLine(placed, col, lastState.board);
    if (line) {
      lastState.game_over = true;
      lastState.status = "TERMINEE";
      lastState.winning_line = line.map(([r, c]) => [r, c]);
      render(lastState);
      showMessage(`🏁 Victoire de ${nameFor(lastState.current_player)} !`);
      return;
    }

    lastState.current_player = lastState.current_player === "R" ? "J" : "R";
    render(lastState);
    return;
  }

  // mode serveur
  if (!lastState.id_partie) {
    showMessage("Clique sur “Nouvelle partie” d’abord 🙂");
    return;
  }

  if (lastState.game_over) return;
  if (isColumnFull(col)) return;

  cancelAiTimer();
  busy = true;

  let res, data;
  try {
    res = await fetch("/api/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        col,
        game_id: GAME_ID,
        client_id: CLIENT_ID
      })
    });
    data = await res.json();
  } catch (e) {
    busy = false;
    showMessage("Erreur réseau");
    return;
  }

  busy = false;

  if (!res.ok) {
    showMessage(data.error || "Erreur");
    return;
  }

  lastMove = findLastMove(lastState.board, data.board);
  lastState = data;
  render(lastState);

  if (lastState.game_over) {
    showMessage(`🏁 Victoire de ${nameFor(lastState.current_player)} !`);
    return;
  }

  if (lastState.type_partie === "IA" && lastState.current_player === lastState.ai_player) {
    aiTimer = setTimeout(aiMove, AI_DELAY_MS);
  }
}

async function aiMove() {
  aiTimer = null;
  if (!lastState || lastState.game_over) return;

  setThinking(true);
  const t0 = performance.now();

  const res = await fetch("/api/ai_move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      game_id: GAME_ID,
      client_id: CLIENT_ID
    })
  });

  const data = await res.json();
  setThinking(false);

  if (!res.ok) {
    showMessage(data.error || "Erreur IA");
    return;
  }

  const dt = Math.round(performance.now() - t0);

  lastMove = findLastMove(lastState.board, data.board);
  lastState = data;
  render(lastState);

  showMessage(`🤖 IA a joué en ${dt} ms`);

  if (lastState.game_over) {
    showMessage(`🏁 Victoire de ${nameFor(lastState.current_player)} !`);
  }
}

function startPolling() {
  if (pollTimer) return;

  (async () => {
    if (!lastState || lastState.game_over || lastState.mode !== "WEB" || !GAME_ID) return;

    const data = await getState(GAME_ID);
    if (!data) return;

    const changed =
      JSON.stringify(data.board) !== JSON.stringify(lastState.board) ||
      data.signature !== lastState.signature ||
      data.current_player !== lastState.current_player ||
      data.game_over !== lastState.game_over ||
      data.player_count !== lastState.player_count ||
      data.client_r !== lastState.client_r ||
      data.client_j !== lastState.client_j;

    if (changed) {
      lastMove = findLastMove(lastState.board, data.board);
      lastState = data;
      render(lastState);
    }
  })();

  pollTimer = setInterval(async () => {
    if (!lastState || lastState.game_over || lastState.mode !== "WEB" || !GAME_ID) {
      stopPolling();
      return;
    }

    const data = await getState(GAME_ID);
    if (!data) return;

    const changed =
      JSON.stringify(data.board) !== JSON.stringify(lastState.board) ||
      data.signature !== lastState.signature ||
      data.current_player !== lastState.current_player ||
      data.game_over !== lastState.game_over ||
      data.player_count !== lastState.player_count ||
      data.client_r !== lastState.client_r ||
      data.client_j !== lastState.client_j;

    if (changed) {
      lastMove = findLastMove(lastState.board, data.board);
      lastState = data;
      render(lastState);
    }
  }, 800);
}

// ===== rendu =====
function setModePill(state) {
  const pill = $("turnPill");
  if (!pill) return;

  pill.innerHTML = "";

  let modeTxt = "";
  let dotColor = "";

  if (state.mode === "LOCAL") {
    modeTxt = "J vs J (locale)";
  } else if (state.type_partie === "IA") {
    modeTxt = "J vs IA";
    dotColor = "#4ade80";
  } else {
    modeTxt = "J vs J (en ligne)";
    dotColor = "#4ade80";
  }

  const dot = document.createElement("span");
  dot.className = "dot";
  if (dotColor) dot.style.backgroundColor = dotColor;

  const label = document.createElement("span");
  label.textContent = modeTxt;

  pill.appendChild(dot);
  pill.appendChild(label);
}

function updateTurnInfo(state) {
  const el = $("turnInfo");
  if (!el || !state) return;

  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    el.innerHTML = "⏳ Partie créée — en attente d’un adversaire.";
    return;
  }

  const turn = state.current_player;
  const turnName = nameFor(turn);

  if (!lastMove) {
    el.innerHTML = `Début de partie — À <b>${escapeHtml(turnName)}</b> de jouer.`;
    return;
  }

  const lastPlayer = turn === "R" ? "J" : "R";
  const lastName = nameFor(lastPlayer);
  const colHuman = lastMove.c + 1;

  el.innerHTML =
    `Dernier coup : <b>${escapeHtml(lastName)}</b> en colonne <b>${colHuman}</b> — ` +
    `À <b>${escapeHtml(turnName)}</b> de jouer.`;
}

function renderRole(state) {
  const roleDiv = $("yourRole");
  if (!roleDiv) return;

  let txt = "-";

  if (state.mode === "WEB" && state.type_partie === "HUMAIN") {
    if (state.client_r === CLIENT_ID) txt = "Joueur 1 — Rouge";
    else if (state.client_j === CLIENT_ID) txt = "Joueur 2 — Jaune";
    else if (state.player_count >= 2) txt = "Spectateur";
    else txt = "En attente";
  } else if (state.mode === "LOCAL") {
    txt = "Local";
  } else if (state.type_partie === "IA") {
    txt = "Humain vs IA";
  }

  roleDiv.textContent = txt;
}

function renderStatus(state) {
  const statusTxt = $("statusTxt");
  const hint = $("hint");
  if (!statusTxt || !hint) return;

  let st = state.status || (state.game_over ? "TERMINEE" : "EN_COURS");

  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    st = "EN ATTENTE d'un adversaire";
  }

  statusTxt.textContent = st;

  hint.textContent = state.game_over
    ? "Partie terminée. Lance une nouvelle partie 👇"
    : (state.type_partie === "IA" && state.current_player === state.ai_player
        ? "Tour de l’IA…"
        : (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2
            ? "Partage le lien ci-dessus 👆"
            : "Survole une colonne puis clique 👇"));
}

function renderMessage(state) {
  const msg = $("message");
  if (!msg) return;

  msg.hidden = false;

  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    let txt = "⏳ En attente d'un adversaire… ";
    if (state.client_r === CLIENT_ID) txt += "Tu joues Rouge. ";
    if (state.client_j === CLIENT_ID) txt += "Tu joues Jaune. ";
    txt += "Partage le lien.";
    msg.innerHTML = txt;
    return;
  }

  if (state.game_over) {
    const cls = state.current_player === "R" ? "red" : "yellow";
    msg.innerHTML = `🏁 Victoire de <span class="${cls}">${escapeHtml(nameFor(state.current_player))}</span> !`;
  } else {
    const cls = state.current_player === "R" ? "red" : "yellow";
    msg.innerHTML = `Tour de <span class="${cls}">${escapeHtml(nameFor(state.current_player))}</span>`;
  }
}

function renderHeader(state) {
  const header = $("colHeader");
  if (!header) return;
  header.innerHTML = "";

  for (let c = 0; c < COLS; c++) {
    const btn = document.createElement("button");
    btn.className = "colBtn";
    btn.textContent = (c + 1);

    const disabled =
      busy ||
      state.game_over ||
      (state.board?.[0]?.[c] !== 0) ||
      (state.type_partie === "IA" && state.current_player === state.ai_player) ||
      (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2);

    if (disabled) btn.classList.add("full");
    btn.disabled = disabled;

    btn.addEventListener("mouseenter", () => {
      hoverCol = c;
      applyPreview();
    });
    btn.addEventListener("mouseleave", () => {
      hoverCol = null;
      applyPreview();
    });
    btn.addEventListener("click", () => play(c));

    header.appendChild(btn);
  }
}

function renderBoard(state) {
  const boardDiv = $("board");
  if (!boardDiv) return;

  boardDiv.innerHTML = "";

  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = document.createElement("div");
      cell.className = "cell";

      cell.addEventListener("mouseenter", () => {
        hoverCol = c;
        applyPreview();
      });
      cell.addEventListener("mouseleave", () => {
        hoverCol = null;
        applyPreview();
      });
      cell.addEventListener("click", () => play(c));

      const piece = document.createElement("div");
      piece.className = "piece";

      const v = state.board?.[r]?.[c];
      if (v === "R") {
        cell.classList.add("filled");
        piece.classList.add("red");
      }
      if (v === "J") {
        cell.classList.add("filled");
        piece.classList.add("yellow");
      }

      cell.appendChild(piece);
      boardDiv.appendChild(cell);
    }
  }
}

function renderHistory(state) {
  const historyDiv = $("history");
  if (!historyDiv) return;

  historyDiv.innerHTML = "";

  let sig = String(state.signature || "");
  if (sig.startsWith("init_")) sig = "";
  const moves = sig.replace(/[^\d]/g, "");

  if (!moves.length) {
    historyDiv.innerHTML = `<div class="logItem"><span class="text">Aucun coup pour l’instant.</span></div>`;
    return;
  }

  const starting = (state.starting_player || "R").toUpperCase();

  for (let i = 0; i < moves.length; i++) {
    const col = Number(moves[i]);
    const moveLetter = (i % 2 === 0) ? starting : (starting === "R" ? "J" : "R");
    const isRed = moveLetter === "R";
    const name = nameFor(moveLetter);

    const item = document.createElement("div");
    item.className = "logItem " + (isRed ? "red" : "yellow");
    if (i === moves.length - 1) item.classList.add("lastMove");

    item.innerHTML = `
      <span class="name">${escapeHtml(name)}</span>
      <span class="text"> place un pion dans la colonne ${col}</span>
      <div class="logTime">Coup #${i + 1}</div>
    `;

    historyDiv.appendChild(item);
  }

  historyDiv.scrollTop = historyDiv.scrollHeight;
}

function applyPreview() {
  const boardDiv = $("board");
  if (!boardDiv) return;

  const cells = boardDiv.children;
  for (let i = 0; i < cells.length; i++) {
    cells[i].classList.remove("preview");
  }

  if (hoverCol == null || lastState?.game_over) return;

  if (lastState?.mode === "WEB" &&
      lastState?.type_partie === "HUMAIN" &&
      lastState?.player_count < 2) {
    return;
  }

  let targetRow = null;
  for (let r = ROWS - 1; r >= 0; r--) {
    if (lastState?.board?.[r]?.[hoverCol] === 0) {
      targetRow = r;
      break;
    }
  }

  if (targetRow == null) return;

  const idx = targetRow * COLS + hoverCol;
  if (cells[idx]) cells[idx].classList.add("preview");
}

function applyLastMove() {
  const boardDiv = $("board");
  if (!boardDiv) return;

  const cells = boardDiv.children;
  for (let i = 0; i < cells.length; i++) {
    cells[i].classList.remove("last");
  }

  if (!lastMove) return;

  const idx = lastMove.r * COLS + lastMove.c;
  if (cells[idx]) cells[idx].classList.add("last");
}

function applyWinningLine(state) {
  const boardDiv = $("board");
  if (!boardDiv) return;

  const cells = boardDiv.children;
  for (let i = 0; i < cells.length; i++) {
    cells[i].classList.remove("win");
  }

  const line = state.winning_line;
  if (!line || !Array.isArray(line)) return;

  for (const [r, c] of line) {
    const idx = r * COLS + c;
    if (cells[idx]) cells[idx].classList.add("win");
  }
}

function updateModeUI() {
  const mode = ($("modeSelect")?.value || "IA").toUpperCase();
  const diffSelect = $("diffSelect");
  const colorSelect = $("colorSelect");
  const nameR = $("playerNameR");
  const nameJ = $("playerNameJ");

  if (diffSelect) {
    diffSelect.disabled = (mode !== "IA");
  }

  if (colorSelect) {
    colorSelect.disabled = (mode === "ONLINE");
  }

  if (mode === "IA") {
    if (playerColor === "R") {
      PLAYER_J_NAME = "IA";
      if (nameJ) {
        nameJ.value = "IA";
        nameJ.disabled = true;
      }
      if (nameR) {
        nameR.disabled = false;
        nameR.value = localStorage.getItem("playerNameR") || PLAYER_R_NAME;
        PLAYER_R_NAME = nameR.value || "Joueur Rouge";
      }
    } else {
      PLAYER_R_NAME = "IA";
      if (nameR) {
        nameR.value = "IA";
        nameR.disabled = true;
      }
      if (nameJ) {
        nameJ.disabled = false;
        nameJ.value = localStorage.getItem("playerNameJ") || PLAYER_J_NAME;
        PLAYER_J_NAME = nameJ.value || "Joueur Jaune";
      }
    }
  } else {
    if (nameR) {
      nameR.disabled = false;
      nameR.value = localStorage.getItem("playerNameR") || PLAYER_R_NAME;
      PLAYER_R_NAME = nameR.value || "Joueur Rouge";
    }
    if (nameJ) {
      nameJ.disabled = false;
      nameJ.value = localStorage.getItem("playerNameJ") || PLAYER_J_NAME;
      PLAYER_J_NAME = nameJ.value || "Joueur Jaune";
    }
  }

  if (lastState) render(lastState);
}

function render(state) {
  if (!state) return;

  setModePill(state);
  renderRole(state);
  renderStatus(state);
  renderMessage(state);
  renderHeader(state);
  renderHistory(state);
  renderBoard(state);

  const linkInput = $("shareLink");
  if (linkInput) {
    if (state.id_partie && GAME_ID) {
      linkInput.value = window.location.href;
    } else {
      linkInput.value = "";
    }
  }

  applyPreview();
  applyLastMove();
  applyWinningLine(state);
  updateTurnInfo(state);
}

// ===== init =====
window.addEventListener("load", async () => {
  if ($("playerNameR")) $("playerNameR").value = PLAYER_R_NAME;
  if ($("playerNameJ")) $("playerNameJ").value = PLAYER_J_NAME;
  if ($("colorSelect")) $("colorSelect").value = playerColor;

  $("btnNew")?.addEventListener("click", newGame);

  $("btnHint")?.addEventListener("click", async () => {
    if (!lastState) return;

    if (lastState.mode === "LOCAL") {
      showMessage("Mode LOCAL : suggestion IA non disponible (utilise mode IA ou une partie serveur).");
      return;
    }

    if (!GAME_ID) {
      showMessage("Crée une partie d’abord 🙂");
      return;
    }

    const res = await fetch("/api/hint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        game_id: GAME_ID,
        client_id: CLIENT_ID
      })
    });

    const data = await res.json();
    if (!res.ok) {
      showMessage(data.error || "Erreur suggestion");
      return;
    }

    const colHuman = data.suggested_col + 1;
    showMessage(`💡 Suggestion IA : jouer en colonne ${colHuman}`);
  });

  $("btnCopyLink")?.addEventListener("click", () => {
    const link = $("shareLink")?.value || "";
    if (!link) return;

    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(link)
        .then(() => setMessageOnly("Lien copié ✅"))
        .catch(() => {});
    } else {
      $("shareLink")?.select();
      document.execCommand("copy");
      setMessageOnly("Lien copié ✅");
    }
  });

  $("modeSelect")?.addEventListener("change", updateModeUI);

  $("playerNameR")?.addEventListener("input", (e) => {
    PLAYER_R_NAME = e.target.value || "Joueur Rouge";
    localStorage.setItem("playerNameR", PLAYER_R_NAME);
    if (lastState) render(lastState);
  });

  $("playerNameJ")?.addEventListener("input", (e) => {
    PLAYER_J_NAME = e.target.value || "Joueur Jaune";
    localStorage.setItem("playerNameJ", PLAYER_J_NAME);
    if (lastState) render(lastState);
  });

  $("colorSelect")?.addEventListener("change", (e) => {
    playerColor = e.target.value || "R";
    localStorage.setItem("playerColor", playerColor);
    updateModeUI();
  });

  updateModeUI();

  const params = new URLSearchParams(window.location.search);

  if (params.has("game_id")) {
    GAME_ID = Number(params.get("game_id"));
    lastState = await getState(GAME_ID);

    if (!lastState) {
      GAME_ID = null;
      history.replaceState({}, "", location.pathname);
      lastState = await getState();
    } else {
      if ($("shareLink")) $("shareLink").value = window.location.href;
      if (lastState.mode === "WEB") startPolling();

      if (lastState.game_over) {
        setMessageOnly("Cette partie est terminée. Clique sur “Nouvelle partie” 🙂");
      }
    }
  } else {
    lastState = await getState();
  }

  if (!lastState) {
    lastState = {
      id_partie: null,
      mode: "LOCAL",
      type_partie: "HUMAIN",
      status: "Aucune partie",
      ai_enabled: false,
      ai_depth: 0,
      board: Array.from({ length: ROWS }, () => Array(COLS).fill(0)),
      current_player: "R",
      starting_player: "R",
      signature: "init",
      game_over: false,
      ai_player: null,
      winning_line: null,
      player_count: 0,
      client_r: null,
      client_j: null
    };
  }

  render(lastState);
});