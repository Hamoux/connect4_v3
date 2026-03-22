"""
Configuration centralisée — jamais de secrets en dur dans le dépôt.

Utiliser des variables d'environnement ou un fichier .env (non versionné).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


@dataclass(frozen=True)
class PipelineConfig:
    """Paramètres pipeline / BGA / génération."""

    project_root: Path
    # BGA (ne jamais committer les mots de passe)
    bga_username: str
    bga_password: str
    # Scraping
    top_n_players: int
    request_delay_min: float
    request_delay_max: float
    max_retries: int
    selenium_implicit_wait: int
    chrome_profile_dir: str | None
    # Bot
    bot_daily_game_cap: int
    bot_depth: int
    # AI vs AI
    ai_default_depth: int
    ai_parallel_workers: int
    ai_games_per_worker: int
    ai_epsilon_random: float
    # DB
    rows: int
    cols: int


def load_config() -> PipelineConfig:
    root = Path(__file__).resolve().parent.parent
    return PipelineConfig(
        project_root=root,
        bga_username=os.getenv("BGA_USERNAME", ""),
        bga_password=os.getenv("BGA_PASSWORD", ""),
        top_n_players=_env_int("BGA_TOP_N_PLAYERS", 30),
        request_delay_min=_env_float("BGA_DELAY_MIN", 1.2),
        request_delay_max=_env_float("BGA_DELAY_MAX", 3.5),
        max_retries=_env_int("BGA_MAX_RETRIES", 4),
        selenium_implicit_wait=_env_int("BGA_SELENIUM_IMPLICIT_WAIT", 10),
        chrome_profile_dir=os.getenv("BGA_CHROME_PROFILE_DIR") or str(root / "selenium_profile"),
        bot_daily_game_cap=_env_int("BGA_BOT_DAILY_CAP", 50),
        bot_depth=_env_int("BGA_BOT_DEPTH", 6),
        ai_default_depth=_env_int("AI_GEN_DEPTH", 4),
        ai_parallel_workers=_env_int("AI_GEN_WORKERS", max(1, (os.cpu_count() or 4) - 1)),
        ai_games_per_worker=_env_int("AI_GEN_GAMES_PER_WORKER", 20),
        ai_epsilon_random=_env_float("AI_GEN_EPSILON", 0.0),
        rows=_env_int("CONNECT4_ROWS", 9),
        cols=_env_int("CONNECT4_COLS", 9),
    )
