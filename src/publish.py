"""
publish.py — Insère l'article trilingue dans alejandro_db.articles (table PWA).

Migration depuis knowledge_base.documents vers la table articles.
Le service cct-journal continue de tourner tous les jours à 11h.
"""
from __future__ import annotations
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Dict, Optional

import psycopg2
import psycopg2.extras

from config import PG_HOST, PG_PORT

logger = logging.getLogger("cct-journal.publish")

# ─── Badge IA sur les images ─────────────────────────────────────────────────

_IMAGE_FACTORY_AVAILABLE = False
try:
    import sys as _sys
    _sys.path.insert(0, "/srv/rag-engine")
    from services.image_factory import ImageFactory
    _IMAGE_FACTORY = ImageFactory()
    _IMAGE_FACTORY_AVAILABLE = True
    logger.info("✅ ImageFactory loaded for AI badge")
except Exception as e:
    logger.warning(f"⚠️ ImageFactory not available: {e}")


def apply_ai_badge(image_url: str) -> bool:
    """Ajoute le badge ✨ IA discret sur une image générée."""
    if not _IMAGE_FACTORY_AVAILABLE or not image_url:
        return False
    try:
        _IMAGE_FACTORY._add_ai_watermark(image_url)
        logger.info(f"✨ AI badge applied: {image_url.split('/')[-1]}")
        return True
    except Exception as e:
        logger.warning(f"⚠️ AI badge failed: {e}")
        return False


# Mapping domain → category_id dans alejandro_db
DOMAIN_CATEGORY = {
    "cultura":     "0cbf59b0-1012-47de-b91e-348600680d65",  # Culture & Traditions
    "gastronomie": "047d7527-d161-4c25-a948-3e6f88aa8a9e",  # Gastronomie & Vin
    "patrimonio":  "6e2d8a37-8f99-4fe8-995a-0499ef80f0ff",  # Histoire & Patrimoine
    "naturaleza":  "573075bf-2c0d-4b84-ba64-1a33107fd03d",  # Géographie & Nature
    "costumbres":  "0cbf59b0-1012-47de-b91e-348600680d65",  # Culture & Traditions
    "economia":    "d4e1056a-ddda-45e6-ac0a-42f2e30c8c2b",  # Terroir & Agriculture
}
DEFAULT_CATEGORY = "c3d1056a-ccca-45e6-ac0a-42f2e30c8c2b"  # Enquêtes & Dossiers
ALEJANDRO_AUTHOR = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"


def _pg() -> psycopg2.connection:
    """Connexion à alejandro_db via DATABASE_URL (comme le RAG Engine)."""
    # Récupère DATABASE_URL depuis l'environnement ou depuis le .env du RAG Engine
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # Fallback : lire depuis le fichier .env du RAG Engine
        import subprocess
        try:
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
        # Dernier fallback : connexion peer
        db_url = "postgresql:///alejandro_db"
    
    return psycopg2.connect(db_url, connect_timeout=10)


def _extract_title(markdown: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    return (title or "Crónica de Alejandro", body)


def _slugify(text: str, max_len: int = 80) -> str:
    text = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).lower().strip()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:max_len]


def _word_aware_truncate(text: str, max_len: int = 350) -> str:
    """Tronque au dernier mot complet avant max_len.
    Ne coupe jamais un mot en plein milieu."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # Revenir en arrière jusqu'à trouver un espace (fin de mot)
    last_space = truncated.rfind(" ")
    if last_space > max_len * 0.7:  # Garder au moins 70% de la longueur
        return truncated[:last_space]
    return truncated


def _strip_title_and_hero(markdown: str) -> str:
    """Supprime le # Titre (H1) et la première image hero du début du contenu.
    Ces éléments sont déjà gérés par le layout de la page (title + featured_image_url).
    Supprime toute image (![...](...)) qui apparaît avant le premier H2."""
    lines = markdown.splitlines()
    result = []
    skipped_title = False
    seen_first_h2 = False
    for line in lines:
        stripped = line.strip()
        # Skip le # Title (première occurrence)
        if not skipped_title and stripped.startswith("# ") and not stripped.startswith("## "):
            skipped_title = True
            continue
        # Détecter si on a atteint le premier H2 (les sections)
        if stripped.startswith("## ") and not stripped.startswith("### "):
            seen_first_h2 = True
        # Skip toute image markdown (![...](...)) avant le premier H2
        # (c'est l'hero image, déjà affichée par featured_image_url)
        if not seen_first_h2 and stripped.startswith("!["):
            continue
        result.append(line)
    return "\n".join(result).strip()


def _extract_excerpt(text: str) -> str:
    """Extrait le chapô (premier paragraphe après le titre et l'image hero).
    Fonctionne sur du contenu avec ou sans # Title."""
    lines = text.splitlines()
    for line in lines:
        stripped = line.strip()
        # Skip lignes vides, images markdown, titres
        if not stripped or stripped.startswith("!["):
            continue
        if stripped.startswith("# ") or stripped.startswith("## "):
            continue
        # Premier paragraphe réel
        return _word_aware_truncate(stripped, 350)
    return ""


def publish_trilingual(
    topic: dict,
    translations: Dict[str, str],
    target_date: datetime | None = None,
    featured_image_url: str = "",
    gallery_json: str = "[]",
) -> Dict[str, int]:
    """
    Publie l'article trilingue dans alejandro_db.articles.
    Retourne un dict {lang: doc_id} (simule l'ancien format pour compatibilité).
    """
    target_date = target_date or datetime.now(timezone.utc)
    
    # Extraire les textes par langue
    es_text = translations.get("es", "")
    fr_text = translations.get("fr", "")
    en_text = translations.get("en", "")
    
    if not es_text:
        logger.error("❌ Aucun texte ES — publication impossible")
        return {}
    
    # Extraire titres et corps
    title_es, body_es = _extract_title(es_text)
    title_fr, body_fr = _extract_title(fr_text) if fr_text else (title_es, "")
    title_en, body_en = _extract_title(en_text) if en_text else (title_es, "")
    
    # Excerpt (chapô)
    excerpt_fr = _extract_excerpt(fr_text) or _extract_excerpt(es_text) or title_es[:200]
    excerpt_es = _extract_excerpt(es_text) or excerpt_fr
    excerpt_en = _extract_excerpt(en_text) or excerpt_fr
    
    # Slug — généré depuis le titre ES pour le SEO/GEO
    slug_base = _slugify(title_es or title_fr or topic['id'], max_len=75)
    slug = slug_base

    # Catégorie (priorité au category_id du topic, sinon mapping par domain)
    category_id = topic.get("category_id") or DOMAIN_CATEGORY.get(topic.get("domain", ""), DEFAULT_CATEGORY)
    
    # Meta SEO
    meta_title = f"{title_fr[:60]} | Club Costa Tropical"
    meta_desc = f"{excerpt_fr[:120]} — Découvrez l'article complet sur le Club Costa Tropical."
    keywords = f"{topic.get('domain', '')}, Costa Tropical, {topic.get('tags', '')}"
    
    # Word count
    word_count = len(es_text.split())
    fr_wc = len(fr_text.split()) if fr_text else word_count
    en_wc = len(en_text.split()) if en_text else word_count
    
    # Contenu pour l'insert — on retire le # Titre (H1) et ![hero](...) du contenu
    # car le layout de la page les affiche déjà via title + featured_image_url
    content_fr = _strip_title_and_hero(fr_text) if fr_text else _strip_title_and_hero(es_text)
    content_es = _strip_title_and_hero(es_text)
    content_en = _strip_title_and_hero(en_text) if en_text else content_fr
    
    conn = _pg()
    try:
        with conn, conn.cursor() as cur:
            # Gérer les collisions de slug : si un article DIFFÉRENT a le même slug,
            # ajouter un suffixe numérique (-2, -3...) jusqu'à trouver un slug libre
            existing = None
            for attempt in range(100):
                cur.execute("SELECT id, title_es FROM articles WHERE slug = %s", (slug,))
                row = cur.fetchone()
                if not row:
                    break  # Slug libre → INSERT
                if row[1] and row[1] == (title_es or title_fr):
                    existing = row
                    break  # Même article (re-run) → UPDATE
                slug = f"{slug_base[:70]}-{attempt + 1}"
            
            if existing:
                # UPDATE
                cur.execute("""
                    UPDATE articles SET
                        title = %s, title_es = %s, title_en = %s,
                        excerpt = %s, excerpt_es = %s, excerpt_en = %s,
                        content = %s, content_es = %s, content_en = %s,
                        word_count = %s,
                        featured_image_url = %s,
                        gallery_images = %s::jsonb,
                        meta_title = %s, meta_description = %s, keywords = %s,
                        is_published = TRUE,
                        published_at = %s,
                        updated_at = NOW()
                    WHERE slug = %s
                """, (
                    title_fr[:200], title_es[:200], title_en[:200],
                    excerpt_fr[:300], excerpt_es[:300], excerpt_en[:300],
                    content_fr, content_es, content_en,
                    word_count,
                    featured_image_url, gallery_json,
                    meta_title[:60], meta_desc[:160], keywords[:200],
                    target_date, slug
                ))
                logger.info(f"🔄 Article mis à jour: {slug}")
                cur.execute("SELECT id FROM articles WHERE slug = %s", (slug,))
                row2 = cur.fetchone()
                article_id = str(row2[0]) if row2 else None
            else:
                # INSERT
                cur.execute("""
                    INSERT INTO articles (
                        title, slug, excerpt, content,
                        content_es, content_en,
                        title_es, title_en,
                        excerpt_es, excerpt_en,
                        author_id, category_id,
                        word_count,
                        featured_image_url, gallery_images,
                        meta_title, meta_description, keywords,
                        is_published, published_at
                    ) VALUES (%s,%s,%s,%s, %s,%s, %s,%s, %s,%s,
                              %s,%s, %s, %s,%s::jsonb, %s,%s,%s, TRUE, %s)
                    RETURNING id
                """, (
                    title_fr[:200], slug, excerpt_fr[:300], content_fr,
                    content_es, content_en,
                    title_es[:200], title_en[:200],
                    excerpt_es[:300], excerpt_en[:300],
                    ALEJANDRO_AUTHOR, category_id,
                    word_count,
                    featured_image_url, gallery_json,
                    meta_title[:60], meta_desc[:160], keywords[:200],
                    target_date
                ))
                article_id = str(cur.fetchone()[0])
                logger.info(f"✅ Article publié: {slug} (id={article_id[:8]}...)")
        
        logger.info(f"   ES={len(es_text)}c ({word_count} mots) | FR={len(fr_text)}c ({fr_wc} mots) | EN={len(en_text)}c ({en_wc} mots)")
        logger.info(f"   Catégorie: {topic.get('domain', '?')} → {category_id}")
        
        # Notifier les moteurs de recherche après publication
        if article_id:
            article_url = f"https://clubcostatropical.es/blog/{slug}"
            try:
                import subprocess
                logger.info(f"📡 SEO ping: {article_url}")
                subprocess.Popen(
                    ["python3", "/root/.hermes/scripts/seo-publish-notify.py", article_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                logger.warning(f"⚠️ SEO ping failed: {e}")
        
        # Retourne le slug réellement utilisé (sémantique, pas topic['id'])
        return slug
    
    except Exception as e:
        logger.error(f"❌ Échec publication: {e}")
        import traceback
        traceback.print_exc()
        return {}
    finally:
        conn.close()
