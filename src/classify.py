"""classify.py — Classification de profondeur du sujet du Journal CCT.

Retourne le niveau de profondeur, le nombre de mots cible et la raison.
Utilise le modèle CLASSIFY (Gemini 2.5 Flash Lite via Gateway) pour analyser
le sujet et déterminer la profondeur appropriée.

Niveaux de profondeur:
- "editorial": 6000 mots — enquêtes, dossiers, grands reportages
- "article": 4000 mots — articles standards
- "flash": 2000 mots — actualité brève, revue de presse
"""

import json
import logging
import os
from typing import Tuple

logger = logging.getLogger("cct-journal.classify")


def classify_topic(topic: dict) -> Tuple[str, int, str]:
    """Classifie le sujet et retourne (level, target_words, reason).

    Utilise un LLM dédié via la Gateway pour analyser la profondeur.
    Fallback: 6000 mots pour Enquêtes & Dossiers, 4000 pour le reste.
    """
    domain = topic.get("domain", "")
    title = topic.get("title", "")
    context = topic.get("context", "")[:300]
    category_id = topic.get("category_id", "")

    try:
        import httpx
        GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
        from pipeline.model_env import get_model

        prompt = (
            "Clasifica este tema de articulo de la Costa Tropical segun su profundidad editorial.\\n\\n"
            + f"Titulo: {title}\\n"
            + f"Dominio: {domain}\\n"
            + f"Contexto: {context}\\n\\n"
            + "Categorias de profundidad:\\n"
            + "- editorial: investigaciones, reportajes, dossieres, grandes temas de fondo → 6000 palabras\\n"
            + "- article: articulos estandar, guias, reportajes → 4000 palabras\\n"
            + "- flash: noticias breves, actualidad, notas → 2000 palabras\\n\\n"
            + "Responde SOLO con un JSON: {\"level\": \"editorial|article|flash\"}"
        )

        r = httpx.post(
            f"{GATEWAY_URL}/v1/generate",
            json={
                "model": get_model("CLASSIFY", "gemini-2.5-flash-lite"),
                "contents": prompt,
                "caller": "cct-journal-classify",
            },
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json().get("text", "")

        import re
        m = re.search(r'\{[^}]+\}', raw)
        if m:
            data = json.loads(m.group(0))
            level = data.get("level", "article")
        else:
            level = "article"

    except Exception as e:
        logger.warning(f"Classify error: {e}, using fallback by domain")
        # Fallback basique par domaine
        deep_domains = {"investigacion", "economia", "patrimonio"}
        level = "editorial" if domain in deep_domains else "article"

    target_map = {"editorial": 6000, "article": 4000, "flash": 2000}
    target_words = target_map.get(level, 4000)

    logger.info(f"Classify: {level} → {target_words} mots")
    return (level, target_words, f"Classification {level}")
