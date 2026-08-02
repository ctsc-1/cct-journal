#!/usr/bin/env python3
"""
fix_h2_fr.py — Corrige UNIQUEMENT les 9 H2 restés en espagnol dans la version FR
de l'article b2f6cfb6, en les remplaçant par leur traduction française propre.
Le reste du FR est correct et n'est pas touché.
Vérification : après correction, aucun H2 FR ne doit être identique à un H2 ES.
"""
import re
import psycopg2

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"

# Correction ciblée : H2 FR espagnol -> H2 FR correct (basé sur la version EN propre)
CORRECTIONS = [
    ("Ana Martínez, la tejedora de Bubión premiada por la Junta",
     "Ana Martínez, l'artisane de Bubión récompensée par la Junte"),
    ("El telar como resistencia: técnicas que se niegan a morir",
     "Le métier à tisser comme résistance : des techniques qui refusent de mourir"),
    ("Alfareros del barro: el oficio que moldea la identidad",
     "Les potiers de la terre : un artisanat qui façonne l'identité"),
    ("La forja y el cuero: oficios que forjan carácter",
     "La forge et le cuir : des métiers qui forgent le caractère"),
    ("La Feria de Oficios de Granada : vitrine et espoir",
     "La Foire des Métiers de Grenade : vitrine et espoir"),
    ("Relevo generacional: la asignatura pendiente",
     "La relève générationnelle : le défi en suspens"),
    ("Mujeres artesanas: el liderazgo femenino en los talleres",
     "Femmes artisanes : le leadership féminin dans les ateliers"),
    ("La economía de lo hecho a mano: precios, mercados y futuro",
     "L'économie du fait main : prix, marchés et avenir"),
    ("El futuro de los oficios: entre la memoria y la innovación",
     "L'avenir des métiers : entre mémoire et innovation"),
]

def main():
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
    cur = conn.cursor()
    cur.execute("SELECT content_fr FROM articles WHERE id=%s", (ART_ID,))
    fr = cur.fetchone()[0]
    cur.execute("SELECT content_es FROM articles WHERE id=%s", (ART_ID,))
    es = cur.fetchone()[0]

    # Vérifier que chaque ancien H2 existe bien dans le FR avant remplacement
    applique = 0
    for ancien, nouveau in CORRECTIONS:
        # Normaliser la ligne H2 exacte
        old_line = f"## {ancien}"
        new_line = f"## {nouveau}"
        if old_line in fr:
            fr = fr.replace(old_line, new_line)
            applique += 1
            print(f"  ✅ {ancien[:45]}")
        else:
            # Essayer sans variantes d'espaces
            old_re = re.compile(rf"^##\s*{re.escape(ancien)}\s*$", re.MULTILINE)
            if old_re.search(fr):
                fr = old_re.sub(new_line, fr)
                applique += 1
                print(f"  ✅ (regex) {ancien[:45]}")
            else:
                print(f"  ⚠️ NON TROUVÉ: {ancien[:45]}")

    print(f"\n{applique}/{len(CORRECTIONS)} H2 corrigés")

    # VÉRIFICATION FINALE : aucun H2 FR ne doit être identique à un H2 ES
    h2_es = [h.lower().strip() for h in re.findall(r"^##\s+(.+)$", es, re.MULTILINE)]
    h2_fr = [h.lower().strip() for h in re.findall(r"^##\s+(.+)$", fr, re.MULTILINE)]
    restants = [h for h in h2_fr if h in h2_es]
    print(f"H2 FR identiques à ES restants : {len(restants)}")
    for r in restants:
        print(f"   ❌ {r[:60]}")

    # N'écrire QUE si aucun H2 espagnol ne subsiste (validation avant écriture)
    if restants:
        print("❌ ABORT : H2 espagnols restants, pas de mise à jour")
    else:
        cur.execute("UPDATE articles SET content_fr=%s WHERE id=%s", (fr, ART_ID))
        conn.commit()
        print(f"✅ DB FR mise à jour (id={ART_ID}) — {len(fr.split())} mots")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
