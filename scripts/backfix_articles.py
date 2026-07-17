#!/usr/bin/env python3
"""Backfix : corrige les 37 articles déjà publiés avec les bugs du pipeline.

Applique _strip_title_and_hero(), corrige les alt text des images FR/EN,
recalcule les excerpts avec word-aware truncation.
"""
import sys
import os
import re
import json

# Ajouter les chemins
sys.path.insert(0, '/srv/cct-journal/src')
sys.path.insert(0, '/srv/rag-engine')

from publish import _strip_title_and_hero, _extract_excerpt

# Connexion DB
db_url = open('/srv/rag-engine/.env').read().strip()
for line in db_url.split('\n'):
    if line.startswith('DATABASE_URL='):
        db_url = line.split('=', 1)[1].strip().strip("'").strip('"')
        break

import psycopg2
conn = psycopg2.connect(db_url, connect_timeout=10)
cur = conn.cursor()

# 1. Récupérer tous les articles avec H1 dans le contenu
cur.execute("""
    SELECT id, slug, content, content_es, content_en, 
           excerpt, excerpt_es, excerpt_en,
           featured_image_url, gallery_images
    FROM articles 
    WHERE published_at >= '2026-06-01' AND content ~ '^#'
    ORDER BY published_at
""")
rows = cur.fetchall()
print(f"📊 {len(rows)} articles à corriger\n")

fixed = 0
errors = 0

for row in rows:
    aid, slug, content_fr, content_es, content_en, \
        excerpt_fr, excerpt_es, excerpt_en, \
        hero_url, gallery_json = row

    try:
        changes = []
        
        # --- Étape 1: Stripper H1 et hero image ---
        new_fr = _strip_title_and_hero(content_fr) if content_fr else ""
        new_es = _strip_title_and_hero(content_es) if content_es else ""
        new_en = _strip_title_and_hero(content_en) if content_en else ""
        
        if new_fr != content_fr:
            changes.append("H1/hero")
        
        # --- Étape 2: Traduire les alt text des images FR/EN ---
        if new_fr and new_es:
            # Extraire les H2 de chaque langue (par position)
            h2_fr = re.findall(r'^##\s+(.+?)(?:\s*\n|$)', new_fr, re.MULTILINE)
            h2_en = re.findall(r'^##\s+(.+?)(?:\s*\n|$)', new_en, re.MULTILINE) if new_en else []
            
            # Remplacer les alt text espagnols dans FR
            if h2_fr:
                counter = [0]
                def _make_replacer(h2s, c):
                    def replacer(m):
                        url = m.group(2)
                        alt = h2s[c[0]] if c[0] < len(h2s) else m.group(1)
                        c[0] += 1
                        return f'![{alt}]({url})'
                    return replacer
                new_fr = re.sub(r'!\[([^\]]+)\]\(([^)]+)\)', _make_replacer(h2_fr, counter), new_fr)
                if counter[0] > 0:
                    changes.append(f"alt FR ({counter[0]} img)")
            
            # Remplacer les alt text espagnols dans EN
            if h2_en:
                counter_en = [0]
                new_en = re.sub(r'!\[([^\]]+)\]\(([^)]+)\)', _make_replacer(h2_en, counter_en), new_en)
                if counter_en[0] > 0:
                    changes.append(f"alt EN ({counter_en[0]} img)")
        
        # --- Étape 3: Recalculer les excerpts ---
        new_ex_fr = _extract_excerpt(new_fr if new_fr else content_fr)
        new_ex_es = _extract_excerpt(new_es if new_es else content_es)
        new_ex_en = _extract_excerpt(new_en if new_en else content_en)
        
        if new_ex_fr != excerpt_fr:
            changes.append("excerpt")
        
        if not changes:
            continue  # Rien à changer
        
        # --- UPDATE ---
        cur.execute("""
            UPDATE articles SET
                content = %s, content_es = %s, content_en = %s,
                excerpt = %s, excerpt_es = %s, excerpt_en = %s
            WHERE id = %s
        """, (
            new_fr, new_es, new_en,
            new_ex_fr, new_ex_es, new_ex_en,
            aid
        ))
        fixed += 1
        print(f"  ✅ {slug[:40]:40s} → {', '.join(changes)}")
        
    except Exception as e:
        errors += 1
        print(f"  ❌ {slug[:40]:40s} → ERREUR: {e}")

conn.commit()
conn.close()
print(f"\n✅ {fixed} articles corrigés | ❌ {errors} erreurs")
