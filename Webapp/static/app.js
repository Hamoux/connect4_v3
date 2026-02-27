const ROWS = 9;
const COLS = 9;

// unique identifier for this browser session; persisted in localStorage
let CLIENT_ID = localStorage.getItem("connect4_client_id");
if (!CLIENT_ID) {
  try {
    CLIENT_ID = (crypto && crypto.randomUUID) ? crypto.randomUUID() : null;
  } catch (e) {
    CLIENT_ID = null;
  }
  if (!CLIENT_ID) {
    // older browsers may not support randomUUID
    CLIENT_ID = 'cid_' + Date.now() + '_' + Math.floor(Math.random()*1e6);
  }
  localStorage.setItem("connect4_client_id", CLIENT_ID);
}

// global error catcher to help debugging
window.addEventListener('error', ev => {
  console.error('JS ERROR', ev.error || ev.message);
  showMessage('Erreur JS: ' + (ev.error?.message || ev.message));
});

console.log('CLIENT_ID', CLIENT_ID); // helpful for tracing

// optional game id if user opened a shared link
let GAME_ID = null;

// parameters that can be changed by the user (UI only)
let PLAYER_R_NAME = "Joueur";   // human if playing red
let PLAYER_J_NAME = "IA";       // computer name
let playerColor = "R";          // default human color

// délai IA (ms) => change si tu veux
const AI_DELAY_MS = 900;

let lastState = null;
let hoverCol = null;
let lastMove = null;
let aiTimer = null;
let busy = false; // waiting for API response
let pollTimer = null;

async function getState(id) {
  // fetch current state; include game_id/client_id if provided
  let url = "/api/state";
  if (id) url += `?game_id=${encodeURIComponent(id)}&client_id=${encodeURIComponent(CLIENT_ID)}`;
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 404) {
      showMessage("Partie introuvable. Vérifie l’URL ou crée une nouvelle partie.");
      return null;
    }
    showMessage(data.error || "Erreur lors de la récupération de la partie");
    return null;
  }
  return data;
}

async function newGame() {
  // when starting a fresh game always forget any previous room id
  GAME_ID = null;
  history.replaceState({}, "", location.pathname);

  busy = false;
  hideMessage();
  lastMove = null;
  cancelAiTimer();

  const mode = document.getElementById("modeSelect")?.value || "IA";
  const difficulty = document.getElementById("diffSelect")?.value || "medium";
  // for online games the server will pick a random colour; we do not send one
  const starting_player = (mode === "ONLINE") ? undefined : (document.getElementById("colorSelect")?.value || playerColor || "R");

  if (mode === "LOCAL") {
    // purely client‑side game
    lastState = {
      id_partie: null,
      mode: "LOCAL",
      type_partie: "HUMAIN",
      status: "EN_COURS",
      ai_enabled: false,
      ai_depth: 0,
      board: Array.from({ length: ROWS }, () => Array(COLS).fill(0)),
      current_player: starting_player,
      game_over: false,
      starting_player: starting_player,
      ai_player: null,
      signature: "init",
      last_situation_id: null,
      winning_line: null,
    };
    render(lastState);
    history.replaceState({}, "", location.pathname);
    stopPolling();
    return;
  }

  // online or IA game
  const payload = { mode, difficulty, starting_player, client_id: CLIENT_ID };
  if (GAME_ID) payload.game_id = GAME_ID;

  const res = await fetch("/api/new", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const state = await res.json();
  if (!res.ok) {
    showMessage(state.error || "Erreur lors de la création de la partie");
    return;
  }
  lastState = state;
  if (state.id_partie) {
    GAME_ID = state.id_partie;
    history.replaceState({}, "", `?game_id=${GAME_ID}`);
    const url = window.location.href;
    const linkInput = document.getElementById("shareLink");
    if (linkInput) {
      linkInput.value = url;
      // attempt to copy automatically
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).catch(() => {});
      }
    }
    startPolling();
  }
  render(state);
}

async function play(col) {
  if (busy) return;

  // block clicks while waiting for an opponent in online human games
  if (lastState?.mode === "WEB" && lastState?.type_partie === "HUMAIN" && lastState.player_count < 2) {
    setMessageOnly("⏳ En attente d'un adversaire…");
    return;
  }

  // local game handling
  if (lastState?.mode === "LOCAL") {
    if (lastState.game_over) return;
    if (isColumnFull(col)) return;
    let placed = null;
    for (let r = ROWS - 1; r >= 0; r--) {
      if (lastState.board[r][col] === 0) { placed = r; lastState.board[r][col] = lastState.current_player; break; }
    }
    if (placed === null) return;
    if (String(lastState.signature).startsWith("init_")) lastState.signature = "";
    lastState.signature += String(col + 1);
    lastMove = { r: placed, c: col };
    const line = jsFindWinningLine(placed, col, lastState.board);
    if (line) {
      lastState.game_over = true;
      lastState.status = "TERMINEE";
      lastState.winning_line = line.map(([r,c]) => [r,c]);
      render(lastState);
      showMessage(`🏁 Victoire de ${nameFor(lastState.current_player)} !`);
      return;
    }
    lastState.current_player = lastState.current_player === "R" ? "J" : "R";
    render(lastState);
    return;
  }

  if (!lastState?.id_partie) {
    showMessage("Clique sur “Nouvelle partie” d’abord 🙂");
    return;
  }
  if (lastState?.game_over) return;
  if (lastState?.type_partie === "IA" && lastState.current_player === lastState.ai_player) {
    return;
  }
  if (isColumnFull(col)) return;

  cancelAiTimer();
  busy = true;

  let res, data;
  try {
    res = await fetch("/api/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ col, game_id: GAME_ID, client_id: CLIENT_ID })
    });
    data = await res.json();
  } catch (err) {
    busy = false;
    console.error("fetch error", err);
    showMessage("Erreur réseau");
    return;
  }
  busy = false;
  if (!res.ok) {
    showMessage(data.error || "Erreur");
    return;
  }

  lastMove = findLastMove(lastState?.board, data?.board);
  lastState = data;
  render(data);

  if (data.game_over) {
    showMessage(`🏁 Victoire de ${nameFor(data.current_player)} !`);
    return;
  }

  if (data.type_partie === "IA" && data.current_player === data.ai_player) {
    aiTimer = setTimeout(aiMove, AI_DELAY_MS);
  }
}

async function aiMove() {
  aiTimer = null;
  if (!lastState || lastState.game_over) return;

  if (lastState.mode === "LOCAL") {
    return; // not used
  }

  const res = await fetch("/api/ai_move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_id: GAME_ID, client_id: CLIENT_ID })
  });
  const data = await res.json();

  if (!res.ok) {
    console.log("AI ERROR:", data);
    return;
  }

  lastMove = findLastMove(lastState?.board, data?.board);
  lastState = data;
  render(data);

  if (data.game_over) {
    showMessage(`🏁 Victoire de ${nameFor(data.current_player)} !`);
  }
}

function cancelAiTimer() {
  if (aiTimer) {
    clearTimeout(aiTimer);
    aiTimer = null;
  }
}

function startPolling(){
  if (pollTimer) return;
  // do an immediate fetch so joining players update without delay
  (async () => {
    if (!lastState || lastState.game_over || lastState.mode !== "WEB") return;
    const data = await getState(GAME_ID);
    if (!data) return;
    const boardChanged = JSON.stringify(data.board) !== JSON.stringify(lastState.board);
    const playersChanged = data.player_count !== lastState.player_count || data.client_r !== lastState.client_r || data.client_j !== lastState.client_j;
    const metaChanged = data.signature !== lastState.signature || data.current_player !== lastState.current_player || data.game_over !== lastState.game_over;
    if (boardChanged) {
      lastMove = findLastMove(lastState.board, data.board);
    }
    if (boardChanged || playersChanged || metaChanged) {
      lastState = data;
      render(data);
    }
  })();

  pollTimer = setInterval(async () => {
    if (!lastState || lastState.game_over || lastState.mode !== "WEB") {
      stopPolling();
      return;
    }
    const data = await getState(GAME_ID);
    if (!data) return;
    const boardChanged = JSON.stringify(data.board) !== JSON.stringify(lastState.board);
    const playersChanged = data.player_count !== lastState.player_count || data.client_r !== lastState.client_r || data.client_j !== lastState.client_j;
    const metaChanged = data.signature !== lastState.signature || data.current_player !== lastState.current_player || data.game_over !== lastState.game_over;
    if (boardChanged) lastMove = findLastMove(lastState.board, data.board);
    if (boardChanged || playersChanged || metaChanged) {
      lastState = data;
      render(data);
    }
  }, 800);
}

function stopPolling(){
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function isColumnFull(col){
  return lastState?.board?.[0]?.[col] !== 0;
}

function findLastMove(prevBoard, newBoard){
  if (!prevBoard || !newBoard) return null;
  for (let r = 0; r < ROWS; r++){
    for (let c = 0; c < COLS; c++){
      if (prevBoard?.[r]?.[c] === 0 && (newBoard?.[r]?.[c] === "R" || newBoard?.[r]?.[c] === "J")){
        return {r,c};
      }
    }
  }
  return null;
}

function showMessage(txt){
  // add a system entry into history as before
  const historyDiv = document.getElementById("history");
  if (historyDiv) {
    const item = document.createElement("div");
    item.className = "logItem system";
    item.innerHTML = `<span class="text">${escapeHtml(txt)}</span>`;
    historyDiv.appendChild(item);
    historyDiv.scrollTop = historyDiv.scrollHeight;
  }
  // also display in the message card
  const msg = document.getElementById("message");
  if (msg) {
    msg.hidden = false;
    msg.innerHTML = txt;
  }
}
// Show a message only in the message card (do not append to history)
function setMessageOnly(txt){
  const msg = document.getElementById("message");
  if (msg) {
    msg.hidden = false;
    msg.innerHTML = txt;
  }
}
function hideMessage(){
  const msg = document.getElementById("message");
  if (msg) { msg.hidden = true; msg.textContent = ""; }
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, m => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[m]));
}

// Affiche "J vs J" ou "J vs IA"
// returns the display name associated with a board letter
function nameFor(letter){
  if (letter === "R") return PLAYER_R_NAME;
  if (letter === "J") return PLAYER_J_NAME;
  return "?";
}

// utility used by local-mode logic
function jsFindWinningLine(r, c, board){
  const directions = [[0,1],[1,0],[1,1],[1,-1]];
  const player = board[r][c];
  for (const [dr,dc] of directions){
    let coords = [];
    for (let i=-3;i<4;i++){
      const nr = r + dr*i, nc = c + dc*i;
      if (nr>=0 && nr<ROWS && nc>=0 && nc<COLS && board[nr][nc]===player){
        coords.push([nr,nc]);
        if (coords.length===4) return coords;
      } else {
        coords = [];
      }
    }
  }
  return null;
}

function setModePill(state){
  const pill = document.getElementById("turnPill");
  pill.innerHTML = "";

  let modeTxt;
  let dotColor = "";
  if (state.mode === "LOCAL") {
    modeTxt = "J vs J (locale)";
  } else if (state.type_partie && state.type_partie.toUpperCase().includes("IA")) {
    modeTxt = "J vs IA";
    dotColor = "green"; // treat IA as online-like
  } else {
    modeTxt = "J vs J (en ligne)";
    dotColor = "green";
  }

  const dot = document.createElement("span");
  dot.className = "dot";
  if (dotColor) dot.style.backgroundColor = dotColor;
  const label = document.createElement("span");
  label.textContent = modeTxt;

  pill.appendChild(dot);
  pill.appendChild(label);
}

function renderHeader(){
  const header = document.getElementById("colHeader");
  if (!header) return;
  header.innerHTML = "";
  if (busy) return; // don't rebuild when waiting for server

  for (let c = 0; c < COLS; c++){
    const btn = document.createElement("button");
    btn.className = "colBtn";
    btn.textContent = (c + 1);

    if (lastState?.game_over || isColumnFull(c) || busy || (lastState?.type_partie === "IA" && lastState.current_player === lastState.ai_player)) btn.classList.add("full");
    if (busy) btn.disabled = true;
    if (lastState?.mode === "WEB" && lastState?.type_partie === "HUMAIN" && lastState.player_count < 2) {
      btn.classList.add("full");
      btn.disabled = true;
    }

    btn.addEventListener("mouseenter", () => { hoverCol = c; applyPreview(); });
    btn.addEventListener("mouseleave", () => { hoverCol = null; applyPreview(); });
    btn.addEventListener("click", () => play(c));

    header.appendChild(btn);
  }
}

function renderHistory(state){
  const historyDiv = document.getElementById("history");
  if (!historyDiv) return;

  historyDiv.innerHTML = "";

  let sig = String(state.signature || "");
  if (sig.startsWith("init_")) sig = "";
  const moves = sig.replace(/[^\d]/g, ""); // colonnes 1..9

  if (!moves.length){
    historyDiv.innerHTML = `<div class="logItem"><span class="text">Aucun coup pour l’instant.</span></div>`;
    return;
  }

  for (let i = 0; i < moves.length; i++){
    const col = Number(moves[i]);
      // determine which board letter played this move based on who started
      const starting = (state.starting_player || "R").toUpperCase();
      const moveLetter = (i % 2 === 0) ? starting : (starting === "R" ? "J" : "R");
      const isRed = (moveLetter === "R");
      const name = nameFor(moveLetter);

    const item = document.createElement("div");
    item.className = "logItem " + (isRed ? "red" : "yellow");
    if (i === moves.length - 1) item.classList.add("lastMove");

    item.innerHTML = `
      <span class="name">${escapeHtml(name)}</span>
      <span class="text"> place un pion dans la colonne ${col}</span>
      <div class="logTime">Coup #${i+1}</div>
    `;
    historyDiv.appendChild(item);
  }

  historyDiv.scrollTop = historyDiv.scrollHeight;
}

function render(state) {
  // pid and signature elements were removed from UI
  setModePill(state);

  // adjust status when waiting for second player
  let st = state.status || (state.game_over ? "TERMINEE" : (state.id_partie ? "EN_COURS" : "Aucune partie"));
  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    st = "EN ATTENTE d'un adversaire";
  }
  document.getElementById("statusTxt").textContent = st;

  // share link field
  const linkInput = document.getElementById("shareLink");
  if (linkInput) {
    if (state.id_partie) {
      const url = window.location.href;
      linkInput.value = url;
    } else {
      linkInput.value = "";
    }
  }

  // waiting popup / color announcement
  if (state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2) {
    let msg = "⏳ En attente d'un adversaire…";
    if (state.client_r === CLIENT_ID) msg += " Tu joues Rouge.";
    if (state.client_j === CLIENT_ID) msg += " Tu joues Jaune.";
    msg += " Partage le lien ci‑dessous.";
    setMessageOnly(msg);
  } else if (state.game_over) {
    // handled later in render
  } else {
    // clear any waiting message unless game over/victory will display
    // we call hideMessage only if we aren't about to display something else
    // (victory message is handled elsewhere)
    if (!(state.game_over)) hideMessage();
  }

  document.getElementById("hint").textContent =
    state.game_over ? "Partie terminée. Lance une nouvelle partie 👇"
                    : (state.type_partie === "IA" && state.current_player === state.ai_player
                       ? "Tour de l’IA…"
                       : "Survole une colonne puis clique 👇");

  // update message card with turn/victory info and color-coded name
  const msg = document.getElementById("message");
  if (msg) {
    // if we're still waiting for a second player, preserve the waiting message
    if (!(state.mode === "WEB" && state.type_partie === "HUMAIN" && state.player_count < 2)) {
      if (state.game_over) {
        const winner = nameFor(state.current_player);
        const cls = state.current_player === "R" ? "red" : "yellow";
        msg.hidden = false;
        msg.innerHTML = `🏁 Victoire de <span class="${cls}">${escapeHtml(winner)}</span> !`;
      } else {
        const currentName = nameFor(state.current_player);
        const cls = state.current_player === "R" ? "red" : "yellow";
        msg.hidden = false;
        msg.innerHTML = `Tour de <span class="${cls}">${escapeHtml(currentName)}</span>`;
      }
    }
  }

  renderHeader();
  renderHistory(state);

  // update your role display
  const roleDiv = document.getElementById("yourRole");
  if (roleDiv) {
    let txt = "-";
    if (state.mode === "WEB" && state.type_partie === "HUMAIN") {
      if (state.client_r === CLIENT_ID) txt = "Joueur 1 — Rouge";
      else if (state.client_j === CLIENT_ID) txt = "Joueur 2 — Jaune";
      else if (state.player_count >= 2) txt = "Spectateur";
      else txt = "En attente (non assigné)";
    } else if (state.mode === "LOCAL") {
      txt = "Local";
    } else if (state.type_partie === "IA") {
      txt = (state.ai_player === (state.starting_player === 'R' ? 'J' : 'R')) ? "Humain" : "IA";
    }
    roleDiv.textContent = txt;
  }

  const boardDiv = document.getElementById("board");
  boardDiv.innerHTML = "";

  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const cell = document.createElement("div");
      cell.className = "cell";

      cell.addEventListener("mouseenter", () => { hoverCol = c; applyPreview(); });
      cell.addEventListener("mouseleave", () => { hoverCol = null; applyPreview(); });
      cell.addEventListener("click", () => play(c));

      const piece = document.createElement("div");
      piece.className = "piece";

      const v = state.board?.[r]?.[c];
      if (v === "R") { cell.classList.add("filled"); piece.classList.add("red"); }
      if (v === "J") { cell.classList.add("filled"); piece.classList.add("yellow"); }

      cell.appendChild(piece);
      boardDiv.appendChild(cell);
    }
  }

  applyPreview();
  applyLastMove();
  applyWinningLine(state);
}

function applyPreview(){
  const boardDiv = document.getElementById("board");
  const cells = boardDiv.children;
  for (let i = 0; i < cells.length; i++) cells[i].classList.remove("preview");
  if (hoverCol == null || lastState?.game_over) return;

  // On montre la case jouable de la colonne
  let targetRow = null;
  for (let r = ROWS - 1; r >= 0; r--) {
    if (lastState?.board?.[r]?.[hoverCol] === 0) { targetRow = r; break; }
  }
  if (targetRow == null) return;

  const idx = targetRow * COLS + hoverCol;
  if (cells[idx]) cells[idx].classList.add("preview");
}

function applyLastMove(){
  const boardDiv = document.getElementById("board");
  const cells = boardDiv.children;
  for (let i = 0; i < cells.length; i++) cells[i].classList.remove("last");
  if (!lastMove) return;

  const idx = lastMove.r * COLS + lastMove.c;
  if (cells[idx]) cells[idx].classList.add("last");
}

function applyWinningLine(state){
  const boardDiv = document.getElementById("board");
  const cells = boardDiv.children;
  for (let i = 0; i < cells.length; i++) cells[i].classList.remove("win");

  const line = state.winning_line;
  if (!line || !Array.isArray(line)) return;

  for (const pair of line){
    const r = pair[0], c = pair[1];
    const idx = r * COLS + c;
    if (cells[idx]) cells[idx].classList.add("win");
  }
}

window.addEventListener("load", async () => {
  document.getElementById("btnNew").addEventListener("click", newGame);
  document.getElementById("btnCopyLink")?.addEventListener("click", () => {
    const linkInput = document.getElementById("shareLink");
    if (linkInput && linkInput.value) {
      linkInput.select();
      document.execCommand('copy');
        setMessageOnly('Lien copié&nbsp;!');
    }
  });

  const nameR = document.getElementById("playerNameR");
  const nameJ = document.getElementById("playerNameJ");
  const colorSelect = document.getElementById("colorSelect");
  const modeSelect = document.getElementById("modeSelect");
  const diffSelect = document.getElementById("diffSelect");

  // load preferences
  playerColor = localStorage.getItem("playerColor") || "R";
  PLAYER_R_NAME = localStorage.getItem("playerNameR") || "Joueur";
  PLAYER_J_NAME = localStorage.getItem("playerNameJ") || "IA";

  if (nameR) nameR.value = PLAYER_R_NAME;
  if (nameJ) nameJ.value = PLAYER_J_NAME;
  if (colorSelect) colorSelect.value = playerColor;

  // if URL contains game_id param, remember it and fetch that game
  const params = new URLSearchParams(window.location.search);
  if (params.has("game_id")) {
    GAME_ID = params.get("game_id");
    lastState = await getState(GAME_ID);
    if (lastState === null) {
      // invalid or expired link – start matching automatically
      setMessageOnly("Lien introuvable, recherche d'une partie... 💬");
      // wipe the bad id and let newGame() handle queueing/matching
      history.replaceState({}, "", location.pathname);
      GAME_ID = null;
      await newGame();
      // newGame has already rendered state and started polling
      lastState = lastState || {};
    } else {
      // existing game – disable "Nouvelle partie" to avoid accidentally
      const btn = document.getElementById("btnNew");
      if (btn) { btn.disabled = true; btn.textContent = "Partie en cours"; }
    }
  } else {
    lastState = await getState();
  }

  if (lastState === null) {
    // this only happens if initial fetch failed without game_id
    lastState = { mode: "LOCAL", game_over: false };
  }

  function updateNameInputs(){
    const mode = modeSelect?.value || "IA";
    if (mode === "IA"){
      // only the human-controlled colour stays editable, other is IA
      if (playerColor === "R"){
        PLAYER_R_NAME = localStorage.getItem("playerNameR") || PLAYER_R_NAME;
        PLAYER_J_NAME = "IA";
        if (nameR) { nameR.disabled = false; nameR.value = PLAYER_R_NAME; }
        if (nameJ) { nameJ.value = "IA"; nameJ.disabled = true; }
      } else {
        PLAYER_J_NAME = localStorage.getItem("playerNameJ") || PLAYER_J_NAME;
        PLAYER_R_NAME = "IA";
        if (nameJ) { nameJ.disabled = false; nameJ.value = PLAYER_J_NAME; }
        if (nameR) { nameR.value = "IA"; nameR.disabled = true; }
      }
    } else {
      // both players human (either LOCAL or ONLINE)
      if (nameR) { nameR.disabled = false; nameR.value = PLAYER_R_NAME; }
      if (nameJ) { nameJ.disabled = false; nameJ.value = PLAYER_J_NAME; }
    }
  }

  function updateModeUI() {
    const mode = modeSelect?.value || "IA";
    if (mode === "IA") {
      if (diffSelect) diffSelect.disabled = false;
    } else {
      if (diffSelect) diffSelect.disabled = true;
    }
    // color selection only matters for LOCAL or IA; online is random
    if (mode === "ONLINE") {
      if (colorSelect) colorSelect.disabled = true;
    } else {
      if (colorSelect) colorSelect.disabled = false;
    }
    updateNameInputs();
  }

  modeSelect?.addEventListener("change", updateModeUI);
  updateModeUI();

  nameR?.addEventListener("input", e => {
    PLAYER_R_NAME = e.target.value || "Joueur";
    localStorage.setItem("playerNameR", PLAYER_R_NAME);
    render(lastState);
  });
  nameJ?.addEventListener("input", e => {
    PLAYER_J_NAME = e.target.value || "IA";
    localStorage.setItem("playerNameJ", PLAYER_J_NAME);
    render(lastState);
  });
  colorSelect?.addEventListener("change", e => {
    playerColor = e.target.value;
    localStorage.setItem("playerColor", playerColor);
    updateNameInputs();
    render(lastState);
  });

  render(lastState);
  if (lastState && lastState.mode === "WEB") {
    startPolling();
  }
});
