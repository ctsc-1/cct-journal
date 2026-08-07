# -*- coding: utf-8 -*-
"""
retraduira_fr_complet.py — Re-traduit TOUT le FR depuis l'ES original (base fiable),
section par section, avec VÉRIFICATION LINGUISTIQUE STRICTE de chaque section
(on rejette et réessaie si la section contient encore des fragments espagnols).

Ordre de production validé par Marc : article ES -> traductions FR/EN (élément par
élément : titre, intro GEO, puis chaque H2). Conforme séquençage (pause 5s).

Résultat écrit dans /tmp/cache/journal-cache/act2_fr_REBUILD.json pour validation
humaine/machine AVANT insertion en DB. N'écrit PAS directement en DB.
"""
import json
import re
import time
import sys
sys.path.insert(0, "/srv/cct-journal/src")
import httpx
import psycopg2

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"
OUT = "/tmp/cache/journal-cache/act2_fr_REBUILD.json"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"

def _key():
    try:
        with open("/root/.hermes/config.yaml") as f:
            m = re.search(r"deepseek:\s*\n\s+api_key:\s*(\S+)", f.read())
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""
KEY = _key()

def _llm(prompt, max_tokens=8192, temp=0.2):
    r = httpx.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens, "temperature": temp},
        timeout=150,
    )
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()

# Verbes/mots-outils espagnols discriminants pour rejeter une mauvaise traduction
ES_VERBS = {
    "tiene","tienen","sabe","suelen","pueden","puede","quiere","quieren","vive",
    "viven","trabaja","trabajan","llega","llegan","guarda","resiste","resisten",
    "pierde","pierden","queda","quedan","surge","surgen","convierten","convierte",
    "amasa","muele","sostiene","fue","fueron","era","está","están","hay","hacen",
    "hace","deja","dejan","señala","explica","recuerda","cree","creen","dice","dicen",
}
ES_FUN = {
    "del","los","las","una","un","para","con","por","pero","sobre","entre","desde",
    "hacia","como","cuando","donde","todo","toda","todos","todas","su","sus","muy",
    "más","menos","sin","cada","el","la","y","de","que","el","al","se","es","son",
}

def detecter_espagnol(texte):
    """Retourne les fragments espagnols trouvés (verbes ES + cohérence)."""
    # Ignorer les marqueurs photo et les sous-titres pour l'analyse
    tx = re.sub(r"\[\[PHOTO:\d+\]\]", "", texte)
    problemas = []
    for ph in re.split(r"(?<=[.!?])\s+", tx.replace("\n", " ")):
        ph = ph.strip()
        if len(ph) < 12:
            continue
        mots = re.findall(r"[a-záéíóúñü]+", ph.lower())
        if not mots:
            continue
        # Présence d'un verbe espagnol marqué
        verbes = [w for w in mots if w in ES_VERBS]
        if verbes:
            # Vérifier que la phrase n'est pas française (harmonie avec mots FR)
            mots_fr = [w for w in mots if w in {"les","des","avec","dans","qui","que","une","pour","sur","sont","par","le","la","faire","être","plus","sur"}]
            if len(mots_fr) < len(mots) * 0.4:
                problemas.append((ph[:120], verbes[:3]))
    return problemas


def traduire_section(es_block, est_titre=False):
    """Traduit un bloc ES en FR, avec retry jusqu'à ce que la sortie soit en français."""
    for attempt in range(4):
        if est_titre:
            prompt = (
                "Tu es un traducteur professionnel. Traduis UNIQUEMENT ce titre de l'espagnol "
                "vers le français. MAX 55 caractères, direct, sans sous-titre.\n"
                f"TITRE ESPAGNOL : {es_block}\n"
                "RÉPONDS UNIQUEMENT AVEC LE TITRE FRANÇAIS :"
            )
        else:
            prompt = (
                "Tu es un traducteur professionnel espagnol→français de haut niveau (style National Geographic).\n"
                "Traduis EN FRANÇAIS le texte espagnol ci-dessous.\n"
                "RÈGLES ABSOLUES :\n"
                "- 100% en Français. Aucun mot espagnol ne doit subsister, sauf noms propres de lieux/personnes.\n"
                "- Traduis le titre '## ' en français.\n"
                "- Conserve la structure markdown (##, tableaux, listes), les chiffres, noms propres, et marqueurs [[PHOTO:N]].\n"
                "- Traduis aussi les phrases entre parenthèses, citations, encadrés.\n"
                f"\nTEXTE ESPAGNOL À TRADUIRE :\n{es_block}"
            )
        out = _llm(prompt, max_tokens=8192, temp=0.15)
        if not out:
            time.sleep(3)
            continue
        if est_titre:
            return out.strip()
        # Vérifier la sortie
        frags = detecter_espagnol(out)
        if not frags:
            return out
        time.sleep(3)
    return out  # dernier essai, sera vérifié globalement après


def main():
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
    cur = conn.cursor()
    cur.execute("SELECT content_es FROM articles WHERE id=%s", (ART_ID,))
    es = cur.fetchone()[0]
    cur.close(); conn.close()

    # 1. Titre
    h1 = re.search(r"^#\s+(.+)$", es, re.MULTILINE)
    title_es = h1.group(1).strip() if h1 else ""
    title_fr = traduire_section(title_es, est_titre=True)
    print(f"1) Titre FR: {title_fr[:50]}")
    time.sleep(5)

    # 2. Intro GEO
    body = re.sub(r"^#\s+.*\n+", "", es).strip()
    intro_es = body[:450]
    intro_fr = traduire_section(intro_es)
    print(f"2) Intro FR: {len(intro_fr)}c")

    # 3. Sections H2×H2
    sections = re.findall(r"(## .+?)(?=\n## |\Z)", es, re.DOTALL)
    print(f"3) {len(sections)} sections...")
    fr_sections = []
    resultats = []
    for i, sec in enumerate(sections):
        h2 = re.search(r"^##\s+(.+)$", sec, re.MULTILINE)
        h2name = h2.group(1)[:45] if h2 else f"S{i+1}"
        fr = traduire_section(sec)
        frags = detecter_espagnol(fr)
        fr_sections.append(fr)
        status = "OK" if not frags else f"⚠️({len(frags)} fragments)"
        resultats.append({"i": i, "h2": h2name, "ok": not frags, "fragments": frags, "troncon": fr[:90]})
        print(f"   [{i+1}/{len(sections)}] {status} {h2name} -> {len(fr)}c")
        time.sleep(5)

    article_fr = f"# {title_fr}\n\n{intro_fr}\n\n" + "\n\n".join(fr_sections)

    # Nettoyer préfixes LLM
    for pref in ["Voici la traduction", "Voici la traduction en français", "Voici la version française", "TITRE :"]:
        if article_fr.strip().lower().startswith(pref.lower()):
            article_fr = article_fr.strip()[len(pref):].lstrip("\n: ")
            break

    # Sauvegarder pour validation AVANT insertion DB
    with open(OUT, "w") as f:
        json.dump({"article_fr": article_fr, "title_fr": title_fr, "sections": resultats}, f, ensure_ascii=False)
    print(f"\n=== RÉSULTAT SAUVÉ dans {OUT} ===")
    print(f"FR: {len(article_fr)} chars, {len(article_fr.split())} mots, {len(sections)} sections")
    nbad = sum(1 for r in resultats if not r["ok"])
    print(f"Sections avec fragments espagnols: {nbad}/{len(sections)}")
    print("⚠️  NB: Le FR est SAUVEGARDÉ pour validation, PAS encore inséré en DB.")


if __name__ == "__main__":
    main()
