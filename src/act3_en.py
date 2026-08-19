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
from translation_cache import (
    get_es, save_es, get_translation, save_translation,
    set_verified, get_verified, save_meta, _compute_slug_from_title,
)

GATEWAY = os.environ.get("GATEWAY_URL", "")  # Gateway Gemini désactivée pour ce profil (19/08/2026)
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "qwen/qwen3.7-plus"  # RÈGLE MARC 19/08/2026: Qwen 3.7 Plus via OpenRouter pour les traductions
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT = 120

DEEPSEEK_API_KEY = None
OPENROUTER_API_KEY = None

def _get_openrouter_key() -> str:
    global OPENROUTER_API_KEY
    if OPENROUTER_API_KEY:
        return OPENROUTER_API_KEY
    try:
        import re
        with open("/root/.hermes/profiles/alejandro-journal/.env", "r") as f:
            for line in f:
                if line.strip().startswith("OPENROUTER_API_KEY"):
                    v = line.split("=", 1)[1].strip()
                    if v:
                        OPENROUTER_API_KEY = v
                        return OPENROUTER_API_KEY
    except Exception:
        pass
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    return OPENROUTER_API_KEY

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
    if "qwen" in MODEL.lower():
        # Qwen 3.7 Plus via OpenRouter (RÈGLE MARC 19/08/2026)
        api_key = _get_openrouter_key()
        if not api_key:
            log("   ⚠️ Clé OpenRouter introuvable, fallback DeepSeek V4 Flash")
            ds_key = _get_deepseek_key()
            r = httpx.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"},
                json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens, "temperature": temp,
                      "reasoning_effort": "none"},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            content = (msg.get("content", "") or "").strip()
            if not content:
                return "[ERREUR_TRADUCTION: reponse vide]"
            return content
        r = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                      "HTTP-Referer": "https://clubcostatropical.es",
                      "X-Title": "CCT Journal Traduction"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens, "temperature": temp},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = (msg.get("content", "") or "").strip()
        if not content:
            return "[ERREUR_TRADUCTION: reponse vide]"
        return content
    if "deepseek" in MODEL.lower():
        api_key = _get_deepseek_key()
        r = httpx.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens, "temperature": temp,
                  "reasoning_effort": "none"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = (msg.get("content", "") or "").strip()
        if not content:
            return "[ERREUR_TRADUCTION: reponse vide]"
        return content
    # Gateway (Gemini) désactivée — fallback DeepSeek Flash
    log("   ⚠️ Fallback Gateway non disponible, utilisation DeepSeek Flash")
    ds_key = _get_deepseek_key()
    r = httpx.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"},
        json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens, "temperature": temp,
              "reasoning_effort": "none"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = (msg.get("content", "") or "").strip()
    if not content:
        return "[ERREUR_TRADUCTION: reponse vide]"
    return content


def run() -> bool:
    log("═══ ACTE 3 — Traduction EN (élément par élément) ═══")

    es_data = load_step("act1_es_validated")
    if not es_data:
        log("❌ Acte 1 non trouvé")
        return False

    article_es = es_data["article_es"]
    title_es = es_data.get("title_es", "")
    log(f"   Article ES chargé: {len(article_es)} chars")

    # Slug unique pour le cache persistant
    slug = _compute_slug_from_title(title_es)
    log(f"   Slug: {slug}")

    # Sauvegarder l'ES dans le cache persistant si pas déjà fait
    if not get_es(slug):
        save_es(slug, article_es)
        save_meta(slug, {
            "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "nb_sections": len(re.findall(r'^##\s+', article_es, re.MULTILINE)),
            "modele_generation": "deepseek-v4-flash",
            "modele_traduction": "qwen-qwen3.7-plus",
            "modele_verification": "deepseek-v4-pro",
            "lang": "en",
        })
        log(f"   📦 ES sauvegardé dans cache persistant")
    else:
        log(f"   📦 ES déjà en cache, utilisation du cache disponible pour {slug}")

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

        # CACHE CHECK
        cached = get_translation(slug, "en", i)
        if cached is not None:
            log(f"   [{i+1}/{len(sections)}] {h2_name}...📦 CACHE HIT", newline=False)
            en_sections.append(cached)
            log(f"{len(cached)}c")
            continue

        log(f"   [{i+1}/{len(sections)}] {h2_name}...", newline=False)
        en = None
        for attempt in range(3):  # 3 tentatives (0, 1, 2) = max 3 appels
            try:
                en = _llm(
                    f"Translate to English. Keep ## heading, ```tables```, images. "
                    f"Proper nouns unchanged.\n\n{section}",
                    max_tokens=8192, temp=0.3,
                )
                break  # Succès → sortir de la boucle
            except Exception as e:
                if attempt < 2:
                    log(f"⚠️ retry {attempt+1}/2: {e}")
                    time.sleep(10)  # Pause plus longue entre retries
                else:
                    log(f"❌ Failed after 3 attempts: {e}")
                    en = "[SECTION NOT TRANSLATED]"
        en_sections.append(en)
        log(f"{len(en)}c" if en != "[SECTION NOT TRANSLATED]" else "⚠️ SECTION NOT TRANSLATED")

        # SAUVEGARDE DANS LE CACHE PERSISTANT
        if en != "[SECTION NOT TRANSLATED]":
            save_translation(slug, "en", i, en)
            save_meta(slug, {"section_en_cached": i, "lang": "en"})

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
