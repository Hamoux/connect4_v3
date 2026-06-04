# Connect 4 Intelligent — Plateforme Web IA

Une plateforme web complète de Puissance 4, développée en binôme, combinant moteur IA, multijoueur en ligne, pipeline de scraping de parties réelles et modèle de Machine Learning entraîné sur des données réelles.

---

## Fonctionnalités

### Modes de jeu
- **Joueur vs IA** — affrontez le moteur Minimax avec trois niveaux de difficulté (facile / moyen / difficile)
- **Local (J vs J)** — deux joueurs sur la même machine, entièrement dans le navigateur
- **En ligne (J vs J)** — création de salle et matchmaking automatique via WebSocket

### Moteur IA — Minimax
- Algorithme **Minimax** avec **élagage alpha-bêta** pour couper les branches inutiles
- **Table de transposition** (cache de positions déjà évaluées) pour éviter les recalculs et accélérer la recherche
- Profondeur de recherche variable selon le niveau de difficulté

### Pipeline de données BGA
- **Scraping automatique** de parties réelles depuis Board Game Arena via **Selenium**
- Gestion de profil Chrome dédié pour contourner les limitations de session
- **Déduplication par signature canonique** : chaque partie est identifiée de façon unique pour éviter les doublons en base
- Importation des parties d'amis (`import_friend_game.py`, `import_friend_games.py`)
- Stockage complet des coups joués dans PostgreSQL avec support de replay

### Pipeline Machine Learning
- Entraînement d'un modèle ML sur les parties scrappées (`connect4_ml_pipeline/`)
- Intégration du modèle dans l'interface via `ai_model_bridge.py`
- Interface de jeu avec recommandations ML (`ui_with_ml.py`)
- Génération de parties synthétiques pour l'entraînement (`generate_games_fast_safe.py`)

---

## Architecture

```
connect4_v3/
├── Webapp/                    # Interface Flask (routes, templates, WebSocket)
├── ai.py                      # Moteur Minimax + alpha-bêta + table de transposition
├── ai_model_bridge.py         # Pont entre le modèle ML et l'interface
├── game.py                    # Logique du jeu (grille, victoire, coups valides)
├── main.py                    # Point d'entrée principal
├── bga_import.py              # Import de parties BGA
├── bga_puppet.py              # Automatisation Selenium BGA
├── scrape_replay_selenium_patched_v3.py  # Scraping des replays BGA
├── explorer_tool_signature.py # Génération de signatures canoniques
├── connect4_ml_pipeline/      # Pipeline ML complet
├── db/                        # Schéma et configuration PostgreSQL
├── scraped_moves/             # Données de parties scrappées
└── utils/                     # Utilitaires divers
```

---

## Stack technique

| Couche | Technologies |
|--------|-------------|
| Backend | Python, Flask, API REST |
| Frontend | JavaScript, HTML, CSS |
| Base de données | PostgreSQL, psycopg2 |
| IA | Minimax, élagage alpha-bêta, table de transposition |
| Machine Learning | Pipeline ML, modèle entraîné sur données BGA |
| Scraping | Selenium, Chrome headless |
| Temps réel | WebSocket (multijoueur en ligne) |

---

## Installation

```bash
# Cloner le dépôt
git clone https://github.com/Hamoux/connect4_v3.git
cd connect4_v3

# Installer les dépendances
pip install -r requirements.txt

# Configurer les variables d'environnement
cp .env.example .env
# Remplir les informations de connexion PostgreSQL dans .env

# Lancer le serveur
python Webapp/app.py
```

Ouvrir http://localhost:5000 dans le navigateur.

---

## Auteurs

- Hamou Djellab
- Celina Ikhlef


