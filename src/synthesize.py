"""
synthesize.py — génère le billet ES depuis le sujet choisi + traductions FR/EN.

Un seul appel LLM pour la génération originale (pas une synthèse),
puis 2 appels pour les traductions.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Dict

import httpx

from config import (
    GATEWAY_URL, GEN_MODEL, TRANSLATION_MODEL,
    SYSTEM_PROMPT_JOURNAL_ES, USER_PROMPT_JOURNAL_ES, TRANSLATE_PROMPT,
)

logger = logging.getLogger("cct-journal.synthesize")


def _gateway_call(model: str, system: str, user: str, caller: str = "cct-journal") -> str:
    """Appelle LLM via llm_router : DeepSeek V4 Flash primaire, Gemini 3.5 Flash fallback.
    FastCheck via Gemini 3.1 Pro si caller contient 'fastcheck'.
    """
    import asyncio
    import sys
    sys.path.insert(0, "/srv/rag-engine")
    from pipeline.llm_router import generate_text, fastcheck_text

    is_fastcheck = "fastcheck" in caller.lower()

    try:
        if is_fastcheck:
            result, _ = asyncio.run(fastcheck_text(user, lang="es", sources="", caller=caller))
            return result if result else ""
        else:
            result = asyncio.run(generate_text(system, user, max_tokens=16000, temperature=0.5, caller=caller))
            return result
    except Exception as e:
        logger.error(f"❌ [{caller}] LLM call failed: {e}")
        return ""


def _deepseek_fallback(system: str, user: str, caller: str = "cct-journal") -> str:
    """Fallback to DeepSeek V4 Flash API when Gateway (Gemini) is unavailable."""
    import os
    from dotenv import load_dotenv
    load_dotenv("/srv/rag-engine/.env")

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not found")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    resp = httpx.post(
        "https://api.deepseek.com/v1/chat/completions",
        json={"model": "deepseek-v4-flash", "messages": messages, "caller": caller},
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    logger.info(f"☀️ DeepSeek V4 Flash fallback OK ({caller})")
    return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


def _strip_llm_prefix(text: str) -> str:
    """Nettoie les préfixes d'instruction que le LLM peut ajouter aux traductions.
    
    Patterns détectés (FR/EN) :
    - "Voici la traduction..."
    - "Absolument. Voici..."
    - "Here is the translation..."
    - "Absolutely. Here is..."
    """
    import re
    patterns = [
        r'^Voici la traduction (de l\'article|en français|en anglais).*?(?:\n|$)\s*',
        r'^Voici le texte traduit.*?(?:\n|$)\s*',
        r'^Voici une proposition de traduction.*?(?:\n|$)\s*',
        r"^Voici une? .*? traduction .*?(?:\n|$)\s*",
        r'^Absolument\.?\s*Voici la traduction.*?(?:\n|$)\s*',
        r'^Here is a translation.*?(?:\n|$)\s*',
        r'^Here is the translation of the article.*?(?:\n|$)\s*',
        r'^Here is the translated text.*?(?:\n|$)\s*',
        r'^Absolutely\.?\s*Here is the translation.*?(?:\n|$)\s*',
        r'^Absolument\.?\s*(?:Voici|Here is).*?(?:\n|$)\s*',
        r'^Ci-dessous,? la traduction.*?(?:\n|$)\s*',
    ]
    for pat in patterns:
        text = re.sub(pat, '', text, count=1, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def _strip_multilingual_tail(text: str) -> str:
    """Coupe toute section de fin qui annonce des traductions (FR/EN/Translations).
    Sécurité au cas où le LLM aurait inclus des traductions malgré le prompt."""
    import re
    patterns = [
        r"\n\s*---\s*\n\s*###?\s*(Traductions?|Translations?|FR|EN|Français|English|Anglais)\b.*",
        r"\n\s*###?\s*(Traductions?|Translations?|FR|EN|Français|English|Anglais)\s*:?\s*\n.*",
    ]
    for pat in patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def generate_spanish(topic: dict, date: datetime | None = None, deep_context: str = "") -> str:
    """Génère le billet ES original depuis un sujet."""
    date = date or datetime.now(timezone.utc)
    fr_es = date.strftime("%-d de %B de %Y").lower()

    user = USER_PROMPT_JOURNAL_ES.format(
        date_fr_es=fr_es,
        domain=topic["domain"],
        tags=", ".join(topic.get("tags", [])),
        topic_title=topic["title"],
        topic_angle=topic.get("angle", "").strip(),
        topic_context=topic.get("context", "").strip(),
    )

    if deep_context:
        user += f"\n\nINVESTIGACIÓN RECIENTE:\n{deep_context[:3000]}"

    # Feedback QC pour les tentatives de correction automatique
    qc_feedback = topic.get("qc_feedback", "")
    if qc_feedback and len(qc_feedback) > 10:
        user += f"\n\nFEEDBACK DE CONTROL DE CALIDAD (correcciones necesarias):\n{qc_feedback}"

    logger.info(f"Generating ES — topic={topic['id']} domain={topic['domain']}")
    system = SYSTEM_PROMPT_JOURNAL_ES.format(target_words=topic.get("target_words", 4000))
    text = _gateway_call(GEN_MODEL, system, user, caller="cct-journal-es")
    text = _strip_multilingual_tail(text)
    logger.info(f"ES generated — {len(text)} chars ({len(text.split())} mots)")
    return text


def translate(source_text: str, target_lang: str) -> str:
    if target_lang not in ("fr", "en"):
        raise ValueError("target_lang doit être 'fr' ou 'en'")
    human = {"fr": "français", "en": "anglais"}[target_lang]
    user = TRANSLATE_PROMPT.format(target_lang_human=human, source_text=source_text)
    logger.info(f"Translating ES → {target_lang.upper()}")
    text = _gateway_call(TRANSLATION_MODEL, f"Tu es traducteur professionnel. Traduis UNIQUEMENT le texte fourni en {human}, sans ajouter de commentaires, sans préfixes, sans explications. Ne répète PAS le texte source.", user, caller=f"cct-journal-{target_lang}")
    text = _strip_llm_prefix(text)
    logger.info(f"  → {len(text)} chars ({len(text.split())} mots)")
    return text


def generate_trilingual(topic: dict, deep_context: str = "") -> Dict[str, str]:
    es = generate_spanish(topic, deep_context=deep_context)
    fr = translate(es, "fr")
    en = translate(es, "en")
    return {"es": es, "fr": fr, "en": en}
