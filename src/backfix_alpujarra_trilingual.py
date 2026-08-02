#!/usr/bin/env python3
"""
backfix_alpujarra_trilingual.py — Restaure les vraies traductions FR/EN de l'article
b2f6cfb6-7719-418b-b580-4bae29455095 (l'ES est intact, FR/EN avaient été écrasés par
un bug du pipeline SEO qui copiait l'ES).

Séquençage : élément par élément (titre → intro → H2×H2), pause 5s entre chaque,
conforme au skill sequencage-action.
"""
import re, sys, time, json, subprocess
sys.path.insert(0, "/srv/cct-journal/src")
import httpx
from act2_fr import _llm as llm_fr
from act3_en import _llm as llm_en

ART_ID = "b2f6cfb6-7719-418b-b580-4bae29455095"

def get_es():
    import psycopg2
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
    cur = conn.cursor()
    cur.execute("SELECT content_es FROM articles WHERE id = %s", (ART_ID,))
    content_es = cur.fetchone()[0]
    cur.close(); conn.close()
    return content_es

def translate_article(content_es, llm, lang):
    """Traduit titre, intro, puis chaque H2 séparément."""
    # Titre (H1)
    h1 = re.search(r"^#\s+(.+)$", content_es, re.MULTILINE)
    title = h1.group(1).strip() if h1 else ""
    title_out = llm(
        f"Translate this title to {lang}. MAX 55 chars, direct, no subtitle.\n\n{title}",
        max_tokens=100, temp=0.3)
    print(f"  [{lang}] Titre: {title_out[:60]}")
    time.sleep(5)

    # Intro GEO (premiers 400 chars après le H1)
    body = re.sub(r"^#\s+.*\n+", "", content_es).strip()
    intro_es = body[:400]
    intro_out = llm(
        f"Translate to {lang}. Keep all figures, place names, data. Max 200-250 chars.\n\n{intro_es}",
        max_tokens=300, temp=0.3)
    print(f"  [{lang}] Intro: {len(intro_out)}c")
    time.sleep(5)

    # H2 par H2
    sections = re.findall(r"(## .+?)(?=\n## |\Z)", content_es, re.DOTALL)
    out_sections = []
    for i, sec in enumerate(sections):
        h2 = re.search(r"^##\s+(.+)$", sec, re.MULTILINE)
        h2_name = h2.group(1)[:50] if h2 else f"Section {i+1}"
        tr = llm(
            f"Translate to {lang}. Keep ## heading, tables, images, proper nouns.\n\n{sec}",
            max_tokens=8192, temp=0.3)
        out_sections.append(tr)
        print(f"  [{lang}] H2 {i+1}/{len(sections)}: {h2_name} -> {len(tr)}c")
        time.sleep(5)

    article = f"# {title_out}\n\n{intro_out}\n\n" + "\n\n".join(out_sections)
    return article, title_out

def update_db(fr_content, fr_title, en_content, en_title):
    import psycopg2
    pwd = open("/etc/cct-journal/pg.pwd").read().strip()
    conn = psycopg2.connect(f"postgresql://alejandro:{pwd}@127.0.0.1:5432/alejandro_db")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE articles SET content_fr=%s, title_fr=%s, content_en=%s, title_en=%s WHERE id=%s",
        (fr_content, fr_title, en_content, en_title, ART_ID))
    cur.close(); conn.close()
    print(f"✅ DB mise à jour ({ART_ID})")

if __name__ == "__main__":
    print("Récupération de l'ES...")
    content_es = get_es()
    print(f"  ES: {len(content_es)} chars, {len(content_es.split())} mots")

    print("\n=== Traduction FR ===")
    fr_content, fr_title = translate_article(content_es, llm_fr, "French")
    print(f"  FR total: {len(fr_content)} chars, {len(fr_content.split())} mots")

    print("\n=== Traduction EN ===")
    en_content, en_title = translate_article(content_es, llm_en, "English")
    print(f"  EN total: {len(en_content)} chars, {len(en_content.split())} mots")

    update_db(fr_content, fr_title, en_content, en_title)
    print("\n✅ Terminé.")
