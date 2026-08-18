#!/usr/bin/env python3
"""
translation_cache.py — Cache persistant entre runs pour les traductions du Journal CCT.

Structure:
  /srv/cct-journal/cache/v1/<slug>/
    es/                     - article ES original (fichier unique: article_es.txt)
    fr/sections/            - 1 fichier par section H2 traduite (section_0.txt, section_1.txt, ...)
    en/sections/            - idem pour anglais
    fr/verified.json        - timestamp de la dernière vérification DeepSeek V4 Pro
    en/verified.json        - idem
    meta.json               - date de création, nombre de sections, modèle utilisé

Toutes les écritures sont atomiques (écriture dans un fichier temporaire, puis rename).
Stdlib only (os, json, pathlib).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

# ─── CONSTANTES ────────────────────────────────────────────────────────
CACHE_ROOT = Path("/srv/cct-journal/cache/v1")


# ─── FONCTIONS INTERNES ────────────────────────────────────────────────

def _slug_path(slug: str) -> Path:
    """Retourne le chemin du répertoire de cache pour un slug donné."""
    # Nettoyage de sécurité: éviter les path traversals
    safe = slug.replace("/", "_").replace("\\", "_").replace("..", "_")
    return CACHE_ROOT / safe


def _section_path(slug: str, lang: str, section_index: int) -> Path:
    """Retourne le chemin du fichier de section traduite."""
    return _slug_path(slug) / lang / "sections" / f"section_{section_index}.txt"


def _verified_path(slug: str, lang: str) -> Path:
    """Retourne le chemin du fichier verified.json pour une langue."""
    return _slug_path(slug) / lang / "verified.json"


def _meta_path(slug: str) -> Path:
    """Retourne le chemin du meta.json pour un slug."""
    return _slug_path(slug) / "meta.json"


def _es_path(slug: str) -> Path:
    """Retourne le chemin du fichier article ES original."""
    return _slug_path(slug) / "es" / "article_es.txt"


def _atomic_write(path: Path, content: str) -> None:
    """
    Écriture atomique: écrit dans un fichier temporaire, puis rename.
    Garantit qu'on ne lit jamais un fichier à moitié écrit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.rename(tmp_path, str(path))
    except BaseException:
        # Nettoyage en cas d'erreur
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _ensure_dir(path: Path) -> None:
    """Crée le répertoire parent si nécessaire."""
    path.parent.mkdir(parents=True, exist_ok=True)


def _compute_slug_from_title(title_es: str) -> str:
    """Calcule un slug à partir du titre ES (même logique que run_pipeline.py)."""
    import re
    slug = re.sub(r"[^a-z0-9-]", "", title_es.lower().replace(" ", "-")[:45]).strip("-")
    return slug


# ─── FONCTIONS EXPORTÉES ─────────────────────────────────────────────

def get_es(slug: str) -> Optional[str]:
    """
    Récupère l'article ES original depuis le cache.

    Args:
        slug: Slug unique de l'article.

    Returns:
        Le texte de l'article ES, ou None si absent.
    """
    path = _es_path(slug)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    return None


def save_es(slug: str, text: str) -> None:
    """
    Sauvegarde l'article ES original dans le cache.

    Args:
        slug: Slug unique de l'article.
        text: Texte complet de l'article ES.
    """
    path = _es_path(slug)
    _atomic_write(path, text)


def get_translation(slug: str, lang: str, section_index: int) -> Optional[str]:
    """
    Récupère une section traduite depuis le cache.

    Args:
        slug: Slug unique de l'article.
        lang: Code langue ('fr' ou 'en').
        section_index: Index de la section H2 (0-indexé).

    Returns:
        Le texte traduit de la section, ou None si absent.
    """
    path = _section_path(slug, lang, section_index)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    return None


def save_translation(slug: str, lang: str, section_index: int, text: str) -> None:
    """
    Sauvegarde une section traduite dans le cache.

    Args:
        slug: Slug unique de l'article.
        lang: Code langue ('fr' ou 'en').
        section_index: Index de la section H2 (0-indexé).
        text: Texte traduit de la section.
    """
    path = _section_path(slug, lang, section_index)
    _atomic_write(path, text)


def get_verified(slug: str, lang: str) -> bool:
    """
    Vérifie si la traduction d'une langue a été validée par DeepSeek V4 Pro.

    Args:
        slug: Slug unique de l'article.
        lang: Code langue ('fr' ou 'en').

    Returns:
        True si la vérification DeepSeek a été effectuée, False sinon.
    """
    path = _verified_path(slug, lang)
    return path.exists()


def set_verified(slug: str, lang: str) -> None:
    """
    Marque une traduction comme vérifiée par DeepSeek V4 Pro.

    Écrit un JSON avec le timestamp actuel.

    Args:
        slug: Slug unique de l'article.
        lang: Code langue ('fr' ou 'en').
    """
    path = _verified_path(slug, lang)
    data = {
        "verified": True,
        "verified_at": time.time(),
        "verified_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "model": "deepseek-v4-pro",
        "lang": lang,
    }
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def invalidate_article(slug: str) -> None:
    """
    Supprime tout le cache d'un article.

    Args:
        slug: Slug unique de l'article.
    """
    path = _slug_path(slug)
    if path.exists():
        shutil.rmtree(path)


def get_cache_stats() -> dict:
    """
    Retourne les statistiques du cache.

    Returns:
        Dictionnaire avec:
        - articles_cached: nombre d'articles en cache
        - total_size_bytes: taille totale du cache sur disque
        - estimated_tokens_saved: estimation du nombre de tokens économisés
        - sections_fr_cached: nombre de sections FR en cache
        - sections_en_cached: nombre de sections EN en cache
        - verified_fr: nombre d'articles FR vérifiés
        - verified_en: nombre d'articles EN vérifiés
    """
    if not CACHE_ROOT.exists():
        return {
            "articles_cached": 0,
            "total_size_bytes": 0,
            "estimated_tokens_saved": 0,
            "sections_fr_cached": 0,
            "sections_en_cached": 0,
            "verified_fr": 0,
            "verified_en": 0,
        }

    articles_cached = 0
    total_size = 0
    sections_fr = 0
    sections_en = 0
    verified_fr = 0
    verified_en = 0

    for slug_dir in CACHE_ROOT.iterdir():
        if not slug_dir.is_dir():
            continue
        articles_cached += 1

        # Taille du répertoire
        for f in slug_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size

        # Sections FR
        fr_sections_dir = slug_dir / "fr" / "sections"
        if fr_sections_dir.exists():
            sections_fr += len(list(fr_sections_dir.glob("section_*.txt")))

        # Sections EN
        en_sections_dir = slug_dir / "en" / "sections"
        if en_sections_dir.exists():
            sections_en += len(list(en_sections_dir.glob("section_*.txt")))

        # Vérifications
        if (slug_dir / "fr" / "verified.json").exists():
            verified_fr += 1
        if (slug_dir / "en" / "verified.json").exists():
            verified_en += 1

    # Estimation: ~4 tokens par mot, ~10 mots par section traduite en moyenne
    estimated_tokens = (sections_fr + sections_en) * 4 * 200

    return {
        "articles_cached": articles_cached,
        "total_size_bytes": total_size,
        "estimated_tokens_saved": estimated_tokens,
        "sections_fr_cached": sections_fr,
        "sections_en_cached": sections_en,
        "verified_fr": verified_fr,
        "verified_en": verified_en,
    }


def article_exists(slug: str) -> bool:
    """
    Vérifie si un article est présent dans le cache.

    Args:
        slug: Slug unique de l'article.

    Returns:
        True si l'article ES est en cache.
    """
    return _es_path(slug).exists()


def save_meta(slug: str, meta: dict) -> None:
    """
    Sauvegarde les métadonnées d'un article dans le cache.

    Args:
        slug: Slug unique de l'article.
        meta: Dictionnaire de métadonnées (date, nb_sections, modèle, etc.).
    """
    path = _meta_path(slug)
    _atomic_write(path, json.dumps(meta, ensure_ascii=False, indent=2))


def get_meta(slug: str) -> Optional[dict]:
    """
    Récupère les métadonnées d'un article depuis le cache.

    Args:
        slug: Slug unique de l'article.

    Returns:
        Dictionnaire de métadonnées, ou None si absent.
    """
    path = _meta_path(slug)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None