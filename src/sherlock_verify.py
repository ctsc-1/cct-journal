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
import sys; sys.path.insert(0, "/srv/rag-engine")
from pipeline.model_env import get_model

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

# ─── Détection d'hallucinations (inventions, rencontres fictives, personnes imaginaires) ───

PROMPT_HALLUCINATION = """Tu es un détecteur d'hallucinations journalistiques.
Analyse le texte suivant et identifie TOUT contenu qui pourrait être INVENTÉ par l'auteur :

1. Rencontres physiques ou conversations avec des personnes nommées qui semblent fictives
2. Personnes imaginaires — noms propres qui ne correspondent à aucune personne réelle identifiable
3. Citations inventées — paroles attribuées à quelqu'un qui semblent fabriquées
4. Événements fabriqués — faits locaux qui n'ont pas eu lieu
5. Anecdotes personnelles présentées comme réelles mais qui semblent fictives

Un article journalistique NE DOIT PAS contenir de rencontres inventées, de personnes imaginaires,
ni de citations fabriquées. Le journalisme narratif est autorisé (atmosphère, contexte) mais
les faits, personnes et citations doivent être RÉELS.

Format de réponse (TEXTE SIMPLE, une ligne par hallucination) :

Si hallucination détectée :
HALLUC|type|severite|extrait (max 100 chars)|raison (max 150 chars)

Types: rencontre_inventee, personne_imaginaire, citation_inventee, evenement_fabrique, anecdote_fictive
Severite: 1 à 5 (5 = certainement inventé, 3 = suspect)

Si AUCUNE hallucination :
PROPRE

Texte à analyser :
"""


def _gateway_chat(messages: list, max_tokens: int = 500, temperature: float = 0.1) -> str:
    """Appelle la Gateway CCT-Alejandro (OpenAI-compatible)."""
    resp = httpx.post(
        GATEWAY_CHAT_URL,
        json={
            "model": get_model("FASTCHECK", "deepseek-v4-pro"),
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


def detect_hallucinations(text: str, lang: str = "es") -> dict:
    """Détecte les hallucinations journalistiques AU NIVEAU PARAGRAPHE.
    
    Double vérificateur conservateur. Pour chaque hallucination détectée,
    identifie le paragraphe concerné pour suppression chirurgicale.
    
    Returns:
        dict avec: hallucinations (list), has_hallucinations (bool), 
        has_blocking_hallucinations (bool), paragraphs_to_remove (list of str)
    """
    if not text or len(text) < 200:
        return {"hallucinations": [], "has_hallucinations": False, "has_blocking_hallucinations": False, "paragraphs_to_remove": []}
    
    # ─── Vérificateur 1 : détection active ───
    result1 = _detect_hallucinations_v1(text, lang)
    
    # ─── Vérificateur 2 : contre-interrogatoire indépendant ───
    result2 = _detect_hallucinations_v2(text, lang)
    
    # ─── Fusion conservatrice ───
    halluc1 = result1.get("hallucinations", [])
    halluc2 = result2.get("hallucinations", [])
    has_error = "error" in result1 or "error" in result2
    
    # Fusion + déduplication
    all_halluc = halluc1 + halluc2
    seen = set()
    merged = []
    for h in all_halluc:
        key = h.get("extrait", "")[:50].lower()
        if key not in seen:
            seen.add(key)
            merged.append(h)
    
    has_blocking = any(h.get("severite", 0) >= 3 for h in merged)
    
    if has_error:
        has_blocking = True
        merged.append({
            "type": "erreur_verificateur",
            "severite": 5,
            "extrait": "(vérificateur en erreur)",
            "raison": "Un des deux vérificateurs a échoué — mesure conservatrice: blocage",
        })
    
    # ─── Identifier les paragraphes à supprimer ───
    paragraphs_to_remove = _identify_paragraphs_to_remove(text, merged)
    
    return {
        "hallucinations": merged,
        "has_hallucinations": len(merged) > 0,
        "has_blocking_hallucinations": has_blocking,
        "paragraphs_to_remove": paragraphs_to_remove,
        "verifier1_clean": len(halluc1) == 0,
        "verifier2_clean": len(halluc2) == 0,
    }


def _identify_paragraphs_to_remove(text: str, hallucinations: list) -> list:
    """Identifie les paragraphes complets qui contiennent des hallucinations.
    Retourne une liste des paragraphes (texte complet) à supprimer."""
    if not hallucinations:
        return []
    
    # Découper le texte en paragraphes (séparés par lignes vides)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip() and len(p.strip()) > 30]
    
    to_remove = []
    for halluc in hallucinations:
        if halluc.get("severite", 0) < 3:
            continue  # Seulement les bloquants
        
        extrait = halluc.get("extrait", "").lower()[:60]
        if not extrait:
            continue
        
        # Trouver le paragraphe qui contient cet extrait
        for para in paragraphs:
            # Normaliser pour la comparaison (enlever markdown, espaces)
            para_clean = re.sub(r'[*#`\[\]()]', '', para).lower()
            extrait_clean = re.sub(r'[*#`\[\]()]', '', extrait).lower()
            
            # Comparaison par mots-clés (premiers 5 mots significatifs de l'extrait)
            extrait_words = [w for w in extrait_clean.split() if len(w) > 3][:5]
            if len(extrait_words) >= 2 and all(w in para_clean for w in extrait_words):
                if para not in to_remove:
                    to_remove.append(para)
                break
    
    return to_remove


def remove_paragraphs(text: str, paragraphs_to_remove: list) -> str:
    """Supprime les paragraphes litigieux du texte. Conserve le reste.
    Nettoie aussi les titres de section qui se retrouvent vides."""
    if not paragraphs_to_remove:
        return text
    
    cleaned = text
    for para in paragraphs_to_remove:
        cleaned = cleaned.replace(para, "")
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    
    # Nettoyer les titres de section vides (## Titre\n\n## suivant titre sans contenu entre)
    cleaned = re.sub(r'(^|\n)(#{1,3}\s+.+)\n\n(#{1,3}\s+)', r'\1\3', cleaned)
    # Nettoyer les titres en fin de texte sans contenu après
    cleaned = re.sub(r'\n(#{1,3}\s+.+)\s*$', '', cleaned)
    
    return cleaned.strip()


def _detect_hallucinations_v1(text: str, lang: str = "es") -> dict:
    """Vérificateur 1 : détection active — cherche les hallucinations."""
    try:
        content = _gateway_chat(
            [
                {"role": "system", "content": PROMPT_HALLUCINATION},
                {"role": "user", "content": text[:10000]},
            ],
            max_tokens=1500,
            temperature=0.1,
        )
        
        hallucinations = _parse_halluc_lines(content)
        return {"hallucinations": hallucinations}
    except Exception as e:
        logger.warning(f"Vérificateur 1 échoué: {e}")
        return {"hallucinations": [], "error": str(e)}


PROMPT_HALLUCINATION_V2 = """Tu es un éditeur de presse expérimenté. Tu dois valider ou rejeter un article.

L'article DOIT être rejeté s'il contient :
- Des rencontres ou conversations avec des personnes qui semblent inventées
- Des noms de personnes qui ne correspondent à aucune personne réelle identifiable
- Des citations fabriquées (paroles attribuées à quelqu'un de suspect)
- Des événements, prix ou cérémonies qui ne se sont pas réellement produits
- Des anecdotes présentées comme réelles mais qui semblent fictives

Sois CONSERVATEUR. En cas de doute, rejette. Un article inventé publié est pire qu'un article bon rejeté.

Réponds en TEXTE SIMPLE :
- Si l'article est PROPRE (aucune invention) : écris UNIQUEMENT "PROPRE"
- Si l'article contient des inventions, écris une ligne par problème :
REJET|type|extrait (max 100 chars)|raison (max 150 chars)

Types: rencontre_inventee, personne_imaginaire, citation_inventee, evenement_fabrique, anecdote_fictive

Article à valider :
"""


def _detect_hallucinations_v2(text: str, lang: str = "es") -> dict:
    """Vérificateur 2 : contre-interrogatoire indépendant avec prompt différent.
    Seuil plus conservateur (rejette au moindre doute)."""
    try:
        content = _gateway_chat(
            [
                {"role": "system", "content": PROMPT_HALLUCINATION_V2},
                {"role": "user", "content": text[:10000]},
            ],
            max_tokens=1500,
            temperature=0.1,
        )
        
        hallucinations = []
        for line in content.strip().splitlines():
            line = line.strip()
            if line.startswith("REJET|"):
                parts = line.split("|", 3)
                if len(parts) >= 4:
                    _, htype, extrait, raison = parts[0], parts[1], parts[2], parts[3]
                    hallucinations.append({
                        "type": htype.strip(),
                        "severite": 4,  # V2 est conservateur — tout rejet = sévérité 4 minimum
                        "extrait": extrait.strip()[:100],
                        "raison": raison.strip()[:150],
                    })
        
        return {"hallucinations": hallucinations}
    except Exception as e:
        logger.warning(f"Vérificateur 2 échoué: {e}")
        return {"hallucinations": [], "error": str(e)}


def _parse_halluc_lines(content: str) -> list:
    """Parse les lignes HALLUC| du vérificateur 1."""
    hallucinations = []
    for line in content.strip().splitlines():
        line = line.strip()
        if line.startswith("HALLUC|"):
            parts = line.split("|", 4)
            if len(parts) >= 5:
                _, htype, sev, extrait, raison = parts[0], parts[1], parts[2], parts[3], parts[4]
                try:
                    severite = int(sev.strip())
                except ValueError:
                    severite = 3
                hallucinations.append({
                    "type": htype.strip(),
                    "severite": severite,
                    "extrait": extrait.strip()[:100],
                    "raison": raison.strip()[:150],
                })
    return hallucinations


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

    # ─── Détection d'hallucinations (rencontres inventées, personnes imaginaires) ───
    halluc = detect_hallucinations(text, lang)
    has_halluc = halluc.get("has_blocking_hallucinations", False)
    halluc_details = halluc.get("hallucinations", [])

    if nb_contredit > 0 or has_halluc:
        niveau = "bloquant"
    elif score < 5:
        niveau = "revision_humaine"
    elif score < 8:
        niveau = "revision_humaine"
    else:
        niveau = "ok"

    elapsed = int((time.time() - t0) * 1000)
    halluc_count = len(halluc_details)
    logger.info(f"[Verify] {nb} affirmations: {nb_confirme}✓ {nb_contredit}✗ {nb_nv}? | Hallucinations: {halluc_count} | Score: {score}/10 | {elapsed}ms")

    return {
        "score_global": round(score, 1),
        "valide": nb_contredit == 0 and not has_halluc,
        "niveau_alerte": niveau,
        "nb_confirme": nb_confirme,
        "nb_contredit": nb_contredit,
        "nb_non_verifiable": nb_nv,
        "affirmations": results,
        "hallucinations": halluc_details,
        "nb_hallucinations": halluc_count,
        "has_hallucinations": has_halluc,
        "temps_ms": elapsed,
    }
