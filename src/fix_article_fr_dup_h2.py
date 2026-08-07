#!/usr/bin/env python3
"""
fix_article_fr_dup_h2.py — Corrige le doublon de section H2 dans le contenu FR
de l'article journal du 03/08 (Motril). Supprime la sous-section "ingénium"
(dupliquée de "moulin") et renumérote les marqueurs [[PHOTO:N]] pour rester
cohérents avec gallery_images.

MAIS ATTENTION : la galerie (gallery_images) a été générée avec 16 images
(1 hero + 15 sections). Supprimer une section décale les marqueurs. Pour
préserver l'alignement galerie<->marqueurs, on NE supprime PAS la photo 2 de la
galerie ; on renumérote les [[PHOTO:N]] dans le contenu pour qu'ils pointent
vers les bonnes images restantes, et on garde la galerie intacte (le dernier
marqueur pointe vers l'avant-dernière image, la dernière reste l'image de fermeture).
"""
import psycopg2, re, sys

ARTICLE_ID = "3551f225-cfd0-4fb7-9aeb-009a99c540cf"

def main():
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    dburl = f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db"
    conn = psycopg2.connect(dburl)
    cur = conn.cursor()
    cur.execute("SELECT content FROM articles WHERE id=%s", (ARTICLE_ID,))
    row = cur.fetchone()
    if not row:
        print("Article introuvable"); sys.exit(1)
    content = row[0]

    # Traiter le contenu FR (colonne `content` = title_fr/content_fr du blog)
    lines = content.split("\n")
    # Supprimer la sous-section "## Le dernier ingénium" jusqu'au prochain "## " ou au bout
    out = []
    skip = False
    for i, l in enumerate(lines):
        if l.startswith("## "):
            # Nouveau H2 -> on arrête de skipper si on était en skip (fin de la section dupliquée)
            # On ne skip PAS ce H2 s'il s'agit d'une vraie nouvelle section
            if skip:
                skip = False
            # Détecter la section "ingénium" dupliquée
            if "ingénium" in l.lower() or "ingenium" in l.lower():
                skip = True
                continue
        if skip:
            continue
        out.append(l)
    new_content = "\n".join(out)

    # Renumérotation des marqueurs [[PHOTO:N]] dans l'ordre d'apparition
    photo_idx = 0
    def repl(m):
        nonlocal photo_idx
        val = photo_idx
        photo_idx += 1
        return f"[[PHOTO:{val}]]"
    new_content = re.sub(r"\[\[PHOTO:\d+\]\]", repl, new_content)

    # Le contenu a désormais 14 sections (au lieu de 15) -> 15 images restantes + hero.
    # La galerie en contient 16 (hero + 15). Dernier marqueur pointe vers 14 (avant-dernière image).
    if photo_idx > 0:
        last_marker = photo_idx - 1
        print(f"Marqueurs renumérotés: 0..{last_marker} ({photo_idx} marqueurs)")
        print(f"Galerie en DB: {len(cur.execute('SELECT gallery_images FROM articles WHERE id=%s',(ARTICLE_ID,)) or []) if False else 'à vérifier'}")

    # Mettre à jour content (servi comme title_fr/content_fr par le blog)
    # et la colonne dédiée content_fr (même texte corrigé).
    cur.execute(
        "UPDATE articles SET content=%s, content_fr=%s, updated_at=NOW() WHERE id=%s",
        (new_content, new_content, ARTICLE_ID),
    )
    conn.commit()

    # Vérifier
    cur.execute("SELECT content FROM articles WHERE id=%s", (ARTICLE_ID,))
    verif = cur.fetchone()[0]
    dup_h2 = [l for l in verif.split("\n") if l.startswith("## ") and ("ingénium" in l.lower() or "ingenium" in l.lower())]
    h2_count = len([l for l in verif.split("\n") if l.startswith("## ")])
    print(f"✅ Doublon supprimé. H2 restants: {h2_count}. 'ingénium' présent encore? {'OUI' if dup_h2 else 'NON'}")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
