#!/usr/bin/env python3
"""
backfix_alpujarra_photos.py — Convertit les <figure> HTML en [[PHOTO:N]] dans les 3 langues
de l'article b2f6cfb6-7719-418b-b580-4bae29455095.
La PWA ne rend que [[PHOTO:N]] résolus via gallery_images.
"""
import re
import psycopg2

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"

def fix_content(text: str) -> str:
    """Remplace chaque <figure>...</figure> par [[PHOTO:N]] dans l'ordre d'apparition."""
    counter = {"n": 0}
    def repl(m):
        n = counter["n"]
        counter["n"] += 1
        return f"[[PHOTO:{n}]]"
    # Remplacer les blocs <figure>...</figure> (sur plusieurs lignes, non-greedy)
    return re.sub(r"<figure[^>]*>.*?</figure>", repl, text, flags=re.DOTALL)

def main():
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT content_es, content_fr, content_en, gallery_images FROM articles WHERE id=%s", (ART_ID,))
    row = cur.fetchone()
    es, fr, en, gallery = row
    print(f"Gallery: {len(gallery)} images")

    es_new = fix_content(es)
    fr_new = fix_content(fr)
    en_new = fix_content(en)

    import json
    # Nombre de PHOTO trouvés dans chaque langue
    photo_re = r"\[\[PHOTO:\d+\]\]"
    print(f"PHOTO ES: {len(re.findall(photo_re, es_new))}")
    print(f"PHOTO FR: {len(re.findall(photo_re, fr_new))}")
    print(f"PHOTO EN: {len(re.findall(photo_re, en_new))}")

    cur.execute(
        "UPDATE articles SET content_es=%s, content_fr=%s, content_en=%s WHERE id=%s",
        (es_new, fr_new, en_new, ART_ID))
    cur.close(); conn.close()
    print("✅ DB mise à jour (PHOTO markers injectés)")

if __name__ == "__main__":
    main()
