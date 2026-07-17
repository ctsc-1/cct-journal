#!/usr/bin/env python3
"""rewrite_batch.py — Réécriture batch des articles courts du Journal CCT.

Usage:
    python3 rewrite_batch.py              # Réécrit tous les articles < 6000 chars
    python3 rewrite_batch.py --dry-run     # Montre ce qui serait fait sans rien écrire
    python3 rewrite_batch.py --limit 5     # Réécrit les 5 plus courts seulement
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rewrite")

MIN_CHARS = 5000  # Seuil batch réécriture (le QC pipeline garde 6000 pour les nouveaux articles)

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")

def get_db_url() -> str:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            import subprocess
            r = subprocess.run(["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                             capture_output=True, text=True, timeout=5)
            line = r.stdout.strip()
            if line:
                db_url = line.split("=", 1)[1].strip("'\"")
        except Exception:
            pass
    return db_url or "postgresql:///alejandro_db"


def fetch_short_articles(limit: Optional[int] = None) -> List[Dict]:
    """Récupère les articles < 6000 chars, triés par longueur croissante."""
    conn = psycopg2.connect(get_db_url(), connect_timeout=5)
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, slug, title, title_es, title_en,
                       COALESCE(content, '') as content,
                       COALESCE(content_es, '') as content_es,
                       COALESCE(content_en, '') as content_en,
                       LENGTH(COALESCE(content, '')) as chars
                FROM articles
                WHERE LENGTH(COALESCE(content, '')) < %s
                  AND is_published = true
                ORDER BY chars ASC
            """, (MIN_CHARS,))
            rows = cur.fetchall()
            if limit:
                return rows[:limit]
            return rows
    finally:
        conn.close()


def gateway_chat(model: str, system: str, user: str, caller: str = "rewrite-batch") -> str:
    """Appelle la Gateway via /v1/chat/completions (pas de rate limit)."""
    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                "caller": caller,
                "max_tokens": 16384,
            },
            timeout=180,
        )
        r.raise_for_status()
        return (r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    except Exception as e:
        log.error(f"Gateway chat error ({model}): {e}")
        return ""


def deep_search(topic_name: str, topic_desc: str) -> str:
    """Recherche via Gateway /v1/chat/completions (pas de rate limit)."""
    system = (
        "Eres un investigador especializado en la Costa Tropical y Andalucía. "
        "Proporciona información detallada y verificada sobre el tema solicitado. "
        "Incluye datos concretos: cifras, fechas, nombres de lugares y personas reales. "
        "Mínimo 800 caracteres de información útil."
    )
    user = f"Tema: {topic_name}\n\n{topic_desc}\n\nProporciona información detallada sobre este tema para escribir un artículo de fondo."
    return gateway_chat("gemini-2.5-flash", system, user)


def generate_es(topic_name: str, deepsearch_result: str) -> str:
    """Génère l'article ES à partir du DeepSearch."""
    system = (
        "Eres Alejandro Ortega, redactor jefe del Club Costa Tropical. "
        "Escribes artículos de fondo para la Costa Tropical (Granada, España). "
        "Estilo: prensa económica elegante, tono factual, datos > adjetivos. "
        "Mínimo 6000 caracteres. Frases de máximo 15 palabras. "
        "Máximo 1 adjetivo por frase. Cero metáforas. "
        "Primera frase = núcleo de la información con cifras clave. "
        "NO uses 'descubre', 'sumérgete', 'bienvenido'. "
        "NO seas sensacionalista. Los hechos hablan por sí mismos."
    )
    user = f"""Tema: {topic_name}

Investigación:
{deepsearch_result[:12000]}

Escribe el artículo completo en español (castellano) siguiendo el estilo de Alejandro Ortega.
ABSORUTAMENTE MÍNIMO 7000 caracteres. Estructura con secciones (H2)."""
    return gateway_chat("gemini-3.1-flash-lite", system, user)


def translate(text: str, target_lang: str) -> str:
    """Traduit l'article ES vers FR ou EN."""
    lang_name = {"fr": "français", "en": "anglais"}.get(target_lang, target_lang)
    system = f"Traduis le texte suivant en {lang_name}. Garde le format markdown (titres H2, etc.). Ne traduis pas les noms de lieux andalous. Ne mets aucun préfixe d'introduction."
    result = gateway_chat("gemini-2.5-flash-lite", system, text)
    # Strip LLM prefixes
    import re
    for pat in [
        r'^Voici la traduction.*?(?:\n|$)\s*',
        r'^Here is (?:a|the) translation.*?(?:\n|$)\s*',
        r'^Absolument.*?(?:\n|$)\s*',
        r'^Absolutely.*?(?:\n|$)\s*',
    ]:
        result = re.sub(pat, '', result)
    return result.strip()


def update_article(article_id: str, content_es: str, content_fr: str, content_en: str) -> bool:
    """UPDATE l'article avec les nouveaux contenus."""
    now = datetime.now(timezone.utc).isoformat()
    conn = psycopg2.connect(get_db_url(), connect_timeout=5)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE articles
                SET content = %s,
                    content_es = %s,
                    content_en = %s,
                    word_count = %s,
                    updated_at = %s
                WHERE id = %s
            """, (
                content_fr, content_es, content_en,
                max(len(content_es.split()), len(content_fr.split()), len(content_en.split())),
                now, article_id
            ))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"Update error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def rewrite_article(article: Dict, dry_run: bool = False) -> bool:
    """Réécrit un article complet."""
    slug = article['slug']
    title_fr = article['title']
    title_es = article['title_es'] or title_fr
    topic_name = title_es
    topic_desc = f"Article à réécrire : {title_fr} (slug: {slug})"

    log.info(f"\n{'='*60}")
    log.info(f"📝 Réécriture : {title_fr}")
    log.info(f"   Slug: {slug} | Actuellement: {article['chars']} chars")

    if dry_run:
        log.info(f"   [DRY RUN] Serait réécrit")
        return True

    # Petite pause avant DeepSearch pour éviter rate limit
    time.sleep(3)

    # 1. DeepSearch
    log.info(f"   🔍 DeepSearch...")
    research = deep_search(topic_name, topic_desc)
    if not research or len(research) < 500:
        log.info(f"   ℹ️ Fallback: génération directe sans DeepSearch")
        content_es = generate_es(topic_name, "")
    else:
        log.info(f"   ✅ DeepSearch: {len(research)}c")
        content_es = generate_es(topic_name, research)
    if not content_es or len(content_es) < MIN_CHARS:
        log.warning(f"   ⚠️ Génération ES trop courte ({len(content_es) if content_es else 0}c), skip")
        return False
    log.info(f"   ✅ ES: {len(content_es)}c")

    # 3. Traductions
    log.info(f"   🌐 Traduction FR...")
    content_fr = translate(content_es, "fr")
    if not content_fr or len(content_fr) < 200:
        content_fr = f"# {title_fr}\n\n[Article en cours de traduction]"
    log.info(f"   ✅ FR: {len(content_fr)}c")

    log.info(f"   🌐 Traduction EN...")
    content_en = translate(content_es, "en")
    if not content_en or len(content_en) < 200:
        content_en = f"# {title_fr}\n\n[Article being translated]"
    log.info(f"   ✅ EN: {len(content_en)}c")

    # 4. UPDATE DB
    log.info(f"   💾 Sauvegarde DB...")
    if update_article(article['id'], content_es, content_fr, content_en):
        log.info(f"   ✅ Article mis à jour !")
        return True
    else:
        log.error(f"   ❌ Erreur DB")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Réécriture batch des articles courts")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans écriture")
    parser.add_argument("--limit", type=int, default=5, help="Nombre max d'articles (défaut: 5)")
    args = parser.parse_args()

    articles = fetch_short_articles(args.limit)
    log.info(f"📊 {len(articles)} articles à réécrire (seuil: {MIN_CHARS} chars)")

    if not articles:
        log.info("✅ Aucun article à réécrire")
        return

    stats = {"rewritten": 0, "skipped": 0, "errors": 0}
    start = time.time()

    for i, article in enumerate(articles, 1):
        log.info(f"\n[{i}/{len(articles)}]")
        try:
            ok = rewrite_article(article, dry_run=args.dry_run)
            if ok:
                stats["rewritten"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            log.error(f"❌ Erreur: {e}")
            stats["errors"] += 1

        # Pause entre articles pour éviter de saturer la Gateway
        if i < len(articles):
            time.sleep(5)

    elapsed = time.time() - start
    log.info(f"\n{'='*60}")
    log.info(f"📊 RÉSULTATS")
    log.info(f"   ✅ Réécrits: {stats['rewritten']}")
    log.info(f"   ⏭️ Skippés: {stats['skipped']}")
    log.info(f"   ❌ Erreurs: {stats['errors']}")
    log.info(f"   ⏱️ Temps: {elapsed:.0f}s ({elapsed/60:.1f}min)")


if __name__ == "__main__":
    main()
