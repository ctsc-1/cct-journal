#!/usr/bin/env python3
"""
resume_journal_publish.py — REPRISE du pipeline cct-journal interrompu pendant les images.

Le run du 03/08/2026 a été interrompu pendant la génération des images de section
(disponible : Actes 1/2/3 validés en cache + hero + images section 1-9).
Ce script reprend à la phase images + publication SANS refaire les 3 actes.
Les images déjà générées sont réutilisées (_generate_fal skip si fichier existant).

Usage:
  python3 resume_journal_publish.py <category_id> <topic_title>

IMPORTANT : N'appelle PAS clear_cache() (contrairement à run_pipeline.run()).
"""
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_cache import load_step

DATE = time.strftime("%Y-%m-%d")

def log(msg: str, newline: bool = True):
    t = datetime.now().strftime("%H:%M:%S")
    if newline:
        print(f"[{t}] {msg}", flush=True)
    else:
        print(f"[{t}] {msg}", end=" ", flush=True)

def _extract_excerpt(text: str) -> str:
    clean = re.sub(r'^#\s+.*\n+', '', text).strip()
    excerpt = clean[:300]
    if len(excerpt) == 300:
        excerpt = excerpt.rsplit(" ", 1)[0]
    return excerpt

def main(category_id: str, topic_title: str):
    # ── 1. Récupérer les artefacts validés (actes 1/2/3) ──
    es_data = load_step("act1_es_validated")
    fr_data = load_step("act2_fr_validated")
    en_data = load_step("act3_en_validated")
    if not es_data:
        log("❌ act1_es_validated manquant dans le cache — impossible de reprendre. Relancer run_pipeline.py")
        return False

    article_es = es_data["article_es"]
    article_fr = fr_data["article_fr"] if fr_data else article_es
    article_en = en_data["article_en"] if en_data else article_es

    title_es = es_data.get("title_es", topic_title)
    title_fr = fr_data.get("title_fr", title_es) if fr_data else title_es
    title_en = en_data.get("title_en", title_es) if en_data else title_es

    slug = re.sub(r"[^a-z0-9-]", "", title_es.lower().replace(" ", "-")[:45]).strip("-")
    log(f"📋 Reprise article : {title_es} (slug={slug})")

    excerpt_es = _extract_excerpt(article_es)
    excerpt_fr = _extract_excerpt(article_fr)
    excerpt_en = _extract_excerpt(article_en)

    # ── 2. Images (Photo Studio) : réutilise les existantes, génère les manquantes ──
    from photo_studio import generate_hero, generate_section_images, inject_markers
    hero_url = generate_hero(article_es, title_es, slug)
    section_gallery = generate_section_images(article_es, slug)

    gallery = []
    if hero_url:
        gallery.append({"url": hero_url, "pos": "center 50%"})
    gallery.extend(section_gallery)
    featured = hero_url

    # ── 3. Insertion DB ──
    from run_pipeline import insert_article
    word_count = len(article_es.split())
    reading_time = max(1, word_count // 200)

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
    log(f"✅ Publication {'réussie' if ok else 'ÉCHOUÉE'} (slug={slug}, {len(gallery)} images)")
    return ok

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    ok = main(sys.argv[1], " ".join(sys.argv[2:]))
    sys.exit(0 if ok else 1)
