const ROWS = 9;
const COLS = 9;

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

async function getState() {
  const res = await fetch("/api/state");
  return await res.json();
}

async function newGame() {
  busy = false;
  hideMessage();
  lastMove = null;
  cancelAiTimer();

  const mode = document.getElementById("modeSelect")?.value || "IA";
  const difficulty = document.getElementById("diffSelect")?.value || "medium";
  
  // include the chosen starting colour so the server can start that player
  const starting_player = document.getElementById("colorSelect")?.value || playerColor || "R";

  const res = await fetch("/api/new", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, difficulty, starting_player })
  });

  const state = await res.json();
  lastState = state;
  render(state);
}

async function play(col) {
  if (busy) return; // ignore while a request is underway
  if (!lastState?.id_partie) {
    showMessage("Clique sur “Nouvelle partie” d’abord 🙂");
    return;
  }
  if (lastState?.game_over) return;
  if (lastState?.type_partie === "IA" && lastState.current_player === lastState.ai_player) {
    // it's AI turn, block clicks
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
      body: JSON.stringify({ col })
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

  // Si IA active et c’est son tour => IA joue après un délai
  if (data.type_partie === "IA" && data.current_player === data.ai_player) {
    aiTimer = setTimeout(aiMove, AI_DELAY_MS);
  }
}

async function aiMove() {
  aiTimer = null;
  if (!lastState || lastState.game_over) return;

  const res = await fetch("/api/ai_move", { method: "POST" });
  const data = await res.json();

  if (!res.ok) {
    // pas une erreur grave, juste afficher en console
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

function setModePill(state){
  const pill = document.getElementById("turnPill");
  pill.innerHTML = "";

  const type = String(state.type_partie || "").toUpperCase();
  const modeTxt = type.includes("IA") ? "J vs IA" : "J vs J";

  const dot = document.createElement("span");
  dot.className = "dot";
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

  const st = state.status || (state.game_over ? "TERMINEE" : (state.id_partie ? "EN_COURS" : "Aucune partie"));
  document.getElementById("statusTxt").textContent = st;

  document.getElementById("hint").textContent =
    state.game_over ? "Partie terminée. Lance une nouvelle partie 👇"
                    : (state.type_partie === "IA" && state.current_player === state.ai_player
                       ? "Tour de l’IA…"
                       : "Survole une colonne puis clique 👇");

  // update message card with turn/victory info and color-coded name
  const msg = document.getElementById("message");
  if (msg) {
    if (state.game_over) {
      const winner = nameFor(state.current_player);
      const cls = state.current_player === "R" ? "red" : "yellow";
      msg.hidden = false;
      msg.innerHTML = `🏁 Victoire de <span class=\"${cls}\">${escapeHtml(winner)}</span> !`;
    } else {
      const currentName = nameFor(state.current_player);
      const cls = state.current_player === "R" ? "red" : "yellow";
      msg.hidden = false;
      msg.innerHTML = `Tour de <span class=\"${cls}\">${escapeHtml(currentName)}</span>`;
    }
  }

  renderHeader();
  renderHistory(state);

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
      // both players human
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

  lastState = await getState();
  render(lastState);
});
