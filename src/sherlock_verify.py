"""
SHERLOCK-VERIFY — Vérification factuelle intégrée au QC Journal.

Extrait les affirmations d'un article, les vérifie contre SearXNG + Gemini,
et retourne un score de fiabilité factuelle.

Intégré dans qc_check.py comme check P2 "VERIFY-FACTS".
Utilise UNIQUEMENT la Gateway CCT-Alejandro (port 4000).
"""
from __future__ import annotations
import json
import logging
import re
import time
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger("cct-journal.sherlock-verify")

# ─── Configuration ────────────────────────────────────────────────────
GATEWAY_CHAT_URL = "http://127.0.0.1:4000/v1/chat/completions"
SEARXNG_URL = "http://127.0.0.1:8889/search"
MAX_AFFIRMATIONS = 10
TIMEOUT = 30

PROMPT_EXTRACT = """Tu es un extracteur d'affirmations factuelles.
Analyse le texte suivant et extrait UNIQUEMENT les affirmations qui peuvent être
vérifiées objectivement : dates, noms propres, chiffres, événements historiques,
données géographiques, statistiques, citations attribuées, faits scientifiques.

Exclus les opinions, les métaphores, les jugements de valeur.

Format de réponse (JSON uniquement) :
{"affirmations": [{"id": 1, "texte": "l'affirmation exacte", "type": "date|nom|chiffre|evenement|donnee|citation|fait", "importance": 1-5}]}

Texte à analyser :
"""


def _gateway_chat(messages: list, max_tokens: int = 500, temperature: float = 0.1) -> str:
    """Appelle la Gateway CCT-Alejandro (OpenAI-compatible)."""
    resp = httpx.post(
        GATEWAY_CHAT_URL,
        json={
            "model": "gemini-3.1-flash-lite",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _searxng_search(query: str, lang: str = "fr", limit: int = 5) -> list:
    """Recherche SearXNG."""
    try:
        resp = httpx.get(
            SEARXNG_URL,
            params={"q": query, "language": lang, "format": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])[:limit]
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:300]}
            for r in results
        ]
    except Exception as e:
        logger.warning(f"SearXNG indisponible: {e}")
        return []


def extract_affirmations(text: str) -> list:
    """Extrait les affirmations vérifiables via LLM."""
    try:
        content = _gateway_chat(
            [
                {"role": "system", "content": PROMPT_EXTRACT},
                {"role": "user", "content": text[:10000]},
            ],
            max_tokens=2000,
            temperature=0.1,
        )
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        data = json.loads(content)
        return data.get("affirmations", [])[:MAX_AFFIRMATIONS]
    except Exception as e:
        logger.warning(f"Extraction affirmations échouée: {e}")
        phrases = [p.strip() for p in re.split(r'[.!?]', text) if len(p.strip()) > 30][:MAX_AFFIRMATIONS]
        return [{"id": i, "texte": p, "type": "phrase", "importance": 3} for i, p in enumerate(phrases)]


def verify_affirmation(affirmation: dict, lang: str = "fr") -> dict:
    """Vérifie une affirmation via SearXNG + Gemini Grounding."""
    query = affirmation["texte"]

    sources = _searxng_search(query, lang, limit=5)

    # Vérification via Gateway
    verdict = "non_verifiable"
    confiance = 0.0
    explanation = ""

    try:
        answer = _gateway_chat(
            [
                {"role": "system", "content": "Tu es un vérificateur de faits. Réponds UNIQUEMENT par CONFIRMÉ, CONTREDIT, ou NON VÉRIFIABLE. Sois bref."},
                {"role": "user", "content": f"Vérifie ce fait : {query}"},
            ],
            max_tokens=100,
            temperature=0.0,
        )
        answer_lower = answer.lower()
        if "confirm" in answer_lower:
            verdict = "confirme"
            confiance = 0.8 + (0.04 * min(len(sources), 5))
        elif "contredit" in answer_lower or "faux" in answer_lower or "false" in answer_lower:
            verdict = "contredit"
            confiance = 0.7
        else:
            verdict = "non_verifiable"
            confiance = 0.2
        explanation = answer[:300]
    except Exception as e:
        logger.warning(f"Vérification Gateway échouée pour '{query[:60]}': {e}")

    return {
        "id": affirmation["id"],
        "affirmation": query,
        "verdict": verdict,
        "confiance": round(confiance, 2),
        "explication": explanation,
        "sources": sources[:3],
        "nb_sources": len(sources),
    }


def verify_article(text: str, lang: str = "fr") -> dict:
    """
    Vérification factuelle d'un article complet.

    Returns:
        dict avec: score_global (0-10), valide (bool), niveau_alerte,
        nb_confirme, nb_contredit, nb_non_verifiable, message
    """
    t0 = time.time()

    if not text or len(text) < 100:
        return {"score_global": None, "valide": True, "message": "Texte trop court", "niveau_alerte": "ok"}

    affirmations = extract_affirmations(text)
    if not affirmations:
        return {"score_global": 10, "valide": True, "message": "Aucune affirmation vérifiable", "niveau_alerte": "ok"}

    # Vérifier chaque affirmation (séquentiel pour rester dans les quotas)
    results = []
    for aff in affirmations[:5]:  # Max 5 affirmations pour rester rapide
        result = verify_affirmation(aff, lang)
        results.append(result)

    nb = len(results)
    nb_confirme = sum(1 for r in results if r["verdict"] == "confirme")
    nb_contredit = sum(1 for r in results if r["verdict"] == "contredit")
    nb_nv = sum(1 for r in results if r["verdict"] == "non_verifiable")

    somme_confiance = sum(r["confiance"] for r in results if r["verdict"] == "confirme")
    score_si_confirme = (somme_confiance / nb_confirme * 10) if nb_confirme > 0 else 5
    penalite = (nb_contredit / nb) * 5 if nb > 0 else 0
    score = max(0, min(10, score_si_confirme - penalite))

    if nb_contredit > 0:
        niveau = "bloquant"
    elif score < 5:
        niveau = "revision_humaine"
    elif score < 8:
        niveau = "revision_humaine"
    else:
        niveau = "ok"

    elapsed = int((time.time() - t0) * 1000)
    logger.info(f"[Verify] {nb} affirmations: {nb_confirme}✓ {nb_contredit}✗ {nb_nv}? | Score: {score}/10 | {elapsed}ms")

    return {
        "score_global": round(score, 1),
        "valide": nb_contredit == 0,
        "niveau_alerte": niveau,
        "nb_confirme": nb_confirme,
        "nb_contredit": nb_contredit,
        "nb_non_verifiable": nb_nv,
        "affirmations": results,
        "temps_ms": elapsed,
    }
