"""
pick.py — choisit le sujet du jour via rotation anti-doublon.

Stratégie :
- Lit topics.yaml (pool de sujets).
- Compare avec historique (documents du domain cronicas-alejandro sur HISTORY_WINDOW_DAYS).
- Sélectionne le sujet le plus ancien non-utilisé (ou round-robin pur si aucun n'a été publié).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import psycopg2
import yaml

from config import TOPICS_PATH, HISTORY_WINDOW_DAYS, PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PWD_PATH

logger = logging.getLogger("cct-journal.pick")


def _pg():
    pwd = PG_PWD_PATH.read_text().strip()
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=pwd,
        connect_timeout=10,
    )


def _load_topics() -> list[dict]:
    data = yaml.safe_load(TOPICS_PATH.read_text())
    return data.get("topics", [])


def _recent_topic_ids() -> dict[str, datetime]:
    """Retourne {topic_id: last_published_date} pour les X derniers jours."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_WINDOW_DAYS)
    result: dict[str, datetime] = {}
    try:
        with _pg() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT meta->>'topic_id' AS topic_id, max(date_publication) AS last_pub
                FROM documents
                WHERE meta->>'pipeline' = 'cct-journal'
                  AND date_publication >= %s
                  AND meta->>'topic_id' IS NOT NULL
                GROUP BY meta->>'topic_id'
            """, (cutoff,))
            for tid, last_pub in cur.fetchall():
                result[tid] = last_pub
    except Exception as e:
        logger.warning(f"History lookup failed (premier run ?): {e}")
    return result


def pick_topic(prefer_domain: Optional[str] = None) -> dict:
    """Sélectionne le sujet du jour.

    Retourne un dict avec id, domain, title, angle, context, tags.
    Stratégie :
      1. Filtre pool sur domain préféré si fourni, sinon tout.
      2. Priorité aux sujets jamais publiés, sinon au plus ancien.
    """
    topics = _load_topics()
    if not topics:
        raise ValueError("topics.yaml est vide")

    history = _recent_topic_ids()

    if prefer_domain:
        candidates = [t for t in topics if t.get("domain") == prefer_domain]
        if not candidates:
            logger.warning(f"Aucun topic pour domain={prefer_domain}, fallback global")
            candidates = topics
    else:
        candidates = topics

    # Priorité : jamais publiés → publiés les plus anciens
    never_used = [t for t in candidates if t["id"] not in history]
    if never_used:
        pick = never_used[0]
        logger.info(f"Topic choisi (jamais publié): {pick['id']}")
        return pick

    # Sinon, le plus ancien
    candidates.sort(key=lambda t: history.get(t["id"]))
    pick = candidates[0]
    last = history[pick["id"]]
    age = (datetime.now(timezone.utc) - last).days
    logger.info(f"Topic choisi (publié il y a {age}j): {pick['id']}")
    return pick


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    t = pick_topic()
    print(f"=== Topic du jour ===")
    print(f"  id     : {t['id']}")
    print(f"  domain : {t['domain']}")
    print(f"  title  : {t['title']}")
    print(f"  tags   : {t.get('tags', [])}")
