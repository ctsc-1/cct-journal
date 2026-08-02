# -*- coding: utf-8 -*-
"""
align_content_fr.py — Aligne les champs par défaut (title, content, excerpt)
sur la version FR valide (title_fr, content_fr) pour l'article journal.
C'est le standard de la table : `content`/`title` = version française par défaut.
Ne touche PAS content_es / content_en.
"""
import re
import sys
sys.path.insert(0, "/srv/cct-journal/src")
import psycopg2

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"
pwd = open("/etc/cct-journal/pg.pwd").read().strip()
conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
conn.autocommit = True
cur = conn.cursor()

# Lire le FR propre (content_fr) et le titre FR
cur.execute("SELECT content_fr, title_fr, excerpt_fr FROM articles WHERE id=%s", (ART_ID,))
cf, tf, ef = cur.fetchone()
print(f"content_fr: {len(cf)} chars")
print(f"title_fr  : {tf}")

# Excerpt = début propre du FR (300 chars)
if not ef or ef.startswith("[FR]") or ef.startswith("<!--"):
    clean = re.sub(r"^#\s+.*\n+", "", cf).strip()
    excerpt = clean[:300]
    if len(excerpt) == 300:
        excerpt = excerpt.rsplit(" ", 1)[0]
else:
    excerpt = ef

# Aligner content/title/excerpt sur le FR
cur.execute(
    "UPDATE articles SET content=%s, title=%s, excerpt=%s WHERE id=%s",
    (cf, tf, excerpt, ART_ID),
)
print("✅ content/title/excerpt alignés sur le FR propre")
cur.close(); conn.close()
