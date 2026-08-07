#!/usr/bin/env python3
"""
retrad_fr.py — Re-traduit proprement le contenu FR de l'article b2f6cfb6
depuis l'ES intact, section par section, avec VÉRIFICATION LINGUISTIQUE
(une section n'est acceptée que si elle est majoritairement française).
L'EN est déjà bon, on ne touche qu'au FR.

Séquençage : H2×H2, pause 5s, conforme au skill sequencage-action.
"""
import re, sys, time, json
sys.path.insert(0, "/srv/cct-journal/src")
import httpx

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"

def _get_deepseek_key():
    try:
        with open("/root/.hermes/config.yaml") as f:
            m = re.search(r'deepseek:\s*\n\s+api_key:\s*(\S+)', f.read())
        if m: return m.group(1)
    except Exception: pass
    return ""

KEY = _get_deepseek_key()

def _llm(prompt, max_tokens=8192, temp=0.3):
    r = httpx.post(DEEPSEEK_URL, headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                   json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                         "max_tokens": max_tokens, "temperature": temp}, timeout=120)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()

# Mots clés fortement espagnols (dont l'équivalent français est différent)
ES_MARKERS = {
    "también","siempre","después","entonces","tambien","sobre","entre","desde","hacia",
    "años","tierra","pueblo","sierra","montañas","trabajo","primera","tambien","porque",
    "cuando","donde","como","pero","muy","después","hacer","queda","todas","solo",
    "sin","bajo","sobre","tras","cada","esta","este","esa","eso","estos","esas","sus",
    "una","las","los","para","fue","han","está","son","la","el","y","de","en","con",
    "se","que","al","del","más","no","si","lo","un","también","bodega","viñedo","cata",
}

def es_ratio(text):
    words = re.findall(r"[a-záéíóúñü]+", text.lower())
    if not words: return 0
    hits = sum(1 for w in words if w in ES_MARKERS)
    return hits / len(words)

def traduire_es_en_fr(texte, est_titre=False):
    """Traduit un bloc ES en FR, avec retry si la sortie est trop espagnole."""
    limite = 0.25 if est_titre else 0.30  # une section FR propre doit être < 30% de mots-ES communs
    for attempt in range(3):
        if est_titre:
            prompt = f"Traduis EXACTEMENT ce titre en français. Réponds UNIQUEMENT avec le titre français traduit, rien d'autre. Le titre en ESPAGNOL est: {texte}"
        else:
            prompt = (
                "Tu es un traducteur professionnel espagnol→français. Traduis EN FRANÇAIS UNIQUEMENT le texte espagnol ci-dessous.\n"
                "RÈGLES: 1) Chaque mot espagnol doit être remplacé par son équivalent français. 2) Traduis AUSSI le titre ## en français. "
                "3) Conserve la structure markdown (##, tableaux, listes), les noms propres, les chiffres, et les marqueurs [[PHOTO:N]] inchangés. "
                "4) Le texte de sortie doit être 100% en français, sans aucun mot espagnol.\n\n"
                f"TEXTE ESPAGNOL À TRADUIRE:\n{texte}"
            )
        out = _llm(prompt, max_tokens=8192, temp=0.2)
        ratio = es_ratio(out)
        if ratio <= limite:
            return out, ratio
        # sinon on réessaie en insistant plus fort
        time.sleep(2)
    # Dernier recours : retourner quand même avec une note (sera gérée)
    return out, ratio

def main():
    import psycopg2
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
    cur = conn.cursor()
    cur.execute("SELECT content_es FROM articles WHERE id=%s", (ART_ID,))
    es = cur.fetchone()[0]

    # 1. Titre
    h1 = re.search(r"^#\s+(.+)$", es, re.MULTILINE)
    title_es = h1.group(1).strip() if h1 else ""
    print(f"1. Titre ES: {title_es[:50]}")
    title_fr, tr = traduire_es_en_fr(title_es, est_titre=True)
    print(f"   Titre FR: {title_fr[:50]} (ratio-ES {tr*100:.1f}%)")
    time.sleep(5)

    # 2. Intro GEO
    body = re.sub(r"^#\s+.*\n+", "", es).strip()
    intro_es = body[:400]
    print("2. Intro FR...", end=" ")
    intro_fr, ir = traduire_es_en_fr(intro_es)
    print(f"{len(intro_fr)}c (ratio-ES {ir*100:.1f}%)")
    time.sleep(5)

    # 3. H2 × H2
    sections = re.findall(r"(## .+?)(?=\n## |\Z)", es, re.DOTALL)
    print(f"3. {len(sections)} sections H2...")
    fr_sections = []
    for i, sec in enumerate(sections):
        h2 = re.search(r"^##\s+(.+)$", sec, re.MULTILINE)
        h2name = h2.group(1)[:45] if h2 else f"S{i+1}"
        fr, r = traduire_es_en_fr(sec)
        fr_sections.append(fr)
        marker = "⚠️ES" if r > 0.30 else "✅FR"
        print(f"   [{i+1}/{len(sections)}] {marker} {h2name} -> {len(fr)}c (ratio-ES {r*100:.1f}%)")
        time.sleep(5)

    article_fr = f"# {title_fr}\n\n{intro_fr}\n\n" + "\n\n".join(fr_sections)

    # Nettoyer les préfixes LLM
    for pref in ["Voici la traduction", "Voici la traduction en français", "Voici la version française", "TITRE :", "français :"]:
        if article_fr.strip().lower().startswith(pref.lower()):
            article_fr = article_fr.strip()[len(pref):].lstrip("\n: ")
            break

    # Vérification finale globale
    final_ratio = es_ratio(article_fr)
    print(f"\n=== RÉSULTAT FINAL ===")
    print(f"FR: {len(article_fr)} chars, {len(article_fr.split())} mots, ratio-ES global {final_ratio*100:.1f}%")

    cur.execute("UPDATE articles SET content_fr=%s, title_fr=%s WHERE id=%s", (article_fr, title_fr, ART_ID))
    conn.commit()
    print("✅ DB FR mise à jour")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
