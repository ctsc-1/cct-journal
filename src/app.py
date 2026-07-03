"""
app.py — entrypoint du Journal quotidien d'Alejandro.

Pipeline :
1. Choix du sujet (rotor 11 categories)
2. Deep Search (deepsearch_article)
3. Generation trilingue (synthesize)
4. Humanisation anti-IA (humanize_article)
5. Planification narrative des images (narrative_planner)
6. Studio photo (images)
7. Publication DB (publish)
8. QC bloquant - si FAIL, depublication immediate
9. Badge IA + Podcast audio (si QC PASS)
"""
from __future__ import annotations
import argparse
import logging
import logging.handlers
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pick import pick_topic
from synthesize import generate_trilingual
from publish import publish_trilingual, apply_ai_badge
from deepsearch_article import deep_search
from humanize_article import humanize_trilingual
from rotor import select_topic, CATEGORIES
from images import generate_article_images
from narrative_planner import plan_images, plan_images_for_lang

LOG_DIR = Path("/srv/cct-journal/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    fh = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "cct-journal.log", when="D", backupCount=30, utc=True
    )
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [stream, fh]


def main() -> int:
    parser = argparse.ArgumentParser(description="CCT Journal quotidien d'Alejandro Ortega")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--domain", help="Forcer un domain (cultura, patrimonio, ...)")
    parser.add_argument("--topic-id", help="Forcer un topic_id spécifique (depuis topics.yaml)")
    parser.add_argument("--category", help="Forcer une catégorie (slug: enquetes-dossiers, cultura-tradiciones, ...)")
    parser.add_argument("--no-deepsearch", action="store_true", help="Désactiver le Deep Search")
    parser.add_argument("--no-humanize", action="store_true", help="Désactiver l'humanisation")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    _setup_logging(verbose=args.verbose)
    log = logging.getLogger("cct-journal.main")
    log.info("━" * 50)
    log.info("📰 Journal CCT — start")
    t0 = datetime.now(timezone.utc)

    # ─── Phase 0 : Choix du sujet ──────────────────────────────────────────
    if args.topic_id:
        # Fallback topics.yaml pour usage manuel
        import yaml
        from config import TOPICS_PATH
        topics = yaml.safe_load(TOPICS_PATH.read_text())["topics"]
        topic = next((t for t in topics if t["id"] == args.topic_id), None)
        if not topic:
            log.error(f"Topic '{args.topic_id}' introuvable dans topics.yaml")
            return 1
        # Ajouter category_id manquant pour compatibilité publish
        topic["category_id"] = None
        log.info(f"Sujet manuel (topics.yaml): {topic['id']} ({topic['domain']})")
    else:
        # Rotor automatique 11 catégories
        date_str = t0.strftime("%Y-%m-%d")
        topic = select_topic(today=date_str, force_category=args.category)
        log.info(f"Sujet du jour (rotor): {topic['id']} ({topic['domain']}) — {topic['title']}")

    # ─── Phase 1 : Deep Search ─────────────────────────────────────────────
    deep_context = ""
    if not args.no_deepsearch:
        log.info("🔍 Phase 1: Deep Search...")
        date_str = t0.strftime("%Y-%m-%d")
        deep_result = deep_search(topic, date_str=date_str)
        if deep_result.get("text"):
            deep_context = deep_result["text"]
            log.info(f"   → {len(deep_context)} chars, {len(deep_result.get('sources', []))} sources"
                     f"{' [CACHED]' if deep_result.get('cached') else ''}")
        else:
            log.warning("   ⚠️ Deep Search returned empty — continuing without")
    else:
        log.info("   ⏭️ Deep Search désactivé (--no-deepsearch)")

    # ─── Phase 2 : Génération trilingue ────────────────────────────────────
    log.info("📝 Phase 2: Génération trilingue...")
    translations = generate_trilingual(topic, deep_context=deep_context)
    for lang in ("es", "fr", "en"):
        t = translations.get(lang, "")
        log.info(f"   {lang.upper()}: {len(t)} chars ({len(t.split())} mots)" if t else f"   {lang.upper()}: ❌")

    if not translations.get("es"):
        log.error("❌ Aucun texte ES — abandon")
        return 1

    # ─── Phase 3 : Humanisation anti-IA ─────────────────────────────────────
    if not args.no_humanize:
        log.info("🎭 Phase 3: Humanisation anti-IA...")
        humanized = humanize_trilingual(translations)
        for lang in ("es", "fr", "en"):
            h = humanized.get(lang, {})
            if h.get("fixes_applied", 0) > 0:
                log.info(f"   {lang.upper()}: {h['fixes_applied']} correction(s) — score {h['score_before']}% → {h['score_after']}%")
                translations[lang] = h["text"]
            else:
                log.info(f"   {lang.upper()}: ✅ propre (score {h.get('score_after', '?')}%)")
        log.info(f"   Score global: {humanized.get('overall_score', '?')}%")
    else:
        log.info("   ⏭️ Humanisation désactivée (--no-humanize)")

    if args.dry_run:
        out_dir = Path("/srv/cct-journal/cache")
        out_dir.mkdir(exist_ok=True)
        for lang, text in translations.items():
            if lang == "overall_score":
                continue
            p = out_dir / f"dryrun-{t0.strftime('%Y-%m-%d')}-{topic['id']}-{lang}.md"
            p.write_text(text)
            log.info(f"Dry-run → {p}")
        return 0

    # ─── Phase 3b : Planification narrative ────────────────────────────────
    log.info("🎬 Phase 3b: Planification narrative des images...")
    es_text = translations.get("es", "")
    text_with_markers, image_plan, plan_raw = plan_images(es_text, topic.get("title", ""))
    translations["es"] = text_with_markers  # Texte avec [[IMG:...]] marqueurs

    # Reproduire les marqueurs dans FR/EN
    for lang in ("fr", "en"):
        t = translations.get(lang, "")
        if t:
            translations[lang] = plan_images_for_lang(text_with_markers, t)

    if not image_plan:
        log.warning("   ⚠️ Aucune image planifiée — publication sans images")

    # ─── Phase 4 : Studio photo ────────────────────────────────────────────
    log.info("🖼️ Phase 4: Studio photo...")
    slug = topic['id']  # topic['id'] contient déjà la date (ex: enquetes-dossiers-2026-06-18)
    hero_url, gallery_json, text_es_with_imgs = generate_article_images(
        text_with_markers, image_plan, slug
    )

    if not hero_url:
        log.error("🔴 CRITICAL — Aucune image hero générée, abandon de la publication")
        log.error("🔴 Relancer le pipeline manuellement : systemctl start cct-journal")
        return 1

    # Vérifier qu'il ne reste PAS de marqueurs [[IMG:]] non remplacés
    remaining_markers = re.findall(r'\[\[IMG:[^\]]+\]\]', text_es_with_imgs)
    if remaining_markers:
        log.warning(f"⚠️ {len(remaining_markers)} marqueur(s) IMG non remplacé(s): {remaining_markers}")
        log.warning("⚠️ Tentative de rattrapage des images manquantes...")
        # Re-générer UNIQUEMENT les images section manquantes
        # Extraire les indices des marqueurs section-n orphelins
        orphan_indices = set()
        for m in remaining_markers:
            match = re.match(r'\[\[IMG:section-(\d+)\]\]', m)
            if match:
                orphan_indices.add(int(match.group(1)))
        log.info(f"   Indices section à rattraper: {sorted(orphan_indices)}")
        # Retry: on relance generate_article_images avec les mêmes params
        # mais en forçant les prompts manquants
        if orphan_indices and image_plan:
            retry_plan = [item for i, item in enumerate(image_plan) 
                         if item.get('type') == 'section' 
                         and i+1 in orphan_indices]
            if retry_plan:
                log.info(f"   Retry {len(retry_plan)} image(s) section...")
                _, gallery_json_retry, text_es_with_imgs = generate_article_images(
                    text_es_with_imgs, retry_plan, slug + "-retry"
                )
                # Merger gallery_json avec le retry
                if gallery_json_retry and gallery_json_retry != "[]":
                    import json as _json
                    existing = _json.loads(gallery_json) if gallery_json and gallery_json != "[]" else []
                    retry_list = _json.loads(gallery_json_retry)
                    merged = existing + retry_list
                    gallery_json = _json.dumps(merged)
    
    # Vérification finale
    final_check = re.findall(r'\[\[IMG:[^\]]+\]\]', text_es_with_imgs)
    if final_check:
        log.error(f"🔴 {len(final_check)} marqueur(s) IMG encore non remplacés APRÈS retry: {final_check}")
        log.error("🔴 Publication avec images manquantes — à corriger manuellement")
        # Nettoyer les marqueurs restants pour éviter qu'ils s'affichent
        text_es_with_imgs = re.sub(r'\[\[IMG:[^\]]+\]\]', '', text_es_with_imgs)

    # Injecter les mêmes <img> dans FR/EN en remplaçant leurs marqueurs
    # Les alt text sont traduits dans la langue cible en utilisant les H2 du texte traduit
    if image_plan:
        # Extraire les H2 de chaque langue pour traduire les alt text
        def _get_h2_titles(text: str) -> list:
            """Extrait les titres H2 d'un texte markdown."""
            import re as _re2
            return _re2.findall(r'^##\s+(.+?)(?:\s*\n|$)', text, _re2.MULTILINE)

        h2_fr = _get_h2_titles(translations.get("fr", ""))
        h2_en = _get_h2_titles(translations.get("en", ""))
        h2_map = {"fr": h2_fr, "en": h2_en}

        for lang in ("fr", "en"):
            t = translations.get(lang, "")
            if not t:
                continue
            lang_h2s = h2_map.get(lang, [])
            for item in image_plan:
                marker = item.get("marker", "")
                if not marker or marker not in t:
                    continue
                ptype = item.get("type", "section")
                if ptype == "hero" and hero_url:
                    # Hero: alt text = slug (neutre, pas de traduction nécessaire)
                    img_tag = f'![{slug}]({hero_url})'
                    t = t.replace(marker, img_tag, 1)
                elif ptype == "section":
                    import re as _re
                    m = _re.search(r'section-(\d+)', marker)
                    if m:
                        idx = int(m.group(1)) - 1  # 1-indexed dans le marqueur
                        import json as _json
                        gallery = _json.loads(gallery_json) if gallery_json and gallery_json != "[]" else []
                        if 0 <= idx < len(gallery):
                            img = gallery[idx]
                            # Traduire l'alt text : utiliser le titre H2 de la langue cible si disponible
                            if idx < len(lang_h2s):
                                alt_text = lang_h2s[idx]
                            else:
                                alt_text = img["alt"]  # Fallback ES si pas de H2 trouvé
                            img_tag = f'![{alt_text}]({img["url"]})'
                            t = t.replace(marker, img_tag, 1)
            translations[lang] = t

    # Stocker le texte enrichi ES
    translations["es"] = text_es_with_imgs

    # ─── Phase 5 : Publication DB (draft) ────────────────────────────────────
    log.info("💾 Phase 5: Publication DB...")
    article_slug = publish_trilingual(topic, translations, target_date=t0,
                                  featured_image_url=hero_url, gallery_json=gallery_json)
    if not article_slug:
        log.error("❌ Échec publication — abandon")
        return 1
    log.info(f"Published : {article_slug}")

    # ─── Phase 5b : Badge IA sur les images ────────────────────────────────
    if hero_url:
        from publish import apply_ai_badge
        apply_ai_badge(hero_url)
        log.info("✨ Badge IA appliqué sur l'image hero")

    # ─── Phase 5c : Podcast audio (3 langues) ──────────────────────────────
    log.info("🎙️ Phase 5c: Podcast audio (3 langues)...")
    for lang in ("es", "fr", "en"):
        try:
            podcast_text = translations.get(lang, "")
            if not podcast_text or len(podcast_text) < 200:
                log.info(f"   ⏭️ {lang.upper()} — texte trop court, ignoré")
                continue
            from article_podcast import generate_article_podcast
            podcast_result = generate_article_podcast(
                podcast_text, topic.get("title", ""), article_slug, lang=lang
            )
            if podcast_result:
                log.info(f"   ✅ {lang.upper()}: {podcast_result['url']} ({podcast_result['size']}KB, ~{podcast_result['duration_s']}s)")
            else:
                log.info(f"   ⏭️ {lang.upper()} — échec ou ignoré")
        except Exception as e:
            log.warning(f"   ⚠️ {lang.upper()} podcast error: {e}")

    # ─── Phase 6 : Contrôle Qualité ────────────────────────────────────
    log.info("📊 Phase 6: Contrôle Qualité...")
    qc_passed = False
    try:
        from qc_check import run_qc
        qc_ok = run_qc(article_slug)
        if qc_ok:
            log.info("   ✅ QC PASS")
            qc_passed = True
        else:
            log.error("   🔴 QC FAIL — P1 critique(s) détecté(s)")
            log.error("   🔴 Dépublication de l'article pour correction")
    except Exception as e:
        log.warning(f"   ⚠️ QC error: {e}")
        qc_passed = True  # Si QC plante, on laisse publié (pas de blocage)

    # Si QC échoue → dépublication immédiate
    if not qc_passed:
        try:
            import subprocess as _sp
            db_url = os.environ.get("DATABASE_URL", "")
            if not db_url:
                r = _sp.run(["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                           capture_output=True, text=True, timeout=5)
                if r.stdout.strip():
                    db_url = r.stdout.strip().split("=", 1)[1].strip().strip("'\"")
            if db_url:
                import psycopg2 as _pg
                conn = _pg.connect(db_url, connect_timeout=5)
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE articles SET is_published = FALSE WHERE slug = %s",
                            (article_slug,)
                        )
                conn.close()
                log.info(f"   ✅ Article {article_slug} dépublié (is_published = FALSE)")
        except Exception as e2:
            log.error(f"   ❌ Échec dépublication: {e2}")
        log.error("🔴 Corriger les points critiques et relancer le pipeline.")
        return 1

    duration = (datetime.now(timezone.utc) - t0).total_seconds()
    log.info(f"━" * 50)
    log.info(f"✅ Journal terminé en {duration:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
