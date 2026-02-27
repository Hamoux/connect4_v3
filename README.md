# connect4_v3
Connect4 Web Game

## Features

- Play against a computer (easy/medium/hard difficulty).
- Two-player **local** games: play on the same machine without network.
- Two-player **online** games: click "Nouvelle partie" to create or join a room. The server keeps a pool of waiting games and will match you with the first available opponent – you don't even need to exchange links. When you do create a room, the first player gets a random colour and a pop‑up reminds them that they must wait for an adversary before playing; column buttons are disabled until a second user connects. The generated share‑link is still provided and automatically copied for convenience, but either player can simply hit "Nouvelle partie" to start matching. Only the first two distinct browser sessions can occupy a room; a third attempt will be rejected with an error. Moves made while waiting are stored and become visible once the opponent joins.
- All online matches are recorded in a PostgreSQL database with replay support.

## Usage

1. Start the server:
   ```bash
   python Webapp/app.py
   ```
2. Open http://localhost:5000 in your browser.
3. Select a mode from the dropdown:
   - **J vs J (local)** – the game runs entirely in the browser.
   - **J vs J (online)** – a new online room is created; copy/share the URL shown after creating the game.
   - **J vs IA** – play against the AI.
4. When playing online, both players must load the shared link; the server will prevent a third client from joining the same game.


