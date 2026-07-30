#!/usr/bin/env python3
"""
run_pipeline.py — Orchestrateur du pipeline 3 actes.
Enchaîne act1_es → act2_fr → act3_en.
Après succès, lance images FAL + galerie + QC final + INSERT DB.

Usage: python3 run_pipeline.py <categorie_id> [--topic "sujet"] [--date YYYY-MM-DD]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from pipeline_cache import load_step, clear_cache

# ─── CONFIG ─────────────────────────────────────────────────
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
# Récupère le mot de passe PostgreSQL depuis /etc/cct-journal/pg.pwd
def _pg_url() -> str:
    try:
        pwd = open("/etc/cct-journal/pg.pwd").read().strip()
        return f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db"
    except Exception:
        return "postgresql:///alejandro_db"
DB_URL = _pg_url()
OUTPUT_DIR = "/srv/pwa/public/images/journal"
SITE = "https://clubcostatropical.es"
AUTHOR_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
DATE = datetime.now().strftime("%Y-%m-%d")

os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg: str, newline: bool = True):
    t = datetime.now().strftime("%H:%M:%S")
    if newline:
        print(f"[{t}] {msg}", flush=True)
    else:
        print(f"[{t}] {msg}", end=" ", flush=True)



# ─── PHASE 5: INSERTION DB ──────────────────────────────────
def insert_article(title_fr: str, title_es: str, title_en: str, slug: str,
                   excerpt_fr: str, excerpt_es: str, excerpt_en: str,
                   content_fr: str, content_es: str, content_en: str,
                   category_id: str, word_count: int, reading_time: int,
                   featured_image_url: str | None, gallery_images: list[dict]) -> bool:
    """INSERT dans la table articles."""
    import psycopg2

    log("💾 Phase 5: Insertion DB")
    article_id = str(uuid.uuid4())
    now = datetime.now()

    try:
        conn = psycopg2.connect(DB_URL, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO articles (
                id, title, title_es, title_en, slug,
                excerpt, excerpt_es, excerpt_en,
                content, content_es, content_en,
                category_id, author_id,
                is_published, published_at, updated_at,
                word_count, reading_time_minutes,
                featured_image_url,
                gallery_images
            ) VALUES (%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s, %s,%s, %s, %s)
        """, (
            article_id, title_fr, title_es, title_en, slug,
            excerpt_fr, excerpt_es, excerpt_en,
            content_fr, content_es, content_en,
            category_id, AUTHOR_ID,
            True, now, now,
            word_count, reading_time,
            featured_image_url,
            json.dumps(gallery_images),
        ))
        conn.commit()
        conn.close()

        log(f"   ✅ INSERT: {slug} (id={article_id[:8]})")

        # Vérification locale (pas d'appel à la PWA externe)
        verify_r = httpx.get(
            f"http://127.0.0.1:3000/api/blog/{slug}",
            headers={"Host": "clubcostatropical.es"},
            timeout=10,
        )
        if verify_r.status_code == 200:
            data = verify_r.json()
            gi = data.get("gallery_images", [])
            if isinstance(gi, str):
                gi = json.loads(gi) if gi else []
            log(f"   ✅ Local API 200: featured={bool(data.get('featured_image'))}, gallery={len(gi)} images")
        else:
            log(f"   ⚠️ Local API {verify_r.status_code}")

        return True
    except Exception as e:
        log(f"   ❌ DB error: {e}")
        return False


# ─── ORCHESTRATEUR PRINCIPAL ────────────────────────────────
def run(category_id: str, topic_title: str, date_str: str = DATE) -> bool:
    """Pipeline complet Phase 0 → Acte 1 → Acte 2 → Acte 3 → Images → DB."""
    start = time.time()
    clear_cache()

    # ── PHASE 0: ÉVALUATION DU POTENTIEL ──
    from phase0_evaluator import evaluate as phase0_evaluate
    from rotor import CATEGORIES, select_category

    # Trouver la catégorie correspondante
    category = next((c for c in CATEGORIES if c["category_id"] == category_id), None)
    if not category:
        category = CATEGORIES[0]

    log(f"📋 Catégorie: {category['name_es']} ({category['id']})")

    topic = None
    max_retries = 2
    for retry in range(max_retries):
        if retry > 0:
            # Essayer la catégorie suivante
            category = select_category(offset=1)
            log(f"🔄 Retry {retry}: catégorie {category['name_es']}")

        topic = phase0_evaluate(category, date_str)
        if topic:
            break
        log(f"   ⚠️ Catégorie {category['name_es']} sans sujet viable")

    if not topic:
        log(f"❌ Aucune catégorie n'a produit de sujet viable après {max_retries} tentatives")
        return False

    # ── ACTE 1: ES ──
    from act1_es import run as act1
    if not act1(topic, date_str):
        log("❌ Pipeline arrêté: Acte 1 (ES) échoué")
        return False

    # ── ACTE 2: FR ──
    from act2_fr import run as act2
    if not act2():
        log("⚠️ Acte 2 (FR) a eu des avertissements mais on continue")
        # Non-bloquant

    # ── ACTE 3: EN ──
    from act3_en import run as act3
    if not act3():
        log("⚠️ Acte 3 (EN) a eu des avertissements mais on continue")

    # ── RÉCUPÉRER LES ARTEFACTS ──
    es_data = load_step("act1_es_validated")
    fr_data = load_step("act2_fr_validated")
    en_data = load_step("act3_en_validated")

    if not es_data:
        log("❌ Artefact ES manquant")
        return False

    article_es = es_data["article_es"]
    article_fr = fr_data["article_fr"] if fr_data else article_es
    article_en = en_data["article_en"] if en_data else article_es

    title_es = es_data.get("title_es", topic_title)
    title_fr = fr_data.get("title_fr", title_es) if fr_data else title_es
    title_en = en_data.get("title_en", title_es) if en_data else title_es

    # Slug depuis le titre ES
    slug = re.sub(r"[^a-z0-9-]", "", title_es.lower().replace(" ", "-")[:45]).strip("-")

    # Excerpts (premiers 300 chars après le H1)
    def _extract_excerpt(text: str) -> str:
        clean = re.sub(r'^#\s+.*\n+', '', text).strip()
        excerpt = clean[:300]
        if len(excerpt) == 300:
            excerpt = excerpt.rsplit(" ", 1)[0]
        return excerpt

    excerpt_es = _extract_excerpt(article_es)
    excerpt_fr = _extract_excerpt(article_fr)
    excerpt_en = _extract_excerpt(article_en)

    # ── IMAGES (Photo Studio) ──
    from photo_studio import generate_hero, generate_section_images, inject_markers

    # HERO séparé
    hero_url = generate_hero(article_es, title_es, slug)

    # Images section (une par H2)
    section_gallery = generate_section_images(article_es, slug)

    # Assemblage galerie: hero en premier, puis images section
    gallery = []
    if hero_url:
        gallery.append({"url": hero_url, "pos": "center 50%"})
    gallery.extend(section_gallery)

    featured = hero_url

    # ── DB ──
    word_count = len(article_es.split())
    reading_time = max(1, word_count // 200)

    # Injection [[PHOTO:N]] dans les 3 langues via le module
    content_es = inject_markers(article_es, gallery)
    content_fr = inject_markers(article_fr, gallery)
    content_en = inject_markers(article_en, gallery)

    ok = insert_article(
        title_fr, title_es, title_en, slug,
        excerpt_fr, excerpt_es, excerpt_en,
        content_fr, content_es, content_en,
        category_id, word_count, reading_time,
        featured, gallery,
    )

    elapsed = time.time() - start
    mins, secs = divmod(int(elapsed), 60)
    log(f"\n{'='*50}")
    log(f"📊 PIPELINE TERMINÉ: {mins}m{secs}s")
    log(f"📝 {word_count} mots, {len(re.findall(r'^## ', article_es, re.MULTILINE))} sections")
    log(f"🖼️  {len(gallery)} images")
    log(f"🌐 {SITE}/blog/{slug}")
    log(f"{'='*50}")

    return ok


if __name__ == "__main__":
    cat_id = sys.argv[1] if len(sys.argv) > 1 else "047d7527-d161-4c25-a948-3e6f88aa8a9e"
    topic = "Gastronomía y vino de la Costa Tropical"
    if "--topic" in sys.argv:
        idx = sys.argv.index("--topic")
        topic = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else topic

    success = run(cat_id, topic)
    sys.exit(0 if success else 1)
