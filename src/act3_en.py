#!/usr/bin/env python3
"""
act3_en.py — Acte 3 : Traduction EN élément par élément.
Ordre: titre → intro GEO → H2 par H2 (pause 5s).
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime

import httpx

from pipeline_cache import load_step, save_step

GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"  # RÈGLE MARC: JAMAIS deepseek-v4-pro, JAMAIS deepseek-chat (déprécié 24/07)
TIMEOUT = 120

DEEPSEEK_API_KEY = None

def _get_deepseek_key() -> str:
    global DEEPSEEK_API_KEY
    if DEEPSEEK_API_KEY:
        return DEEPSEEK_API_KEY
    try:
        import re
        with open("/root/.hermes/config.yaml", "r") as f:
            m = re.search(r'deepseek:\s*\n\s+api_key:\s*(\S+)', f.read())
        if m:
            DEEPSEEK_API_KEY = m.group(1)
            return DEEPSEEK_API_KEY
    except Exception:
        pass
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    return DEEPSEEK_API_KEY


def log(msg: str, newline: bool = True):
    t = datetime.now().strftime("%H:%M:%S")
    if newline:
        print(f"[{t}] {msg}", flush=True)
    else:
        print(f"[{t}] {msg}", end=" ", flush=True)


def _llm(prompt: str, max_tokens: int = 4096, temp: float = 0.3) -> str:
    if "deepseek" in MODEL.lower():
        api_key = _get_deepseek_key()
        r = httpx.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens, "temperature": temp,
                  "reasoning_effort": "none"},  # ponytail: désactive mode thinking DeepSeek
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = msg.get("content", "").strip()
        # NE PAS prendre reasoning_content (blabla interne, pas une traduction). Si vide -> signaler
        if not content:
            return "[ERREUR_TRADUCTION: reponse vide]"
        return content
    r = httpx.post(
        f"{GATEWAY}/v1/chat/completions",
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens, "temperature": temp},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def run() -> bool:
    log("═══ ACTE 3 — Traduction EN (élément par élément) ═══")

    es_data = load_step("act1_es_validated")
    if not es_data:
        log("❌ Acte 1 non trouvé")
        return False

    article_es = es_data["article_es"]
    title_es = es_data.get("title_es", "")
    log(f"   Article ES chargé: {len(article_es)} chars")

    # ── 1. Titre ──
    log("   1) Translating title...", newline=False)
    h1_match = re.search(r'^#\s+(.+)$', article_es, re.MULTILINE)
    title_en = None
    if h1_match:
        title_en = _llm(
            f"Translate this title to English. MAX 55 chars. DIRECT, no subtitle.\n\n{h1_match.group(1)}",
            max_tokens=100, temp=0.3,
        )
        log(f"{title_en[:60]}")
    else:
        title_en = title_es
        log("(kept)")

    # ── 2. Intro GEO ──
    log("   2) Translating GEO intro...", newline=False)
    body_after_h1 = re.sub(r'^#\s+.*\n+', '', article_es).strip()
    intro_es = body_after_h1[:400]
    intro_en = _llm(
        f"Translate to English. Keep all figures, place names, data. Max 200-250 chars.\n\n{intro_es}",
        max_tokens=300, temp=0.3,
    )
    log(f"{len(intro_en)}c")

    # ── 3. H2 par H2 ──
    sections = re.findall(r'(## .+?)(?=\n## |\Z)', article_es, re.DOTALL)
    log(f"   3) Translating H2 by H2: {len(sections)} sections...")

    en_sections = []
    for i, section in enumerate(sections):
        h2_name = "Section"
        h2_match = re.search(r'^##\s+(.+)$', section, re.MULTILINE)
        if h2_match:
            h2_name = h2_match.group(1)[:50]

        log(f"   [{i+1}/{len(sections)}] {h2_name}...", newline=False)
        try:
            en = _llm(
                f"Translate to English. Keep ## heading, ```tables```, images. "
                f"Proper nouns unchanged.\n\n{section}",
                max_tokens=8192, temp=0.3,
            )
            en_sections.append(en)
            log(f"{len(en)}c")
        except Exception as e:
            log(f"❌ {e}")
            en_sections.append(section)
        time.sleep(5)

    # ── Assemblage ──
    article_en = f"# {title_en}\n\n{intro_en}\n\n" + "\n\n".join(en_sections)

    for prefix in ["Here is the translation", "TITLE:", "CONTENU ORIGINAL:", "Absolutely"]:
        if article_en.startswith(prefix):
            h_match = re.search(r'(^#+\s)', article_en[len(prefix):], re.MULTILINE)
            if h_match:
                article_en = article_en[len(prefix):][h_match.start():]
            break

    log(f"   EN: {len(article_en)} chars, {len(article_en.split())} words")

    save_step("act3_en_validated", {
        "article_en": article_en,
        "title_en": title_en,
        "title_es": title_es,
    })

    log(f"✅ ACTE 3 TERMINÉ")
    return True


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
