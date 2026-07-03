"""
deepsearch_article.py — Recherche web enrichie pour le Journal CCT.

Appelle le Gateway /v1/deep-research avec contrainte géographique Andalousie.
Cache les résultats 30 jours dans deepsearch_cache (alejandro_db).
Nettoyage automatique des entrées expirées à chaque nouvelle insertion.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
import psycopg2
import psycopg2.extras

logger = logging.getLogger("cct-journal.deepsearch")

# ─── Gateway Endpoint ───────────────────────────────────────────────────────

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
DEEPSEARCH_ENDPOINT = f"{GATEWAY_URL}/v1/deep-research"
DEFAULT_MAX_TOKENS = 4000
CACHE_TTL_DAYS = 30

# ─── Contrainte géographique Andalousie (injectée dans le prompt) ──────────

ANDALUSIA_CONSTRAINT = (
    "IMPORTANT : Tu es un journaliste spécialisé sur l'Andalousie et la Costa Tropical. "
    "Utilise UNIQUEMENT des sources andalouses (Ideal.es, Granada Hoy, Diario de Almería, "
    "Junta de Andalucía, Diputación de Granada, AEMET, IECA, etc.) et des sources "
    "nationales espagnoles. Tu ne mentionnes QUE des lieux, personnes et faits liés "
    "à l'Andalousie, à la province de Grenade et à la Costa Tropical. "
    "Ignore les résultats qui ne concernent pas l'Andalousie."
)

# ─── DB ─────────────────────────────────────────────────────────────────────

def _get_db_url() -> str:
    """Récupère DATABASE_URL comme publish.py."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            import subprocess
            result = subprocess.run(
                ["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                capture_output=True, text=True, timeout=5
            )
            line = result.stdout.strip()
            if line:
                db_url = line.split("=", 1)[1].strip("'\"")
        except Exception as e:
            logger.warning(f"Impossible de lire DATABASE_URL: {e}")
    if not db_url:
        db_url = "postgresql:///alejandro_db"
    return db_url


def _make_cache_key(topic: dict, date_str: str) -> str:
    """Génère une clé de cache unique pour un sujet + date."""
    raw = f"{topic['id']}:{date_str}:{topic.get('domain', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _check_cache(cache_key: str) -> Optional[dict]:
    """Vérifie si une entrée cache valide (< 30 jours) existe."""
    try:
        conn = psycopg2.connect(_get_db_url(), connect_timeout=5)
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT response_json FROM deepsearch_cache "
                "WHERE cache_key = %s "
                "AND created_at > NOW() - INTERVAL %s",
                (cache_key, f"{CACHE_TTL_DAYS} days")
            )
            row = cur.fetchone()
            if row:
                logger.info(f"✅ Cache HIT: {cache_key}")
                return row[0]  # déjà un dict (JSONB → psycopg2)
        conn.close()
    except Exception as e:
        logger.warning(f"⚠️ Cache check error: {e}")
    return None


def _save_cache(cache_key: str, data: dict):
    """Sauvegarde dans le cache et nettoie les entrées expirées."""
    try:
        conn = psycopg2.connect(_get_db_url(), connect_timeout=5)
        with conn, conn.cursor() as cur:
            # Upsert
            cur.execute("""
                INSERT INTO deepsearch_cache (cache_key, response_json)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (cache_key)
                DO UPDATE SET response_json = %s::jsonb, created_at = NOW()
            """, (cache_key, json.dumps(data), json.dumps(data)))
            # Cleanup
            cur.execute("DELETE FROM deepsearch_cache WHERE created_at < NOW() - INTERVAL '30 days'")
        conn.close()
        logger.info(f"💾 Cache SAVED: {cache_key}")
    except Exception as e:
        logger.warning(f"⚠️ Cache save error: {e}")


# ─── Deep Search Call ────────────────────────────────────────────────────────

def _build_query(topic: dict) -> str:
    """Construit la requête de recherche avec contrainte géographique Andalousie."""
    domain = topic.get("domain", "")
    title = topic["title"]
    angle = topic.get("angle", "")
    context = topic.get("context", "")

    prompt = f"RECHERCHE POUR ARTICLE DE PRESSE\n\n"
    prompt += f"Domaine : {domain}\n"
    prompt += f"Sujet : {title}\n"
    if angle:
        prompt += f"Angle journalistique : {angle}\n"
    if context:
        prompt += f"Contexte : {context[:1500]}\n"
    prompt += f"\n{ANDALUSIA_CONSTRAINT}\n\n"
    prompt += (
        "Cherche des informations actuelles, des données chiffrées, "
        "des faits vérifiables, des citations d'experts ou d'institutions. "
        "Résume les découvertes en 2-3 paragraphes avec les sources."
    )
    return prompt


def deep_search(topic: dict, date_str: str | None = None, force: bool = False) -> dict:
    """
    Effectue une recherche web enrichie pour un sujet du Journal CCT.
    
    Args:
        topic: Le sujet (depuis topics.yaml)
        date_str: Date au format YYYY-MM-DD (défaut: aujourd'hui)
        force: Si True, ignore le cache
    
    Returns:
        {
            "text": str,          # Résultat de la recherche
            "sources": list,      # Sources web citées
            "cached": bool,       # True si réponse du cache
            "in_tokens": int,
            "out_tokens": int,
            "cost_eur": float,
        }
    """
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache_key = _make_cache_key(topic, date_str)

    # Vérifier le cache
    if not force:
        cached = _check_cache(cache_key)
        if cached:
            return {
                "text": cached.get("text", ""),
                "sources": cached.get("sources", []),
                "cached": True,
                "in_tokens": cached.get("in_tokens", 0),
                "out_tokens": cached.get("out_tokens", 0),
                "cost_eur": cached.get("cost_eur", 0),
            }

    # Cache miss → appeler le Gateway
    query = _build_query(topic)
    logger.info(f"🔍 DeepSearch: {topic['id']} — {topic['title'][:60]}")
    
    payload = {
        "contents": query,
        "system_instruction": "Eres un periodista de investigación especializado en Andalucía y la Costa Tropical. Busca datos concretos, cifras verificables y fuentes actuales. Responde en español.",
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0.1,
    }

    try:
        r = httpx.post(
            DEEPSEARCH_ENDPOINT,
            json=payload,
            timeout=90,
        )
        r.raise_for_status()
        data = r.json()

        result = {
            "text": data.get("text", ""),
            "sources": data.get("sources", []),
            "cached": False,
            "in_tokens": data.get("in_tokens", 0),
            "out_tokens": data.get("out_tokens", 0),
            "cost_eur": data.get("cost_eur", 0),
        }

        logger.info(f"   → {len(result['text'])} chars, {len(result['sources'])} sources, {result['cost_eur']:.5f}€")

        # Sauvegarder dans le cache
        _save_cache(cache_key, result)
        return result

    except httpx.HTTPStatusError as e:
        logger.error(f"❌ DeepSearch HTTP {e.response.status_code}: {e.response.text[:200]}")
        return {"text": "", "sources": [], "cached": False, "in_tokens": 0, "out_tokens": 0, "cost_eur": 0}
    except Exception as e:
        logger.error(f"❌ DeepSearch error: {e}")
        return {"text": "", "sources": [], "cached": False, "in_tokens": 0, "out_tokens": 0, "cost_eur": 0}
