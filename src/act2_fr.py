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
    log("═══ ACTE 2 — Traduction FR (élément par élément) ═══")

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
            "lang": "fr",
        })
        log(f"   📦 ES sauvegardé dans cache persistant")
    else:
        log(f"   📦 ES déjà en cache, utilisation du cache disponible pour {slug}")

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

        # VÉRIFICATION DU CACHE PERSISTANT
        cached = get_translation(slug, "fr", i)
        if cached is not None:
            log(f"   [{i+1}/{len(sections)}] {h2_name}...📦 CACHE HIT", newline=False)
            fr_sections.append(cached)
            log(f"{len(cached)}c")
            continue

        log(f"   [{i+1}/{len(sections)}] {h2_name}...", newline=False)
        fr = None
        for attempt in range(3):  # 3 tentatives (0, 1, 2) = max 3 appels
            try:
                fr = _llm(
                    f"Traduis en français. Conserve ## titre, ```tableaux```, images. "
                    f"Noms propres inchangés.\n\n{section}",
                    max_tokens=8192, temp=0.3,
                )
                break  # Succès → sortir de la boucle
            except Exception as e:
                if attempt < 2:
                    log(f"⚠️ retry {attempt+1}/2: {e}")
                    time.sleep(10)  # Pause plus longue entre retries
                else:
                    log(f"❌ Échec après 3 tentatives: {e}")
                    fr = "[SECTION NON TRADUITE]"
        fr_sections.append(fr)
        log(f"{len(fr)}c" if fr != "[SECTION NON TRADUITE]" else "⚠️ SECTION NON TRADUITE")

        # SAUVEGARDE DANS LE CACHE PERSISTANT
        if fr != "[SECTION NON TRADUITE]":
            save_translation(slug, "fr", i, fr)
            save_meta(slug, {"section_fr_cached": i, "lang": "fr"})

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
