"""pipeline_cache.py — Cache inter-actes pour le pipeline 3 actes.
Chaque acte écrit son artefact validé. L'orchestrateur lit les artefacts.
"""
import json
from pathlib import Path
from typing import Optional

CACHE_DIR = Path("/tmp/cache/journal-cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def save_step(step_name: str, data: dict) -> Path:
    """Sauvegarde un artefact validé. Retourne le chemin."""
    path = CACHE_DIR / f"{step_name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def load_step(step_name: str) -> Optional[dict]:
    """Charge un artefact validé. Retourne None si absent."""
    path = CACHE_DIR / f"{step_name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
    return None


def step_exists(step_name: str) -> bool:
    """Vérifie si un artefact existe."""
    return (CACHE_DIR / f"{step_name}.json").exists()


def clear_cache():
    """Vide tous les artefacts du cache."""
    for f in CACHE_DIR.glob("step*.json"):
        f.unlink()
    for f in CACHE_DIR.glob("act*.json"):
        f.unlink()
