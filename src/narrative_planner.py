"""
narrative_planner.py — Planification narrative des images pour le Journal CCT.

Un LLM (gemini-3.1-pro) analyse le texte de l'article section par section,
génère des prompts photo hyper-spécifiques dignes d'un photoreporter,
et insère des marqueurs inviolables [[IMG:N]] dans le texte.

Ces marqueurs sont ensuite remplacés par le studio photo par les vraies <img>.
Personne ni aucune étape ultérieure ne doit modifier/supprimer les marqueurs.
"""
from __future__ import annotations
import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("cct-journal.narrative")

# ─── Gateway ─────────────────────────────────────────────────────────────────
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
NARRATIVE_MODEL = "gemini-3.1-pro-preview"  # 50K RPD, gratuit — intelligence max
FALLBACK_MODEL = "gemini-3-flash-preview"    # Fallback si pro surchargé

# ─── Prompt système du directeur de la photo ────────────────────────────────

SYSTEM_PROMPT = """Eres el **director de fotografía** del Club Costa Tropical, un fotoperiodista de la escuela de National Geographic.

Tu misión: analizar el texto de un artículo y generar **prompts fotográficos hiperrealistas y específicos** para cada sección del artículo. Cada prompt debe ser una descripción visual tan precisa que un fotógrafo humano podría salir a la calle y tomar exactamente esa foto.

REGLAS ABSOLUTAS:
1. **Cada sección ## H2** del artículo debe tener EXACTAMENTE UN prompt fotográfico.
2. Los prompts deben mencionar **lugares reales** de la Costa Tropical (Motril, Almuñécar, Salobreña, La Herradura, Castell de Ferro, Órgiva, las Alpujarras, la Axarquía, la vega, la sierra, el mar, etc.).
3. Los prompts deben incluir **detalles concretos** extraídos del texto: cultivos, edificios, personajes, momentos del día, condiciones climáticas.
4. **Prohibido** usar prompts genéricos como "paisaje", "gente", "vida local". Cada prompt debe ser único y específico del contenido de la sección.
5. **Formato periodístico** estilo El País / National Geographic: realismo documental, nada de fantasía ni surrealismo.
6. Para la sección de apertura (antes del primer H2, si existe), generar un prompt para la imagen HERO.

DEVUELVE EXACTAMENTE ESTE JSON, sin explicaciones, sin markdown:
{
  "hero": "Prompt para la imagen principal del artículo (si hay texto antes del primer H2)",
  "section_prompts": [
    "Prompt fotográfico detallado para la primera sección",
    "Prompt fotográfico detallado para la segunda sección"
  ]
}

Los prompts deben estar en el MISMO ORDEN que las secciones en el texto.
NO incluyas los títulos de las secciones en el JSON. Solo los prompts en orden."""


def _call_llm(system: str, user: str, model: str = NARRATIVE_MODEL, timeout: int = 60) -> str:
    """Appelle le Gateway LLM."""
    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/generate",
            json={"model": model, "contents": f"{system}\n\n{user}", "caller": "cct-journal-narrative"},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("text", "")
    except httpx.HTTPStatusError as e:
        if model != FALLBACK_MODEL:
            logger.warning(f"⚠️ {model} error, fallback vers {FALLBACK_MODEL}")
            return _call_llm(system, user, FALLBACK_MODEL, timeout)
        logger.error(f"❌ LLM error: {e.response.text[:200]}")
        return ""
    except Exception as e:
        logger.error(f"❌ LLM error: {e}")
        return ""


def _parse_article(text: str) -> Tuple[str, List[str]]:
    """Extrait le lead (avant premier H2) et les titres de sections H2.

    Returns:
        (lead_text, list_of_section_titles)
    """
    lines = text.split("\n")
    lead_parts = []
    sections = []
    in_lead = True

    for line in lines:
        stripped = line.strip()
        if in_lead and stripped.startswith("## ") and not stripped.startswith("### "):
            in_lead = False
        if in_lead and stripped and not stripped.startswith("# "):
            lead_parts.append(stripped)
        if not in_lead and stripped.startswith("## ") and not stripped.startswith("### "):
            title = stripped.replace("## ", "").strip()
            if title:
                sections.append(title)

    lead = " ".join(lead_parts)[:500] if lead_parts else ""
    return lead, sections


def plan_images(article_text: str, title: str) -> Tuple[str, List[Dict], str]:
    """Analyse le texte et planifie les photos à générer.

    Args:
        article_text: Texte complet de l'article (ES, markdown)
        title: Titre de l'article

    Returns:
        (text_with_markers, plan, raw_llm_response)
        text_with_markers = texte avec marqueurs [[IMG:hero]] et [[IMG:section-N]]
        plan = [{"section": str, "prompt": str, "type": "hero"|"section"}]
        raw_llm_response = réponse brute du LLM (pour debug)
    """
    lead, sections = _parse_article(article_text)

    # Construire le prompt utilisateur avec les titres exacts des sections
    sections_str = "\n".join(f"  - {s}" for s in sections) if sections else "(aucune section)"
    user_prompt = f"TÍTULO: {title}\n\nTEXT:\n\n{article_text[:8000]}\n\n---\n\nSECCIONES DETECTADAS (usa EXACTAMENTE estos títulos, no los modifiques):\n{sections_str}\n\nGenera un prompt para HERO y para CADA sección listada arriba. Los títulos de sección en el JSON deben ser IDÉNTICOS a los listados."

    if not sections and not lead:
        logger.warning("⚠️ Aucune section H2 trouvée dans l'article")
        return article_text, [], ""

    # Appel LLM
    logger.info(f"🎬 Planification narrative: {len(sections)} section(s) détectée(s)")
    raw = _call_llm(SYSTEM_PROMPT, user_prompt)

    # Parser la réponse JSON
    plan = []
    try:
        # Extraire le JSON de la réponse
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
        else:
            logger.warning("⚠️ Pas de JSON dans la réponse LLM, planification générique")
            data = {"sections": [{"section_title": s, "prompt": f"Fotografía documental sobre {s} en la Costa Tropical."} for s in sections]}
    except json.JSONDecodeError:
        logger.warning("⚠️ JSON invalide, planification générique")
        data = {"sections": [{"section_title": s, "prompt": f"Fotografía documental sobre {s} en la Costa Tropical."} for s in sections]}

    # Construire le plan à partir des TITRES RÉELS du texte + prompts du LLM par position
    hero_prompt = data.get("hero", "")
    if hero_prompt and lead:
        plan.append({"section": "hero", "prompt": hero_prompt[:500], "type": "hero", "marker": "[[IMG:hero]]"})

    # Récupérer les prompts section du LLM (liste ordonnée)
    section_prompts_llm = data.get("section_prompts", [])
    if not section_prompts_llm and isinstance(data.get("sections"), list):
        # Fallback ancien format
        section_prompts_llm = [s.get("prompt", "") for s in data["sections"] if isinstance(s, dict)]

    text_with_markers = article_text

    for i, sec_title in enumerate(sections):
        prompt = (section_prompts_llm[i][:500] if i < len(section_prompts_llm) and section_prompts_llm[i]
                  else f"Fotografía documental sobre {sec_title} en la Costa Tropical.")

        marker = f"[[IMG:section-{i+1}]]"
        plan.append({"section": sec_title, "prompt": prompt, "type": "section", "marker": marker})

        # Insérer le marqueur APRÈS le titre H2 (en utilisant le titre EXACT du texte)
        pattern = re.compile(
            r'(^|\n)(##\s*' + re.escape(sec_title) + r'\s*(?:\n\n|\n))',
            re.IGNORECASE | re.MULTILINE
        )
        replacement = r'\1\2' + marker + r'\n\n'
        new_text, count = pattern.subn(replacement, text_with_markers, count=1)

        if count > 0:
            text_with_markers = new_text
            logger.info(f"   📍 Marqueur [[IMG:section-{i+1}]] inséré après ## {sec_title[:40]}")
        else:
            logger.warning(f"   ⚠️ Section '## {sec_title[:40]}' non trouvée — marqueur non inséré")

    # Marqueur hero si présent
    if hero_prompt and lead:
        # Insérer [[IMG:hero]] après le titre H1
        h1_match = re.search(r'^#\s+(.+)$', text_with_markers, re.MULTILINE)
        if h1_match:
            marker = "[[IMG:hero]]\n\n"
            text_with_markers = text_with_markers.replace(
                h1_match.group(0) + "\n\n",
                h1_match.group(0) + "\n\n" + marker,
                1
            )
            logger.info("   📍 Marqueur [[IMG:hero]] inséré après le titre")

    n_plans = len(plan)
    if n_plans > 0:
        logger.info(f"✅ Planification: {n_plans} image(s) planifiée(s)")
        for p in plan:
            logger.info(f"   [{p['type']:7}] {p['prompt'][:80]}...")
    else:
        logger.warning("⚠️ Aucune image planifiée")

    return text_with_markers, plan, raw


def plan_images_for_lang(text_with_markers_es: str, lang_text: str) -> str:
    """Reproduit les marqueurs [[IMG:...]] dans les traductions FR/EN.

    Utilise les POSITIONS des sections H2 (invariant entre langues)
    plutôt que le contenu textuel des titres (variable).
    """
    # Compter les marqueurs dans le texte ES
    markers = re.findall(r'\[\[IMG:[^\]]+\]\]', text_with_markers_es)
    if not markers:
        return lang_text

    # Trouver les positions des H2 dans la traduction
    h2_positions = [m.start() for m in re.finditer(r'^##\s+', lang_text, re.MULTILINE)]

    result = lang_text
    offset = 0

    # Marqueur hero — insérer après le H1
    hero_markers = [m for m in markers if 'hero' in m]
    for m in hero_markers:
        h1_match = re.search(r'^#\s+(.+)$', result, re.MULTILINE)
        if h1_match:
            insert_after = h1_match.end()
            result = result[:insert_after] + '\n\n' + m + result[insert_after:]
            offset += len(m) + 2
        else:
            # Pas de H1 dans la traduction → insérer tout au début
            result = m + '\n\n' + result
            offset += len(m) + 2
            logger.warning("⚠️ Aucun H1 trouvé dans la traduction — marqueur hero inséré au début")

    # Marqueurs section — insérer après chaque H2 par position
    section_markers = [m for m in markers if 'section' in m]
    for i, m in enumerate(section_markers):
        if i >= len(h2_positions):
            break
        pos = h2_positions[i] + offset
        # Trouver la fin du H2 (le \n qui termine la ligne)
        end_of_line = result.find('\n', pos)
        if end_of_line == -1:
            continue
        insert_at = end_of_line + 1
        result = result[:insert_at] + '\n\n' + m + result[insert_at:]
        offset += len(m) + 2

    return result
