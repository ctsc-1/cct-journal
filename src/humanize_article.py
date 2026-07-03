"""
humanize_article.py — Humanisation anti-IA pour le Journal CCT.

Wrapper vers le validator existant dans /srv/rag-engine/domains/humanization/validator.py.
Détecte et corrige 29+ patterns IA en FR/ES/EN.
"""
from __future__ import annotations
import logging
import sys
from typing import Dict

logger = logging.getLogger("cct-journal.humanize")

# Import du validator du RAG Engine
_HUMANIZER_AVAILABLE = False
try:
    sys.path.insert(0, "/srv/rag-engine")
    from domains.humanization.validator import validate_humanization, calculate_ia_score
    _HUMANIZER_AVAILABLE = True
    logger.info("✅ Humanizer module loaded from RAG Engine")
except ImportError as e:
    logger.warning(f"⚠️ Humanizer not available: {e}")


def humanize(text: str, lang: str = "es", auto_fix: bool = True) -> Dict:
    """
    Humanise un texte en détectant et corrigeant les patterns IA.
    
    Args:
        text: Texte à humaniser
        lang: Langue (fr, es, en)
        auto_fix: Si True, corrige automatiquement les patterns courants
    
    Returns:
        {
            "text": str,            # Texte final (corrigé si auto_fix)
            "fixed": bool,          # True si des corrections ont été appliquées
            "score_before": int,    # Score IA avant correction (0-100)
            "score_after": int,     # Score IA après correction (0-100)
            "fixes_applied": int,   # Nombre de corrections
            "detections": list,     # Liste des patterns détectés
        }
    """
    if not _HUMANIZER_AVAILABLE:
        logger.warning("⚠️ Humanizer unavailable — returning text as-is")
        return {
            "text": text,
            "fixed": False,
            "score_before": 0,
            "score_after": 0,
            "fixes_applied": 0,
            "detections": [],
        }

    # Valider et corriger
    result = validate_humanization(text, lang=lang, auto_fix=auto_fix)

    fixed_text = result.get("fixed_text", text)
    score_before = result.get("score_before", {})
    score_after = result.get("score_after", score_before)
    fixes = result.get("fixes_applied", 0)

    logger.info(
        f"🤖 Humanize [{lang}]: score {score_before.get('score', '?')}% → "
        f"{score_after.get('score', '?')}% | fixes={fixes} | "
        f"acceptable={'✅' if result.get('is_acceptable', False) else '⚠️'}"
    )

    return {
        "text": fixed_text,
        "fixed": fixes > 0 or score_after.get("score", 100) < score_before.get("score", 0),
        "score_before": score_before.get("score", 0),
        "score_after": score_after.get("score", 0),
        "fixes_applied": fixes,
        "detections": score_before.get("detections", []),
    }


def humanize_trilingual(translations: Dict[str, str], auto_fix: bool = True) -> Dict[str, Dict]:
    """
    Humanise les 3 langues d'un article.
    
    Args:
        translations: {"es": str, "fr": str, "en": str}
        auto_fix: Corriger automatiquement
    
    Returns:
        {
            "es": {"text": str, "score_before": ..., ...},
            "fr": {...},
            "en": {...},
            "overall_score": int,  # Score moyen après humanisation
        }
    """
    results = {}
    scores = []

    for lang in ("es", "fr", "en"):
        text = translations.get(lang, "")
        if not text:
            logger.warning(f"⚠️ No text for {lang} — skipping humanization")
            results[lang] = {"text": text, "fixed": False, "score_before": 0, "score_after": 0, "fixes_applied": 0, "detections": []}
            continue

        result = humanize(text, lang=lang, auto_fix=auto_fix)
        results[lang] = result
        scores.append(result["score_after"])

    # Score moyen
    overall = int(sum(scores) / len(scores)) if scores else 0

    return {
        **results,
        "overall_score": overall,
    }
