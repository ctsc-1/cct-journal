"""
article_cortex.py — Phase Réflexion pour le Journal CCT.

Analyse le sujet du rotor AVANT génération :
- Angle éditorial précis
- Pièges à éviter (hallucinations, données manquantes)
- Structure H2 recommandée
- Types d'images suggérées

Modèle : configurable via /etc/cct/models.env (MODEL_CORTEX)
Fallback : deepseek-v4-flash si modèle non alloué
Consommation : ~500 tokens — peut utiliser un modèle "intelligent"
"""
from __future__ import annotations
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger("cct-journal.cortex")

GATEWAY_URL = "http://127.0.0.1:4000"

# Modèle par défaut si MODEL_CORTEX non défini dans models.env
DEFAULT_CORTEX_MODEL = "deepseek-v4-flash"

CORTEX_SYSTEM_PROMPT = """Eres un editor jefe adjunto especializado en **analisis editorial previo**.

Tu unica funcion es ANALIZAR un tema propuesto y producir un plan editorial
estructurado. NO escribes contenido, NO produces articulos.

Debes ser preciso, critico y detectar problemas ANTES de que se generen.

Analiza estos aspectos:
1. **ANGULO EDITORIAL** — Que enfoque especifico diferenciara este articulo?
2. **PELIGROS** — Datos que podrian faltar, riesgos de alucinacion, temas sensibles
3. **ESTRUCTURA H2** — Que secciones, en que orden, cual es el punto fuerte de cada una
4. **IMAGENES** — Que tipo de imagen necesita cada seccion (mapa, primer plano, paisaje, infografia)
5. **ANTI-DOBLON** — Este tema se ha tratado en los ultimos 45 dias? Cual era el angulo entonces?

Responde EXACTAMENTE en este formato JSON, sin añadir nada mas:
```json
{
    "angle": "texto de 2-3 frases explicando el angulo",
    "dangers": ["peligro 1", "peligro 2"],
    "h2_structure": ["Titulo H2 1", "Titulo H2 2", "Titulo H2 3"],
    "image_types": ["mapa", "paisaje", "primer_plano", "infografia"],
    "key_points": ["dato clave 1", "dato clave 2"],
    "anti_duplicate_note": "texto si aplica, o 'sin duplicado'"
}
```
"""


def _load_cortex_model() -> str:
    """Charge le modèle cortex depuis /etc/cct/models.env, sinon fallback."""
    try:
        for line in open("/etc/cct/models.env"):
            if line.startswith("MODEL_CORTEX="):
                return line.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass
    return DEFAULT_CORTEX_MODEL


def cortex_refine(
    topic: Dict,
    recent_topics: Optional[List[str]] = None,
    deep_context: str = "",
) -> Dict:
    """
    Analyse un sujet et produit un plan editorial structuré.

    Args:
        topic: Dictionnaire du sujet (id, domain, title, angle, context)
        recent_topics: Liste des sujets traités récemment (anti-doublon)

    Returns:
        {
            "success": bool,
            "refined_angle": str,
            "dangers": List[str],
            "h2_structure": List[str],
            "image_types": List[str],
            "key_points": List[str],
            "anti_duplicate_note": str,
            "raw_response": str,       # Réponse brute du LLM
        }
        ou {"success": False} si erreur
    """
    model = _load_cortex_model()
    logger.info(f"🧠 Cortex: analysing topic '{topic.get('id', '?')}' with {model}")

    # Construire le prompt utilisateur
    lines = [
        f"**Tema del articulo:** {topic.get('title', 'sin titulo')}",
        f"**Dominio editorial:** {topic.get('domain', '?')}",
        f"**Angulo propuesto:** {topic.get('angle', 'sin angulo')}",
        f"**Contexto:** {topic.get('context', 'sin contexto')[:2000] if topic.get('context') else 'sin contexto'}",
    ]

    if deep_context:
        lines.append(f"\n**DeepSearch reciente:**\n{deep_context[:2000]}")

    if recent_topics:
        lines.append(f"\n**Temas recientes (anti-duplicado):**")
        for t in recent_topics[:10]:
            lines.append(f"  - {t}")

    user_prompt = "\n".join(lines)

    try:
        import httpx
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": CORTEX_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,  # Fiable, pas créatif
            "max_tokens": 1024,
        }

        resp = httpx.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()

        if not content:
            logger.warning("⚠️ Cortex: réponse vide")
            return {"success": False, "error": "Empty response"}

        # Extraire le JSON de la réponse
        result = _parse_cortex_response(content)

        logger.info(
            f"✅ Cortex OK: angle={len(result.get('refined_angle', ''))} chars, "
            f"{len(result.get('dangers', []))} dangers, "
            f"{len(result.get('h2_structure', []))} H2 sections"
        )
        result["success"] = True
        result["raw_response"] = content
        result["model_used"] = model
        return result

    except Exception as e:
        logger.warning(f"⚠️ Cortex error (mode degradé): {e}")
        return {"success": False, "error": str(e)}


def _parse_cortex_response(content: str) -> Dict:
    """Extract JSON from the LLM response, handling markdown code blocks."""
    import re

    # Try to find JSON in ```json ... ``` block
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find JSON directly (any { } block)
    json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    # Fallback: return everything as raw text
    return {
        "refined_angle": content[:500],
        "dangers": [],
        "h2_structure": [],
        "image_types": [],
        "key_points": [],
        "anti_duplicate_note": "",
        "_parse_warning": "JSON non parsé, contenu brut dans refined_angle"
    }


def cortex_feedback_to_prompt(cortex_result: Dict) -> str:
    """
    Convertit le résultat du cortex en feedback injectable dans le prompt de synthèse.
    Mode dégradé : retourne "" si le cortex a échoué.
    """
    if not cortex_result.get("success"):
        return ""

    lines = []

    angle = cortex_result.get("refined_angle", "")
    if angle:
        lines.append(f"### 🎯 ÁNGULO EDITORIAL REFINADO\n{angle}\n")

    dangers = cortex_result.get("dangers", [])
    if dangers:
        lines.append("### ⚠️ PELIGROS IDENTIFICADOS — EVITAR")
        for d in dangers:
            lines.append(f"- {d}")
        lines.append("")

    key_points = cortex_result.get("key_points", [])
    if key_points:
        lines.append("### ✅ PUNTOS CLAVE A CUBRIR")
        for k in key_points[:5]:
            lines.append(f"- {k}")
        lines.append("")

    h2 = cortex_result.get("h2_structure", [])
    if h2:
        lines.append("### 📋 ESTRUCTURA H2 SUGERIDA")
        for i, h in enumerate(h2, 1):
            lines.append(f"  {i}. {h}")
        lines.append("")

    anti = cortex_result.get("anti_duplicate_note", "")
    if anti and anti != "sin duplicado":
        lines.append(f"### 🔄 NOTA ANTI-DUPLICADO\n{anti}\n")

    return "\n".join(lines)


# Pour test rapide
if __name__ == "__main__":
    import sys
    test_topic = {
        "id": "test-topic",
        "domain": "gastronomia",
        "title": "El vino de la Alpujarra: tradición y altitud",
        "angle": "Cómo la altitud extrema y las variedades autóctonas están redefiniendo la viticultura alpujarreña",
        "context": "La DOP Vinos de Granada protege más de 500.000 botellas anuales. Tres subzonas. La Vijiriego es una variedad blanca autóctona recuperada.",
    }
    result = cortex_refine(test_topic)
    print(json.dumps(result, indent=2, ensure_ascii=False))
