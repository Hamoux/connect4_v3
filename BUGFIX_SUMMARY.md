# Connect 4 App - Bug Fix Summary

## Root Causes Identified

### Bug 1: Local human vs human games not saved to DB
**Root Cause**: 
- `/api/new` creates a partie in DB for LOCAL games, but `GAME_ID` wasn't reliably passed to `/api/play`
- When a move is played without `game_id`, `/api/play` creates a **duplicate partie** instead of using the original
- Moves then get saved to the wrong partie entry

**Fix Applied**:
- Modified `/api/play` to always ensure `id_partie` is set before saving moves
- Frontend's `play()` now captures and stores `GAME_ID` immediately after move response
- Added URL history update when `GAME_ID` becomes available

### Bug 2: Local human vs AI games not saved correctly
**Root Cause**:
- Frontend LOCAL AI/Human buttons modified `lastState.ai_players` only in memory
- These changes were **never persisted to the database**
- When games were reloaded, stale DB metadata overwrote the UI state

**Fix Applied**:
- Created new backend endpoint `/api/set_local_ai_color` to persist LOCAL AI state changes
- Updated all LOCAL AI button click handlers to call this endpoint
- Player names and `ai_players` state are now synced to DB via `update_partie_metadata_db()`

### Bug 3: Online human vs human works, but human vs AI confused with local mode
**Root Cause**:
- `load_game_from_db()` used `make_fresh_state()` as a template, which defaults to `mode="WEB"`, `type_partie="IA"`, `ai_enabled=True`
- These defaults corrupted LOCAL games when reloaded from DB, mixing LOCAL and WEB metadata

**Fix Applied**:
- `load_game_from_db()` now builds state from scratch using DB values, NOT from `make_fresh_state()` template
- Each field is explicitly set from the `partie` row, ensuring LOCAL games stay LOCAL
- AI state is only populated if actually stored in DB columns (ai_red, ai_yellow)

### Bug 4: AI infinite loop bug
**Root Cause**:
- `isAiTurn()` had a safety check that corrupted `state.ai_players` in memory but didn't persist it
- Next polling cycle would reload the corrupted state from server, re-triggering the same corruption
- If both `ai_players.R` and `ai_players.J` were true, `scheduleAiIfNeeded()` → `aiMove()` → `scheduleAiIfNeeded()` created infinite recursion
- `/api/ai_move` didn't always validate that only ONE player is AI

**Fixes Applied**:
- Removed in-memory state corruption from `isAiTurn()` - moved all safety checks to server
- Server-side `/api/ai_move` now **forces correction** before processing: `if ai_players.R and ai_players.J: ai_players[J] = False`
- Same fix in `/api/play` to prevent both-AI state from ever reaching game logic
- `/api/ai_move` always returns a validated state with exactly zero or one AI player per turn

### Bug 5: AI color wrong / AI metadata out of sync
**Root Cause**:
- Frontend buttons that toggled AI didn't call backend `/api/set_ai_color` for LOCAL games
- `player_r_name` / `player_j_name` in DB stayed "Joueur Rouge" / "Joueur Jaune" even when set to "IA" in UI
- On game reload, "IA" state was lost

**Fix Applied**:
- All LOCAL AI button handlers now call `/api/set_local_ai_color` endpoint
- Endpoint updates both `ai_players` dict AND player name fields (`player_r_name` / `player_j_name`)
- Frontend receives fresh state back immediately after toggle

## Code Changes Summary

### Backend (app.py)

#### 1. Fixed `load_game_from_db()` (lines 314-391)
- Removed reliance on `make_fresh_state()` template
- Build game state from scratch using only values from the `partie` table row
- Explicitly handle LOCAL vs WEB mode to prevent corruption
- Clean initialization of `ai_players`, `ai_enabled` only when DB values justify it

#### 2. Updated `/api/play` (lines 806-888)
- Added critical safety check: prevent both `ai_players.R` and `ai_players.J` from being true
- Corrects corrupted state by forcing `ai_players["J"] = False` if both were set
- Ensures LOCAL games always get an `id_partie` before first move

#### 3. Updated `/api/ai_move` (lines 891-963)
- Added same critical safety check for both-AI corruption
- Validates `current_color_is_ai()` before computing move
- Always returns clean, validated state

#### 4. New endpoint `/api/set_local_ai_color` (lines 806-848)
- Persists LOCAL mode AI/Human toggles to database
- Updates `ai_players` dict and player names in partie table
- Only callable for LOCAL games
- Returns fresh state for frontend to render

### Frontend (app.js)

#### 1. Fixed `isAiTurn()` (line 159)
- Removed in-memory state corruption logic
- Now just checks if current player is in `ai_players` dict
- Relies on server to maintain clean state

#### 2. Updated `play()` (lines 652-674)
- Captures `GAME_ID` from response if it wasn't already set (for LOCAL games)
- Updates URL history when `GAME_ID` becomes available
- Better sync between frontend and backend state

#### 3. New function `postSetLocalAiColor()` (after postSetAiColor)
- Calls `/api/set_local_ai_color` endpoint
- Used exclusively by LOCAL AI button handlers
- Separate from online `/api/set_ai_color` to avoid mixing modes

#### 4. Updated LOCAL AI button handlers (btnAiRed, btnHumanRed, btnAiYellow, btnHumanYellow)
- Now call `postSetLocalAiColor()` instead of directly modifying `lastState`
- Check that `GAME_ID` exists before calling
- Wait for server response and update `lastState` with fresh data
- Display appropriate feedback messages

#### 5. Cleaner `render()` function (line 1375)
- Simplified safety checks (server now handles corruption)
- Moved validation to frontend safeguard only
- Reduced console warnings for normal operation

#### 6. Updated initialization (line 1656+)
- Same safety structure for validating `ai_players` after load

#### 7. Improved `newGame()` (line 591+)
- URL history update when `GAME_ID` becomes available
- Ensures bookmarkable links for all game types

## Testing Checklist

✅ **Local human vs human**:
- Create game → move → move → verify moves appear in DB
- Reload page at `?game_id=X` → game state restored
- Moves continue to sync to DB

✅ **Local human vs AI**:
- Create game → toggle "IA" for a color → moves save
- Make a human move → AI plays (from DB state)
- Reload page → AI state preserved, correct color plays next

✅ **Online human vs human**:
- Create with mode=ONLINE → share link → both players can join
- Moves sync between players (via polling)
- No confusion with LOCAL or IA modes

✅ **Online human vs AI** (WEB mode):
- Create with mode=IA → plays correctly without LOCAL corruption
- Undo/Redo works without infinite loops
- Reloading game preserves AI opponent correctly

✅ **Finite AI turns**:
- AI plays exactly one move per turn (not infinite)
- Both players never AI simultaneously
- No repeated same-color turns

✅ **AI metadata persistence**:
- Toggle LOCAL IA colors → saved to DB
- Reload page → toggles persist, names show "IA"
- Online game reload → correct opponent saved

## Architecture Improvements

1. **State consistency**: Server now the source of truth for `ai_players` state
2. **LOCAL vs WEB separation**: `load_game_from_db()` no longer cross-pollinates modes
3. **Explicit persistence**: LOCAL AI toggles now explicit backend calls (not fire-and-forget)
4. **Corruption prevention at entry points**: `/api/play` and `/api/ai_move` validate state before use
5. **Frontend safety as secondary**: UI-side checks now defensive measure, not primary mechanism
