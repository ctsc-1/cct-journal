"""
ai_detectability.py — Détecteur de rédaction IA pour le Journal CCT.
Utilise Gemini 2.5 Flash Lite via la Gateway pour analyser un texte
et retourner un score de détectabilité (0-100) + patterns spécifiques.

Loop Engineering: GENERATE → VERIFY (ce module) → REFLECT & FIX → LOOP
"""
from __future__ import annotations
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("cct-journal.ai-detectability")

GATEWAY_URL = "http://127.0.0.1:4000"
DETECTOR_MODEL = "deepseek-v4-flash"

DETECTOR_PROMPT = """Eres un experto en detección de textos generados por inteligencia artificial.
Analiza el texto siguiente y determina si fue escrito por un humano o por una IA.

TU ANÁLISIS DEBE SER PRECISO Y ESPECÍFICO. Identifica los patrones CONCRETOS que delatan a la IA.

CRITERIOS DE DETECCIÓN (analiza cada uno):
1. **Longitud de frases** — ¿Todas las frases tienen una longitud similar? Los humanos alternan frases muy cortas con muy largas.
2. **Estructura de párrafos** — ¿Cada párrafo tiene exactamente la misma estructura (presentación + desarrollo + cierre)?
3. **Simetría de secciones** — ¿Cada sección H2 tiene aproximadamente el mismo número de palabras?
4. **Transiciones** — ¿Todas las transiciones entre párrafos son fluidas y lógicas? Los humanos a veces saltan bruscamente.
5. **Vocabulario** — ¿Hay repetición de ciertas estructuras sintácticas? ¿Demasiada variedad "perfecta"?
6. **Ritmo** — ¿El texto tiene un ritmo mecánico o natural? Los textos IA son "demasiado consistentes".
7. **Marcadores discursivos** — ¿Uso excesivo de "además", "por otro lado", "sin embargo", "asimismo", "no obstante"?
8. **Paralelismos** — ¿Las frases dentro de un mismo párrafo tienen estructuras gramaticales paralelas?
9. **Concreción vs abstracción** — ¿El texto se mantiene en un nivel de abstracción uniforme o alterna detalles concretos con observaciones generales?
10. **Naturalidad** — ¿El texto parece escrito por alguien que conoce el tema de primera mano o por alguien que investigó y sintetizó?

INSTRUCCIONES DE SALIDA:
Debes responder EXACTAMENTE en este formato, sin añadir nada más:

SCORE: [número del 0 al 100, donde 0=claramente humano, 100=claramente IA]
PATTERNS:
- [patrón específico identificado, con cita textual entre comillas]
- [siguiente patrón...]
FEEDBACK:
[2-3 frases de feedback accionable para que el autor pueda reescribir el texto de forma menos detectable. Sé específico: menciona frases exactas y cómo mejorarlas.]

TEXTO A ANALIZAR:
"""


def detect_ai_patterns(text: str, lang: str = "es") -> Dict:
    """
    Analyse un texte et retourne un score de détectabilité IA + patterns.

    Args:
        text: Le texte à analyser
        lang: Langue (es, fr, en)

    Returns:
        {
            "score": int (0-100),
            "patterns": List[str],
            "feedback": str,
            "success": bool,
            "error": str | None
        }
    """
    if not text or len(text.strip()) < 200:
        return {
            "score": 0,
            "patterns": [],
            "feedback": "Texte trop court pour analyse fiable.",
            "success": False,
            "error": "Texte trop court"
        }

    # Limiter à 8000 caractères pour ne pas saturer le contexte
    sample = text[:8000]

    try:
        import httpx
        payload = {
            "model": DETECTOR_MODEL,
            "messages": [
                {"role": "system", "content": DETECTOR_PROMPT},
                {"role": "user", "content": f"Analiza este texto en {lang}:\n\n{sample}"}
            ],
            "temperature": 0.1,  # Basse température pour analyse cohérente
            "max_tokens": 1024,
        }

        resp = httpx.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        content = ""
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        if not content:
            return {"score": 50, "patterns": [], "feedback": "Analyse vide", "success": False, "error": "Réponse vide"}

        # Parser la réponse structurée
        score, patterns, feedback = _parse_response(content)

        logger.info(
            f"📊 AI Detectability: score={score}% | patterns={len(patterns)} | "
            f"feedback={'oui' if feedback else 'non'}"
        )

        return {
            "score": score,
            "patterns": patterns,
            "feedback": feedback,
            "success": True,
            "error": None,
        }

    except Exception as e:
        logger.warning(f"⚠️ AI detectability error: {e}")
        return {
            "score": 50,  # En cas d'erreur, on ne bloque pas
            "patterns": [],
            "feedback": f"Erreur technique: {e}",
            "success": False,
            "error": str(e),
        }


def _parse_response(content: str) -> tuple:
    """Parse la réponse du LLM pour extraire score, patterns, feedback."""
    score = 50  # Valeur par défaut prudente
    patterns = []
    feedback = ""

    lines = content.strip().split("\n")
    current_section = None

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith("SCORE:"):
            try:
                # Extraire le nombre
                score_str = stripped.split(":", 1)[1].strip()
                # Prendre le premier nombre trouvé
                import re
                nums = re.findall(r'\d+', score_str)
                if nums:
                    score = int(nums[0])
                    score = max(0, min(100, score))
            except (ValueError, IndexError):
                pass

        elif upper.startswith("PATTERNS"):
            current_section = "patterns"
            continue

        elif upper.startswith("FEEDBACK"):
            current_section = "feedback"
            continue

        elif stripped.startswith("-") and current_section == "patterns":
            pattern = stripped.lstrip("- ").strip()
            if pattern:
                patterns.append(pattern)

        elif current_section == "feedback":
            if feedback:
                feedback += " " + stripped
            else:
                feedback = stripped

    return score, patterns, feedback


def ai_feedback_to_prompt(feedback: str, patterns: List[str], score: int, lang: str = "es") -> str:
    """
    Convertit les résultats de détection en feedback actionnable
    pour injection dans le prompt de régénération.

    Returns:
        str: Texte de feedback formaté pour le LLM
    """
    if not patterns and not feedback:
        return ""

    threshold = 30  # Seuil : si score > 30, on donne le feedback complet
    if score <= threshold:
        return ""

    lines = [
        f"### 🚨 DETECCIÓN DE REDACCIÓN IA — SCORE: {score}%",
        "El texto anterior ha sido detectado como generado por IA por un analizador externo.",
        "Debes reescribirlo para que sea INDISTINGUIBLE de un texto escrito por un humano.",
        "",
        "**REGLAS PARA LA REWRITACIÓN (sin inventar nada, sin cambiar datos):**",
    ]

    if patterns:
        lines.append("")
        lines.append("**Patrones específicos detectados en tu texto:**")
        for i, p in enumerate(patterns[:5], 1):
            lines.append(f"{i}. {p}")

    if feedback:
        lines.append("")
        lines.append(f"**Feedback del detector:** {feedback}")

    lines.append("")
    lines.append("**CRÍTICO — NO HAGAS ESTO:**")
    lines.append("- No inventes personas, encuentros, citas o escenarios narrativos")
    lines.append("- No añadas opiniones personales ni incises ficticias")
    lines.append("- No cambies ningún dato, cifra o hecho del texto original")
    lines.append("")
    lines.append("**HAZ ESTO EN SU LUGAR:**")
    lines.append("- Varía la longitud de las frases (alterna muy cortas con muy largas)")
    lines.append("- Rompe la simetría entre secciones (unas más largas, otras más cortas)")
    lines.append("- Usa transiciones menos perfectas: a veces un salto de párrafo seco basta")
    lines.append("- No todas las secciones necesitan la misma estructura interna")
    lines.append("- Escribe como un periodista que domina el tema, no como un alumno que hace un trabajo")
    lines.append("")
    lines.append("Reescribe el texto COMPLETO aplicando estas correcciones, preservando TODOS los datos y hechos.")

    return "\n".join(lines)


# Pour test rapide
if __name__ == "__main__":
    import sys
    test_text = sys.stdin.read() if not sys.stdin.isatty() else "Texte de test court."
    result = detect_ai_patterns(test_text)
    print(f"Score: {result['score']}%")
    print(f"Patterns ({len(result['patterns'])}):")
    for p in result['patterns']:
        print(f"  - {p}")
    print(f"Feedback: {result['feedback'][:200]}")
