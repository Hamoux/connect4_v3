/**
 * Front-end Puissance 4 — communication avec l’API Flask existante uniquement.
 */

const ROWS = 9;
const COLS = 9;
const AI_DELAY_MS = 850;

// --- Identifiant client (session) ---
let CLIENT_ID = sessionStorage.getItem("connect4_client_id");
if (!CLIENT_ID) {
  try {
    CLIENT_ID = crypto?.randomUUID?.() ?? null;
  } catch {
    CLIENT_ID = null;
  }
  if (!CLIENT_ID) {
    CLIENT_ID = "cid_" + Date.now() + "_" + Math.floor(Math.random() * 1e6);
  }
  sessionStorage.setItem("connect4_client_id", CLIENT_ID);
}

// --- État applicatif ---
let lastState = null;
let GAME_ID = null;
let busy = false;
let hoverCol = null;
let lastMove = null;
let aiTimer = null;
let pollTimer = null;
let paused = false;

let PLAYER_R_NAME = localStorage.getItem("playerNameR") || "Joueur rouge";
let PLAYER_J_NAME = localStorage.getItem("playerNameJ") || "Joueur jaune";
let humanColor = localStorage.getItem("humanColor") || "R";

const uiPrefs = {
  mode: "IA",
  difficulty: "4",
  startingPlayer: "R",
  humanColor: "R"
};

let committedMode = "IA";
let committedDifficulty = "4";
let suppressSelectChange = false;
let pendingConfirmCallback = null;

const undoStack = [];
const redoStack = [];
let lastBoardSnapshot = null;

let hintColumn = null;
let hintTimer = null;

// --- Utilitaires DOM ---
function $(id) {
  return document.getElementById(id);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])
  );
}

function deepCloneBoard(board) {
  return board.map((row) => row.slice());
}

function snapshotForUndo(state) {
  return {
    board: deepCloneBoard(state.board),
    current_player: state.current_player,
    starting_player: state.starting_player,
    signature: state.signature,
    game_over: state.game_over,
    status: state.status,
    winning_line: state.winning_line ? state.winning_line.map((x) => [...x]) : null,
    ai_enabled: !!state.ai_enabled,
    ai_players: { ...(state.ai_players || { R: false, J: false }) },
    ai_depth: state.ai_depth || 0,
    ai_player: state.ai_player || null,
    player_r_name: state.player_r_name || PLAYER_R_NAME,
    player_j_name: state.player_j_name || PLAYER_J_NAME
  };
}

function applySnapshot(state, snap) {
  state.board = deepCloneBoard(snap.board);
  state.current_player = snap.current_player;
  state.starting_player = snap.starting_player;
  state.signature = snap.signature;
  state.game_over = snap.game_over;
  state.status = snap.status;
  state.winning_line = snap.winning_line ? snap.winning_line.map((x) => [...x]) : null;
  state.ai_enabled = !!snap.ai_enabled;
  state.ai_players = { ...(snap.ai_players || { R: false, J: false }) };
  state.ai_depth = snap.ai_depth || 0;
  state.ai_player = snap.ai_player || null;
  state.player_r_name = snap.player_r_name || PLAYER_R_NAME;
  state.player_j_name = snap.player_j_name || PLAYER_J_NAME;
}

function cloneFullState(state) {
  try {
    return JSON.parse(JSON.stringify(state));
  } catch {
    return snapshotForUndo(state);
  }
}

function syncUiPrefsFromForm() {
  uiPrefs.mode = ($("modeSelect")?.value || "IA").toUpperCase();
  uiPrefs.difficulty = $("diffSelect")?.value || "4";
  uiPrefs.startingPlayer = ($("colorSelect")?.value || "R").toUpperCase();
  uiPrefs.humanColor = ($("humanColorSelect")?.value || "R").toUpperCase();
  committedMode = uiPrefs.mode;
  committedDifficulty = uiPrefs.difficulty;
}

function hasActiveGame() {
  if (!lastState || lastState.game_over) return false;
  let sig = String(lastState.signature || "");
  if (sig.startsWith("init_")) sig = "";
  const digits = sig.replace(/[^\d]/g, "");
  if (digits.length > 0) return true;
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      if (lastState.board?.[r]?.[c] !== 0) return true;
    }
  }
  return false;
}

function parseSignatureToCols(sig) {
  let s = String(sig || "");
  if (s.startsWith("init_")) s = "";
  const digits = s.replace(/[^\d]/g, "");
  const out = [];
  for (let i = 0; i < digits.length; i++) {
    const d = parseInt(digits[i], 10);
    if (d >= 1 && d <= COLS) out.push(d - 1);
  }
  return out;
}

function lastMoveFromSignature(state) {
  let sig = String(state.signature || "");
  if (sig.startsWith("init_")) sig = "";
  const digits = sig.replace(/[^\d]/g, "");
  if (!digits.length) return null;
  const lastCol = parseInt(digits[digits.length - 1], 10) - 1;
  if (lastCol < 0 || lastCol >= COLS) return null;
  for (let r = ROWS - 1; r >= 0; r--) {
    if (state.board?.[r]?.[lastCol] !== 0) return { r, c: lastCol };
  }
  return null;
}

function myOnlineColor(state) {
  if (!state) return null;
  if (state.client_r === CLIENT_ID) return "R";
  if (state.client_j === CLIENT_ID) return "J";
  return null;
}

function isAiTurn(state) {
  if (!state || state.game_over) return false;
  const aiPlayers = state.ai_players || { R: false, J: false };
  return !!aiPlayers[state.current_player];
}

// --- Synchronisation serveur pour undo/redo JvIA ---
async function resyncServerStateFromSnapshot(targetSnap) {
  stopPolling();
  cancelAiTimer();
  clearHint();

  const mode = uiPrefs.mode;
  const difficulty = uiPrefs.difficulty;
  const starting_player = mode === "ONLINE" ? undefined : (targetSnap.starting_player || "R");

  const payload = {
    mode,
    difficulty,
    starting_player,
    human_player: uiPrefs.humanColor,
    client_id: CLIENT_ID,
    player_r_name: targetSnap.player_r_name || PLAYER_R_NAME,
    player_j_name: targetSnap.player_j_name || PLAYER_J_NAME
  };

  const { ok, data: fresh } = await postNewGame(payload);
  if (!ok) {
    showMessage(fresh.error || "Impossible de synchroniser l’état.");
    return false;
  }

  lastState = fresh;
  GAME_ID = fresh.id_partie;
  history.replaceState({}, "", `?game_id=${GAME_ID}`);

  const moves = parseSignatureToCols(targetSnap.signature);

  for (let i = 0; i < moves.length; i++) {
    const col = moves[i];
    const st = await getState(GAME_ID);
    if (!st) {
      showMessage("Impossible de synchroniser l’état.");
      return false;
    }
    lastState = st;

    if (st.game_over) break;

    if (isAiTurn(st)) {
      const r = await postAiMove();
      if (!r.ok) {
        showMessage(r.data.error || "Erreur lors du coup de l’IA.");
        return false;
      }
      lastState = r.data;
    } else {
      const r = await postPlay(col);
      if (!r.ok) {
        showMessage(r.data.error || "Erreur lors du coup.");
        return false;
      }
      lastState = r.data;
    }

    if (lastState.game_over) break;
  }

  const targetAi = targetSnap.ai_players || { R: false, J: false };
  for (const color of ["R", "J"]) {
    const currentAi = !!(lastState.ai_players || {})[color];
    if (currentAi !== !!targetAi[color]) {
      const swap = await postSetAiColor(color, !!targetAi[color]);
      if (!swap.ok) {
        showMessage(swap.data.error || "Erreur lors de la restauration des joueurs IA.");
        return false;
      }
      lastState = swap.data;
    }
  }

  lastBoardSnapshot = null;
  lastMove = lastMoveFromSignature(lastState);

  if (lastState.mode === "WEB") startPolling();

  const linkInput = $("shareLink");
  if (linkInput) linkInput.value = window.location.href;

  render(lastState);
  scheduleAiIfNeeded();
  return true;
}

function showConfirmModal(message, onYes) {
  const overlay = $("confirmOverlay");
  const text = $("confirmModalText");
  if (text) text.textContent = message;
  if (overlay) {
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
  }
  pendingConfirmCallback = onYes;
}

function hideConfirmModal() {
  const overlay = $("confirmOverlay");
  if (overlay) {
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
  }
  pendingConfirmCallback = null;
}

function updateShareLinkVisibility() {
  const field = $("shareLinkField");
  const mode = ($("modeSelect")?.value || "").toUpperCase();
  if (!field) return;
  field.hidden = mode !== "ONLINE";
}

function updateUndoHelpText() {
  const help = $("undoHelp");
  if (!help || !lastState) return;
  if (lastState.mode === "LOCAL") {
    help.innerHTML =
      "Annuler / rétablir : un coup à la fois en <strong>joueur contre joueur (local)</strong>.";
  } else if (lastState.mode === "WEB" && lastState.type_partie === "IA") {
    help.innerHTML =
      "Annuler / rétablir : retire le dernier <strong>tour complet</strong> (ton coup et celui de l’IA).";
  } else {
    help.innerHTML = "Annuler / rétablir : non disponible en ligne (deux joueurs).";
  }
}

function syncSelectsFromLoadedState(state) {
  if (!state) return;

  suppressSelectChange = true;
  try {
    if (state.mode === "LOCAL") {
      if ($("modeSelect")) $("modeSelect").value = "LOCAL";
    } else if (state.type_partie === "IA") {
      if ($("modeSelect")) $("modeSelect").value = "IA";
    } else {
      if ($("modeSelect")) $("modeSelect").value = "ONLINE";
    }

    if (state.ai_depth != null && $("diffSelect")) {
      $("diffSelect").value = String(state.ai_depth);
    }

    if (state.starting_player && $("colorSelect")) {
      $("colorSelect").value = state.starting_player;
    }

    if (state.type_partie === "IA" && $("humanColorSelect")) {
      const aiColor = state.ai_player === "R" ? "R" : "J";
      const human = aiColor === "R" ? "J" : "R";
      $("humanColorSelect").value = human;
      humanColor = human;
      localStorage.setItem("humanColor", human);
    }

    syncUiPrefsFromForm();
    updateModeUI();
    updateShareLinkVisibility();
  } finally {
    suppressSelectChange = false;
  }
}

function clearHint() {
  if (hintTimer) {
    clearTimeout(hintTimer);
    hintTimer = null;
  }
  hintColumn = null;
}

function scheduleHintClear(ms) {
  clearHint();
  hintTimer = setTimeout(() => {
    hintColumn = null;
    hintTimer = null;
    if (lastState) render(lastState);
  }, ms);
}

// --- Noms affichés ---
function nameFor(letter) {
  if (letter === "R") return lastState?.player_r_name || PLAYER_R_NAME || "Joueur rouge";
  if (letter === "J") return lastState?.player_j_name || PLAYER_J_NAME || "Joueur jaune";
  return "—";
}

// --- API ---
async function getState(id) {
  let url = "/api/state";
  if (id) {
    url += `?game_id=${encodeURIComponent(id)}&client_id=${encodeURIComponent(CLIENT_ID)}`;
  }
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) {
    setMessageOnly(data.error || "Erreur lors de la récupération de l’état.");
    return null;
  }
  return data;
}

async function postNewGame(payload) {
  const res = await fetch("/api/new", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postPlay(col) {
  const res = await fetch("/api/play", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ col, game_id: GAME_ID, client_id: CLIENT_ID })
  });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postAiMove() {
  const res = await fetch("/api/ai_move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_id: GAME_ID, client_id: CLIENT_ID })
  });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postHint() {
  const res = await fetch("/api/hint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_id: GAME_ID, client_id: CLIENT_ID })
  });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postSetAiColor(color, enabled) {
  const res = await fetch("/api/set_ai_color", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      game_id: GAME_ID,
      client_id: CLIENT_ID,
      color,
      enabled,
      player_r_name: localStorage.getItem("playerNameR") || PLAYER_R_NAME,
      player_j_name: localStorage.getItem("playerNameJ") || PLAYER_J_NAME
    })
  });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postLocalAiMove(board, player, depth) {
  const res = await fetch("/api/local_ai_move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ board, player, depth })
  });
  const data = await res.json();
  if (!res.ok) return { ok: false, error: data.error || "Erreur IA locale" };
  return { ok: true, col: data.col };
}

// --- Messages ---
function setMessageOnly(txt) {
  const msg = $("message");
  if (!msg) return;
  msg.hidden = false;
  msg.innerHTML = txt;
}

function hideMessageBox() {
  const msg = $("message");
  if (msg) {
    msg.hidden = true;
    msg.textContent = "";
  }
}

function showHistoryLine(txt, className = "log-item--system") {
  const historyDiv = $("history");
  if (!historyDiv) return;
  const item = document.createElement("div");
  item.className = "log-item " + className;
  item.innerHTML = `<span class="log-item__text">${escapeHtml(txt)}</span>`;
  historyDiv.appendChild(item);
  historyDiv.scrollTop = historyDiv.scrollHeight;
}

function showMessage(txt) {
  showHistoryLine(txt);
  setMessageOnly(txt);
}

// --- Plateau local ---
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
  const dirs = [
    [0, 1],
    [1, 0],
    [1, 1],
    [1, -1]
  ];
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

// --- En-tête ---
function updateHeaderStatus(state) {
  const dot = $("statusDot");
  const text = $("headerStatusText");
  if (!text) return;

  if (!state) {
    text.textContent = "Aucune donnée";
    dot?.classList.remove("header-status__dot--play", "header-status__dot--wait", "header-status__dot--done");
    return;
  }

  if (state.game_over) {
    text.textContent = "Terminée";
    dot?.classList.remove("header-status__dot--play", "header-status__dot--wait");
    dot?.classList.add("header-status__dot--done");
    return;
  }

  if (paused) {
    text.textContent = "En pause";
    dot?.classList.remove("header-status__dot--play", "header-status__dot--done");
    dot?.classList.add("header-status__dot--wait");
    return;
  }

  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    text.textContent = "En attente d’un adversaire";
    dot?.classList.remove("header-status__dot--play", "header-status__dot--done");
    dot?.classList.add("header-status__dot--wait");
    return;
  }

  text.textContent = "En cours";
  dot?.classList.remove("header-status__dot--wait", "header-status__dot--done");
  dot?.classList.add("header-status__dot--play");
}

// --- Polling / IA ---
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

function setThinking(on) {
  const el = $("aiThinking");
  if (!el) return;
  el.hidden = !on;
}

function scheduleAiIfNeeded() {
  if (!lastState || lastState.game_over || paused || busy) return;
  if (isAiTurn(lastState)) {
    cancelAiTimer();
    aiTimer = setTimeout(aiMove, AI_DELAY_MS);
  }
}

function startPolling() {
  if (pollTimer) return;

  const tick = async () => {
    if (!lastState || lastState.game_over || lastState.mode !== "WEB" || !GAME_ID) {
      stopPolling();
      return;
    }
    const data = await getState(GAME_ID);
    if (!data) return;

    const changed =
      data.signature !== lastState.signature ||
      data.current_player !== lastState.current_player ||
      data.game_over !== lastState.game_over ||
      data.player_count !== lastState.player_count ||
      data.client_r !== lastState.client_r ||
      data.client_j !== lastState.client_j ||
      JSON.stringify(data.ai_players || {}) !== JSON.stringify(lastState.ai_players || {}) ||
      data.player_r_name !== lastState.player_r_name ||
      data.player_j_name !== lastState.player_j_name;

    if (changed) {
      lastMove = findLastMove(lastState.board, data.board);
      lastState = data;
      render(lastState);
      scheduleAiIfNeeded();
    }
  };

  tick();
  pollTimer = setInterval(tick, 800);
}

// --- Pause ---
function resetPauseUiOnly() {
  paused = false;
  const overlay = $("pauseOverlay");
  if (overlay) {
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
  }
  $("board")?.classList.remove("is-paused");
  const bp = $("btnPause");
  if (bp) bp.textContent = "Pause";
}

function setPaused(value) {
  paused = !!value;
  if (paused) cancelAiTimer();
  const overlay = $("pauseOverlay");
  const board = $("board");
  if (overlay) {
    overlay.hidden = !paused;
    overlay.setAttribute("aria-hidden", paused ? "false" : "true");
  }
  if (board) board.classList.toggle("is-paused", paused);
  updateHeaderStatus(lastState);
  if (lastState) render(lastState);
  if (!paused) scheduleAiIfNeeded();
}

// --- Undo / redo ---
function canUseUndoRedo() {
  if (!lastState || paused) return false;
  if (lastState.mode === "LOCAL") return true;
  if (lastState.mode === "WEB" && lastState.type_partie === "IA") return true;
  return false;
}

function updateUndoRedoButtons() {
  const u = $("btnUndo");
  const r = $("btnRedo");
  const allowed = canUseUndoRedo() && !busy;
  if (u) {
    u.disabled = !allowed || undoStack.length === 0;
  }
  if (r) {
    r.disabled = !allowed || redoStack.length === 0;
  }
}

async function undo() {
  if (!canUseUndoRedo() || undoStack.length === 0 || !lastState) return;

  if (lastState.mode === "LOCAL") {
    const current = snapshotForUndo(lastState);
    const prev = undoStack.pop();
    redoStack.push(current);
    applySnapshot(lastState, prev);
    lastMove = null;
    clearHint();
    hideMessageBox();
    render(lastState);
    showHistoryLine("Coup annulé.", "log-item--system");
    scheduleAiIfNeeded();
    return;
  }

  if (lastState.mode === "WEB" && lastState.type_partie === "IA") {
    busy = true;
    const prev = undoStack.pop();
    redoStack.push(cloneFullState(lastState));
    const ok = await resyncServerStateFromSnapshot(prev);
    busy = false;
    if (!ok) {
      undoStack.push(prev);
      redoStack.pop();
      updateUndoRedoButtons();
      return;
    }
    clearHint();
    hideMessageBox();
    showHistoryLine("Coup annulé.", "log-item--system");
  }
}

async function redo() {
  if (!canUseUndoRedo() || redoStack.length === 0 || !lastState) return;

  if (lastState.mode === "LOCAL") {
    const current = snapshotForUndo(lastState);
    const next = redoStack.pop();
    undoStack.push(current);
    applySnapshot(lastState, next);
    lastMove = null;
    clearHint();
    render(lastState);
    showHistoryLine("Coup rétabli.", "log-item--system");
    scheduleAiIfNeeded();
    return;
  }

  if (lastState.mode === "WEB" && lastState.type_partie === "IA") {
    busy = true;
    const next = redoStack.pop();
    undoStack.push(cloneFullState(lastState));
    const ok = await resyncServerStateFromSnapshot(next);
    busy = false;
    if (!ok) {
      redoStack.push(next);
      undoStack.pop();
      updateUndoRedoButtons();
      return;
    }
    clearHint();
    showHistoryLine("Coup rétabli.", "log-item--system");
  }
}

// --- Nouvelle partie ---
async function newGame() {
  busy = false;
  hideMessageBox();
  lastMove = null;
  lastBoardSnapshot = null;
  cancelAiTimer();
  setThinking(false);
  clearHint();
  undoStack.length = 0;
  redoStack.length = 0;

  const mode = ($("modeSelect")?.value || "IA").toUpperCase();
  const difficulty = $("diffSelect")?.value || "4";

  const starting_player =
    mode === "ONLINE" ? undefined : (($("colorSelect")?.value || "R").toUpperCase());

  const human_player =
    mode === "IA" ? (($("humanColorSelect")?.value || humanColor || "R").toUpperCase()) : undefined;

  GAME_ID = null;
  history.replaceState({}, "", location.pathname);

  if (mode === "LOCAL") {
    const start = starting_player === "R" || starting_player === "J" ? starting_player : "R";
    lastState = {
      id_partie: null,
      mode: "LOCAL",
      type_partie: "HUMAIN",
      status: "EN_COURS",
      ai_enabled: false,
      ai_depth: Number(difficulty),
      ai_player: null,
      ai_players: { R: false, J: false },
      board: Array.from({ length: ROWS }, () => Array(COLS).fill(0)),
      current_player: start,
      starting_player: start,
      signature: "init",
      game_over: false,
      winning_line: null,
      player_count: 1,
      client_r: null,
      client_j: null,
      player_r_name: PLAYER_R_NAME,
      player_j_name: PLAYER_J_NAME
    };
    stopPolling();
    resetPauseUiOnly();
    syncUiPrefsFromForm();
    render(lastState);
    return;
  }

  const payload = {
    mode,
    difficulty,
    starting_player,
    human_player,
    client_id: CLIENT_ID,
    player_r_name: PLAYER_R_NAME,
    player_j_name: PLAYER_J_NAME
  };

  const { ok, data: state } = await postNewGame(payload);
  if (!ok) {
    setMessageOnly(state.error || "Erreur lors de la création de la partie.");
    return;
  }

  lastState = state;

  if (state.id_partie) {
    GAME_ID = state.id_partie;
    history.replaceState({}, "", `?game_id=${GAME_ID}`);
    const linkInput = $("shareLink");
    if (linkInput) linkInput.value = window.location.href;
    if (state.mode === "WEB") startPolling();
  }

  resetPauseUiOnly();
  syncUiPrefsFromForm();
  render(lastState);
  scheduleAiIfNeeded();
}

// --- Jouer ---
async function play(col) {
  if (paused || busy || !lastState) return;

  if (lastState.mode === "WEB" && lastState.type_partie === "HUMAIN" && lastState.player_count < 2) {
    setMessageOnly("En attente d’un adversaire… Partage le lien de la partie.");
    return;
  }

  if (isAiTurn(lastState)) {
    return;
  }

  if (lastState.mode === "LOCAL") {
    if (lastState.game_over) return;
    if (isColumnFull(col)) return;

    undoStack.push(snapshotForUndo(lastState));
    redoStack.length = 0;

    let placed = null;
    for (let r = ROWS - 1; r >= 0; r--) {
      if (lastState.board[r][col] === 0) {
        placed = r;
        lastState.board[r][col] = lastState.current_player;
        break;
      }
    }
    if (placed === null) return;

    if (String(lastState.signature).startsWith("init_")) lastState.signature = "";
    lastState.signature += String(col + 1);
    lastMove = { r: placed, c: col };
    clearHint();

    const line = jsFindWinningLine(placed, col, lastState.board);
    if (line) {
      lastState.game_over = true;
      lastState.status = "TERMINEE";
      lastState.winning_line = line.map(([rr, cc]) => [rr, cc]);
      render(lastState);
      setMessageOnly(
        `Victoire de <span class="name-${lastState.current_player === "R" ? "red" : "yellow"}">${escapeHtml(
          nameFor(lastState.current_player)
        )}</span> !`
      );
      return;
    }

    lastState.current_player = lastState.current_player === "R" ? "J" : "R";
    render(lastState);
    scheduleAiIfNeeded();
    return;
  }

  if (!lastState.id_partie) {
    showMessage("Clique d’abord sur « Nouvelle partie ».");
    return;
  }
  if (lastState.game_over) return;
  if (isColumnFull(col)) return;

  if (lastState.mode === "WEB" && lastState.type_partie === "IA") {
    undoStack.push(cloneFullState(lastState));
    redoStack.length = 0;
  }

  cancelAiTimer();
  busy = true;
  clearHint();

  let res;
  try {
    res = await postPlay(col);
  } catch {
    busy = false;
    if (lastState.mode === "WEB" && lastState.type_partie === "IA" && undoStack.length) {
      undoStack.pop();
    }
    showMessage("Erreur réseau.");
    return;
  }

  busy = false;
  if (!res.ok) {
    if (lastState.mode === "WEB" && lastState.type_partie === "IA" && undoStack.length) {
      undoStack.pop();
    }
    showMessage(res.data.error || "Erreur lors du coup.");
    return;
  }

  const data = res.data;
  lastMove = findLastMove(lastState.board, data.board);
  lastState = data;
  render(lastState);

  if (lastState.game_over) {
    setMessageOnly(
      `Victoire de <span class="name-${lastState.current_player === "R" ? "red" : "yellow"}">${escapeHtml(
        nameFor(lastState.current_player)
      )}</span> !`
    );
    return;
  }

  scheduleAiIfNeeded();
}

async function aiMove() {
  aiTimer = null;
  if (!lastState || lastState.game_over || paused) return;
  if (!isAiTurn(lastState)) return;

  setThinking(true);
  const t0 = performance.now();

  if (lastState.mode === "LOCAL") {
    const player = lastState.current_player;
    const depth = Number(lastState.ai_depth || 4);

    let res;
    try {
      res = await postLocalAiMove(lastState.board, player, depth);
    } catch {
      setThinking(false);
      showMessage("Erreur locale de l’IA.");
      return;
    }

    setThinking(false);

    if (!res.ok) {
      showMessage(res.error || "Erreur de l’IA.");
      return;
    }

    const col = res.col;
    let placed = null;

    for (let r = ROWS - 1; r >= 0; r--) {
      if (lastState.board[r][col] === 0) {
        placed = r;
        lastState.board[r][col] = player;
        break;
      }
    }

    if (placed === null) {
      showMessage("Coup IA impossible.");
      return;
    }

    if (String(lastState.signature).startsWith("init_")) lastState.signature = "";
    lastState.signature += String(col + 1);

    const dt = Math.round(performance.now() - t0);
    lastMove = { r: placed, c: col };
    clearHint();

    const line = jsFindWinningLine(placed, col, lastState.board);
    if (line) {
      lastState.game_over = true;
      lastState.status = "TERMINEE";
      lastState.winning_line = line.map(([rr, cc]) => [rr, cc]);
      render(lastState);
      showHistoryLine(`L’IA (${player === "R" ? "rouge" : "jaune"}) a joué (${dt} ms).`, "log-item--system");
      setMessageOnly(
        `Victoire de <span class="name-${player === "R" ? "red" : "yellow"}">${escapeHtml(
          nameFor(player)
        )}</span> !`
      );
      return;
    }

    lastState.current_player = player === "R" ? "J" : "R";
    render(lastState);
    showHistoryLine(`L’IA (${player === "R" ? "rouge" : "jaune"}) a joué (${dt} ms).`, "log-item--system");
    scheduleAiIfNeeded();
    return;
  }

  let res;
  try {
    res = await postAiMove();
  } catch {
    setThinking(false);
    showMessage("Erreur réseau.");
    return;
  }

  const data = res.data;
  setThinking(false);

  if (!res.ok) {
    showMessage(data.error || "Erreur de l’IA.");
    return;
  }

  const dt = Math.round(performance.now() - t0);
  lastMove = findLastMove(lastState.board, data.board);
  lastState = data;
  render(lastState);
  showHistoryLine(`L’IA a joué (${dt} ms).`, "log-item--system");

  if (lastState.game_over) {
    setMessageOnly(
      `Victoire de <span class="name-${lastState.current_player === "R" ? "red" : "yellow"}">${escapeHtml(
        nameFor(lastState.current_player)
      )}</span> !`
    );
    return;
  }

  scheduleAiIfNeeded();
}

// --- Rendu ---
function setModePill(state) {
  const pill = $("turnPill");
  if (!pill) return;
  pill.innerHTML = "";

  let modeTxt = "";
  const dot = document.createElement("span");
  dot.className = "pill__dot";

  if (state.mode === "LOCAL") {
    const aiPlayers = state.ai_players || { R: false, J: false };
    if (aiPlayers.R || aiPlayers.J) {
      modeTxt = "Local (avec IA)";
      dot.style.background = "#a78bfa";
    } else {
      modeTxt = "Joueur contre joueur (local)";
    }
  } else if (state.type_partie === "IA") {
    modeTxt = "Joueur contre IA";
    dot.style.background = "#4ade80";
  } else if (state.mode === "WEB" && state.type_partie === "HUMAIN" && (state.ai_players?.R || state.ai_players?.J)) {
    modeTxt = "En ligne (+ IA remplaçante)";
    dot.style.background = "#a78bfa";
  } else {
    modeTxt = "Joueur contre joueur (en ligne)";
    dot.style.background = "#38bdf8";
  }

  const label = document.createElement("span");
  label.textContent = modeTxt;
  pill.appendChild(dot);
  pill.appendChild(label);
}

function renderRole(state) {
  const roleDiv = $("yourRole");
  if (!roleDiv) return;

  const aiPlayers = state.ai_players || { R: false, J: false };

  if (state.mode === "WEB" && state.type_partie === "HUMAIN") {
    const myColor = myOnlineColor(state);

    if (!myColor) {
      if (state.player_count >= 2) roleDiv.textContent = "Spectateur";
      else roleDiv.textContent = "En attente d’un adversaire…";
      return;
    }

    const colorText = myColor === "R" ? "rouges" : "jaunes";
    if (aiPlayers[myColor]) {
      roleDiv.textContent = `Tu es ${colorText} — l’IA joue actuellement à ta place.`;
    } else {
      roleDiv.textContent = `Tu joues les ${colorText}.`;
    }
    return;
  }

  if (state.mode === "LOCAL") {
    const redTxt = aiPlayers.R ? "rouge = IA" : "rouge = humain";
    const yellowTxt = aiPlayers.J ? "jaune = IA" : "jaune = humain";
    roleDiv.textContent = `Partie locale — ${redTxt}, ${yellowTxt}.`;
    return;
  }

  if (state.type_partie === "IA") {
    const aiColor = state.ai_player;
    const human = aiColor === "R" ? "jaune" : "rouge";
    roleDiv.textContent = `Tu affrontes l’IA sur ce navigateur. Tu joues ${human}.`;
    return;
  }

  roleDiv.textContent = "—";
}

function renderStatusText(state) {
  const statusTxt = $("statusTxt");
  if (!statusTxt) return;

  let st = state.status || (state.game_over ? "TERMINEE" : "EN_COURS");
  if (paused) st = "EN PAUSE";
  else if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    st = "EN ATTENTE D’UN ADVERSAIRE";
  }
  statusTxt.textContent = st;
}

function updateTurnInfo(state) {
  const el = $("turnInfo");
  if (!el || !state) return;

  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    el.innerHTML = "Partie créée — en attente d’un second joueur.";
    return;
  }

  const turn = state.current_player;
  const turnName = nameFor(turn);

  if (!lastMove) {
    el.innerHTML = `Début de partie — à <b>${escapeHtml(turnName)}</b> de jouer.`;
    return;
  }

  const lastPlayer = turn === "R" ? "J" : "R";
  const lastName = nameFor(lastPlayer);
  const colHuman = lastMove.c + 1;
  el.innerHTML =
    `Dernier coup : <b>${escapeHtml(lastName)}</b> en colonne <b>${colHuman}</b> — ` +
    `à <b>${escapeHtml(turnName)}</b> de jouer.`;
}

function renderMessage(state) {
  const msg = $("message");
  if (!msg) return;

  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    msg.hidden = false;
    let txt = "En attente d’un adversaire… ";
    if (state.client_r === CLIENT_ID) txt += "Tu joues les rouges. ";
    if (state.client_j === CLIENT_ID) txt += "Tu joues les jaunes. ";
    txt += "Partage le lien.";
    msg.innerHTML = txt;
    return;
  }

  msg.hidden = false;
  if (state.game_over) {
    const cls = state.current_player === "R" ? "name-red" : "name-yellow";
    msg.innerHTML = `Victoire de <span class="${cls}">${escapeHtml(nameFor(state.current_player))}</span> !`;
  } else {
    const cls = state.current_player === "R" ? "name-red" : "name-yellow";
    msg.innerHTML = `Tour de <span class="${cls}">${escapeHtml(nameFor(state.current_player))}</span>`;
  }
}

function interactionLocked(state) {
  return (
    paused ||
    busy ||
    !state ||
    state.game_over ||
    isAiTurn(state) ||
    (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2)
  );
}

function renderColHeader(state) {
  const header = $("colHeader");
  if (!header) return;
  header.innerHTML = "";

  const locked = interactionLocked(state);

  for (let c = 0; c < COLS; c++) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "col-btn";
    btn.textContent = String(c + 1);
    const full = state.board?.[0]?.[c] !== 0;
    const disabled = locked || full;
    if (disabled) btn.classList.add("col-btn--blocked");
    btn.disabled = disabled;
    if (hintColumn === c) btn.classList.add("col-btn--hint");

    btn.addEventListener("mouseenter", () => {
      if (interactionLocked(lastState)) return;
      hoverCol = c;
      applyPreview();
      highlightColButton(c);
    });
    btn.addEventListener("mouseleave", () => {
      hoverCol = null;
      applyPreview();
      highlightColButton(null);
    });
    btn.addEventListener("click", () => play(c));

    header.appendChild(btn);
  }
}

function highlightColButton(col) {
  const header = $("colHeader");
  if (!header) return;
  const buttons = header.querySelectorAll(".col-btn");
  buttons.forEach((b, i) => {
    b.classList.toggle("col-btn--hover", col !== null && i === col);
  });
}

function renderBoard(state) {
  const boardDiv = $("board");
  if (!boardDiv) return;

  boardDiv.innerHTML = "";

  const prev = lastBoardSnapshot;

  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = document.createElement("div");
      cell.className = "cell";
      if (interactionLocked(state)) cell.classList.add("cell--disabled");

      cell.addEventListener("mouseenter", () => {
        if (interactionLocked(lastState)) return;
        hoverCol = c;
        applyPreview();
        highlightColButton(c);
      });
      cell.addEventListener("mouseleave", () => {
        hoverCol = null;
        applyPreview();
        highlightColButton(null);
      });
      cell.addEventListener("click", () => play(c));

      const piece = document.createElement("div");
      piece.className = "piece";

      const v = state.board?.[r]?.[c];
      const prevV = prev?.[r]?.[c];

      if (v === "R" || v === "J") {
        piece.classList.add(v === "R" ? "piece--red" : "piece--yellow");
        piece.classList.add("piece--visible");
        const isNew = prevV === 0 || prevV === undefined;
        if (isNew) piece.classList.add("piece--drop");
      }

      cell.appendChild(piece);
      boardDiv.appendChild(cell);
    }
  }

  lastBoardSnapshot = deepCloneBoard(state.board);
}

function applyPreview() {
  const boardDiv = $("board");
  if (!boardDiv || !lastState) return;

  const cells = boardDiv.querySelectorAll(".cell");
  cells.forEach((cell) => {
    cell.classList.remove("cell--preview", "cell--preview-red", "cell--preview-yellow");
  });

  if (hoverCol == null || lastState.game_over || interactionLocked(lastState)) return;

  let targetRow = null;
  for (let r = ROWS - 1; r >= 0; r--) {
    if (lastState.board?.[r]?.[hoverCol] === 0) {
      targetRow = r;
      break;
    }
  }
  if (targetRow == null) return;

  const idx = targetRow * COLS + hoverCol;
  const cell = cells[idx];
  if (!cell) return;
  cell.classList.add("cell--preview");
  cell.classList.add(lastState.current_player === "R" ? "cell--preview-red" : "cell--preview-yellow");
}

function applyLastMove() {
  const boardDiv = $("board");
  if (!boardDiv) return;
  const cells = boardDiv.querySelectorAll(".cell");
  cells.forEach((c) => c.classList.remove("cell--last"));
  if (!lastMove) return;
  const idx = lastMove.r * COLS + lastMove.c;
  cells[idx]?.classList.add("cell--last");
}

function applyWinningLine(state) {
  const boardDiv = $("board");
  if (!boardDiv) return;
  const cells = boardDiv.querySelectorAll(".cell");
  cells.forEach((c) => c.classList.remove("cell--win"));
  const line = state.winning_line;
  if (!line || !Array.isArray(line)) return;
  for (const [r, c] of line) {
    const idx = r * COLS + c;
    cells[idx]?.classList.add("cell--win");
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
    const empty = document.createElement("div");
    empty.className = "log-item log-item--system";
    empty.textContent = "Aucun coup pour l’instant.";
    historyDiv.appendChild(empty);
    return;
  }

  const starting = (state.starting_player || "R").toUpperCase();

  for (let i = 0; i < moves.length; i++) {
    const col = Number(moves[i]);
    const moveLetter = i % 2 === 0 ? starting : starting === "R" ? "J" : "R";
    const isRed = moveLetter === "R";
    const name = nameFor(moveLetter);

    const item = document.createElement("div");
    item.className = "log-item " + (isRed ? "log-item--red" : "log-item--yellow");
    if (i === moves.length - 1) item.classList.add("log-item--last");

    item.innerHTML = `
      <span class="log-item__name">${escapeHtml(name)}</span>
      <span> colonne ${col}</span>
      <div class="log-meta">Coup ${i + 1}</div>
    `;
    historyDiv.appendChild(item);
  }
  historyDiv.scrollTop = historyDiv.scrollHeight;
}

function updateModeUI() {
  const mode = ($("modeSelect")?.value || "IA").toUpperCase();
  const diffSelect = $("diffSelect");
  const difficultyField = $("difficultyField");
  const colorSelect = $("colorSelect");
  const humanColorSelect = $("humanColorSelect");
  const startHint = $("startHint");
  const nameR = $("playerNameR");
  const nameJ = $("playerNameJ");

  if (difficultyField) difficultyField.style.display = mode === "IA" ? "" : "none";
  if (diffSelect) diffSelect.disabled = mode !== "IA";

  if (colorSelect) colorSelect.disabled = mode === "ONLINE";
  if (humanColorSelect) humanColorSelect.disabled = mode !== "IA";

  if (startHint) startHint.classList.toggle("is-visible", mode === "ONLINE");

  const selectedHumanColor = ($("humanColorSelect")?.value || humanColor || "R").toUpperCase();
  humanColor = selectedHumanColor;

  if (mode === "IA") {
    if (selectedHumanColor === "R") {
      PLAYER_J_NAME = "IA";

      if (nameJ) {
        nameJ.value = "IA";
        nameJ.disabled = true;
      }

      if (nameR) {
        nameR.disabled = false;
        nameR.value = localStorage.getItem("playerNameR") || PLAYER_R_NAME;
        PLAYER_R_NAME = nameR.value || "Joueur rouge";
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
        PLAYER_J_NAME = nameJ.value || "Joueur jaune";
      }
    }
  } else {
    if (nameR) {
      nameR.disabled = false;
      nameR.value = localStorage.getItem("playerNameR") || PLAYER_R_NAME;
      PLAYER_R_NAME = nameR.value || "Joueur rouge";
    }

    if (nameJ) {
      nameJ.disabled = false;
      nameJ.value = localStorage.getItem("playerNameJ") || PLAYER_J_NAME;
      PLAYER_J_NAME = nameJ.value || "Joueur jaune";
    }
  }

  updateShareLinkVisibility();
  if (lastState) render(lastState);
}

function updateAiColorButtons(state) {
  const btnAiRed = $("btnAiRed");
  const btnHumanRed = $("btnHumanRed");
  const btnAiYellow = $("btnAiYellow");
  const btnHumanYellow = $("btnHumanYellow");

  if (!btnAiRed || !btnHumanRed || !btnAiYellow || !btnHumanYellow) return;

  const hideAll = () => {
    for (const btn of [btnAiRed, btnHumanRed, btnAiYellow, btnHumanYellow]) {
      btn.hidden = true;
      btn.disabled = true;
    }
  };

  hideAll();

  if (!state || state.game_over) return;

  const aiPlayers = state.ai_players || { R: false, J: false };

  if (state.mode === "LOCAL") {
    btnAiRed.hidden = false;
    btnHumanRed.hidden = false;
    btnAiYellow.hidden = false;
    btnHumanYellow.hidden = false;

    btnAiRed.disabled = busy || paused || aiPlayers.R;
    btnHumanRed.disabled = busy || paused || !aiPlayers.R;

    btnAiYellow.disabled = busy || paused || aiPlayers.J;
    btnHumanYellow.disabled = busy || paused || !aiPlayers.J;
    return;
  }

  if (state.mode === "WEB" && state.type_partie === "HUMAIN") {
    const myColor = myOnlineColor(state);
    if (!myColor) return;

    if (myColor === "R") {
      btnAiRed.hidden = false;
      btnHumanRed.hidden = false;
      btnAiRed.disabled = busy || paused || aiPlayers.R;
      btnHumanRed.disabled = busy || paused || !aiPlayers.R;
    } else {
      btnAiYellow.hidden = false;
      btnHumanYellow.hidden = false;
      btnAiYellow.disabled = busy || paused || aiPlayers.J;
      btnHumanYellow.disabled = busy || paused || !aiPlayers.J;
    }
  }
}

function render(state) {
  if (!state) return;

  setModePill(state);
  renderRole(state);
  renderStatusText(state);
  renderMessage(state);
  renderColHeader(state);
  renderHistory(state);
  renderBoard(state);

  const linkInput = $("shareLink");
  if (linkInput) {
    linkInput.value = state.id_partie && GAME_ID ? window.location.href : "";
  }

  applyPreview();
  applyLastMove();
  applyWinningLine(state);
  updateTurnInfo(state);
  updateHeaderStatus(state);
  updateUndoRedoButtons();
  updateUndoHelpText();
  updateAiColorButtons(state);
}

// --- Initialisation ---
window.addEventListener("error", (ev) => {
  console.error("Erreur JS", ev.error || ev.message);
  setMessageOnly("Erreur : " + (ev.error?.message || ev.message));
});

window.addEventListener("load", async () => {
  if ($("playerNameR")) $("playerNameR").value = PLAYER_R_NAME;
  if ($("playerNameJ")) $("playerNameJ").value = PLAYER_J_NAME;
  if ($("humanColorSelect")) $("humanColorSelect").value = humanColor;
  if ($("colorSelect")) $("colorSelect").value = uiPrefs.startingPlayer || "R";
  if ($("diffSelect")) $("diffSelect").value = uiPrefs.difficulty;

  $("btnNew")?.addEventListener("click", newGame);

  $("btnUndo")?.addEventListener("click", () => {
    void undo().catch((err) => console.error(err));
  });
  $("btnRedo")?.addEventListener("click", () => {
    void redo().catch((err) => console.error(err));
  });

  $("confirmModalYes")?.addEventListener("click", async () => {
    const fn = pendingConfirmCallback;
    hideConfirmModal();
    if (typeof fn === "function") await fn();
  });

  $("confirmModalNo")?.addEventListener("click", () => {
    hideConfirmModal();
  });

  $("btnPause")?.addEventListener("click", () => {
    if (!lastState) return;
    setPaused(!paused);
    $("btnPause").textContent = paused ? "Reprendre" : "Pause";
  });

  $("btnResume")?.addEventListener("click", () => {
    setPaused(false);
    const bp = $("btnPause");
    if (bp) bp.textContent = "Pause";
  });

  // --- Bouton suggestion adapté au mode local ---
  $("btnHint")?.addEventListener("click", async () => {
    if (!lastState || paused) return;

    if (lastState.game_over) {
      showMessage("La partie est terminée.");
      return;
    }

    if (lastState.mode === "LOCAL") {
      const player = lastState.current_player;
      const depth = Number(lastState.ai_depth || 4);

      const res = await postLocalAiMove(lastState.board, player, depth);
      if (!res.ok) {
        showMessage(res.error || "Aucune suggestion disponible.");
        return;
      }

      const col = res.col;
      if (typeof col !== "number") {
        showMessage("Réponse locale inattendue.");
        return;
      }

      hintColumn = col;
      render(lastState);
      showHistoryLine(`Suggestion locale : colonne ${col + 1}.`, "log-item--system");
      scheduleHintClear(8000);
      return;
    }

    if (!GAME_ID) {
      showMessage("Crée d’abord une partie.");
      return;
    }

    const res = await postHint();
    if (!res.ok) {
      showMessage(res.data.error || "Aucune suggestion disponible.");
      return;
    }

    const col = res.data.suggested_col;
    if (typeof col !== "number") {
      showMessage("Réponse du serveur inattendue.");
      return;
    }

    hintColumn = col;
    render(lastState);
    showHistoryLine(`Suggestion : colonne ${col + 1}.`, "log-item--system");
    scheduleHintClear(8000);
  });

  $("btnCopyLink")?.addEventListener("click", () => {
    const link = $("shareLink")?.value || "";
    if (!link) return;
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(link).then(() => setMessageOnly("Lien copié.")).catch(() => {});
    } else {
      $("shareLink")?.select();
      document.execCommand("copy");
      setMessageOnly("Lien copié.");
    }
  });

  $("btnAiRed")?.addEventListener("click", async () => {
    if (!lastState) return;

    if (lastState.mode === "LOCAL") {
      lastState.ai_players = { ...(lastState.ai_players || { R: false, J: false }), R: true };
      lastState.ai_enabled = true;
      lastState.ai_depth = Number($("diffSelect")?.value || lastState.ai_depth || 4);
      lastState.player_r_name = "IA";
      render(lastState);
      showHistoryLine("Le joueur rouge est désormais contrôlé par l’IA.", "log-item--system");
      scheduleAiIfNeeded();
      return;
    }

    if (!GAME_ID) {
      showMessage("Aucune partie active.");
      return;
    }
    const res = await postSetAiColor("R", true);
    if (!res.ok) {
      showMessage(res.data.error || "Impossible d’activer l’IA pour rouge.");
      return;
    }
    lastState = res.data;
    render(lastState);
    showHistoryLine("Le joueur rouge est désormais contrôlé par l’IA.", "log-item--system");
    scheduleAiIfNeeded();
  });

  $("btnHumanRed")?.addEventListener("click", async () => {
    if (!lastState) return;

    if (lastState.mode === "LOCAL") {
      lastState.ai_players = { ...(lastState.ai_players || { R: false, J: false }), R: false };
      lastState.ai_enabled = !!(lastState.ai_players.R || lastState.ai_players.J);
      lastState.player_r_name = localStorage.getItem("playerNameR") || "Joueur rouge";
      render(lastState);
      showHistoryLine("Le joueur rouge redevient humain.", "log-item--system");
      return;
    }

    if (!GAME_ID) {
      showMessage("Aucune partie active.");
      return;
    }
    const res = await postSetAiColor("R", false);
    if (!res.ok) {
      showMessage(res.data.error || "Impossible de rendre rouge humain.");
      return;
    }
    lastState = res.data;
    render(lastState);
    showHistoryLine("Le joueur rouge redevient humain.", "log-item--system");
  });

  $("btnAiYellow")?.addEventListener("click", async () => {
    if (!lastState) return;

    if (lastState.mode === "LOCAL") {
      lastState.ai_players = { ...(lastState.ai_players || { R: false, J: false }), J: true };
      lastState.ai_enabled = true;
      lastState.ai_depth = Number($("diffSelect")?.value || lastState.ai_depth || 4);
      lastState.player_j_name = "IA";
      render(lastState);
      showHistoryLine("Le joueur jaune est désormais contrôlé par l’IA.", "log-item--system");
      scheduleAiIfNeeded();
      return;
    }

    if (!GAME_ID) {
      showMessage("Aucune partie active.");
      return;
    }
    const res = await postSetAiColor("J", true);
    if (!res.ok) {
      showMessage(res.data.error || "Impossible d’activer l’IA pour jaune.");
      return;
    }
    lastState = res.data;
    render(lastState);
    showHistoryLine("Le joueur jaune est désormais contrôlé par l’IA.", "log-item--system");
    scheduleAiIfNeeded();
  });

  $("btnHumanYellow")?.addEventListener("click", async () => {
    if (!lastState) return;

    if (lastState.mode === "LOCAL") {
      lastState.ai_players = { ...(lastState.ai_players || { R: false, J: false }), J: false };
      lastState.ai_enabled = !!(lastState.ai_players.R || lastState.ai_players.J);
      lastState.player_j_name = localStorage.getItem("playerNameJ") || "Joueur jaune";
      render(lastState);
      showHistoryLine("Le joueur jaune redevient humain.", "log-item--system");
      return;
    }

    if (!GAME_ID) {
      showMessage("Aucune partie active.");
      return;
    }
    const res = await postSetAiColor("J", false);
    if (!res.ok) {
      showMessage(res.data.error || "Impossible de rendre jaune humain.");
      return;
    }
    lastState = res.data;
    render(lastState);
    showHistoryLine("Le joueur jaune redevient humain.", "log-item--system");
  });

  $("modeSelect")?.addEventListener("change", (e) => {
    if (suppressSelectChange) return;
    const newVal = e.target.value.toUpperCase();
    const oldVal = committedMode;
    if (newVal === oldVal) return;

    if (hasActiveGame()) {
      e.target.value = oldVal;
      showConfirmModal("Changer le mode de jeu va terminer la partie en cours. Continuer ?", async () => {
        suppressSelectChange = true;
        try {
          $("modeSelect").value = newVal;
          syncUiPrefsFromForm();
          updateModeUI();
          updateShareLinkVisibility();
          await newGame();
        } finally {
          suppressSelectChange = false;
        }
      });
    } else {
      syncUiPrefsFromForm();
      updateModeUI();
      updateShareLinkVisibility();
    }
  });

  $("diffSelect")?.addEventListener("change", (e) => {
    if (suppressSelectChange) return;
    const newVal = e.target.value;
    const oldVal = committedDifficulty;
    if (newVal === oldVal) return;

    if (hasActiveGame()) {
      e.target.value = oldVal;
      showConfirmModal("Changer la profondeur va terminer la partie en cours. Continuer ?", async () => {
        suppressSelectChange = true;
        try {
          $("diffSelect").value = newVal;
          syncUiPrefsFromForm();
          await newGame();
        } finally {
          suppressSelectChange = false;
        }
      });
    } else {
      syncUiPrefsFromForm();
    }
  });

  $("playerNameR")?.addEventListener("input", (e) => {
    PLAYER_R_NAME = e.target.value || "Joueur rouge";
    localStorage.setItem("playerNameR", PLAYER_R_NAME);
    const aiPlayers = lastState?.ai_players || { R: false, J: false };
    if (lastState && !aiPlayers.R) {
      lastState.player_r_name = PLAYER_R_NAME;
      render(lastState);
    }
  });

  $("playerNameJ")?.addEventListener("input", (e) => {
    PLAYER_J_NAME = e.target.value || "Joueur jaune";
    localStorage.setItem("playerNameJ", PLAYER_J_NAME);
    const aiPlayers = lastState?.ai_players || { R: false, J: false };
    if (lastState && !aiPlayers.J) {
      lastState.player_j_name = PLAYER_J_NAME;
      render(lastState);
    }
  });

  $("humanColorSelect")?.addEventListener("change", (e) => {
    humanColor = (e.target.value || "R").toUpperCase();
    localStorage.setItem("humanColor", humanColor);
    syncUiPrefsFromForm();
    updateModeUI();
  });

  $("colorSelect")?.addEventListener("change", () => {
    syncUiPrefsFromForm();
    updateModeUI();
  });

  updateModeUI();
  syncUiPrefsFromForm();
  updateShareLinkVisibility();

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
        setMessageOnly("Cette partie est terminée. Lance une nouvelle partie.");
      }
      syncSelectsFromLoadedState(lastState);
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
      ai_depth: 4,
      ai_player: null,
      ai_players: { R: false, J: false },
      board: Array.from({ length: ROWS }, () => Array(COLS).fill(0)),
      current_player: "R",
      starting_player: "R",
      signature: "init",
      game_over: false,
      winning_line: null,
      player_count: 0,
      client_r: null,
      client_j: null,
      player_r_name: PLAYER_R_NAME,
      player_j_name: PLAYER_J_NAME
    };
  }

  $("btnPause").textContent = "Pause";
  render(lastState);
  scheduleAiIfNeeded();
});