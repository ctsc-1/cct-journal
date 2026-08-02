#!/usr/bin/env python3
"""
act2_fr.py — Acte 2 : Traduction FR élément par élément.
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
MODEL = "deepseek-chat"  # V4 Flash sans thinking
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
                  "max_tokens": max_tokens, "temperature": temp},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = msg.get("content", "") or msg.get("reasoning_content", " ")
        return content.strip()
    r = httpx.post(
        f"{GATEWAY}/v1/chat/completions",
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens, "temperature": temp},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def run() -> bool:
    log("═══ ACTE 2 — Traduction FR (élément par élément) ═══")

    es_data = load_step("act1_es_validated")
    if not es_data:
        log("❌ Acte 1 non trouvé")
        return False

    article_es = es_data["article_es"]
    title_es = es_data.get("title_es", "")
    log(f"   Article ES chargé: {len(article_es)} chars")

    # ── 1. Traduire le TITRE ──
    log("   1) Traduction du titre...", newline=False)
    h1_match = re.search(r'^#\s+(.+)$', article_es, re.MULTILINE)
    title_fr = None
    if h1_match:
        title_fr = _llm(
            f"Traduis ce titre en français. MAX 55 caractères. DIRECT, pas de sous-titre.\n\n{h1_match.group(1)}",
            max_tokens=100, temp=0.3,
        )
        log(f"{title_fr[:60]}")
    else:
        title_fr = title_es
        log("(conservé)")

    # ── 2. Traduire l'INTRO GEO (200 premiers caractères après H1) ──
    log("   2) Traduction intro GEO...", newline=False)
    body_after_h1 = re.sub(r'^#\s+.*\n+', '', article_es).strip()
    intro_es = body_after_h1[:400]  # prendre plus large pour contexte
    intro_fr = _llm(
        f"Traduis en français cette introduction d'article. Conserve toutes les "
        f"cifras, noms de lieux, données. Max 200-250 caractères.\n\n{intro_es}",
        max_tokens=300, temp=0.3,
    )
    log(f"{len(intro_fr)}c")

    # ── 3. Traduire chaque H2 (titre + contenu) un par un ──
    sections = re.findall(r'(## .+?)(?=\n## |\Z)', article_es, re.DOTALL)
    log(f"   3) Traduction H2 par H2: {len(sections)} sections...")

    fr_sections = []
    for i, section in enumerate(sections):
        h2_title = re.search(r'^##\s+(.+)$', section, re.MULTILINE)
        h2_name = h2_title.group(1)[:50] if h2_title else f"Section {i+1}"

        log(f"   [{i+1}/{len(sections)}] {h2_name}...", newline=False)
        try:
            fr = _llm(
                f"Traduis en français. Conserve ## titre, ```tableaux```, images. "
                f"Noms propres inchangés.\n\n{section}",
                max_tokens=8192, temp=0.3,
            )
            fr_sections.append(fr)
            log(f"{len(fr)}c")
        except Exception as e:
            log(f"❌ {e}")
            fr_sections.append(section)
        time.sleep(5)  # Pause anti-saturation

    # ── Assemblage ──
    article_fr = f"# {title_fr}\n\n{intro_fr}\n\n" + "\n\n".join(fr_sections)

    # Nettoyage préfixes LLM
    for prefix in ["Voici la traduction", "Voici les corrections", "TITRE :"]:
        if article_fr.startswith(prefix):
            h_match = re.search(r'(^#+\s)', article_fr[len(prefix):], re.MULTILINE)
            if h_match:
                article_fr = article_fr[len(prefix):][h_match.start():]
            break

    log(f"   FR: {len(article_fr)} chars, {len(article_fr.split())} mots")

    save_step("act2_fr_validated", {
        "article_fr": article_fr,
        "title_fr": title_fr,
        "title_es": title_es,
    })

    log(f"✅ ACTE 2 TERMINÉ")
    return True


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
