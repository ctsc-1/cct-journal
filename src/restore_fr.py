#!/usr/bin/env python3
"""
restore_fr.py — Restaure le contenu FR de l'article depuis le cache pipeline ORIGINAL
(act2_fr_validated.json) qui était propre (0 H2 espagnols), puis réinjecte les
marqueurs [[PHOTO:N]] (hero avant 1er H2, 1 image après chaque H2).
Cette version était la bonne avant que le bug SEO + mes back-fixs ne l'écrasent.
"""
import json, re, time
import psycopg2

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"
CACHE_FR = "/tmp/cache/journal-cache/act2_fr_validated.json"

def inject_photos(article_text, gallery_photos_count=16):
    """Injecte [[PHOTO:N]] : hero avant 1er H2, puis 1 par H2. galerie = [hero, sec1..sec15]."""
    lines = article_text.split("\n")
    result = []
    img_idx = 0
    used_hero = False
    for line in lines:
        stripped = line.strip()
        # Hero avant premier H2
        if not used_hero and stripped.startswith("## "):
            if img_idx < gallery_photos_count:
                result.append(f"[[PHOTO:{img_idx}]]")
                result.append("")
                img_idx += 1
                used_hero = True
        result.append(line)
        # Image après chaque H2
        if stripped.startswith("## ") and used_hero:
            if img_idx < gallery_photos_count:
                result.append("")
                result.append(f"[[PHOTO:{img_idx}]]")
                img_idx += 1
    return "\n".join(result)

def main():
    # Lire le FR propre depuis le cache
    with open(CACHE_FR) as f:
        data = json.load(f)
    fr_text = data.get("article_fr", "")
    title_fr = data.get("title_fr", "")

    # Nettoyer préfixes LLM éventuels
    for pref in ["Voici la traduction", "Voici la traduction en français", "TITRE :"]:
        if fr_text.strip().lower().startswith(pref.lower()):
            m = re.search(r"(^#+\s)", fr_text[len(pref):], re.MULTILINE)
            if m:
                fr_text = fr_text[len(pref):][m.start():]
            else:
                fr_text = fr_text[len(pref):]
            break

    # Réinjecter les marqueurs photo (le cache n'a pas d'images, c'était pur texte)
    fr_with_photos = inject_photos(fr_text)
    photos = len(re.findall(r"\[\[PHOTO:\d+\]\]", fr_with_photos))

    # Vérifier qu'il n'y a pas de H2 espagnols
    h2_es = ['tejedora','telar como resistencia','alfareros del barro','forja y el cuero',
             'relevo generacional','mujeres artesanas','la economía de lo hecho','laboratorio',
             'el futuro de los oficios','68% de los','museo vivo','la junta','las técnicas']
    h2s = re.findall(r"^##\s+(.+)$", fr_with_photos, re.MULTILINE)
    nb_es = sum(1 for h in h2s if any(m in h.lower() for m in h2_es))

    print(f"Titre FR: {title_fr}")
    print(f"FR: {len(fr_with_photos.split())} mots, {len(h2s)} H2, {photos} PHOTO, {nb_es} H2 espagnols")
    for h in h2s[:5]:
        print("  H2:", h[:60])

    # Phase de validation avant écriture
    if nb_es > 0:
        print("❌ ABORT : encore des H2 espagnols, on ne touche pas à la DB")
        return

    # Mettre à jour la DB (content_fr + title_fr) — sans toucher ES/EN
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("UPDATE articles SET content_fr=%s, title_fr=%s WHERE id=%s",
                (fr_with_photos, title_fr, ART_ID))
    print(f"✅ DB FR restaurée (id={ART_ID})")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
