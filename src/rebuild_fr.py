# -*- coding: utf-8 -*-
"""
rebuild_fr.py — Restaure le FR depuis le cache pipeline original (base fiable),
corrige les H2 espagnols/mixtes, les doublons, injecte les [[PHOTO:N]], puis
VALIDE (aucun H2 = ES, aucun résidu espagnol) AVANT d'écrire en DB.

Étapes séquencées : 1) charger cache, 2) corriger H2, 3) injecter photos,
4) valider, 5) écrire DB. On ne passe à l'étape suivante que si la précédente est OK.
"""
import json
import re
import psycopg2

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"
CACHE_FR = "/tmp/cache/journal-cache/act2_fr_validated.json"

# Corrections de H2 sur la base CACHE (H2 espagnol/mixte -> FR propre)
H2_CORRECTIONS = [
    ("Ana Martínez, la tisserande de Bubión récompensée par la Junta",
     "Ana Martínez, la tisserande de Bubión récompensée par la Junte"),
    ("Alfareros del barro: el oficio que moldea la identidad",
     "Les potiers de la terre : un artisanat qui façonne l'identité"),
    ("Esparteros : la l'alfa qui soutient la mémoire",
     "Les espartiers : l'alfa qui soutient la mémoire"),
    ("La Feria de Oficios de Granada : vitrine et espoir",
     "La Foire des Métiers de Grenade : vitrine et espoir"),
    ("Relevo generacional: la asignatura pendiente",
     "La relève générationnelle : le défi en suspens"),
]

# Mots espagnols de contrôle (hors noms propres/lieux légitimes)
VRAI_ES = {
    "años","también","tambien","después","despues","entonces","siempre","trabajo",
    "pueblo","sierra","tierra","hacer","queda","todas","solo","sobre","entre",
    "hacia","desde","porque","cuando","donde","pero","muy","sin","bajo","tras",
    "cada","esta","este","esa","eso","para","ella","ellos","tiene","hace","tienen",
    "durante","primera","segunda","luego","así","asi","mismo","misma","ahora",
    "nada","todo","toda","unos","unas","más","mas","son","está","estan","hayan",
    "fue","han","era","fueron","estaría","fórmula","experimento","comarca",
    "observan","atención","datos","regiones","vaciada","modelo","contradice",
    "fatalismo","demográfico","interior","peninsular","números","panorama",
    "ensayado","combina","combinación","cuyos","ofrecen","resultados","trata",
    "solución","definitiva","caso","estudio","pesa","sí","visto",
}
# Mots qui sont aussi du français (ne pas compter)
FR_OK = {"entre","sur","si","son","est","il","la","le","de","la","on","en","et",
         "mar","pan","vino","casa","via","pero"}


def charge_cache():
    with open(CACHE_FR) as f:
        return json.load(f)


def corriger_h2(fr):
    for ancien, nouveau in H2_CORRECTIONS:
        old_line = f"## {ancien}"
        new_line = f"## {nouveau}"
        if old_line in fr:
            fr = fr.replace(old_line, new_line)
        else:
            old_re = re.compile(rf"^##\s*{re.escape(ancien)}\s*$", re.MULTILINE)
            if old_re.search(fr):
                fr = old_re.sub(new_line, fr)
    return fr


def supprimer_doublon_h1(fr):
    """Supprime les H1/H2 en double quasi identiques (ex: '68%' répété)."""
    lines = fr.split("\n")
    vus = set()
    out = []
    for line in lines:
        if line.strip().startswith(("## ", "# ")):
            norm = re.sub(r"\s+", " ", line.strip().lower())[:60]
            if norm in vus:
                continue  # doublon
            vus.add(norm)
        out.append(line)
    return "\n".join(out)


def injecter_photos(fr):
    lines = fr.split("\n")
    result = []
    img_idx = 0
    used_hero = False
    for line in lines:
        stripped = line.strip()
        if not used_hero and stripped.startswith("## "):
            if img_idx < 16:
                result.append(f"[[PHOTO:{img_idx}]]")
                result.append("")
                img_idx += 1
                used_hero = True
        result.append(line)
        if stripped.startswith("## ") and used_hero:
            if img_idx < 16:
                result.append("")
                result.append(f"[[PHOTO:{img_idx}]]")
                img_idx += 1
    return "\n".join(result)


def residus_es(texte):
    """Retourne la liste des résidus espagnols (mots ES sans équivalent FR) trouvés."""
    corps = re.sub(r"^#+ .+$", "", texte, flags=re.MULTILINE)
    corps = re.sub(r"\[\[PHOTO:\d+\]\]", "", corps)
    words = re.findall(r"[a-záéíóúñü]+", corps.lower())
    residus = []
    for w in words:
        if w in VRAI_ES and w not in FR_OK and len(w) >= 4:
            residus.append(w)
    return residus


def main():
    # ÉTAPE 1 — charger la base cache
    data = charge_cache()
    fr = data.get("article_fr", "")
    title_fr = data.get("title_fr", "")
    print(f"ÉTAPE 1: Cache chargé — {len(fr.split())} mots, {len(re.findall(r'^##', fr, re.MULTILINE))} H2")

    # ÉTAPE 2 — corriger les H2 espagnols/mixtes
    fr = corriger_h2(fr)
    fr = supprimer_doublon_h1(fr)
    h2s = re.findall(r"^##\s+(.+)$", fr, re.MULTILINE)
    print(f"ÉTAPE 2: H2 corrigés — {len(h2s)} H2 uniques")

    # ÉTAPE 3 — injecter les marqueurs photo
    fr_photos = injecter_photos(fr)
    nb_photo = len(re.findall(r"\[\[PHOTO:\d+\]\]", fr_photos))
    print(f"ÉTAPE 3: {nb_photo} marqueurs photo injectés")

    # ÉTAPE 4 — VALIDATION
    # 4a: aucun H2 FR identique à un H2 ES
    # Récupérer les H2 ES depuis la DB (pour comparaison)
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
    cur = conn.cursor()
    cur.execute("SELECT content_es FROM articles WHERE id=%s", (ART_ID,))
    es = cur.fetchone()[0]
    h2_es = [h.lower().strip() for h in re.findall(r"^##\s+(.+)$", es, re.MULTILINE)]
    h2_fr_final = [h.lower().strip() for h in re.findall(r"^##\s+(.+)$", fr_photos, re.MULTILINE)]
    h2_identique = [h for h in h2_fr_final if h in h2_es]
    # 4b: résidus espagnols dans les sections
    residus = residus_es(fr_photos)

    print(f"ÉTAPE 4: Validation — H2 identiques à ES: {len(h2_identique)}, résidus ES: {len(residus)}")
    for h in h2_identique:
        print(f"   ❌ H2 espagnol: {h[:55]}")
    if residus:
        print(f"   ❌ Résidus: {residus[:20]}")
        # montrer un contexte
        m = re.search(r"(.{0,30})" + re.escape(residus[0]) + r"(.{0,30})", fr_photos, re.IGNORECASE)
        if m:
            print(f"      ...{m.group(0)}...")

    if h2_identique or residus:
        print("❌ ABORT: FR pas encore propre, on ne touche pas à la DB")
        cur.close(); conn.close()
        return

    # ÉTAPE 5 — écrire en DB (uniquement si validation passée)
    cur.execute("UPDATE articles SET content_fr=%s, title_fr=%s WHERE id=%s",
                (fr_photos, title_fr, ART_ID))
    conn.commit()
    print(f"ÉTAPE 5: ✅ DB FR écrite ({len(fr_photos.split())} mots, id={ART_ID})")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
