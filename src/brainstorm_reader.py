"""
brainstorm_reader.py — Lit les brainstormings Hermes (Skill B) depuis article_brainstorms.

Utilisé par le pipeline article pour savoir si un brainstorming a déjà été
produit par le cron Hermes LLM (05h00). Si oui, on l'utilise.
Sinon, on appelle article_cortex directement (Skill A, mode dégradé).
"""
from __future__ import annotations
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger("cct-journal.brainstorm-reader")

PG_DSN = "host=127.0.0.1 dbname=alejandro_db user=alejandro password=AndaluciaRocks2025"


def get_brainstorm(topic_id: str) -> Optional[Dict]:
    """
    Récupère le brainstorming Hermes pour un topic_id donné.
    Marque le brainstorming comme lu (read_at + used).

    Returns:
        Dict avec les champs du cortex, ou None si pas trouvé
    """
    try:
        import psycopg2
        conn = psycopg2.connect(PG_DSN)
        cur = conn.cursor()

        cur.execute(
            "SELECT topic_id, refined_angle, dangers, h2_structure, "
            "       image_types, key_points, anti_duplicate_note, raw_response, "
            "       model_used, source "
            "FROM article_brainstorms "
            "WHERE topic_id = %s AND used = FALSE "
            "ORDER BY created_at DESC LIMIT 1",
            (topic_id,)
        )
        row = cur.fetchone()

        if not row:
            conn.close()
            return None

        result = {
            "success": True,
            "refined_angle": row[1] or "",
            "dangers": list(row[2]) if row[2] else [],
            "h2_structure": list(row[3]) if row[3] else [],
            "image_types": list(row[4]) if row[4] else [],
            "key_points": list(row[5]) if row[5] else [],
            "anti_duplicate_note": row[6] or "",
            "raw_response": row[7] or "",
            "model_used": row[8] or "",
            "source": row[9] or "hermes",
        }

        # Marquer comme lu
        cur.execute(
            "UPDATE article_brainstorms SET read_at = %s, used = TRUE "
            "WHERE topic_id = %s",
            (datetime.now(timezone.utc), topic_id)
        )
        conn.commit()
        conn.close()

        logger.info(f"📖 Brainstorm Hermes trouvé pour {topic_id} — source={result['source']}")
        return result

    except Exception as e:
        logger.warning(f"⚠️ Brainstorm reader error: {e}")
        return None


def write_brainstorm(topic_id: str, topic_title: str, domain: str,
                     result: Dict, source: str = "cortex") -> bool:
    """
    Écrit un brainstorming dans article_brainstorms.
    Utilisé par le cron Hermes (Skill B) ou par le cortex intégré (Skill A).
    UPSERT : si le topic_id existe déjà, on met à jour.
    """
    try:
        import psycopg2
        from psycopg2.extras import Json
        conn = psycopg2.connect(PG_DSN)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO article_brainstorms
                (topic_id, topic_title, domain, refined_angle, dangers,
                 h2_structure, image_types, key_points, anti_duplicate_note,
                 raw_response, model_used, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (topic_id) DO UPDATE SET
                refined_angle = EXCLUDED.refined_angle,
                dangers = EXCLUDED.dangers,
                h2_structure = EXCLUDED.h2_structure,
                image_types = EXCLUDED.image_types,
                key_points = EXCLUDED.key_points,
                anti_duplicate_note = EXCLUDED.anti_duplicate_note,
                raw_response = EXCLUDED.raw_response,
                model_used = EXCLUDED.model_used,
                source = EXCLUDED.source,
                created_at = NOW(),
                read_at = NULL,
                used = FALSE
        """, (
            topic_id, topic_title, domain,
            result.get("refined_angle", ""),
            list(result.get("dangers", [])),
            list(result.get("h2_structure", [])),
            list(result.get("image_types", [])),
            list(result.get("key_points", [])),
            result.get("anti_duplicate_note", ""),
            result.get("raw_response", ""),
            result.get("model_used", ""),
            source,
        ))
        conn.commit()
        conn.close()
        logger.info(f"💾 Brainstorm écrit pour {topic_id} (source={source})")
        return True

    except Exception as e:
        logger.warning(f"⚠️ Brainstorm write error: {e}")
        return False
