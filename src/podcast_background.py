"""podcast_background.py — Génère les podcasts en arrière-plan après la publication.

Lancé par app.py via subprocess.Popen pour éviter que le TTS (lent) ne bloque
le pipeline principal et ne cause un timeout systemd.

Usage: python3 podcast_background.py <slug>

Lit l'article depuis la DB, génère les podcasts ES/FR/EN,
et met à jour audio_url dans la DB.
"""
from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

# Ajouter le chemin src pour les imports
SRC_DIR = Path(__file__).parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import psycopg2

# Config logging basique — écrit dans un fichier dédié
LOG_DIR = Path("/srv/cct-journal/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "podcast-background.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("podcast.background")


def _get_pg_url() -> str:
    """Récupère DATABASE_URL."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            import subprocess as _sp
            r = _sp.run(
                ["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                capture_output=True, text=True, timeout=5
            )
            line = r.stdout.strip()
            if line:
                db_url = line.split("=", 1)[1].strip("'\"")
        except Exception:
            pass
    if not db_url:
        db_url = "postgresql:///alejandro_db"
    return db_url


def _get_article(slug: str) -> dict | None:
    """Récupère le contenu trilingue de l'article depuis la DB."""
    try:
        conn = psycopg2.connect(_get_pg_url(), connect_timeout=5)
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT title, title_es, title_en, "
                "content, content_es, content_en "
                "FROM articles WHERE slug = %s",
                (slug,)
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "title_fr": row[0] or "",
                "title_es": row[1] or "",
                "title_en": row[2] or "",
                "text_fr": row[3] or "",
                "text_es": row[4] or "",
                "text_en": row[5] or "",
            }
        logger.warning(f"⚠️ Article '{slug}' introuvable en DB")
        return None
    except Exception as e:
        logger.error(f"❌ DB error: {e}")
        return None


def main() -> int:
    if len(sys.argv) < 2:
        logger.error("Usage: python3 podcast_background.py <slug>")
        return 1

    slug = sys.argv[1]
    logger.info(f"🎙️ Podcast background start pour '{slug}'")

    article = _get_article(slug)
    if not article:
        logger.error(f"❌ Article '{slug}' introuvable — abandon")
        return 1

    # Importer le module de podcast
    from article_podcast import generate_article_podcast

    # Contenu et titre ES pour la source (les autres langues sont des adaptations)
    es_text = article["text_es"]
    es_title = article["title_es"]

    if not es_text or len(es_text) < 200:
        logger.warning(f"⚠️ Texte ES trop court ({len(es_text)}c) — abandon")
        return 1

    # Générer pour chaque langue (ES d'abord car c'est la langue source)
    langs = ["es", "fr", "en"]
    for lang in langs:
        # Récupérer le texte adapté à la langue
        if lang == "es":
            text = es_text
            title = es_title
        elif lang == "fr":
            text = article["text_fr"] or es_text
            title = article["title_fr"] or es_title
        else:  # en
            text = article["text_en"] or es_text
            title = article["title_en"] or es_title

        if not text or len(text) < 200:
            logger.info(f"   ⏭️ {lang.upper()} — texte trop court, ignoré")
            continue

        try:
            logger.info(f"   🎙️ Génération {lang.upper()}...")
            result = generate_article_podcast(text, title, slug, lang=lang)
            if result:
                logger.info(f"   ✅ {lang.upper()}: {result['url']} ({result['size']}KB, ~{result['duration_s']}s)")
            else:
                logger.info(f"   ⏭️ {lang.upper()} — échec ou ignoré")
        except Exception as e:
            logger.warning(f"   ⚠️ {lang.upper()} error: {e}")

    logger.info(f"✅ Podcast background terminé pour '{slug}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
