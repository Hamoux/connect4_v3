/**
 * Front-end Puissance 4 — communication avec l'API Flask existante + nouvelles fonctionnalités.
 * Nouvelles features:
 *  - Mode peinture (peindre/effacer des pions librement)
 *  - Prédiction "Rouge gagne dans X tours"
 *  - Bannière dernier coup détaillée (mobile-friendly)
 *  - Bibliothèque d'ouverture passée à l'IA
 *  - Analyse "à qui de jouer" après le painting
 */

const ROWS = 9;
const COLS = 9;
const AI_DELAY_MS = 850;

// --- Identifiant client (session) ---
let CLIENT_ID = sessionStorage.getItem("connect4_client_id");
if (!CLIENT_ID) {
  try { CLIENT_ID = crypto?.randomUUID?.() ?? null; } catch { CLIENT_ID = null; }
  if (!CLIENT_ID) CLIENT_ID = "cid_" + Date.now() + "_" + Math.floor(Math.random() * 1e6);
  sessionStorage.setItem("connect4_client_id", CLIENT_ID);
}

// --- État applicatif ---
let lastState = null;
let GAME_ID = null;
let busy = false;
let aiThinking = false;
let hoverCol = null;
let lastMove = null;
let aiTimer = null;
let pollTimer = null;
let paused = false;
let blockAutoAiUntilHumanAction = false;
let aiRequestSerial = 0;
let suggestBusy = false;
let autoAnalysisEnabled = false;

let PLAYER_R_NAME = localStorage.getItem("playerNameR") || "Joueur rouge";
let PLAYER_J_NAME = localStorage.getItem("playerNameJ") || "Joueur jaune";
let humanColor = localStorage.getItem("humanColor") || "R";

const uiPrefs = { mode: "IA", difficulty: "4", startingPlayer: "R", humanColor: "R", aiMode: "hybrid" };
let committedMode = "IA";
let committedDifficulty = "4";
let committedAiMode = "hybrid";
let suppressSelectChange = false;
let pendingConfirmCallback = null;

const undoStack = [];
const redoStack = [];
let lastBoardSnapshot = null;

let hintColumn = null;
let hintScores = null;
let hintTimer = null;

// ── Mode peinture ─────────────────────────────────────────────────────────────
let paintMode = false;          // true = mode peinture actif
let paintColor = "R";           // couleur du pinceau courant
let paintBoard = null;          // copie du plateau en cours de peinture
let paintStartingPlayer = "R";  // pour déduire à qui de jouer

// ── Prédiction ────────────────────────────────────────────────────────────────
let predictionResult = null;

// --- Utilitaires DOM ---
function $(id) { return document.getElementById(id); }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, m =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])
  );
}

function deepCloneBoard(board) { return board.map(row => row.slice()); }

function snapshotForUndo(state) {
  return {
    board: deepCloneBoard(state.board),
    current_player: state.current_player,
    starting_player: state.starting_player,
    signature: state.signature,
    game_over: state.game_over,
    status: state.status,
    winning_line: state.winning_line ? state.winning_line.map(x => [...x]) : null,
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
  state.winning_line = snap.winning_line ? snap.winning_line.map(x => [...x]) : null;
  state.ai_enabled = !!snap.ai_enabled;
  state.ai_players = { ...(snap.ai_players || { R: false, J: false }) };
  state.ai_depth = snap.ai_depth || 0;
  state.ai_player = snap.ai_player || null;
  state.player_r_name = snap.player_r_name || PLAYER_R_NAME;
  state.player_j_name = snap.player_j_name || PLAYER_J_NAME;
}

function cloneFullState(state) {
  try { return JSON.parse(JSON.stringify(state)); } catch { return snapshotForUndo(state); }
}

function syncUiPrefsFromForm() {
  uiPrefs.mode = ($("modeSelect")?.value || "IA").toUpperCase();
  uiPrefs.difficulty = $("diffSelect")?.value || "4";
  uiPrefs.aiMode = ($("aiModeSelect")?.value || "hybrid").toLowerCase();
  uiPrefs.startingPlayer = ($("colorSelect")?.value || "R").toUpperCase();
  uiPrefs.humanColor = ($("humanColorSelect")?.value || "R").toUpperCase();
  committedMode = uiPrefs.mode;
  committedDifficulty = uiPrefs.difficulty;
  committedAiMode = uiPrefs.aiMode;
}

function hasActiveGame() {
  if (!lastState || lastState.game_over) return false;
  let sig = String(lastState.signature || "");
  if (sig.startsWith("init_")) sig = "";
  if (sig.replace(/[^\d]/g, "").length > 0) return true;
  for (let r = 0; r < ROWS; r++)
    for (let c = 0; c < COLS; c++)
      if (lastState.board?.[r]?.[c] !== 0) return true;
  return false;
}

function parseSignatureToCols(sig) {
  let s = String(sig || "");
  if (s.startsWith("init_")) return [];
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
  if (sig.startsWith("init_")) return null;
  const digits = sig.replace(/[^\d]/g, "");
  if (!digits.length) return null;
  const lastCol = parseInt(digits[digits.length - 1], 10) - 1;
  if (lastCol < 0 || lastCol >= COLS) return null;
  for (let r = ROWS - 1; r >= 0; r--)
    if (state.board?.[r]?.[lastCol] !== 0) return { r, c: lastCol };
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
  if (aiPlayers.R && aiPlayers.J && state.mode !== "LOCAL") {
    aiPlayers.R = false;
    aiPlayers.J = false;
    state.ai_players = aiPlayers;
  }
  return !!aiPlayers[state.current_player];
}

// --- Synchronisation serveur pour undo/redo JvIA ---
async function resyncServerStateFromSnapshot(targetSnap) {
  stopPolling();
  cancelAiTimer();
  clearHint();
  predictionResult = null;

  const mode = uiPrefs.mode;
  const difficulty = uiPrefs.difficulty;
  const starting_player = mode === "ONLINE" ? undefined : (targetSnap.starting_player || "R");

  const payload = {
    mode, difficulty, starting_player,
    human_player: uiPrefs.humanColor,
    client_id: CLIENT_ID,
    player_r_name: targetSnap.player_r_name || PLAYER_R_NAME,
    player_j_name: targetSnap.player_j_name || PLAYER_J_NAME
  };

  const { ok, data: fresh } = await postNewGame(payload);
  if (!ok) { showMessage(fresh.error || "Impossible de synchroniser l'état."); return false; }

  lastState = fresh;
  GAME_ID = fresh.id_partie || null;

  if (lastState.mode === "WEB" && GAME_ID) {
    history.replaceState({}, "", `?game_id=${GAME_ID}`);
  } else {
    history.replaceState({}, "", location.pathname);
  }

  const moves = parseSignatureToCols(targetSnap.signature);

  for (let i = 0; i < moves.length; i++) {
    const col = moves[i];
    const st = await getState(GAME_ID);
    if (!st) { showMessage("Impossible de synchroniser l'état."); return false; }
    lastState = st;
    if (st.game_over) break;
    if (isAiTurn(st)) {
      const r = lastState.mode === "LOCAL" ? await postLocalAiMove(lastState.board, lastState.current_player, Number(lastState.ai_depth || 4)) : await postAiMove();
      if (!r.ok) { showMessage((r.data || {}).error || r.error || "Erreur lors du coup de l'IA."); return false; }
      lastState = r.data || lastState;
    } else {
      const r = await postPlay(col);
      if (!r.ok) { showMessage(r.data.error || "Erreur lors du coup."); return false; }
      lastState = r.data;
    }
    if (lastState.game_over) break;
  }

  const targetAi = targetSnap.ai_players || { R: false, J: false };
  for (const color of ["R", "J"]) {
    const currentAi = !!(lastState.ai_players || {})[color];
    if (currentAi !== !!targetAi[color]) {
      const swap = await postSetAiColor(color, !!targetAi[color]);
      if (!swap.ok) { showMessage(swap.data.error || "Erreur lors de la restauration."); return false; }
      lastState = swap.data;
    }
  }

  lastBoardSnapshot = null;
  lastMove = lastMoveFromSignature(lastState);
  if (lastState.mode === "WEB") startPolling();
  const linkInput = $("shareLink");
  if (linkInput) linkInput.value = lastState.mode === "WEB" ? window.location.href : "";
  render(lastState);
  scheduleAiIfNeeded();
  return true;
}

function showConfirmModal(message, onYes) {
  const overlay = $("confirmOverlay");
  const text = $("confirmModalText");
  if (text) text.textContent = message;
  if (overlay) { overlay.hidden = false; overlay.setAttribute("aria-hidden", "false"); }
  pendingConfirmCallback = onYes;
}

function hideConfirmModal() {
  const overlay = $("confirmOverlay");
  if (overlay) { overlay.hidden = true; overlay.setAttribute("aria-hidden", "true"); }
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
  if (lastState.mode === "LOCAL")
    help.innerHTML = "Annuler / rétablir : un coup à la fois en <strong>joueur contre joueur (local)</strong>.";
  else if (lastState.mode === "WEB" && lastState.type_partie === "IA")
    help.innerHTML = "Annuler / rétablir : retire le dernier <strong>tour complet</strong> (ton coup et celui de l'IA).";
  else
    help.innerHTML = "Annuler / rétablir : non disponible en ligne (deux joueurs).";
}

function syncSelectsFromLoadedState(state) {
  if (!state) return;
  suppressSelectChange = true;
  try {
    if (state.mode === "LOCAL" && state.type_partie === "IA_VS_IA") { if ($("modeSelect")) $("modeSelect").value = "IA_VS_IA"; }
    else if (state.mode === "LOCAL" && state.type_partie === "IA") { if ($("modeSelect")) $("modeSelect").value = "IA"; }
    else if (state.mode === "LOCAL") { if ($("modeSelect")) $("modeSelect").value = "LOCAL"; }
    else { if ($("modeSelect")) $("modeSelect").value = "ONLINE"; }
    if (state.ai_depth != null && $("diffSelect")) $("diffSelect").value = String(state.ai_depth);
    if (state.ai_mode != null && $("aiModeSelect")) $("aiModeSelect").value = String(state.ai_mode).toLowerCase();
    if (state.starting_player && $("colorSelect")) $("colorSelect").value = state.starting_player;
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
  } finally { suppressSelectChange = false; }
}

function clearHint() {
  if (hintTimer) { clearTimeout(hintTimer); hintTimer = null; }
  hintColumn = null;
  hintScores = null;
}

function scheduleHintClear(ms) {
  clearHint();
  hintTimer = setTimeout(() => { hintColumn = null; hintTimer = null; if (lastState) render(lastState); }, ms);
}

function nameFor(letter) {
  if (letter === "R") return lastState?.player_r_name || PLAYER_R_NAME || "Joueur rouge";
  if (letter === "J") return lastState?.player_j_name || PLAYER_J_NAME || "Joueur jaune";
  return "—";
}


function invalidateAiWork() {
  aiRequestSerial += 1;
  cancelAiTimer();
  setThinking(false);
}

function setSuggestBusy(on) {
  suggestBusy = !!on;
  const btn = $("btnHint");
  if (!btn) return;
  btn.disabled = on || busy;
  btn.textContent = on ? "⏳ Suggestion..." : "💡 Suggérer";
}


// --- API ---
async function getState(id) {
  let url = "/api/state";
  if (id) url += `?game_id=${encodeURIComponent(id)}&client_id=${encodeURIComponent(CLIENT_ID)}`;
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) { setMessageOnly(data.error || "Erreur lors de la récupération de l'état."); return null; }
  return data;
}

async function postNewGame(payload) {
  const res = await fetch("/api/new", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postPlay(col) {
  const res = await fetch("/api/play", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ col, game_id: GAME_ID, client_id: CLIENT_ID }) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postAiMove() {
  const res = await fetch("/api/ai_move", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ game_id: GAME_ID, client_id: CLIENT_ID }) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postHint() {
  const depth = Number($("diffSelect")?.value) || Number(lastState?.ai_depth) || 4;
  const res = await fetch("/api/hint", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ game_id: GAME_ID, client_id: CLIENT_ID, ai_depth: depth, ai_mode: uiPrefs.aiMode }) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postSetAiColor(color, enabled) {
  const res = await fetch("/api/set_ai_color", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_id: GAME_ID, client_id: CLIENT_ID, color, enabled, player_r_name: localStorage.getItem("playerNameR") || PLAYER_R_NAME, player_j_name: localStorage.getItem("playerNameJ") || PLAYER_J_NAME })
  });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postSetAiPrefs(depth, ai_mode) {
  const res = await fetch("/api/set_ai_prefs", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_id: GAME_ID, client_id: CLIENT_ID, ai_depth: depth, ai_mode })
  });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postLocalAiMove(board, player, depth) {
  const sig = lastState?.signature || "";
  // N'utiliser la bibliothèque d'ouverture QUE si la partie a commencé normalement
  // (signature contient des chiffres = coups réels joués)
  // Après mode peinture : signature = "init" → pas de moves_history
  // → l'IA évalue la position réelle avec minimax pur, sans biais d'ouverture
  const hasRealMoves = sig !== "init" && /\d/.test(sig);
  const moves_history = hasRealMoves ? parseSignatureToCols(sig) : null;
  const res = await fetch("/api/local_ai_move", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ board, player, depth, moves_history, ai_mode: uiPrefs.aiMode }) });
  const data = await res.json();
  if (!res.ok) return { ok: false, error: data.error || "Erreur IA locale" };
  return { ok: true, col: data.col };
}

async function postPredict(board, currentPlayer, depth) {
  const res = await fetch("/api/predict", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ board, current_player: currentPlayer, depth: depth || 6, ai_mode: uiPrefs.aiMode }) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postSimulate(board, currentPlayer, depth) {
  const res = await fetch("/api/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ board, current_player: currentPlayer, depth }) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postPaintAnalyze(board, startingPlayer) {
  const res = await fetch("/api/paint", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ board, starting_player: startingPlayer }) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postPaintHint(board, currentPlayer, depth) {
  const res = await fetch("/api/paint_hint", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ board, current_player: currentPlayer, depth, ai_mode: uiPrefs.aiMode }) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function fetchDbGames(limit = 100) {
  const res = await fetch(`/api/db_games?limit=${encodeURIComponent(limit)}`);
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postLoadGame(gameId) {
  const res = await fetch("/api/load_game", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ game_id: gameId }) });
  const data = await res.json();
  return { ok: res.ok, data };
}


async function postRestoreState(snapshot) {
  const res = await fetch("/api/restore_state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_id: GAME_ID || lastState?.id_partie, snapshot })
  });
  const data = await res.json();
  return { ok: res.ok, data };
}


async function fetchModelStatus() {
  const res = await fetch("/api/model_status");
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postPaintCommit(board, startingPlayer) {
  const res = await fetch("/api/paint", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ game_id: GAME_ID || lastState?.id_partie || null, board, starting_player: startingPlayer }) });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function postImportSignature(signature, startingPlayer) {
  const res = await fetch("/api/import_signature", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ signature, starting_player: startingPlayer }) });
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
  if (msg) { msg.hidden = true; msg.textContent = ""; }
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

function showMessage(txt) { showHistoryLine(txt); setMessageOnly(txt); }

// --- Plateau local ---
function isColumnFull(col) { return lastState?.board?.[0]?.[col] !== 0; }

function findLastMove(prevBoard, newBoard) {
  if (!prevBoard || !newBoard) return null;
  for (let r = 0; r < ROWS; r++)
    for (let c = 0; c < COLS; c++)
      if (prevBoard?.[r]?.[c] === 0 && (newBoard?.[r]?.[c] === "R" || newBoard?.[r]?.[c] === "J"))
        return { r, c };
  return null;
}

function jsFindWinningLine(r, c, board) {
  const dirs = [[0, 1], [1, 0], [1, 1], [1, -1]];
  const player = board[r][c];
  for (const [dr, dc] of dirs) {
    let coords = [];
    for (let i = -3; i < 4; i++) {
      const nr = r + dr * i, nc = c + dc * i;
      if (nr >= 0 && nr < ROWS && nc >= 0 && nc < COLS && board[nr][nc] === player) {
        coords.push([nr, nc]);
        if (coords.length === 4) return coords;
      } else { coords = []; }
    }
  }
  return null;
}

// --- En-tête ---
function updateHeaderStatus(state) {
  const dot = $("statusDot");
  const text = $("headerStatusText");
  if (!text) return;
  if (!state) { text.textContent = "Aucune donnée"; dot?.classList.remove("header-status__dot--play", "header-status__dot--wait", "header-status__dot--done"); return; }
  if (paintMode) { text.textContent = "Mode peinture"; dot?.classList.remove("header-status__dot--play", "header-status__dot--done"); dot?.classList.add("header-status__dot--wait"); return; }
  if (state.game_over) { text.textContent = "Terminée"; dot?.classList.remove("header-status__dot--play", "header-status__dot--wait"); dot?.classList.add("header-status__dot--done"); return; }
  if (paused) { text.textContent = "En pause"; dot?.classList.remove("header-status__dot--play", "header-status__dot--done"); dot?.classList.add("header-status__dot--wait"); return; }
  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) { text.textContent = "En attente d'un adversaire"; dot?.classList.remove("header-status__dot--play", "header-status__dot--done"); dot?.classList.add("header-status__dot--wait"); return; }
  text.textContent = "En cours";
  dot?.classList.remove("header-status__dot--wait", "header-status__dot--done");
  dot?.classList.add("header-status__dot--play");
}

// --- Polling / IA ---
function cancelAiTimer() { if (aiTimer) { clearTimeout(aiTimer); aiTimer = null; } }
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
function setThinking(on) { const el = $("aiThinking"); if (!el) return; el.hidden = !on; aiThinking = !!on; }

function scheduleAiIfNeeded() {
  if (!lastState || lastState.game_over || paused || busy || aiThinking || paintMode) return;
  if (blockAutoAiUntilHumanAction) return;
  if (isAiTurn(lastState)) { cancelAiTimer(); aiTimer = setTimeout(aiMove, AI_DELAY_MS); }
}

function startPolling() {
  if (pollTimer) return;
  const tick = async () => {
    if (!lastState || lastState.game_over || lastState.mode !== "WEB" || !GAME_ID) { stopPolling(); return; }
    const data = await getState(GAME_ID);
    if (!data) return;
    const changed = data.signature !== lastState.signature || data.current_player !== lastState.current_player || data.game_over !== lastState.game_over || data.player_count !== lastState.player_count || data.client_r !== lastState.client_r || data.client_j !== lastState.client_j || JSON.stringify(data.ai_players || {}) !== JSON.stringify(lastState.ai_players || {}) || data.player_r_name !== lastState.player_r_name || data.player_j_name !== lastState.player_j_name;
    if (changed) { lastMove = findLastMove(lastState.board, data.board); lastState = data; render(lastState); scheduleAiIfNeeded(); }
  };
  tick();
  pollTimer = setInterval(tick, 800);
}

// --- Pause ---
function resetPauseUiOnly() {
  paused = false;
  const overlay = $("pauseOverlay");
  if (overlay) { overlay.hidden = true; overlay.setAttribute("aria-hidden", "true"); }
  $("board")?.classList.remove("is-paused");
  const bp = $("btnPause");
  if (bp) bp.textContent = "Pause";
}

function setPaused(value) {
  paused = !!value;
  if (paused) invalidateAiWork();
  const overlay = $("pauseOverlay");
  const board = $("board");
  if (overlay) { overlay.hidden = !paused; overlay.setAttribute("aria-hidden", paused ? "false" : "true"); }
  if (board) board.classList.toggle("is-paused", paused);
  updateHeaderStatus(lastState);
  if (lastState) render(lastState);
  if (!paused) scheduleAiIfNeeded();
}

// --- Undo / redo ---
function canUseUndoRedo() {
  if (!lastState || paused || paintMode) return false;
  if (lastState.mode === "LOCAL") return true;
  if (lastState.mode === "WEB" && lastState.type_partie === "IA") return true;
  return false;
}

function updateUndoRedoButtons() {
  const u = $("btnUndo"), r = $("btnRedo");
  const allowed = canUseUndoRedo() && !busy;
  if (u) u.disabled = !allowed || undoStack.length === 0;
  if (r) r.disabled = !allowed || redoStack.length === 0;
}

async function undo() {
  if (!canUseUndoRedo() || undoStack.length === 0 || !lastState) return;
  if (lastState.mode === "LOCAL") {
    const current = snapshotForUndo(lastState);
    const prev = undoStack.pop();
    redoStack.push(current);
    applySnapshot(lastState, prev);
    blockAutoAiUntilHumanAction = true;
    invalidateAiWork();
    lastMove = null; clearHint(); hideMessageBox();
    render(lastState);
    if (GAME_ID) {
      const res = await postRestoreState(prev);
      if (res.ok) { lastState = res.data; render(lastState); }
      else { showMessage("Erreur de synchronisation serveur."); }
    }
    showHistoryLine("Coup annulé.", "log-item--system");
    if (!lastState.game_over) void runPrediction();
    return;
  }
  if (lastState.mode === "WEB" && lastState.type_partie === "IA") {
    busy = true;
    const prev = undoStack.pop();
    redoStack.push(cloneFullState(lastState));
    const ok = await resyncServerStateFromSnapshot(prev);
    busy = false;
    if (!ok) { undoStack.push(prev); redoStack.pop(); updateUndoRedoButtons(); return; }
    clearHint(); hideMessageBox(); showHistoryLine("Coup annulé.", "log-item--system");
  }
}

async function redo() {
  if (!canUseUndoRedo() || redoStack.length === 0 || !lastState) return;
  if (lastState.mode === "LOCAL") {
    const current = snapshotForUndo(lastState);
    const next = redoStack.pop();
    undoStack.push(current);
    applySnapshot(lastState, next);
    blockAutoAiUntilHumanAction = true;
    invalidateAiWork();
    lastMove = null; clearHint();
    render(lastState);
    if (GAME_ID) {
      const res = await postRestoreState(next);
      if (res.ok) { lastState = res.data; render(lastState); }
      else { showMessage("Erreur de synchronisation serveur."); }
    }
    showHistoryLine("Coup rétabli.", "log-item--system");
    if (!lastState.game_over) void runPrediction();
    return;
  }
  if (lastState.mode === "WEB" && lastState.type_partie === "IA") {
    busy = true;
    const next = redoStack.pop();
    undoStack.push(cloneFullState(lastState));
    const ok = await resyncServerStateFromSnapshot(next);
    busy = false;
    if (!ok) { redoStack.push(next); undoStack.pop(); updateUndoRedoButtons(); return; }
    clearHint(); showHistoryLine("Coup rétabli.", "log-item--system");
  }
}

// --- Nouvelle partie ---
async function newGame() {
  busy = false; hideMessageBox(); lastMove = null; lastBoardSnapshot = null;
  invalidateAiWork(); setSuggestBusy(false); clearHint(); stopPolling();
  undoStack.length = 0; redoStack.length = 0;
  predictionResult = null;

  if (paintMode) exitPaintMode(false);

  const mode = ($("modeSelect")?.value || "IA").toUpperCase();
  const difficulty = $("diffSelect")?.value || "4";
  const starting_player = mode === "ONLINE" ? undefined : (($("colorSelect")?.value || "R").toUpperCase());
  const human_player = mode === "IA" ? (($("humanColorSelect")?.value || humanColor || "R").toUpperCase()) : undefined;
  blockAutoAiUntilHumanAction = false;

  GAME_ID = null;
  history.replaceState({}, "", location.pathname);

  const payload = { mode, difficulty, ai_mode: uiPrefs.aiMode, starting_player, human_player, client_id: CLIENT_ID, player_r_name: PLAYER_R_NAME, player_j_name: PLAYER_J_NAME };
  const { ok, data: state } = await postNewGame(payload);
  if (!ok) { setMessageOnly(state.error || "Erreur lors de la création de la partie."); return; }

  lastState = state;
  if (state.id_partie) GAME_ID = state.id_partie;

  const linkInput = $("shareLink");
  if (state.mode === "WEB" && GAME_ID) {
    history.replaceState({}, "", `?game_id=${GAME_ID}`);
    if (linkInput) linkInput.value = window.location.href;
    startPolling();
  } else {
    history.replaceState({}, "", location.pathname);
    if (linkInput) linkInput.value = "";
    stopPolling();
  }

  resetPauseUiOnly();
  syncUiPrefsFromForm();
  render(lastState);
  scheduleAiIfNeeded();
  refreshDbGames().catch(() => {});
  refreshModelStatus().catch(() => {});
  if (!lastState.game_over) void runPrediction();
}

// --- Jouer ---
async function play(col) {
  if (paintMode || paused || busy || !lastState) return;

  if (!GAME_ID && !lastState.id_partie) { showMessage("Clique d'abord sur « Nouvelle partie »."); return; }

  if (lastState.mode === "WEB" && lastState.type_partie === "HUMAIN" && lastState.player_count < 2) {
    setMessageOnly("En attente d'un adversaire… Partage le lien de la partie."); return;
  }

  if (isAiTurn(lastState)) return;
  if (lastState.game_over) return;
  if (isColumnFull(col)) return;

  if (lastState.mode === "LOCAL") {
    undoStack.push(snapshotForUndo(lastState));
    redoStack.length = 0;
  } else if (lastState.mode === "WEB" && lastState.type_partie === "IA") {
    undoStack.push(cloneFullState(lastState));
    redoStack.length = 0;
  }

  cancelAiTimer(); busy = true; clearHint();

  const playingPlayer = lastState.current_player;
  let placedRow = null;
  for (let r = ROWS - 1; r >= 0; r--) {
    if (lastState.board[r][col] === 0) { placedRow = r; lastState.board[r][col] = playingPlayer; break; }
  }
  if (placedRow !== null) {
    lastMove = { r: placedRow, c: col };
    lastState.current_player = playingPlayer === "R" ? "J" : "R";
    render(lastState);
    lastState.current_player = playingPlayer;
    lastState.board[placedRow][col] = 0;
  }

  let res;
  try { res = await postPlay(col); } catch {
    busy = false;
    showMessage("Erreur réseau."); return;
  }

  busy = false;
  if (!res.ok) {
    if (GAME_ID) {
      const recovered = await getState(GAME_ID);
      if (recovered) { lastState = recovered; render(lastState); }
    }
    showMessage(res.data.error || "Erreur lors du coup."); return;
  }

  const data = res.data;
  lastMove = findLastMove(lastState.board, data.board) || lastMove;
  lastState = data;
  GAME_ID = data.id_partie || GAME_ID;
  render(lastState);

  if (lastState.game_over) {
    setMessageOnly(`Victoire de <span class="name-${lastState.current_player === "R" ? "red" : "yellow"}">${escapeHtml(nameFor(lastState.current_player))}</span> !`);
    return;
  }
  blockAutoAiUntilHumanAction = false;
  scheduleAiIfNeeded();
  if (!lastState.game_over) void runPrediction();
}

async function aiMove() {
  aiTimer = null;
  if (!lastState || lastState.game_over || paused || paintMode) return;
  if (!isAiTurn(lastState)) return;
  if (!GAME_ID && !lastState.id_partie) { showMessage("Clique d'abord sur « Nouvelle partie »."); return; }

  const requestSerial = ++aiRequestSerial;

  if (lastState.mode === "LOCAL") {
    undoStack.push(snapshotForUndo(lastState));
    redoStack.length = 0;
  } else if (lastState.mode === "WEB" && lastState.type_partie === "IA") {
    undoStack.push(cloneFullState(lastState));
    redoStack.length = 0;
  }

  setThinking(true);
  const t0 = performance.now();

  let res;
  if (lastState.mode === "LOCAL") {
    try {
      const response = await fetch("/api/local_ai_move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_id: GAME_ID || lastState.id_partie, depth: Number(lastState.ai_depth || 4) })
      });
      const data = await response.json();
      res = { ok: response.ok, data };
    } catch {
      setThinking(false); showMessage("Erreur locale de l'IA."); return;
    }
  } else {
    try { res = await postAiMove(); } catch { setThinking(false); showMessage("Erreur réseau."); return; }
  }

  const data = res.data;
  if (requestSerial !== aiRequestSerial) { return; }
  if (!res.ok) { setThinking(false); showMessage((data || {}).error || "Erreur de l'IA."); return; }

  const dt = Math.round(performance.now() - t0);
  lastMove = findLastMove(lastState.board, data.board) || lastMoveFromSignature(data);
  lastState = data;
  GAME_ID = data.id_partie || GAME_ID;
  render(lastState);
  setThinking(false);
  showHistoryLine(`L'IA a joué (${dt} ms).`, "log-item--system");

  if (lastState.game_over) {
    setMessageOnly(`Victoire de <span class="name-${lastState.current_player === "R" ? "red" : "yellow"}">${escapeHtml(nameFor(lastState.current_player))}</span> !`);
    return;
  }
  blockAutoAiUntilHumanAction = false;
  scheduleAiIfNeeded();
  if (!lastState.game_over) void runPrediction();
}

// ─────────────────────────────────────────────────────────────────────────────
// MODE PEINTURE
// ─────────────────────────────────────────────────────────────────────────────

function enterPaintMode() {
  paintMode = true;
  cancelAiTimer();
  stopPolling();
  // Ne pas hériter du starting_player de l'ancienne partie
  // On laisse à "R" par défaut, mais ce sera recalculé à la validation
  paintStartingPlayer = "R";  // sera écrasé par l'inférence au moment du Valider
  paintBoard = lastState
    ? deepCloneBoard(lastState.board)
    : Array.from({ length: ROWS }, () => Array(COLS).fill(0));
  paintColor = "R";
  predictionResult = null;
  updatePaintUI();
  render(lastState || makeDummyState());
  showHistoryLine("Mode peinture activé. Clique sur les cellules pour placer/effacer des pions.", "log-item--system");
  updateHeaderStatus(lastState);
}

async function exitPaintMode(apply = true) {
  if (!paintMode) return;
  paintMode = false;
  // Mettre à jour l'UI immédiatement — le bouton Valider disparaît tout de suite
  updatePaintUI();

  if (apply && paintBoard) {
    const nb_r = paintBoard.flat().filter(x => x === "R").length;
    const nb_j = paintBoard.flat().filter(x => x === "J").length;
    const diff = Math.abs(nb_r - nb_j);

    if (diff > 1) {
      paintMode = true;
      showMessage(`Position invalide : ${nb_r} rouges, ${nb_j} jaunes (différence ${diff}). Corrige avant de valider.`);
      updatePaintUI();
      return;
    }

    const startingPlayer = ($("colorSelect")?.value || "R").toUpperCase();
    const res = await postPaintCommit(deepCloneBoard(paintBoard), startingPlayer);
    if (!res.ok) {
      paintMode = true;
      showMessage(res.data?.error || "Impossible de valider le mode peinture.");
      updatePaintUI();
      return;
    }

    lastState = res.data;
    GAME_ID = lastState.id_partie || GAME_ID;
    lastMove = null;
    lastBoardSnapshot = null;
    clearHint();
    // Après validation, l'état peint devient la nouvelle base.
    undoStack.length = 0;
    redoStack.length = 0;
    predictionResult = null;
    history.replaceState({}, "", location.pathname);
    stopPolling();
    render(lastState);
    if (lastState.game_over) {
      const winner = lastState.current_player;
      setMessageOnly(`Position peinte : victoire de <span class="name-${winner === "R" ? "red" : "yellow"}">${escapeHtml(nameFor(winner))}</span>.`);
      showHistoryLine(`Mode peinture validé — victoire de ${winner === "R" ? "Rouge" : "Jaune"} détectée.`, "log-item--system");
    } else {
      showHistoryLine(`Mode peinture validé. À ${lastState.current_player === "R" ? "Rouge 🔴" : "Jaune 🟡"} de jouer.`, "log-item--system");
      scheduleAiIfNeeded();
    }
  } else {
    // Annulation : on restore l'affichage sans modifier lastState
    if (lastState) render(lastState);
  }

  updateHeaderStatus(lastState);
}

/** Détecte s'il y a un gagnant sur un plateau donné (sans modifier l'état). */
function jsWinnerOnBoard(board) {
  const dirs = [[0,1],[1,0],[1,1],[1,-1]];
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const p = board[r][c];
      if (!p || p === 0) continue;
      for (const [dr, dc] of dirs) {
        let cnt = 1;
        let rr = r + dr, cc = c + dc;
        while (rr >= 0 && rr < ROWS && cc >= 0 && cc < COLS && board[rr][cc] === p) {
          cnt++; if (cnt >= 4) return p;
          rr += dr; cc += dc;
        }
      }
    }
  }
  return null;
}

/** Trouve les 4 cases de la ligne gagnante pour le joueur donné. */
function jsFindFirstWinLine(board, player) {
  const dirs = [[0,1],[1,0],[1,1],[1,-1]];
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      if (board[r][c] !== player) continue;
      for (const [dr, dc] of dirs) {
        const cells = [];
        for (let i = 0; i < 4; i++) {
          const rr = r + dr*i, cc = c + dc*i;
          if (rr < 0 || rr >= ROWS || cc < 0 || cc >= COLS || board[rr][cc] !== player) break;
          cells.push([rr, cc]);
        }
        if (cells.length === 4) return cells;
      }
    }
  }
  return null;
}

function makeDummyState() {
  return {
    id_partie: null, mode: "LOCAL", type_partie: "HUMAIN", status: "EN_COURS",
    ai_enabled: false, ai_depth: 4, ai_player: null, ai_players: { R: false, J: false },
    board: Array.from({ length: ROWS }, () => Array(COLS).fill(0)),
    current_player: "R", starting_player: "R", signature: "init",
    game_over: false, winning_line: null, player_count: 1,
    client_r: null, client_j: null, player_r_name: PLAYER_R_NAME, player_j_name: PLAYER_J_NAME
  };
}

function updatePaintUI() {
  const paintPanel = $("paintPanel");
  const btnEnterPaint = $("btnEnterPaint");
  const btnExitPaint = $("btnExitPaint");

  if (paintPanel) paintPanel.hidden = !paintMode;
  if (btnEnterPaint) btnEnterPaint.hidden = paintMode;
  if (btnExitPaint) btnExitPaint.hidden = !paintMode;

  // Mettre à jour le bouton de couleur sélectionné
  const btnPaintR = $("btnPaintRed");
  const btnPaintJ = $("btnPaintYellow");
  const btnPaintErase = $("btnPaintErase");

  if (btnPaintR) btnPaintR.classList.toggle("btn-active", paintMode && paintColor === "R");
  if (btnPaintJ) btnPaintJ.classList.toggle("btn-active", paintMode && paintColor === "J");
  if (btnPaintErase) btnPaintErase.classList.toggle("btn-active", paintMode && paintColor === "0");
}

function paintCell(r, c) {
  if (!paintMode || !paintBoard) return;
  const current = paintBoard[r][c];

  if (paintColor === "0") {
    paintBoard[r][c] = 0;
  } else {
    // Toggle: si même couleur, effacer; sinon, peindre
    paintBoard[r][c] = current === paintColor ? 0 : paintColor;
  }

  updatePaintCounters();
  renderPaintBoard();
}

function updatePaintCounters() {
  if (!paintBoard) return;
  const nb_r = paintBoard.flat().filter(x => x === "R").length;
  const nb_j = paintBoard.flat().filter(x => x === "J").length;
  const diff = Math.abs(nb_r - nb_j);

  const counterEl = $("paintCounters");
  if (counterEl) {
    counterEl.innerHTML = `<span class="paint-counter paint-counter--red">🔴 ${nb_r}</span> <span class="paint-counter paint-counter--yellow">🟡 ${nb_j}</span>`;
    if (diff > 1) counterEl.innerHTML += ` <span class="paint-counter paint-counter--error">⚠ Différence trop grande (${diff})</span>`;
  }

  // Déduire à qui de jouer depuis les pions (même logique que exitPaintMode)
  const inferEl = $("paintInferPlayer");
  if (inferEl) {
    if (diff > 1) {
      inferEl.textContent = "Position invalide";
      inferEl.style.color = "var(--red)";
    } else {
      let cp, sp;
      if (nb_r > nb_j)       { sp = "R"; cp = "J"; }
      else if (nb_j > nb_r)  { sp = "J"; cp = "R"; }
      else                   { sp = ($("colorSelect")?.value || "R").toUpperCase(); cp = sp; }
      inferEl.textContent = `À ${cp === "R" ? "Rouge 🔴" : "Jaune 🟡"} de jouer (${sp === "R" ? "Rouge" : "Jaune"} a commencé)`;
      inferEl.style.color = cp === "R" ? "var(--red)" : "var(--yellow)";
    }
  }
}

function renderPaintBoard() {
  // Re-render le plateau avec paintBoard
  const boardDiv = $("board");
  if (!boardDiv || !paintBoard) return;
  boardDiv.innerHTML = "";

  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = document.createElement("div");
      cell.className = "cell cell--paint";
      cell.style.cursor = "crosshair";

      cell.addEventListener("click", () => paintCell(r, c));
      cell.addEventListener("touchstart", e => { e.preventDefault(); paintCell(r, c); }, { passive: false });

      const piece = document.createElement("div");
      piece.className = "piece";
      const v = paintBoard[r][c];
      if (v === "R" || v === "J") {
        piece.classList.add(v === "R" ? "piece--red" : "piece--yellow");
        piece.classList.add("piece--visible");
      }
      cell.appendChild(piece);
      boardDiv.appendChild(cell);
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// PRÉDICTION
// ─────────────────────────────────────────────────────────────────────────────

async function runPrediction(manual = false) {
  if (!manual && !autoAnalysisEnabled) return;
  const btnPredict = $("btnPredict");
  if (btnPredict) { btnPredict.disabled = true; btnPredict.textContent = "Analyse…"; }

  let board, currentPlayer;

  if (paintMode && paintBoard) {
    const nb_r = paintBoard.flat().filter(x => x === "R").length;
    const nb_j = paintBoard.flat().filter(x => x === "J").length;
    if (Math.abs(nb_r - nb_j) > 1) {
      showMessage("Position peinte invalide pour l'analyse.");
      if (btnPredict) { btnPredict.disabled = false; btnPredict.textContent = "🔮 Analyser"; }
      return;
    }
    board = deepCloneBoard(paintBoard);
    let cp2;
    if (nb_r > nb_j)       cp2 = "J";
    else if (nb_j > nb_r)  cp2 = "R";
    else                   cp2 = ($("colorSelect")?.value || "R").toUpperCase();
    currentPlayer = cp2;
  } else if (lastState && !lastState.game_over) {
    board = deepCloneBoard(lastState.board);
    currentPlayer = lastState.current_player;
  } else {
    if (btnPredict) { btnPredict.disabled = false; btnPredict.textContent = "🔮 Analyser"; }
    showMessage("Aucune partie en cours à analyser.");
    return;
  }

  const depth = parseInt($("diffSelect")?.value, 10) || Number(lastState?.ai_depth) || 4;
  const res = await postPredict(board, currentPlayer, depth);

  if (btnPredict) { btnPredict.disabled = false; btnPredict.textContent = "🔮 Analyser"; }

  if (!res.ok) { showMessage(res.data?.error || "Erreur lors de l'analyse."); return; }

  predictionResult = res.data;
  renderPrediction();
  showHistoryLine(`Analyse : ${res.data.message}`, "log-item--system");
}

function renderPrediction() {
  const el = $("predictionBox");
  if (!el) return;

  if (!predictionResult) { el.hidden = true; return; }

  el.hidden = false;
  const { winner, moves, certain, message } = predictionResult;

  let cls = "";
  if (winner === "R") cls = "prediction--red";
  else if (winner === "J") cls = "prediction--yellow";
  else cls = "prediction--neutral";

  el.className = `prediction-box ${cls}`;

  let icon = "🎯";
  if (winner === "R") icon = "🔴";
  else if (winner === "J") icon = "🟡";
  else if (winner === "draw") icon = "🤝";

  el.innerHTML = `<span class="prediction-icon">${icon}</span> <strong>${escapeHtml(message)}</strong>${certain ? "" : " <em>(estimation)</em>"}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// RENDU
// ─────────────────────────────────────────────────────────────────────────────

function setModePill(state) {
  const pill = $("turnPill");
  if (!pill) return;
  pill.innerHTML = "";
  let modeTxt = "";
  const dot = document.createElement("span");
  dot.className = "pill__dot";

  if (paintMode) { modeTxt = "Mode peinture"; dot.style.background = "#f59e0b"; }
  else if (state.mode === "LOCAL") {
    const aiPlayers = state.ai_players || { R: false, J: false };
    if (aiPlayers.R || aiPlayers.J) { modeTxt = "Local (avec IA)"; dot.style.background = "#a78bfa"; }
    else modeTxt = "Joueur contre joueur (local)";
  } else if (state.type_partie === "IA") { modeTxt = "Joueur contre IA"; dot.style.background = "#4ade80"; }
  else if (state.mode === "WEB" && state.type_partie === "HUMAIN" && (state.ai_players?.R || state.ai_players?.J)) { modeTxt = "En ligne (+ IA remplaçante)"; dot.style.background = "#a78bfa"; }
  else { modeTxt = "Joueur contre joueur (en ligne)"; dot.style.background = "#38bdf8"; }

  const label = document.createElement("span");
  label.textContent = modeTxt;
  pill.appendChild(dot);
  pill.appendChild(label);
}

function renderRole(state) {
  const roleDiv = $("yourRole");
  if (!roleDiv) return;
  const aiPlayers = state.ai_players || { R: false, J: false };

  if (paintMode) { roleDiv.textContent = "Mode peinture : place les pions librement."; return; }

  if (state.mode === "WEB" && state.type_partie === "HUMAIN") {
    const myColor = myOnlineColor(state);
    if (!myColor) { roleDiv.textContent = state.player_count >= 2 ? "Spectateur" : "En attente d'un adversaire…"; return; }
    const colorText = myColor === "R" ? "rouges" : "jaunes";
    roleDiv.textContent = aiPlayers[myColor] ? `Tu es ${colorText} — l'IA joue actuellement à ta place.` : `Tu joues les ${colorText}.`;
    return;
  }

  if (state.mode === "LOCAL") {
    const redTxt = aiPlayers.R ? "rouge = IA" : "rouge = humain";
    const yellowTxt = aiPlayers.J ? "jaune = IA" : "jaune = humain";
    roleDiv.textContent = `Partie locale — ${redTxt}, ${yellowTxt}.`; return;
  }

  if (state.type_partie === "IA") {
    const aiColor = state.ai_player;
    const human = aiColor === "R" ? "jaune" : "rouge";
    roleDiv.textContent = `Tu affrontes l'IA. Tu joues ${human}.`; return;
  }

  roleDiv.textContent = "—";
}

function renderStatusText(state) {
  const statusTxt = $("statusTxt");
  if (!statusTxt) return;
  let st = state.status || (state.game_over ? "TERMINEE" : "EN_COURS");
  if (paintMode) st = "MODE PEINTURE";
  else if (paused) st = "EN PAUSE";
  else if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) st = "EN ATTENTE D'UN ADVERSAIRE";
  statusTxt.textContent = st;
}

function updateTurnInfo(state) {
  const el = $("turnInfo");
  if (!el || !state) return;

  if (paintMode) {
    el.innerHTML = `Mode peinture — pinceau : <b>${paintColor === "R" ? "Rouge 🔴" : paintColor === "J" ? "Jaune 🟡" : "Gomme"}</b>`;
    return;
  }

  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    el.innerHTML = "Partie créée — en attente d'un second joueur."; return;
  }

  const turn = state.current_player;
  const turnName = nameFor(turn);
  const turnClass = turn === "R" ? "name-red" : "name-yellow";

  if (!lastMove) {
    el.innerHTML = `Début de partie — à <b class="${turnClass}">${escapeHtml(turnName)}</b> de jouer.`; return;
  }

  const lastPlayer = turn === "R" ? "J" : "R";
  const lastName = nameFor(lastPlayer);
  const lastClass = lastPlayer === "R" ? "name-red" : "name-yellow";
  const colHuman = lastMove.c + 1;

  // Bannière claire et mobile-friendly
  el.innerHTML =
    `Dernier coup : <b class="${lastClass}">${escapeHtml(lastName)}</b> → colonne <b>${colHuman}</b>` +
    ` — à <b class="${turnClass}">${escapeHtml(turnName)}</b> de jouer.`;
}

function renderMessage(state) {
  const msg = $("message");
  if (!msg) return;

  if (paintMode) { msg.hidden = true; return; }

  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    msg.hidden = false;
    let txt = "En attente d'un adversaire… ";
    if (state.client_r === CLIENT_ID) txt += "Tu joues les rouges. ";
    if (state.client_j === CLIENT_ID) txt += "Tu joues les jaunes. ";
    txt += "Partage le lien.";
    msg.innerHTML = txt; return;
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
  return paintMode || paused || busy || !state || state.game_over || isAiTurn(state) ||
    (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2);
}

function renderColHeader(state) {
  const header = $("colHeader");
  if (!header) return;
  header.innerHTML = "";
  if (paintMode) return;

  const locked = interactionLocked(state);
  for (let c = 0; c < COLS; c++) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "col-btn";
    const score = hintScores && Object.prototype.hasOwnProperty.call(hintScores, String(c))
      ? hintScores[String(c)]
      : null;
    btn.textContent = score === null || score === undefined ? String(c + 1) : `${c + 1} (${score})`;
    const full = state.board?.[0]?.[c] !== 0;
    const disabled = locked || full;
    if (disabled) btn.classList.add("col-btn--blocked");
    btn.disabled = disabled;
    if (hintColumn === c) btn.classList.add("col-btn--hint");

    btn.addEventListener("mouseenter", () => { if (interactionLocked(lastState)) return; hoverCol = c; applyPreview(); highlightColButton(c); });
    btn.addEventListener("mouseleave", () => { hoverCol = null; applyPreview(); highlightColButton(null); });
    btn.addEventListener("click", () => play(c));
    header.appendChild(btn);
  }
}

function highlightColButton(col) {
  const header = $("colHeader");
  if (!header) return;
  header.querySelectorAll(".col-btn").forEach((b, i) => b.classList.toggle("col-btn--hover", col !== null && i === col));
}

function renderBoard(state) {
  if (paintMode) { renderPaintBoard(); return; }

  const boardDiv = $("board");
  if (!boardDiv) return;
  boardDiv.innerHTML = "";
  const prev = lastBoardSnapshot;

  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = document.createElement("div");
      cell.className = "cell";
      if (interactionLocked(state)) cell.classList.add("cell--disabled");

      cell.addEventListener("mouseenter", () => { if (interactionLocked(lastState)) return; hoverCol = c; applyPreview(); highlightColButton(c); });
      cell.addEventListener("mouseleave", () => { hoverCol = null; applyPreview(); highlightColButton(null); });
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
  if (!boardDiv || !lastState || paintMode) return;
  const cells = boardDiv.querySelectorAll(".cell");
  cells.forEach(cell => cell.classList.remove("cell--preview", "cell--preview-red", "cell--preview-yellow"));
  if (hoverCol == null || lastState.game_over || interactionLocked(lastState)) return;
  let targetRow = null;
  for (let r = ROWS - 1; r >= 0; r--) { if (lastState.board?.[r]?.[hoverCol] === 0) { targetRow = r; break; } }
  if (targetRow == null) return;
  const idx = targetRow * COLS + hoverCol;
  const cell = cells[idx];
  if (!cell) return;
  cell.classList.add("cell--preview");
  cell.classList.add(lastState.current_player === "R" ? "cell--preview-red" : "cell--preview-yellow");
}

function applyLastMove() {
  const boardDiv = $("board");
  if (!boardDiv || paintMode) return;
  const cells = boardDiv.querySelectorAll(".cell");
  cells.forEach(c => c.classList.remove("cell--last"));
  if (!lastMove) return;
  const idx = lastMove.r * COLS + lastMove.c;
  cells[idx]?.classList.add("cell--last");
}

function applyWinningLine(state) {
  const boardDiv = $("board");
  if (!boardDiv || paintMode) return;
  const cells = boardDiv.querySelectorAll(".cell");
  cells.forEach(c => c.classList.remove("cell--win"));
  const line = state.winning_line;
  if (!line || !Array.isArray(line)) return;
  for (const [r, c] of line) cells[r * COLS + c]?.classList.add("cell--win");
}

function renderHistory(state) {
  const historyDiv = $("history");
  if (!historyDiv) return;
  historyDiv.innerHTML = "";

  let sig = String(state.signature || "");
  const moves = sig.startsWith("init_") ? "" : sig.replace(/[^\d]/g, "");

  if (!moves.length) {
    const empty = document.createElement("div");
    empty.className = "log-item log-item--system";
    empty.textContent = "Aucun coup pour l'instant.";
    historyDiv.appendChild(empty);
    return;
  }

  const starting = (state.starting_player || "R").toUpperCase();
  for (let i = 0; i < moves.length; i++) {
    const col = Number(moves[i]);
    const moveLetter = i % 2 === 0 ? starting : (starting === "R" ? "J" : "R");
    const isRed = moveLetter === "R";
    const name = moveLetter === "R"
      ? (state.player_r_name || PLAYER_R_NAME || "Joueur rouge")
      : (state.player_j_name || PLAYER_J_NAME || "Joueur jaune");

    const item = document.createElement("div");
    item.className = "log-item " + (isRed ? "log-item--red" : "log-item--yellow");
    if (i === moves.length - 1) item.classList.add("log-item--last");
    item.innerHTML = `<span class="log-item__name">${escapeHtml(name)}</span><span> colonne ${col}</span><div class="log-meta">Coup ${i + 1}</div>`;
    historyDiv.appendChild(item);
  }
  historyDiv.scrollTop = historyDiv.scrollHeight;
}

function updateModeUI() {
  const mode = ($("modeSelect")?.value || "IA").toUpperCase();
  const difficultyField = $("difficultyField");
  const aiModeField = $("aiModeField");
  const colorSelect = $("colorSelect");
  const humanColorSelect = $("humanColorSelect");
  const startHint = $("startHint");
  const nameR = $("playerNameR");
  const nameJ = $("playerNameJ");

  if (difficultyField) difficultyField.style.display = (mode === "IA" || mode === "LOCAL" || mode === "IA_VS_IA") ? "" : "none";
  if (aiModeField) aiModeField.style.display = (mode === "IA" || mode === "LOCAL" || mode === "IA_VS_IA") ? "" : "none";
  if ($("diffSelect")) $("diffSelect").disabled = false;
  if ($("aiModeSelect")) $("aiModeSelect").disabled = (mode !== "IA" && mode !== "LOCAL" && mode !== "IA_VS_IA");
  if (colorSelect) colorSelect.disabled = mode === "ONLINE";
  if (humanColorSelect) humanColorSelect.disabled = mode !== "IA";
  if (startHint) startHint.classList.toggle("is-visible", mode === "ONLINE");

  const selectedHumanColor = ($("humanColorSelect")?.value || humanColor || "R").toUpperCase();
  humanColor = selectedHumanColor;

  if (mode === "IA") {
    if (selectedHumanColor === "R") {
      PLAYER_J_NAME = "IA";
      if ($("playerNameJ")) { $("playerNameJ").value = "IA"; $("playerNameJ").disabled = true; }
      if (nameR) { nameR.disabled = false; nameR.value = localStorage.getItem("playerNameR") || PLAYER_R_NAME; PLAYER_R_NAME = nameR.value || "Joueur rouge"; }
    } else {
      PLAYER_R_NAME = "IA";
      if ($("playerNameR")) { $("playerNameR").value = "IA"; $("playerNameR").disabled = true; }
      if (nameJ) { nameJ.disabled = false; nameJ.value = localStorage.getItem("playerNameJ") || PLAYER_J_NAME; PLAYER_J_NAME = nameJ.value || "Joueur jaune"; }
    }
  } else if (mode === "IA_VS_IA") {
    PLAYER_R_NAME = "IA Rouge";
    PLAYER_J_NAME = "IA Jaune";
    if (nameR) { nameR.disabled = true; nameR.value = "IA Rouge"; }
    if (nameJ) { nameJ.disabled = true; nameJ.value = "IA Jaune"; }
  } else {
    if (nameR) { nameR.disabled = false; nameR.value = localStorage.getItem("playerNameR") || PLAYER_R_NAME; PLAYER_R_NAME = nameR.value || "Joueur rouge"; }
    if (nameJ) { nameJ.disabled = false; nameJ.value = localStorage.getItem("playerNameJ") || PLAYER_J_NAME; PLAYER_J_NAME = nameJ.value || "Joueur jaune"; }
  }

  updateShareLinkVisibility();
  if (lastState) render(lastState);
}

function updateAiColorButtons(state) {
  const btnAiRed = $("btnAiRed"), btnHumanRed = $("btnHumanRed");
  const btnAiYellow = $("btnAiYellow"), btnHumanYellow = $("btnHumanYellow");
  if (!btnAiRed || !btnHumanRed || !btnAiYellow || !btnHumanYellow) return;

  // Tout cacher par défaut
  for (const btn of [btnAiRed, btnHumanRed, btnAiYellow, btnHumanYellow]) {
    btn.hidden = true; btn.disabled = true;
  }

  // Pas de partie, partie terminée, mode peinture → rien
  if (!state || state.game_over || paintMode) return;
  
  // HIDE for ALL WEB games (IA or HUMAIN) - buttons only for LOCAL
  if (state.mode === "WEB") return;
  
  // LOCAL mode only: show buttons
  if (state.mode !== "LOCAL") return;

  const aiPlayers = state.ai_players || { R: false, J: false };

  // Afficher les 4 boutons — seulement en LOCAL
  btnAiRed.hidden = false;    btnHumanRed.hidden = false;
  btnAiYellow.hidden = false; btnHumanYellow.hidden = false;

  btnAiRed.disabled    = busy || paused || !!aiPlayers.R;
  btnHumanRed.disabled = busy || paused || !aiPlayers.R;
  btnAiYellow.disabled    = busy || paused || !!aiPlayers.J;
  btnHumanYellow.disabled = busy || paused || !aiPlayers.J;
}

function render(state) {
  if (!state) return;
  
  // CRITICAL SAFETY CHECK: Ensure ai_players is never corrupted
  if (!state.ai_players || typeof state.ai_players !== "object") {
    state.ai_players = { R: false, J: false };
  }
  if (state.ai_players.R && state.ai_players.J && state.mode !== "LOCAL") {
    console.warn("SAFETY: Detected both players as AI in WEB mode outside IA_VS_IA - disabling yellow AI to prevent infinite loop");
    state.ai_players.J = false;
  }
  
  setModePill(state);
  renderRole(state);
  renderStatusText(state);
  renderMessage(state);
  renderColHeader(state);
  if (!paintMode) renderHistory(state);
  renderBoard(state);
  const linkInput = $("shareLink");
  if (linkInput) linkInput.value = state.mode === "WEB" && state.id_partie && GAME_ID ? window.location.href : "";
  if (!paintMode) {
    applyPreview();
    applyLastMove();
    applyWinningLine(state);
  }
  updateTurnInfo(state);
  updateHeaderStatus(state);
  updateUndoRedoButtons();
  updateUndoHelpText();
  updateAiColorButtons(state);
  renderPrediction();
  updatePaintUI();
  if (paintMode) updatePaintCounters();
}

function ensureDbControls() {
  let container = document.getElementById("dbImportBox");
  if (container) return;
  const panel = document.querySelector('.panel--controls');
  if (!panel) return;
  container = document.createElement('div');
  container.id = 'dbImportBox';
  container.className = 'field';
  container.innerHTML = `
    <label class="field__label" for="dbGameSelect">Importer depuis la BDD</label>
    <select id="dbGameSelect" class="field__input"></select>
    <div class="toolbar"><button type="button" class="btn" id="btnRefreshDbGames">↻ Rafraîchir</button><button type="button" class="btn btn-primary" id="btnLoadDbGame">Charger</button></div>
    <div class="help-text" id="modelStatusTxt"></div>
  `;
  panel.appendChild(container);
}

async function refreshModelStatus() {
  const el = $("modelStatusTxt");
  if (!el) return;
  const res = await fetchModelStatus();
  if (!res.ok) { el.textContent = "Statut modèle indisponible"; return; }
  const d = res.data;
  if (d.model_loaded) {
    el.textContent = "IA : ML hybride actif (ML + Minimax)";
    el.style.color = "var(--green, #4ade80)";
  } else {
    el.textContent = "IA : Minimax pur (modele ML non disponible)";
    el.style.color = "var(--yellow, #fbbf24)";
  }
}

async function refreshDbGames() {
  const sel = $("dbGameSelect");
  if (!sel) return;
  const res = await fetchDbGames(200);
  if (!res.ok) return;
  const prev = sel.value;
  sel.innerHTML = '';
  for (const g of res.data.games || []) {
    const opt = document.createElement('option');
    opt.value = g.id_partie;
    opt.textContent = `#${g.id_partie} | ${g.mode} | ${g.type_partie} | ${g.status} | ${g.signature || ''}`;
    sel.appendChild(opt);
  }
  if (prev) sel.value = prev;
}

function rebuildUndoFromSnapshots(snapshots, activeState) {
  undoStack.length = 0;
  redoStack.length = 0;
  if (!Array.isArray(snapshots) || snapshots.length === 0) return;
  for (let i = 0; i < snapshots.length - 1; i++) {
    undoStack.push(cloneFullState(snapshots[i]));
  }
}

async function loadSelectedDbGame() {
  const sel = $("dbGameSelect");
  if (!sel || !sel.value) return;
  const res = await postLoadGame(Number(sel.value));
  if (!res.ok) { showMessage(res.data?.error || 'Impossible de charger la partie.'); return; }
  invalidateAiWork();
  setSuggestBusy(false);
  lastState = res.data.state;
  GAME_ID = lastState.id_partie || null;
  blockAutoAiUntilHumanAction = false;
  rebuildUndoFromSnapshots(res.data.snapshots || [], lastState);
  lastMove = lastMoveFromSignature(lastState);
  if (lastState.mode === 'WEB' && GAME_ID) {
    history.replaceState({}, '', `?game_id=${GAME_ID}`);
    startPolling();
  } else {
    history.replaceState({}, '', location.pathname);
    stopPolling();
  }
  syncSelectsFromLoadedState(lastState);
  render(lastState);
  scheduleAiIfNeeded();
  if (!lastState.game_over) void runPrediction();
}

// ─────────────────────────────────────────────────────────────────────────────
// INITIALISATION
// ─────────────────────────────────────────────────────────────────────────────

window.addEventListener("error", ev => {
  console.error("Erreur JS", ev.error || ev.message);
  setMessageOnly("Erreur : " + (ev.error?.message || ev.message));
});

window.addEventListener("load", async () => {
  // Mesure la vraie hauteur du header pour --header-h
  function updateHeaderHeight() {
    const h = document.querySelector(".site-header")?.offsetHeight || 68;
    document.documentElement.style.setProperty("--header-h", h + "px");
  }
  updateHeaderHeight();
  new ResizeObserver(updateHeaderHeight).observe(document.querySelector(".site-header") || document.body);

  if ($("playerNameR")) $("playerNameR").value = PLAYER_R_NAME;
  ensureDbControls();
  if ($("playerNameJ")) $("playerNameJ").value = PLAYER_J_NAME;
  if ($("humanColorSelect")) $("humanColorSelect").value = humanColor;
  if ($("colorSelect")) $("colorSelect").value = uiPrefs.startingPlayer || "R";
  if ($("diffSelect")) $("diffSelect").value = uiPrefs.difficulty;

  $("btnNew")?.addEventListener("click", newGame);
  $("btnUndo")?.addEventListener("click", () => void undo().catch(err => console.error(err)));
  $("btnRedo")?.addEventListener("click", () => void redo().catch(err => console.error(err)));

  $("confirmModalYes")?.addEventListener("click", async () => {
    const fn = pendingConfirmCallback;
    hideConfirmModal();
    if (typeof fn === "function") await fn();
  });
  $("confirmModalNo")?.addEventListener("click", hideConfirmModal);

  $("btnPause")?.addEventListener("click", () => {
    if (!lastState) return;
    setPaused(!paused);
    $("btnPause").textContent = paused ? "Reprendre" : "Pause";
  });
  $("btnResume")?.addEventListener("click", () => { setPaused(false); const bp = $("btnPause"); if (bp) bp.textContent = "Pause"; });


  // ── Mode peinture ─────────────────────────────────────────────────────────
  $("btnEnterPaint")?.addEventListener("click", () => {
    if (paintMode) return;
    enterPaintMode();
  });

  $("btnExitPaint")?.addEventListener("click", () => exitPaintMode(true));
  $("btnExitPaintDiscard")?.addEventListener("click", () => exitPaintMode(false));

  $("btnPaintRed")?.addEventListener("click", () => { paintColor = "R"; updatePaintUI(); if (lastState) updateTurnInfo(lastState); });
  $("btnPaintYellow")?.addEventListener("click", () => { paintColor = "J"; updatePaintUI(); if (lastState) updateTurnInfo(lastState); });
  $("btnPaintErase")?.addEventListener("click", () => { paintColor = "0"; updatePaintUI(); if (lastState) updateTurnInfo(lastState); });

  $("btnPaintClear")?.addEventListener("click", () => {
    paintBoard = Array.from({ length: ROWS }, () => Array(COLS).fill(0));
    updatePaintCounters();
    renderPaintBoard();
  });

  $("btnPaintAiHint")?.addEventListener("click", async () => {
    if (!paintBoard) return;
    const nb_r = paintBoard.flat().filter(x => x === "R").length;
    const nb_j = paintBoard.flat().filter(x => x === "J").length;
    if (Math.abs(nb_r - nb_j) > 1) { showMessage("Position invalide (différence de pions > 1)."); return; }
    // Déduire à qui c'est de jouer depuis les pions
    let cp;
    if (nb_r > nb_j)       cp = "J";
    else if (nb_j > nb_r)  cp = "R";
    else                   cp = ($("colorSelect")?.value || "R").toUpperCase();

    const depth = normalize_depth_js($("diffSelect")?.value, 4);
    const res = await postPaintHint(deepCloneBoard(paintBoard), cp, depth);
    if (!res.ok) { showMessage(res.data.error || "Erreur IA."); return; }
    const col = res.data.suggested_col;
    showHistoryLine(`IA suggère la colonne ${col + 1} pour ${cp === "R" ? "Rouge" : "Jaune"}.`, "log-item--system");

    // Surligner la colonne suggérée
    const boardDiv = $("board");
    if (boardDiv) {
      boardDiv.querySelectorAll(".cell").forEach((cell, idx) => {
        const cellCol = idx % COLS;
        cell.classList.toggle("cell--hint-paint", cellCol === col);
      });
      setTimeout(() => boardDiv.querySelectorAll(".cell--hint-paint").forEach(c => c.classList.remove("cell--hint-paint")), 4000);
    }
  });

  function normalize_depth_js(v, def) {
    const d = parseInt(v, 10);
    if (isNaN(d)) return def;
    return Math.max(2, Math.min(9, d));
  }

  // ── Prédiction ────────────────────────────────────────────────────────────
  $("btnPredict")?.addEventListener("click", () => void runPrediction(true));

  $("autoAnalysisToggle")?.addEventListener("change", (e) => {
    autoAnalysisEnabled = !!e.target.checked;
    if (!autoAnalysisEnabled) { predictionResult = null; renderPrediction(); }
  });

  // ── Suggestion ───────────────────────────────────────────────────────────
  $("btnHint")?.addEventListener("click", async () => {
    if (!lastState || paused || suggestBusy) return;
    if (lastState.game_over) { showMessage("La partie est terminée."); return; }
    if (!GAME_ID) { showMessage("Crée d'abord une partie."); return; }

    setSuggestBusy(true);
    setThinking(true);
    try {
      const res = await postHint();
      if (!res.ok) { showMessage((res.data && res.data.error) || "Aucune suggestion."); return; }
      const col = res.data.suggested_col;
      if (typeof col !== "number") { showMessage("Réponse inattendue."); return; }
      hintColumn = col;
      hintScores = res.data.scores || null;
      render(lastState);
      showHistoryLine(`Suggestion : colonne ${col + 1}.`, "log-item--system");
      scheduleHintClear(8000);
    } finally {
      setThinking(false);
      setSuggestBusy(false);
    }
  });

  $("btnRefreshDbGames")?.addEventListener("click", () => void refreshDbGames());
  $("btnLoadDbGame")?.addEventListener("click", () => void loadSelectedDbGame());

  $("btnCopyLink")?.addEventListener("click", () => {
    const link = $("shareLink")?.value || "";
    if (!link) return;
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(link).then(() => setMessageOnly("Lien copié.")).catch(() => {});
    else { $("shareLink")?.select(); document.execCommand("copy"); setMessageOnly("Lien copié."); }
  });

  // ── Import signature depuis fichier .txt ──────────────────────────────────
  $("importSigFile")?.addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const statusEl = $("importSigStatus");
    if (statusEl) { statusEl.hidden = false; statusEl.textContent = `Lecture de ${file.name}…`; }

    try {
      const text = await file.text();
      const sig = text.replace(/\s+/g, "").trim();
      if (!sig || !/\d/.test(sig)) {
        if (statusEl) { statusEl.textContent = "Fichier invalide : aucun chiffre trouvé."; statusEl.style.color = "var(--red, #f87171)"; }
        return;
      }

      const startingPlayer = ($("colorSelect")?.value || "R").toUpperCase();
      const res = await postImportSignature(sig, startingPlayer);
      if (!res.ok) {
        if (statusEl) { statusEl.textContent = res.data?.error || "Erreur lors de l'import."; statusEl.style.color = "var(--red, #f87171)"; }
        return;
      }

      lastState = res.data;
      GAME_ID = lastState.id_partie || GAME_ID;
      lastMove = null;
      lastBoardSnapshot = null;
      clearHint();
      undoStack.length = 0;
      redoStack.length = 0;
      predictionResult = null;
      paintMode = false;
      history.replaceState({}, "", location.pathname);
      stopPolling();
      render(lastState);

      const nbMoves = res.data.moves_count || sig.replace(/[^\d]/g, "").length;
      if (statusEl) { statusEl.textContent = `${file.name} importé (${nbMoves} coups).`; statusEl.style.color = "var(--green, #4ade80)"; }
      showHistoryLine(`Position importée depuis ${file.name} — signature : ${sig} (${nbMoves} coups).`, "log-item--system");

      if (lastState.game_over) {
        setMessageOnly("Position importée : partie terminée.");
      } else {
        scheduleAiIfNeeded();
      }
    } catch (err) {
      console.error("Import signature error:", err);
      const msg = err?.message || "Erreur inconnue";
      if (statusEl) { statusEl.textContent = "Erreur : " + msg; statusEl.style.color = "var(--red, #f87171)"; }
      showMessage("Erreur lors de l'import : " + msg);
    }

    // Reset le file input pour pouvoir re-charger le même fichier
    e.target.value = "";
  });

  // ── Boutons IA/Humain ─────────────────────────────────────────────────────
  $("btnAiRed")?.addEventListener("click", async () => {
    if (!lastState) return;
    const depth = Math.max(2, Math.min(9, parseInt($("diffSelect")?.value, 10) || 4));
    blockAutoAiUntilHumanAction = false;
    setThinking(false);
    if (GAME_ID) {
      const res = await postSetAiColor("R", true);
      if (!res.ok) { showMessage(res.data?.error || "Impossible d'activer l'IA pour rouge."); return; }
      lastState = res.data;
      lastState.ai_depth = depth;
    } else {
      lastState.ai_players = { ...(lastState.ai_players || { R: false, J: false }), R: true };
      lastState.ai_enabled = true;
      lastState.ai_depth = depth;
      lastState.player_r_name = "IA";
    }
    render(lastState);
    showHistoryLine("Rouge est désormais contrôlé par l'IA.", "log-item--system");
    scheduleAiIfNeeded();
  });

  $("btnHumanRed")?.addEventListener("click", async () => {
    if (!lastState) return;
    invalidateAiWork();
    if (GAME_ID) {
      const res = await postSetAiColor("R", false);
      if (!res.ok) { showMessage(res.data?.error || "Impossible de rendre rouge humain."); return; }
      lastState = res.data;
    } else {
      lastState.ai_players = { ...(lastState.ai_players || { R: false, J: false }), R: false };
      lastState.ai_enabled = !!(lastState.ai_players.R || lastState.ai_players.J);
      lastState.player_r_name = localStorage.getItem("playerNameR") || PLAYER_R_NAME || "Joueur rouge";
    }
    render(lastState);
    showHistoryLine("Rouge redevient humain.", "log-item--system");
    blockAutoAiUntilHumanAction = false;
    scheduleAiIfNeeded();
  });

  $("btnAiYellow")?.addEventListener("click", async () => {
    if (!lastState) return;
    const depth = Math.max(2, Math.min(9, parseInt($("diffSelect")?.value, 10) || 4));
    blockAutoAiUntilHumanAction = false;
    setThinking(false);
    if (GAME_ID) {
      const res = await postSetAiColor("J", true);
      if (!res.ok) { showMessage(res.data?.error || "Impossible d'activer l'IA pour jaune."); return; }
      lastState = res.data;
      lastState.ai_depth = depth;
    } else {
      lastState.ai_players = { ...(lastState.ai_players || { R: false, J: false }), J: true };
      lastState.ai_enabled = true;
      lastState.ai_depth = depth;
      lastState.player_j_name = "IA";
    }
    render(lastState);
    showHistoryLine("Jaune est désormais contrôlé par l'IA.", "log-item--system");
    scheduleAiIfNeeded();
  });

  $("btnHumanYellow")?.addEventListener("click", async () => {
    if (!lastState) return;
    invalidateAiWork();
    if (GAME_ID) {
      const res = await postSetAiColor("J", false);
      if (!res.ok) { showMessage(res.data?.error || "Impossible de rendre jaune humain."); return; }
      lastState = res.data;
    } else {
      lastState.ai_players = { ...(lastState.ai_players || { R: false, J: false }), J: false };
      lastState.ai_enabled = !!(lastState.ai_players.R || lastState.ai_players.J);
      lastState.player_j_name = localStorage.getItem("playerNameJ") || PLAYER_J_NAME || "Joueur jaune";
    }
    render(lastState);
    showHistoryLine("Jaune redevient humain.", "log-item--system");
    blockAutoAiUntilHumanAction = false;
    scheduleAiIfNeeded();
  });

  $("modeSelect")?.addEventListener("change", (e) => {
    if (suppressSelectChange) return;
    const newVal = e.target.value.toUpperCase();
    const oldVal = committedMode;
    if (newVal === oldVal) return;
    if (hasActiveGame()) {
      e.target.value = oldVal;
      showConfirmModal("Changer le mode va terminer la partie en cours. Continuer ?", async () => {
        suppressSelectChange = true;
        try { $("modeSelect").value = newVal; syncUiPrefsFromForm(); updateModeUI(); updateShareLinkVisibility(); await newGame(); } finally { suppressSelectChange = false; }
      });
    } else { syncUiPrefsFromForm(); updateModeUI(); updateShareLinkVisibility(); }
  });

  $("diffSelect")?.addEventListener("change", async (e) => {
    if (suppressSelectChange) return;
    if (busy || aiThinking) {
      const oldVal = committedDifficulty;
      if (e.target) e.target.value = oldVal;
      showMessage("Attends que l'IA ait fini de réfléchir avant de changer la profondeur.");
      return;
    }
    const newVal = e.target.value, oldVal = committedDifficulty;
    if (newVal === oldVal) return;
    syncUiPrefsFromForm();
    committedDifficulty = uiPrefs.difficulty;

    if (hasActiveGame() && GAME_ID) {
      const res = await postSetAiPrefs(uiPrefs.difficulty, uiPrefs.aiMode);
      if (!res.ok) { showMessage(res.data?.error || "Impossible d'enregistrer la profondeur IA."); return; }
      lastState = res.data;
      render(lastState);
      scheduleAiIfNeeded();
      if (!lastState.game_over) void runPrediction();
      showHistoryLine("Profondeur IA appliquée.", "log-item--system");
      return;
    }

    if (hasActiveGame() && lastState) {
      lastState.ai_depth = uiPrefs.difficulty;
      render(lastState);
      scheduleAiIfNeeded();
      if (!lastState.game_over) void runPrediction();
      showHistoryLine("Profondeur IA appliquée.", "log-item--system");
    }

    if (paintMode) void runPrediction(true);
  });

  $("aiModeSelect")?.addEventListener("change", async (e) => {
    if (suppressSelectChange) return;
    if (busy || aiThinking) {
      const oldVal = committedAiMode;
      if (e.target) e.target.value = oldVal;
      showMessage("Attends que l'IA ait fini de réfléchir avant de changer le type d'IA.");
      return;
    }
    const newVal = (e.target.value || "hybrid").toLowerCase();
    const oldVal = committedAiMode;
    if (newVal === oldVal) return;
    syncUiPrefsFromForm();
    committedAiMode = uiPrefs.aiMode;

    if (hasActiveGame() && GAME_ID) {
      const res = await postSetAiPrefs(uiPrefs.difficulty, uiPrefs.aiMode);
      if (!res.ok) { showMessage(res.data?.error || "Impossible d'enregistrer le type d'IA."); return; }
      lastState = res.data;
      render(lastState);
      scheduleAiIfNeeded();
      if (!lastState.game_over) void runPrediction();
      showHistoryLine("Type d'IA appliqué.", "log-item--system");
      return;
    }

    if (hasActiveGame() && lastState) {
      lastState.ai_mode = uiPrefs.aiMode;
      render(lastState);
      scheduleAiIfNeeded();
      if (!lastState.game_over) void runPrediction();
      showHistoryLine("Type d'IA appliqué.", "log-item--system");
    }
  });

  $("btnApplySettings")?.addEventListener("click", async () => {
    if (busy || aiThinking) {
      showMessage("Attends que l'IA ait fini de réfléchir avant d'appliquer les réglages.");
      return;
    }
    syncUiPrefsFromForm();
    committedDifficulty = uiPrefs.difficulty;
    committedAiMode = uiPrefs.aiMode;
    if (hasActiveGame() && GAME_ID) {
      const res = await postSetAiPrefs(uiPrefs.difficulty, uiPrefs.aiMode);
      if (!res.ok) { showMessage(res.data?.error || "Impossible d'appliquer les réglages IA."); return; }
      lastState = res.data;
      render(lastState);
      scheduleAiIfNeeded();
      if (!lastState.game_over) void runPrediction();
      showHistoryLine("Réglages IA appliqués.", "log-item--system");
      return;
    }

    if (hasActiveGame() && lastState) {
      lastState.ai_depth = uiPrefs.difficulty;
      lastState.ai_mode = uiPrefs.aiMode;
      render(lastState);
      scheduleAiIfNeeded();
      if (!lastState.game_over) void runPrediction();
      showHistoryLine("Réglages IA appliqués.", "log-item--system");
      return;
    }

    await newGame();
  });

  $("playerNameR")?.addEventListener("input", (e) => {
    PLAYER_R_NAME = e.target.value || "Joueur rouge";
    localStorage.setItem("playerNameR", PLAYER_R_NAME);
    const aiPlayers = lastState?.ai_players || { R: false, J: false };
    if (lastState && !aiPlayers.R) { lastState.player_r_name = PLAYER_R_NAME; render(lastState); }
  });

  $("playerNameJ")?.addEventListener("input", (e) => {
    PLAYER_J_NAME = e.target.value || "Joueur jaune";
    localStorage.setItem("playerNameJ", PLAYER_J_NAME);
    const aiPlayers = lastState?.ai_players || { R: false, J: false };
    if (lastState && !aiPlayers.J) { lastState.player_j_name = PLAYER_J_NAME; render(lastState); }
  });

  $("humanColorSelect")?.addEventListener("change", (e) => { humanColor = (e.target.value || "R").toUpperCase(); localStorage.setItem("humanColor", humanColor); syncUiPrefsFromForm(); updateModeUI(); });
  $("colorSelect")?.addEventListener("change", () => { syncUiPrefsFromForm(); updateModeUI(); });

  updateModeUI(); syncUiPrefsFromForm(); updateShareLinkVisibility();

  const params = new URLSearchParams(window.location.search);
  if (params.has("game_id")) {
    GAME_ID = Number(params.get("game_id"));
    lastState = await getState(GAME_ID);
    if (!lastState) { GAME_ID = null; history.replaceState({}, "", location.pathname); lastState = await getState(); }
    else {
      // SAFETY: Ensure state is valid
      if (!lastState.ai_players || typeof lastState.ai_players !== "object") {
        lastState.ai_players = { R: false, J: false };
      }
      // SAFETY: Prevent both players from being AI in WEB mode
      if (lastState.ai_players.R && lastState.ai_players.J && lastState.mode !== "LOCAL") {
        lastState.ai_players.J = false;
      }
      if (lastState.mode === "WEB") {
        if ($("shareLink")) $("shareLink").value = window.location.href;
        startPolling();
      } else {
        history.replaceState({}, "", location.pathname);
        if ($("shareLink")) $("shareLink").value = "";
        stopPolling();
      }
      if (lastState.game_over) setMessageOnly("Cette partie est terminée. Lance une nouvelle partie.");
      syncSelectsFromLoadedState(lastState);
    }
  } else { lastState = await getState(); }

  if (!lastState) {
    lastState = { id_partie: null, mode: "LOCAL", type_partie: "HUMAIN", status: "Aucune partie", ai_enabled: false, ai_depth: 4, ai_player: null, ai_players: { R: false, J: false }, board: Array.from({ length: ROWS }, () => Array(COLS).fill(0)), current_player: "R", starting_player: "R", signature: "init", game_over: false, winning_line: null, player_count: 0, client_r: null, client_j: null, player_r_name: PLAYER_R_NAME, player_j_name: PLAYER_J_NAME };
  }
  refreshDbGames().catch(() => {});
  refreshModelStatus().catch(() => {});

  // Final safety check
  if (!lastState.ai_players || typeof lastState.ai_players !== "object") {
    lastState.ai_players = { R: false, J: false };
  }
  if (lastState.ai_players.R && lastState.ai_players.J && lastState.mode !== "LOCAL") {
    lastState.ai_players.J = false;
  }

  $("btnPause").textContent = "Pause";
  render(lastState);
  scheduleAiIfNeeded();
});