/**
 * Front-end Puissance 4 — communication avec l’API Flask existante uniquement.
 * Sections : configuration, API, historique annuler/rétablir (mode local), rendu, événements.
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
let playerColor = localStorage.getItem("playerColor") || "R";

/**
 * Préférences UI (mode / difficulté) alignées sur les listes — mises à jour après confirmation
 * ou chargement d’une partie.
 */
const uiPrefs = {
  mode: "IA",
  difficulty: "medium",
  startingPlayer: "R"
};
let committedMode = "IA";
let committedDifficulty = "medium";

/** Évite la modale lors du remplissage programmatique des listes. */
let suppressSelectChange = false;

let pendingConfirmCallback = null;

/** Pile annuler / rétablir : mode local, ou partie JvIA serveur (resynchronisation par rejouage). */
const undoStack = [];
const redoStack = [];

/** Dernière grille rendue (pour animer uniquement les nouveaux pions). */
let lastBoardSnapshot = null;

/** Colonne mise en évidence par « Coup suggéré » (API /api/hint). */
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

/**
 * Copie l’état jouable nécessaire à l’annulation (mode local).
 */
function snapshotForUndo(state) {
  return {
    board: deepCloneBoard(state.board),
    current_player: state.current_player,
    starting_player: state.starting_player,
    signature: state.signature,
    game_over: state.game_over,
    status: state.status,
    winning_line: state.winning_line ? state.winning_line.map((x) => [...x]) : null
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
  uiPrefs.difficulty = ($("diffSelect")?.value || "medium").toLowerCase();
  uiPrefs.startingPlayer = ($("colorSelect")?.value || "R").toUpperCase();
  committedMode = uiPrefs.mode;
  committedDifficulty = uiPrefs.difficulty;
}

function depthToDifficulty(d) {
  const n = Number(d);
  if (n <= 2) return "easy";
  if (n <= 4) return "medium";
  return "hard";
}

/** Partie en cours avec au moins un pion ou un coup enregistré. */
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

/**
 * Recrée une partie serveur puis rejoue la séquence jusqu’à l’état cible (annuler / rétablir JvIA).
 */
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

    if (st.type_partie === "IA") {
      if (st.current_player === st.ai_player) {
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
      $("diffSelect").value = depthToDifficulty(state.ai_depth);
    }
    if (state.starting_player && $("colorSelect")) {
      $("colorSelect").value = state.starting_player;
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

// --- API (chemins inchangés) ---
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

// --- Plateau (logique locale) ---
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

// --- En-tête statut global ---
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

// --- Polling ---
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

/** Après reprise, relance le tour IA si nécessaire (timer annulé pendant la pause). */
function scheduleAiIfNeeded() {
  if (!lastState || lastState.game_over || paused || busy) return;
  if (lastState.type_partie === "IA" && lastState.current_player === lastState.ai_player) {
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
  };

  tick();
  pollTimer = setInterval(tick, 800);
}

// --- Pause ---
/** Remet l’UI de pause sans re-rendre (utilisé au démarrage d’une nouvelle partie). */
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

// --- Annuler / rétablir ---
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
  const difficulty = ($("diffSelect")?.value || "medium").toLowerCase();
  const starting_player =
    mode === "ONLINE" ? undefined : (($("colorSelect")?.value || playerColor || "R").toUpperCase());

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
      ai_depth: 0,
      board: Array.from({ length: ROWS }, () => Array(COLS).fill(0)),
      current_player: start,
      starting_player: start,
      signature: "init",
      game_over: false,
      ai_player: null,
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

  if (lastState.type_partie === "IA" && lastState.current_player === lastState.ai_player && !lastState.game_over) {
    aiTimer = setTimeout(aiMove, AI_DELAY_MS);
  }
}

// --- Jouer ---
async function play(col) {
  if (paused || busy || !lastState) return;

  if (lastState.mode === "WEB" && lastState.type_partie === "HUMAIN" && lastState.player_count < 2) {
    setMessageOnly("En attente d’un adversaire… Partage le lien de la partie.");
    return;
  }

  if (lastState.type_partie === "IA" && lastState.current_player === lastState.ai_player) {
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
      lastState.winning_line = line.map(([r, c]) => [r, c]);
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

  if (lastState.type_partie === "IA" && lastState.current_player === lastState.ai_player) {
    aiTimer = setTimeout(aiMove, AI_DELAY_MS);
  }
}

async function aiMove() {
  aiTimer = null;
  if (!lastState || lastState.game_over || paused) return;

  setThinking(true);
  const t0 = performance.now();

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
  }
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
    modeTxt = "Joueur contre joueur (local)";
  } else if (state.type_partie === "IA") {
    modeTxt = "Joueur contre IA";
    dot.style.background = "#4ade80";
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

  if (state.mode === "WEB" && state.type_partie === "HUMAIN") {
    if (state.client_r === CLIENT_ID) roleDiv.textContent = "Tu joues les rouges.";
    else if (state.client_j === CLIENT_ID) roleDiv.textContent = "Tu joues les jaunes.";
    else if (state.player_count >= 2) roleDiv.textContent = "Spectateur";
    else roleDiv.textContent = "En attente d’un adversaire…";
  } else if (state.mode === "LOCAL") {
    roleDiv.textContent = "Partie locale — les deux joueurs sur cet appareil.";
  } else if (state.type_partie === "IA") {
    roleDiv.textContent = "Tu affrontes l’IA sur ce navigateur.";
  } else {
    roleDiv.textContent = "—";
  }
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
    (state.type_partie === "IA" && state.current_player === state.ai_player) ||
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
  const startHint = $("startHint");
  const nameR = $("playerNameR");
  const nameJ = $("playerNameJ");

  if (difficultyField) difficultyField.style.display = mode === "IA" ? "" : "none";
  if (diffSelect) diffSelect.disabled = mode !== "IA";

  if (colorSelect) colorSelect.disabled = mode === "ONLINE";
  if (startHint) startHint.classList.toggle("is-visible", mode === "ONLINE");

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
}

// --- Initialisation ---
window.addEventListener("error", (ev) => {
  console.error("Erreur JS", ev.error || ev.message);
  setMessageOnly("Erreur : " + (ev.error?.message || ev.message));
});

window.addEventListener("load", async () => {
  if ($("playerNameR")) $("playerNameR").value = PLAYER_R_NAME;
  if ($("playerNameJ")) $("playerNameJ").value = PLAYER_J_NAME;
  if ($("colorSelect")) $("colorSelect").value = playerColor;

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

  $("btnHint")?.addEventListener("click", async () => {
    if (!lastState || paused) return;

    if (lastState.mode === "LOCAL") {
      showMessage("Indisponible en mode local : lance une partie contre l’IA pour obtenir une suggestion.");
      return;
    }
    if (!GAME_ID) {
      showMessage("Crée d’abord une partie.");
      return;
    }
    if (lastState.game_over) {
      showMessage("La partie est terminée.");
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
    const newVal = e.target.value.toLowerCase();
    const oldVal = committedDifficulty;
    if (newVal === oldVal) return;

    if (hasActiveGame()) {
      e.target.value = oldVal;
      showConfirmModal("Changer la difficulté va terminer la partie en cours. Continuer ?", async () => {
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
    if (lastState) render(lastState);
  });

  $("playerNameJ")?.addEventListener("input", (e) => {
    PLAYER_J_NAME = e.target.value || "Joueur jaune";
    localStorage.setItem("playerNameJ", PLAYER_J_NAME);
    if (lastState) render(lastState);
  });

  $("colorSelect")?.addEventListener("change", (e) => {
    playerColor = e.target.value || "R";
    localStorage.setItem("playerColor", playerColor);
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
      client_j: null,
      player_r_name: PLAYER_R_NAME,
      player_j_name: PLAYER_J_NAME
    };
  }

  $("btnPause").textContent = "Pause";
  render(lastState);
});
