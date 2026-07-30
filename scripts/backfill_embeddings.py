#!/usr/bin/env python3
"""
backfill_embeddings.py — Remplit la colonne embedding des articles sans embedding.
Usage: python3 backfill_embeddings.py [--limit N] [--dry-run]

Utilise la Gateway (127.0.0.1:4000/v1/embed) — gemini-embedding-2, RPD illimité.
Exécution directe depuis Zambra (VPS2), pas via le pipeline alejandro-journal.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import httpx
import psycopg2

# ─── CONFIG ─────────────────────────────────────────────────
GATEWAY = "http://127.0.0.1:4000"
MODEL = "gemini-embedding-2"
BATCH_SIZE = 5
SLEEP_BETWEEN = 2  # secondes entre chaque embedding (anti-stress)


def get_db_url() -> str:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            result = subprocess.run(
                ["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                db_url = result.stdout.strip().split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    return db_url or "postgresql:///alejandro_db"


def get_embedding(text: str) -> list[float] | None:
    """Génère un embedding via Gateway."""
    try:
        r = httpx.post(
            f"{GATEWAY}/v1/embed",
            json={
                "model": MODEL,
                "contents": text[:8000],
                "task_type": "RETRIEVAL_DOCUMENT",
                "output_dimensionality": 768,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("embedding") or data.get("data", [{}])[0].get("embedding")
    except Exception as e:
        print(f"  ⚠️ Embedding échoué: {e}")
        return None


def backfill(limit: int = 0, dry_run: bool = False):
    """Remplit les embeddings manquants."""
    db_url = get_db_url()
    conn = psycopg2.connect(db_url, connect_timeout=10)
    cur = conn.cursor()

    # Compter
    cur.execute(
        "SELECT COUNT(*) FROM articles "
        "WHERE is_published = TRUE AND embedding_gemini IS NULL"
    )
    total = cur.fetchone()[0]
    print(f"📊 {total} articles sans embedding (publiés)")

    if dry_run:
        cur.execute(
            "SELECT id, title_es, published_at FROM articles "
            "WHERE is_published = TRUE AND embedding IS NULL "
            "ORDER BY published_at DESC"
            + (f" LIMIT {limit}" if limit else "")
        )
        rows = cur.fetchall()
        print(f"\n🔍 DRY RUN — {len(rows)} articles seraient traités:")
        for row in rows:
            print(f"  [{row[2].strftime('%Y-%m-%d') if row[2] else '?'}] {row[1][:80]}")
        cur.close()
        conn.close()
        return

    # Traitement
    query = (
        "SELECT id, COALESCE(content_es, '') FROM articles "
        "WHERE is_published = TRUE AND embedding_gemini IS NULL "
        "ORDER BY published_at DESC"
    )
    if limit:
        query += f" LIMIT {limit}"

    cur.execute(query)
    rows = cur.fetchall()

    print(f"🚀 Génération de {len(rows)} embeddings...")
    success = 0
    for i, (article_id, content) in enumerate(rows):
        print(f"  [{i+1}/{len(rows)}] {article_id[:8]}...", end=" ", flush=True)

        if not content or len(content) < 500:
            print("⏩ contenu trop court, skip")
            continue

        embedding = get_embedding(content)
        if not embedding:
            print("❌ échec")
            continue

        if not dry_run:
            try:
                cur.execute(
                    "UPDATE articles SET embedding_gemini = %s::vector, updated_at = NOW() WHERE id = %s",
                    (embedding, article_id),
                )
                conn.commit()
                print(f"✅ {len(embedding)}d")
                success += 1
            except Exception as e:
                conn.rollback()
                print(f"❌ DB: {e}")
        else:
            print(f"✅ (dry)")

        time.sleep(SLEEP_BETWEEN)

    cur.close()
    conn.close()

    print(f"\n✅ {success}/{len(rows)} embeddings générés")


if __name__ == "__main__":
    limit = 0
    dry_run = "--dry-run" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    backfill(limit=limit, dry_run=dry_run)
