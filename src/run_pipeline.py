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
from translation_cache import get_es, save_es, save_meta, article_exists, _compute_slug_from_title

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
OUTPUT_DIR = "/srv/rag-engine/static/DEPARTEMENT_ICONOGRAPHIE/JOURNAL"
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

    # Truncate des titres à 200 chars max (ponytail: contrainte DB)
    title_fr = title_fr[:200] if len(title_fr) > 200 else title_fr
    title_es = title_es[:200] if len(title_es) > 200 else title_es
    title_en = title_en[:200] if len(title_en) > 200 else title_en
    slug = slug[:200]

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

        # TRIGGER SEO IMMÉDIAT (isolé au profil journal — 07/08/2026)
        # Ancienne version appelait le script d'un autre profil sous /root/.hermes
        # (inaccessible en lecture pour cct-journal → Permission denied). On exécute
        # désormais la copie locale src/seo_pipeline.py (outillage interne du profil).
        try:
            log('[SEO] Declenchement instantane seo_pipeline (local)...')
            import subprocess, os
            seo_env = dict(os.environ)
            seo_env['DATABASE_URL'] = DB_URL
            subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), 'seo_pipeline.py')],
                env=seo_env, check=False, timeout=180,
            )
        except Exception as seo_err:
            log(f'   [WARN] Instant SEO Trigger: {seo_err}')

        return True
    except Exception as e:
        log(f"   ❌ DB error: {e}")
        return False


# ─── ORCHESTRATEUR PRINCIPAL ────────────────────────────────
def run(category_id: str, topic_title: str, date_str: str = DATE) -> bool:
    """Pipeline complet Phase 0 → Acte 1 → Acte 2 → Acte 3 → Images → DB."""
    start = time.time()
    clear_cache()

    # ── RESTAURATION DU CACHE PERSISTANT (translation_cache) ──
    # Le cache persistant survit à clear_cache() car il est sur disque (/srv/cct-journal/cache/v1/).
    # On ne restaure rien ici — les actes 2/3 vérifient directement translation_cache.
    # Le cache inter-actes (pipeline_cache) est volontairement vidé pour garantir
    # la fraîcheur de l'article ES généré.

    # ── PHASE 0: ÉVALUATION DU POTENTIEL ──
    from phase0_evaluator import evaluate as phase0_evaluate
    from rotor import CATEGORIES, select_category

    # Trouver la catégorie correspondante
    category = next((c for c in CATEGORIES if c["category_id"] == category_id), None)
    if not category:
        category = CATEGORIES[0]

    log(f"📋 Catégorie: {category['name_es']} ({category['id']})")

    topic = None
    max_retries = 12  # Essayer toutes les catégories du rotor avant d'abandonner
    for retry in range(max_retries):
        if retry > 0:
            # Essayer la catégorie suivante.
            # offset=retry : avance de `retry` crans à chaque itération pour parcourir
            # les 12 catégories du rotor sans jamais se répéter (correctif 03/08/2026).
            category = select_category(offset=retry)
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

    # ── SAUVEGARDE DANS LE CACHE PERSISTANT ──
    es_data_after_act1 = load_step("act1_es_validated")
    if es_data_after_act1:
        article_es = es_data_after_act1.get("article_es", "")
        title_es = es_data_after_act1.get("title_es", topic_title)
        slug = _compute_slug_from_title(title_es)
        if not get_es(slug):
            save_es(slug, article_es)
            save_meta(slug, {
                "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "nb_sections": len(re.findall(r'^##\s+', article_es, re.MULTILINE)),
                "modele_generation": "deepseek-v4-flash",
                "modele_traduction": "deepseek-v4-flash",
                "modele_verification": "deepseek-v4-pro",
            })
            log(f"📦 ES sauvegardé dans cache persistant ({slug})")
        else:
            log(f"📦 ES déjà en cache persistant ({slug})")
    else:
        log("⚠️ Impossible de sauvegarder l'ES dans le cache persistant — artefact manquant")

    # ── FACT-CHECK ES + AUTO-CORRECT ──
    from fact_check_es import fact_check
    es_check_data = load_step("act1_es_validated")
    if es_check_data and "article_es" in es_check_data:
        es_alerts = fact_check(es_check_data["article_es"])
        if es_alerts:
            for a in es_alerts:
                log(f"[FACT-CHECK] [{a['type']}] {a['entite']}: trouvé '{a['valeur_trouvee']}', attendu '{a['valeur_attendue']}'")
            log(f"⚠️ {len(es_alerts)} alerte(s) factuelle(s) détectée(s) dans article ES")
            if len(es_alerts) > 5:
                log("❌ FACT-CHECK: Plus de 5 alertes factuelles → tentative d'auto-correct...")
                try:
                    from auto_correct_es import auto_correct
                    from auto_correct_dates import correct_dates
                    from pipeline_cache import save_step as _save
                    es_check_data["article_es"], nb_correct_es = auto_correct(es_check_data["article_es"], es_alerts)
                    es_check_data["article_es"], nb_correct_dates = correct_dates(es_check_data["article_es"])
                    nb_total = nb_correct_es + nb_correct_dates
                    if nb_total > 0:
                        _save("act1_es_validated", es_check_data)
                        log(f"✅ AUTO-CORRECT: {nb_correct_es} entité(s) + {nb_correct_dates} date(s) corrigée(s) — re-vérification...")
                        es_alerts2 = fact_check(es_check_data["article_es"])
                        if len(es_alerts2) > 5:
                            log(f"❌ FACT-CHECK: Encore {len(es_alerts2)} alertes après auto-correct — blocage pipeline")
                            return False
                        log(f"✅ AUTO-CORRECT réussi: {len(es_alerts2)} alerte(s) restante(s), poursuite autorisée")
                    else:
                        log(f"❌ FACT-CHECK: Auto-correct n'a rien pu corriger sur {len(es_alerts)} alertes — blocage pipeline")
                        return False
                except ImportError as ie:
                    log(f"❌ FACT-CHECK: Modules auto-correct non disponibles ({ie}) — blocage pipeline")
                    return False
            else:
                log(f"   ✅ FACT-CHECK: < 5 alertes, auto-correct appliqué...")
                try:
                    from auto_correct_es import auto_correct
                    from auto_correct_dates import correct_dates
                    from pipeline_cache import save_step as _save
                    es_check_data["article_es"], nb_correct_es = auto_correct(es_check_data["article_es"], es_alerts)
                    es_check_data["article_es"], nb_correct_dates = correct_dates(es_check_data["article_es"])
                    if nb_correct_es + nb_correct_dates > 0:
                        _save("act1_es_validated", es_check_data)
                        log(f"   ✅ {nb_correct_es} entité(s) + {nb_correct_dates} date(s) corrigée(s) automatiquement")
                except ImportError:
                    pass  # auto-correct non disponible, on continue sans
        else:
            log("   ✅ FACT-CHECK ES: Aucune anomalie factuelle détectée")
    else:
        log("   ⚠️ FACT-CHECK: Artefact ES non disponible pour vérification")

    # ── ACTE 2: FR ──
    from act2_fr import run as act2
    if not act2():
        log("❌ Pipeline arrêté: Acte 2 (FR) échoué — traduction FR non produite")
        return False

    # ── ACTE 3: EN ──
    from act3_en import run as act3
    if not act3():
        log("❌ Pipeline arrêté: Acte 3 (EN) échoué — traduction EN non produite")
        return False

    # ── RÉCUPÉRER LES ARTEFACTS ──
    es_data = load_step("act1_es_validated")
    fr_data = load_step("act2_fr_validated")
    en_data = load_step("act3_en_validated")

    if not es_data:
        log("❌ Artefact ES manquant")
        return False

    article_es = es_data["article_es"]
    article_fr = fr_data["article_fr"]
    article_en = en_data["article_en"]

    title_es = es_data.get("title_es", topic_title)
    title_fr = fr_data["title_fr"]
    title_en = en_data["title_en"]

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

    # ── CHECK ES DÉTECTION AVANT INSERT ──
    def _spanish_word(word: str) -> bool:
        """Détecte si un mot est typiquement espagnol par ses accents."""
        return bool(re.search(r'[áéíóúüñ¿¡]|ción|siones|miento|mientos|mente|dad|idades|blica|blico|gobierno|municipio|años|dónde|garcía|lópez|rodríguez|gónzalez|pérez|ández|ánica|ónico|idad|mente|iendo|ando', word, re.IGNORECASE))

    for lang_name, content in [("FR", content_fr), ("EN", content_en)]:
        words = content.split()
        if len(words) < 20:
            continue  # trop court pour juger
        es_count = sum(1 for w in words if _spanish_word(w))
        ratio = es_count / len(words)
        if ratio > 0.10:
            log(f"❌ DÉTECTION ES: {lang_name} contient {ratio*100:.1f}% de marqueurs espagnols (seuil 10%) — blocage INSERT")
            log(f"   {es_count}/{len(words)} mots avec marqueurs ES")
            # Log les 20 premiers mots suspectés pour débogage
            suspect_words = [w for w in words if _spanish_word(w)][:20]
            log(f"   Exemples: {', '.join(suspect_words)}")
            return False
        log("   ✅ Check ES {lang}: {ratio:.1f}% < 10% — OK".format(lang=lang_name, ratio=ratio*100))

    # ── FACT-CHECK FR/EN AVANT INSERT ──
    for lang_name, content in [("FR", content_fr), ("EN", content_en)]:
        lang_alerts = fact_check(content)
        if lang_alerts:
            for a in lang_alerts:
                log("[FACT-CHECK] [{t}] {e}: trouvé '{v}', attendu '{a}'".format(
                    t=a["type"], e=a["entite"], v=a["valeur_trouvee"], a=a["valeur_attendue"]))
            log("⚠️ {n} alerte(s) factuelle(s) détectée(s) dans article {lang}".format(
                n=len(lang_alerts), lang=lang_name))
            if len(lang_alerts) > 3:
                log("❌ FACT-CHECK: Plus de 3 alertes factuelles dans {lang} — résidus ES hallucinés, blocage INSERT".format(
                    lang=lang_name))
                return False
            log("   ✅ FACT-CHECK {lang}: < 3 alertes, poursuite autorisée".format(lang=lang_name))
        else:
            log("   ✅ FACT-CHECK {lang}: Aucune anomalie factuelle détectée".format(lang=lang_name))

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

    # ── PODCAST AUDIO (fix 08/08/2026, alerte A1) ─────────────────────
    # Re-branche la génération du podcast (script DeepSeek = fix + TTS Gateway).
    # Fire-and-forget : en arrière-plan, ne retarde ni ne fait échouer la
    # publication si le TTS plante. Reutilise podcast_background.py existant.
    try:
        import subprocess as _sp_journal
        _podcast_proc = _sp_journal.Popen(
            ["/srv/cct-journal/.venv/bin/python", "src/podcast_background.py", slug],
            cwd="/srv/cct-journal",
            stdout=_sp_journal.DEVNULL, stderr=_sp_journal.DEVNULL,
            start_new_session=True,
        )
        log(f"🎙️ Podcast lancé en arrière-plan pour {slug} (PID {_podcast_proc.pid})")
    except Exception as _pe:
        log(f"⚠️ Podcast arrière-plan non lancé: {_pe}")

    # Supprimé 07/08/2026 — les images du journal passent désormais par le canal
    # /api/static/DEPARTEMENT_ICONOGRAPHIE/JOURNAL servé temps réel par le RAG.
    # Plus besoin de rebuild + restart PWA à chaque publication (l'ancien bloc échouait
    # en EACCES sur /srv/pwa/.next/trace et retardait la mise en ligne).
    return ok


if __name__ == "__main__":
    cat_id = sys.argv[1] if len(sys.argv) > 1 else "047d7527-d161-4c25-a948-3e6f88aa8a9e"
    topic = "Gastronomía y vino de la Costa Tropical"
    if "--topic" in sys.argv:
        idx = sys.argv.index("--topic")
        topic = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else topic

    success = run(cat_id, topic)
    sys.exit(0 if success else 1)
