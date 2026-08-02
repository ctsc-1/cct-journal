# -*- coding: utf-8 -*-
"""
insere_fr_propre.py — Insère le FR final VALIDÉ (act2_fr_FINAL.json) dans la DB,
en ne touchant QUE content_fr et title_fr (ES et EN intacts).
Affiche AVANT insertion le résumé pour vérification, et n'écrit qu'après confirmation
que le FR est propre (0 fragment espagnol, 15 H2, marqueurs 0..15).
"""
import json
import re
import sys
sys.path.insert(0, "/srv/cct-journal/src")
import psycopg2

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"
SRC = "/tmp/cache/journal-cache/act2_fr_FINAL.json"

# Re-vérifications complètes avant insertion
with open(SRC) as f:
    data = json.load(f)
fr = data["article_fr"]
title_fr = data["title_fr"]

ES_VERBS = {
    "tiene","tienen","sabe","suelen","pueden","puede","quiere","quieren","vive",
    "viven","trabaja","trabajan","llega","llegan","guarda","resiste","resisten",
    "pierde","pierden","queda","quedan","surge","surgen","convierten","convierte",
    "amasa","muele","sostiene","fue","fueron","era","está","están","hay","hacen",
    "hace","deja","dejan","señala","explica","recuerda","cree","creen","dice","dicen",
}

# 1. Nb H2 et doublons
h2s = re.findall(r"^##\s+(.+)$", fr, re.MULTILINE)
from collections import Counter
dups = {k: v for k, v in Counter(h.lower() for h in h2s).items() if v > 1}

# 2. Marqueurs photo
markers = [int(m) for m in re.findall(r"\[\[PHOTO:(\d+)\]\]", fr)]
seq_ok = markers == list(range(len(markers)))

# 3. Fragments espagnols
tx = re.sub(r"\[\[PHOTO:\d+\]\]", "", fr)
frag = 0
for ph in re.split(r"(?<=[.!?])\s+", tx.replace("\n", " ")):
    if len(ph) < 12:
        continue
    mots = re.findall(r"[a-záéíóúñü]+", ph.lower())
    verbes = [w for w in mots if w in ES_VERBS]
    if verbes:
        motsfr = [w for w in mots if w in {"les","des","avec","dans","qui","que","une","pour","sur","sont","par","la","le"}]
        if len(motsfr) < len(mots) * 0.4:
            frag += 1

print("=== VALIDATION AVANT INSERTION ===")
print(f"Titre FR   : {title_fr}")
print(f"Mots       : {len(fr.split())}")
print(f"H2         : {len(h2s)} (doublons: {len(dups)})")
print(f"Marqueurs  : {len(markers)} (séquence 0..n correcte: {seq_ok})")
print(f"Fragments ES: {frag}")

ok = (len(dups) == 0 and seq_ok and frag == 0 and len(h2s) == 15)
print(f"\n=> PRÊT À INSÉRER : {ok}")
if not ok:
    print("❌ ABORT")
    sys.exit(1)

# Insertion : ne toucher que FR
pwd = open("/etc/cct-journal/pg.pwd").read().strip()
conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
conn.autocommit = True
cur = conn.cursor()
cur.execute("UPDATE articles SET content_fr=%s, title_fr=%s WHERE id=%s", (fr, title_fr, ART_ID))
print(f"✅ content_fr et title_fr mis à jour (id={ART_ID})")
cur.close(); conn.close()
