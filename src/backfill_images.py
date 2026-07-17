#!/usr/bin/env python3
"""
backfill_images.py — Rattrapage des images inline pour les articles
dont le slug a été modifié (sémantique) après la génération initiale.

Utilise generate_article_images_manual() du module images.py
pour créer les images section et les injecter dans le texte.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Ajouter cct-journal au path
sys.path.insert(0, "/srv/cct-journal/src")
sys.path.insert(0, "/srv/rag-engine")

from images import generate_article_images_manual_sync

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/srv/cct-journal/logs/backfill-images.log")
    ]
)
logger = logging.getLogger("backfill-images")

DB_URL = os.popen("grep '^DATABASE_URL=' /srv/rag-engine/.env 2>/dev/null | cut -d= -f2-").read().strip()

async def get_articles_to_fix():
    """Récupère les articles publiés avec slug sémantique ET sans gallery."""
    import asyncpg
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch("""
        SELECT a.id::text, a.slug, LEFT(a.title, 80) as title_fr,
               a.content_es, c.slug as cat_slug,
               a.featured_image_url IS NOT NULL as has_hero
        FROM articles a
        JOIN categories c ON a.category_id = c.id
        WHERE a.is_published = true
          AND a.content_es IS NOT NULL AND LENGTH(a.content_es) > 3000
          AND (a.gallery_images IS NULL OR a.gallery_images = '[]' OR a.gallery_images = 'null')
          -- Filtrer slugs sémantiques (ne suivent PAS le format categorie-YYYY-MM-DD)
          AND a.slug NOT LIKE 'enquetes-dossiers-____-__-__'
          AND a.slug NOT LIKE 'culture-traditions-____-__-__'
          AND a.slug NOT LIKE 'gastronomie-vin-____-__-__'
          AND a.slug NOT LIKE 'geographie-nature-____-__-__'
          AND a.slug NOT LIKE 'activites-aventure-____-__-__'
          AND a.slug NOT LIKE 'histoire-patrimoine-____-__-__'
          AND a.slug NOT LIKE 'le-journal-d-alejandro-____-__-__'
          AND a.slug NOT LIKE 'terroir-agriculture-____-__-__'
          AND a.slug NOT LIKE 'l-hebdo-du-club-____-__-__'
          AND a.slug NOT LIKE 'revue-de-presse-____-__-__'
          AND a.slug NOT LIKE 'vie-pratique-____-__-__'
          AND a.slug NOT LIKE 'les-chroniques-de-charly-____-__-__'
        ORDER BY a.updated_at DESC
        LIMIT 20
    """)
    await conn.close()
    return rows

async def update_article_db(article_id: str, hero_url: str, gallery_json: str,
                            text_es: str, text_fr: str, text_en: str):
    """Met à jour l'article en DB avec les nouvelles images."""
    import asyncpg
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute("""
            UPDATE articles SET
                featured_image_url = COALESCE($2, featured_image_url),
                gallery_images = $3,
                content_es = $4,
                content = $5,
                content_en = $6,
                updated_at = NOW()
            WHERE id = $1::uuid
        """, article_id,
            hero_url if hero_url else None,
            gallery_json,
            text_es,
            text_fr,
            text_en)
        logger.info(f"   ✅ DB mise à jour: {article_id}")
    except Exception as e:
        logger.error(f"   ❌ DB update error: {e}")
    finally:
        await conn.close()

def inject_images_in_other_langs(text_es_with_imgs: str, text_fr: str, text_en: str) -> tuple:
    """Reproduit les balises img de ES dans FR/EN via les marqueurs [[IMG:...]].
    
    Comme ES a les marqueurs remplacés par des <img>, on cherche les patterns
    ![alt](url) dans ES et on les insère aux mêmes positions relatives dans FR/EN.
    """
    import re
    # Extraire les balises img de ES avec leur position
    img_pattern = re.compile(r'(!\[.*?\]\(.*?\))')
    es_img_tags = img_pattern.findall(text_es_with_imgs)
    
    if not es_img_tags:
        return text_fr, text_en
    
    # Compter les marqueurs [[IMG:...]] dans FR/EN
    marker_pattern = re.compile(r'(\[\[IMG:[^\]]+\]\])')
    
    for lang_text in [text_fr, text_en]:
        markers = marker_pattern.findall(lang_text)
        if not markers:
            # Pas de marqueurs dans cette langue → insérer après le titre
            h1 = re.search(r'^#\s+(.+)$', lang_text, re.MULTILINE)
            modified = lang_text
            for i, img_tag in enumerate(es_img_tags):
                # Trouver la section H2 correspondante
                # Chercher la section après laquelle l'image a été placée en ES
                # Approche simple : insérer l'image après le titre
                pass
    
    # Approche simplifiée : remplacer les marqueurs [[IMG:...]] dans FR/EN
    # par les mêmes URLs que ES
    fr_result = text_fr
    en_result = text_en
    
    img_idx = 0
    for lang_text, result_key in [(text_fr, 'fr'), (text_en, 'en')]:
        result = lang_text
        markers = marker_pattern.findall(result)
        for marker in markers:
            if img_idx < len(es_img_tags):
                result = result.replace(marker, es_img_tags[img_idx], 1)
                img_idx += 1
        if result_key == 'fr':
            fr_result = result
        else:
            en_result = result
    
    # Si pas de marqueurs, on insère après le premier H1
    if not marker_pattern.findall(text_fr):
        h1 = re.search(r'(^#\s+.+$)', text_fr, re.MULTILINE)
        if h1:
            fr_result = text_fr.replace(h1.group(1), h1.group(1) + "\n\n" + "\n\n".join(es_img_tags[:3]), 1)
    if not marker_pattern.findall(text_en):
        h1 = re.search(r'(^#\s+.+$)', text_en, re.MULTILINE)
        if h1:
            en_result = text_en.replace(h1.group(1), h1.group(1) + "\n\n" + "\n\n".join(es_img_tags[:3]), 1)
    
    return fr_result, en_result

async def main():
    articles = await get_articles_to_fix()
    logger.info(f"📋 {len(articles)} article(s) à traiter")
    
    if not articles:
        logger.info("✅ Aucun article à traiter")
        return
    
    for a in articles:
        article_id = a['id']
        slug = a['slug']
        title = a['title_fr'] or slug
        text_es = a['content_es']
        has_hero = a['has_hero']
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🖼️ Traitement: {title[:60]} ({slug})")
        logger.info(f"   ID: {article_id} | Hero: {'✅' if has_hero else '❌'} | Texte ES: {len(text_es)} chars")
        
        # Générer les images via le mode manuel
        try:
            hero_url, gallery_json, text_with_images = generate_article_images_manual_sync(
                article_text=text_es,
                title=title,
                slug=slug,
                category_name="Costa Tropical"
            )
            
            if not gallery_json or gallery_json == "[]":
                logger.warning(f"   ⚠️ Aucune image générée pour {slug}")
                continue
            
            gallery = json.loads(gallery_json)
            logger.info(f"   ✅ Images: hero={'✅' if hero_url else '⏳'} | sections={len(gallery)}")
            
            # Récupérer FR/EN actuels
            import asyncpg
            conn = await asyncpg.connect(DB_URL)
            row = await conn.fetchrow(
                "SELECT content, content_en FROM articles WHERE id = $1::uuid", article_id
            )
            text_fr = row['content'] if row else ""
            text_en = row['content_en'] if row else ""
            await conn.close()
            
            # Injecter les images dans FR/EN
            fr_with_imgs, en_with_imgs = inject_images_in_other_langs(
                text_with_images, text_fr, text_en
            )
            
            # Mettre à jour la DB
            await update_article_db(
                article_id, hero_url, gallery_json,
                text_with_images, fr_with_imgs, en_with_imgs
            )
            
            logger.info(f"   ✅ Article {slug} mis à jour avec {len(gallery)} images section")
            
        except Exception as e:
            logger.error(f"   ❌ Erreur pour {slug}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    logger.info(f"\n{'='*60}")
    logger.info("✅ Rattrapage terminé")

if __name__ == "__main__":
    asyncio.run(main())
