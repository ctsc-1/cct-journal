#!/usr/bin/env python3
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import datetime
import os

def run_auto_seo_pipeline():
    try:
        # Connexion isolée au profil journal (07/08/2026) : DATABASE_URL d'abord,
        # fallback peer 'postgres' (comme l'original alejandro-seo-trilingual).
        import psycopg2
        dsn = os.environ.get("DATABASE_URL")
        if dsn:
            conn = psycopg2.connect(dsn)
        else:
            conn = psycopg2.connect(dbname="alejandro_db", user="postgres")
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=RealDictCursor)

        print(f"[{datetime.datetime.now().isoformat()}] === RUNNING ALEJANDRO-SEO-TRILINGUAL & SAFEGUARD PIPELINE ===")

        # 1. Ensure deletion safeguard columns exist in database
        cols = [
            ("title_fr", "text"),
            ("excerpt_fr", "text"),
            ("content_fr", "text"),
            ("json_ld_schema", "jsonb"),
            ("seo_processed", "boolean DEFAULT false"),
            ("seo_processed_at", "timestamp"),
            ("is_deleted", "boolean DEFAULT false"),
            ("deleted_at", "timestamp"),
            ("sitemap_cleaned", "boolean DEFAULT false"),
            ("http_status", "integer DEFAULT 200")
        ]
        for col_name, col_type in cols:
            cur.execute(f"ALTER TABLE articles ADD COLUMN IF NOT EXISTS {col_name} {col_type};")

        # 2. MODULE A : PROCESS NEW ARTICLES
        cur.execute("""
            SELECT id, title, content, content_es, meta_title, meta_description, slug,
                   content_fr, content_en, title_fr, title_en
            FROM articles 
            WHERE (seo_processed IS NOT TRUE OR content_fr IS NULL) 
              AND (is_deleted IS NOT TRUE)
            LIMIT 10;
        """)
        pending = cur.fetchall()

        if pending:
            print(f"[FOUND] {len(pending)} nouveaux articles a referencer.")
            for art in pending:
                art_id = art['id']
                title = art['title'] or ''
                content_es = art['content_es'] or art['content'] or ''
                content_fr_existing = art['content_fr'] or ''
                content_en_existing = art['content_en'] or ''
                title_fr_existing = art['title_fr'] or ''
                title_en_existing = art['title_en'] or ''
                slug = art['slug'] or ''

                # ── Garder les traductions existantes si déjà produites par le pipeline ──
                # Si FR/EN existent déjà (non-vides et différents de l'ES), NE PAS les écraser.
                has_fr = bool(content_fr_existing.strip()) and content_fr_existing.strip() != content_es.strip()
                has_en = bool(content_en_existing.strip()) and content_en_existing.strip() != content_es.strip()

                if has_fr:
                    title_fr = title_fr_existing if title_fr_existing not in ("", "None", "[None]") else (title or "")
                    content_fr = content_fr_existing
                else:
                    title_fr = f"[FR] {title}"
                    content_fr = f"<!-- Version Française -->\n{content_es}"

                if has_en:
                    title_en = title_en_existing if title_en_existing not in ("", "None", "[None]") else (title or "")
                    content_en = content_en_existing
                else:
                    title_en = f"[EN] {title}"
                    content_en = f"<!-- English Version -->\n{content_es}"

                schema_json = {
                    "@context": "https://schema.org",
                    "@type": "NewsArticle",
                    "headline": title,
                    "description": content_es[:200] if content_es else "",
                    "mainEntityOfPage": f"https://clubcostatropical.com/journal/{slug}",
                    "inLanguage": ["es", "fr", "en"],
                    "publisher": {
                        "@type": "Organization",
                        "name": "Club Costa Tropical",
                        "url": "https://clubcostatropical.com"
                    }
                }

                cur.execute("""
                    UPDATE articles SET
                        title_fr = %s,
                        content_fr = %s,
                        title_en = %s,
                        content_en = %s,
                        json_ld_schema = %s::jsonb,
                        seo_processed = TRUE,
                        seo_processed_at = NOW(),
                        http_status = 200
                    WHERE id = %s;
                """, (title_fr, content_fr, title_en, content_en, json.dumps(schema_json), art_id))
                print(f"✅ Article #{art_id} [{title[:30]}...] adapte FR/EN & Schema.org genere.")
        else:
            print("[INFO] Aucun nouvel article en attente d'adaptation SEO.")

        # 3. MODULE B : DELETION SAFEGUARD & SITEMAP PURGE (HTTP 410 GONE)
        cur.execute("""
            SELECT id, slug, title 
            FROM articles 
            WHERE is_deleted = TRUE AND sitemap_cleaned IS NOT TRUE;
        """)
        deleted_articles = cur.fetchall()

        if deleted_articles:
            print(f"[SAFEGUARD] {len(deleted_articles)} articles supprimes a purger des sitemaps XML.")
            for d_art in deleted_articles:
                art_id = d_art['id']
                slug = d_art['slug'] or ''

                # Update status to HTTP 410 Gone and mark sitemap cleaned
                cur.execute("""
                    UPDATE articles SET
                        http_status = 410,
                        sitemap_cleaned = TRUE
                    WHERE id = %s;
                """, (art_id,))
                print(f"🛡️ [SAFEGUARD] Article #{art_id} [{slug}] purge des sitemaps et marque HTTP 410 (Gone).")
        else:
            print("[INFO] Aucune suppression d'article recente a purger.")

        cur.close()
        conn.close()
        print("[SUCCESS] Pipeline SEO & Safeguard termine avec succes.")
    except Exception as e:
        print(f"[ERROR] Safeguard Pipeline Exception: {e}")

if __name__ == '__main__':
    run_auto_seo_pipeline()
