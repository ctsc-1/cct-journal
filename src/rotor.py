"""
rotor.py — Sélection du sujet du Journal CCT par rotation sur 11 catégories.

Remplace le système topics.yaml par un rotor automatique.
Chaque jour : catégorie tournante → sujet auto-généré via LLM → Deep Search.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx
import sys; sys.path.insert(0, "/srv/rag-engine")
from pipeline.model_env import get_model

logger = logging.getLogger("cct-journal.rotor")

# ─── 11 Catégories du Journal CCT ───────────────────────────────────────────

CATEGORIES = [
    {
        "id": "communes-villages",
        "category_id": "8fc513c8-2222-4f7d-801b-f52506c4116c",
        "name_es": "Municipios y Pueblos",
        "domain": "territorio",
        "description": "Portrait complet d'une commune de la Costa Tropical : histoire, géographie, économie, spécialités locales, vie quotidienne.",
        "angle": "Récit documentaire complet : des origines historiques aux enjeux actuels, en passant par l'économie locale, les spécialités et la qualité de vie.",
        "tags": ["municipios", "pueblos", "territorio", "historia_local", "economia_local"],
    },
    {
        "id": "enquetes-dossiers",
        "category_id": "c3d1056a-ccca-45e6-ac0a-42f2e30c8c2b",
        "name_es": "Enquêtes & Dossiers",
        "domain": "investigacion",
        "description": "Grands dossiers de fond sur la Costa Tropical : urbanisme, économie, société, environnement.",
        "angle": "Analyse approfondie avec données chiffrées, contexte historique et perspectives.",
        "tags": ["investigacion", "dossier", "analisis"],
    },
    {
        "id": "cultura-tradiciones",
        "category_id": "0cbf59b0-1012-47de-b91e-348600680d65",
        "name_es": "Cultura y Tradiciones",
        "domain": "cultura",
        "description": "Fêtes, traditions, artisanat, patrimoine immatériel de la Costa Tropical.",
        "angle": "Récit vivant des traditions andalouses, témoignages, atmosphère.",
        "tags": ["cultura", "tradiciones", "fiestas", "artesania"],
    },
    {
        "id": "gastronomia-vino",
        "category_id": "047d7527-d161-4c25-a948-3e6f88aa8a9e",
        "name_es": "Gastronomía y Vino",
        "domain": "gastronomia",
        "description": "Gastronomie locale, vins de la Costa, produits du terroir, chiringuitos, marchés.",
        "angle": "Voyage sensoriel : saveurs, producteurs, recettes traditionnelles, accords mets et vins.",
        "tags": ["gastronomia", "vino", "productos_locales", "cocina"],
    },
    {
        "id": "geografia-naturaleza",
        "category_id": "573075bf-2c0d-4b84-ba64-1a33107fd03d",
        "name_es": "Geografía y Naturaleza",
        "domain": "naturaleza",
        "description": "Paysages, plages, montagnes, parcs naturels, climat, biodiversité de la région.",
        "angle": "Description immersive des merveilles naturelles, conseils écotourisme, données climatiques.",
        "tags": ["naturaleza", "geografia", "playas", "montañas", "clima"],
    },
    {
        "id": "actividades-aventura",
        "category_id": "a0fd785c-50dd-4fad-aa42-ec20015f0e7e",
        "name_es": "Actividades y Aventura",
        "domain": "turismo",
        "description": "Sports nautiques, randonnée, plongée, escalade, activités de plein air.",
        "angle": "Guide pratique des activités : où, quand, comment, conseils et bons plans.",
        "tags": ["actividades", "aventura", "deportes", "turismo_activo"],
    },
    {
        "id": "historia-patrimonio",
        "category_id": "6e2d8a37-8f99-4fe8-995a-0499ef80f0ff",
        "name_es": "Historia y Patrimonio",
        "domain": "patrimonio",
        "description": "Sites historiques, monuments, archéologie, mémoire locale de la Costa Tropical.",
        "angle": "Voyage dans le temps : vestiges, personnages historiques, anecdotes et héritage.",
        "tags": ["historia", "patrimonio", "arqueologia", "monumentos"],
    },
    {
        "id": "diario-alejandro",
        "category_id": "b2c1056a-bbba-45e6-ac0a-42f2e30c8c2b",
        "name_es": "El Diario de Alejandro",
        "domain": "costumbres",
        "description": "Billet personnel d'Alejandro Ortega : observations, humeur, rencontres, vie quotidienne.",
        "angle": "Chronique intime et subjective, regard d'un journaliste amoureux de sa terre.",
        "tags": ["diario", "personal", "cotidiano", "reflexion"],
    },
    {
        "id": "terruno-agricultura",
        "category_id": "d4e1056a-ddda-45e6-ac0a-42f2e30c8c2b",
        "name_es": "Terruño y Agricultura",
        "domain": "economia",
        "description": "Agriculture locale, marchés de producteurs, cultures (avocat, mangue, canne à sucre), élevage.",
        "angle": "Plongée dans le monde agricole : saisons, producteurs, défis, saveurs du terroir.",
        "tags": ["agricultura", "terruno", "productores", "campo"],
    },
    {
        "id": "semanario-club",
        "category_id": "e5f1056a-eeee-45e6-ac0a-42f2e30c8c2b",
        "name_es": "El Semanario del Club",
        "domain": "club",
        "description": "Actualité du Club Costa Tropical : nouveaux membres, événements, fonctionnalités, vie communautaire.",
        "angle": "Newsletter engageante : ce qui s'est passé cette semaine, à venir, coups de cœur.",
        "tags": ["club", "semanal", "miembros", "comunidad"],
    },
    {
        "id": "revista-prensa",
        "category_id": "b4c7c458-0e7e-4528-97d2-8a744bf0398b",
        "name_es": "Revista de Prensa",
        "domain": "actualidad",
        "description": "Revue de presse commentée de l'actualité de la Costa Tropical et de l'Andalousie.",
        "angle": "Sélection éditorialisée des articles marquants de la semaine, avec analyse Alejandro.",
        "tags": ["prensa", "actualidad", "revista"],
    },
]

ROTATOR_KEY = "/srv/cct-journal/data/rotator_index"


def _load_index() -> int:
    """Lit l'index actuel du rotor depuis le stockage persistant."""
    try:
        if os.path.exists(ROTATOR_KEY):
            with open(ROTATOR_KEY) as f:
                return int(f.read().strip())
    except (ValueError, OSError):
        pass
    return -1  # Première fois


def _save_index(idx: int):
    """Sauvegarde l'index du rotor dans le stockage persistant."""
    try:
        os.makedirs(os.path.dirname(ROTATOR_KEY), exist_ok=True)
        with open(ROTATOR_KEY, "w") as f:
            f.write(str(idx))
    except OSError as e:
        logger.warning(f"⚠️ Rotor index save error: {e}")


def select_category(today: str | None = None) -> dict:
    """Sélectionne la catégorie du jour par rotation.

    Args:
        today: Date YYYY-MM-DD (défaut: aujourd'hui)

    Returns:
        La catégorie choisie (dict avec id, name_es, domain, description, angle, tags, category_id)
    """
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_idx = _load_index()

    # Premier run ou reset : commence à 0
    if last_idx < 0 or last_idx >= len(CATEGORIES) - 1:
        new_idx = 0
    else:
        new_idx = last_idx + 1

    category = CATEGORIES[new_idx]
    _save_index(new_idx)

    logger.info(f"🎯 Catégorie du jour [{new_idx + 1}/{len(CATEGORIES)}] : {category['name_es']}")
    return category


def _get_recent_titles(limit: int = 7) -> list:
    """Récupère les N derniers titres d'articles publiés pour éviter les répétitions."""
    import os, subprocess, re
    # Lit DATABASE_URL depuis l'environnement ou depuis le .env du RAG Engine
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            result = subprocess.run(
                ["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                db_url = result.stdout.strip().split("=", 1)[1].strip().strip("\"'")
        except (subprocess.TimeoutExpired, OSError):
            pass
    if not db_url:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT title_es FROM articles WHERE published_at IS NOT NULL ORDER BY published_at DESC LIMIT %s",
            (limit,)
        )
        titles = [row[0] for row in cur.fetchall() if row[0]]
        cur.close()
        conn.close()
        return titles
    except Exception:
        return []


def generate_topic(category: dict, date_str: str | None = None) -> dict:
    """Génère un sujet spécifique pour la catégorie du jour via Gateway LLM.

    Args:
        category: La catégorie sélectionnée
        date_str: Date YYYY-MM-DD

    Returns:
        Dict compatible avec l'ancien format topics.yaml :
        {id, domain, title, angle, context, tags, category_id}
    """
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    day_name = day_names[dt.weekday()]

    # Récupérer les 7 derniers titres d'articles pour anti-répétition
    recent_titles = _get_recent_titles(limit=7)
    
    # Prompt spécifique pour communes-villages
    communes_prompt_extra = ""
    if category["id"] == "communes-villages":
        communes_prompt_extra = (
            "Específicamente, elige UNA de las 97 localidades de la Costa Tropical "
            "(Motril, Salobreña, Almuñécar, Órgiva, Torvizcón, Vélez de Benaudalla, "
            "Los Guájares, Molvízar, Ítrabo, Jete, Otívar, Lújar, Gualchos, Castell de Ferro, "
            "Carchuna, Calahonda, Sorvilán, Polopos, Rubite, Órgiva, Cádiar, Cástaras, "
            "Juviles, Lobras, Bérchules, Busquístar, Pórtugos, Trévelez, Turón, Válor, "
            "Ugíjar, Murtas, Albondón, Adra, Berja, Dalías, El Ejido, La Mojonera, "
            "Vícar, Roquetas de Mar, Enix, Felix, etc.) y genera un artículo completo "
            "sobre ella: historia, geografía, economía, especialidades locales, "
            "calidad de vida, y qué la hace única. Elige una localidad DIFERENTE "
            "a las que se hayan tratado en los últimos 30 días.\n\n"
        )
    
    # Prompt court pour générer le sujet
    prompt = (
        f"Eres el redactor jefe del Club Costa Tropical. Hoy es {day_name} {date_str}.\n\n"
        f"Categoría del día: {category['name_es']}\n"
        f"Descripción: {category['description']}\n"
        f"Angle: {category['angle']}\n\n"
        f"{communes_prompt_extra}"
        "Genera un tema de artículo para hoy. El tema debe ser CONCRETO, actual y anclado "
        "en la realidad de la Costa Tropical (Motril, Almuñécar, Salobreña, Alpujarra, etc.).\n\n"
        "⚠️ REGLAS ABSOLUTAS:\\n"
        f"- ROTACIÓN ESTRICTA de 7 días: NO puedes repetir el mismo tema "
        f"tratado en los últimos 7 artículos. Los temas de los últimos 7 días son: "
        f"{'; '.join(recent_titles)}. Elige algo COMPLETAMENTE DIFERENTE.\\n"
        "- **PROHIBIDO** el tema de la sequía por falta de canalizaciones "
        "del embalse de Rules. No se trata este tema hasta SEPTIEMBRE de 2026.\\n"
        "- Busca variedad temática: cultura, deporte, gastronomía, turismo, "
        "sociedad, economía local, agricultura sostenible."
        "\\n"
        "- TONO: El artículo NO debe ser agresivo ni acusador. "
        "Alejandro Ortega observa, constata, analiza — no acusa, no denuncia, "
        "no hace juicios morales. Prohibido el tono 'ya-qué-falta-qué'. "
        "Los hechos hablan por sí mismos.\\n\\n"
        "Devuelve SOLO un JSON con:\n"
        "{\n"
        '  "title": "Título del artículo (30-50 caracteres, máximo 55. DIRECTO. Sin subtítulo. Que no sea una frase completa. Ej: \\\"El pulpo seco de Castell\\\" no \\\"El ritual del sol y el viento: el pulpo seco de Castell\\\")",\n'
        '  "angle": "Ángulo narrativo específico para hoy (1-2 frases)",\n'
        '  "context": "Contexto: datos concretos, lugares, personajes, cifras si las conoces (3-5 líneas)"\n'
        "}\n\n"
        "NO expliques nada. Solo el JSON."
    )

    GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/generate",
            json={
                "model": get_model("ROTOR", "gemini-2.5-flash-lite"),
                "contents": prompt,
                "caller": "cct-journal-rotor",
            },
            timeout=30,
        )
        r.raise_for_status()
        raw = r.json().get("text", "")

        # Extraire le JSON
        import re
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
        else:
            logger.warning("⚠️ No JSON in rotor response, using generic topic")
            data = {}

        topic = {
            "id": f"{category['id']}-{date_str}",
            "domain": category["domain"],
            "title": data.get("title", f"Crónica de {category['name_es']}"),
            "angle": data.get("angle", category.get("angle", "")),
            "context": data.get("context", category.get("description", "")),
            "tags": category.get("tags", []),
            "category_id": category["category_id"],
        }

        logger.info(f"📌 Sujet généré: {topic['title'][:80]}")
        return topic

    except Exception as e:
        logger.warning(f"⚠️ Topic generation error: {e}, using generic topic")
        return {
            "id": f"{category['id']}-{date_str}",
            "domain": category["domain"],
            "title": f"Crónica de {category['name_es']}",
            "angle": category.get("angle", ""),
            "context": category.get("description", ""),
            "tags": category.get("tags", []),
            "category_id": category["category_id"],
        }


def select_topic(today: str | None = None, force_category: str | None = None) -> dict:
    """Point d'entrée unique : sélectionne catégorie + génère sujet.

    Args:
        today: Date YYYY-MM-DD
        force_category: Slug de catégorie à forcer (optionnel)

    Returns:
        Dict topic complet {id, domain, title, angle, context, tags, category_id}
    """
    if force_category:
        category = next(
            (c for c in CATEGORIES if c["id"] == force_category),
            CATEGORIES[0]
        )
        logger.info(f"🎯 Catégorie forcée: {category['name_es']}")
    else:
        category = select_category(today)

    topic = generate_topic(category, today)

    # S'assurer que category_id est dans le topic
    topic["category_id"] = topic.get("category_id", category["category_id"])

    return topic
