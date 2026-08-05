#!/usr/bin/env python3
"""
photo_studio.py — Studio photo pour le Journal CCT.

Deux fonctions distinctes :
1. generate_hero() — Hero 16:9 basée sur titre + lead + thème global
2. generate_section_images() — Une image par H2, basée sur le titre ET le paragraphe

Règle absolue : JAMAIS mentionner "National Geographic" dans les prompts FAL.
Le modèle insère le nom dans l'image si le prompt le contient.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# ─── CONFIG ─────────────────────────────────────────────────
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL_LIGHT = "deepseek-v4-flash"  # Modèle officiel Marc (04/07/2026: JAMAIS deepseek-v4-pro) — non-pensée, content direct. Ne PAS utiliser deepseek-chat (déprécié 24/07/2026) ni deepseek-v4-pro (interdit)
OUTPUT_DIR = Path("/srv/pwa/public/images/journal")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATE = datetime.now().strftime("%Y-%m-%d")
TIMEOUT_IMAGE = 120  # génération FAL
TIMEOUT_LLM = 60     # génération de prompt

DEEPSEEK_API_KEY = None

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


def _llm(prompt: str, max_tokens: int = 500, temp: float = 0.4) -> str:
    """Appel LLM pour générer des prompts photo — DeepSeek direct si applicable."""
    if "deepseek" in MODEL_LIGHT.lower():
        api_key = _get_deepseek_key()
        r = httpx.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": MODEL_LIGHT, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens, "temperature": temp,
                  "reasoning_effort": "none"},  # ponytail: empêche DeepSeek d'entrer en mode thinking (04/08/2026)
            timeout=TIMEOUT_LLM,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        # Correctif 03/08/2026 : NE JAMAIS fallback sur reasoning_content — le mode
        # thinking re-cite la consigne (titre/paragraphe espagnol) qui, passée à FAL,
        # fait dessiner le texte par FLUX. content vide => prompt générique.
        if not content:
            return "Documentary photograph of the Costa Tropical scene, natural Mediterranean light, no text, no letters"
        return content
    r = httpx.post(
        f"{GATEWAY}/v1/chat/completions",
        json={
            "model": MODEL_LIGHT,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temp,
        },
        timeout=TIMEOUT_LLM,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _generate_fal(prompt: str, filename: str, width: int = 1024, height: int = 576) -> Optional[str]:
    """Génère une image via MCP FAL (port 8700) et retourne l'URL publique."""
    import sys
    sys.path.insert(0, "/srv/rag-engine")
    from pipeline.mcp_image import generate_and_save

    path = OUTPUT_DIR / filename
    if path.exists() and path.stat().st_size > 1000:
        log(f"   ⏩ Déjà existant: {filename}")
        return f"/images/journal/{filename}"

    output_base = str(path.with_suffix(""))
    try:
        saved_path = asyncio.run(generate_and_save(
            prompt, output_base, max_width=1200, width=width, height=height,
            timeout=TIMEOUT_IMAGE
        ))
        if saved_path:
            log(f"   ✅ {Path(saved_path).name}")

            # Post-processing: WebP (déjà WebP via MCP FAL) — PAS d'upload GDrive.
            # FAL/Replicate génèrent directement du WebP léger (mcp_fal_server.py
            # écrit .webp q80), il n'y a aucun PNG lourd à sauvegarder. L'upload
            # GDrive était inutile (rien de volumineux à archiver). Simplif.
            from image_postprocess import process_fal_output
            webp_result = process_fal_output(Path(saved_path), upload_to_drive=False)
            if webp_result:
                return f"/images/journal/{webp_result.name}"
            else:
                return f"/images/journal/{Path(saved_path).name}"
    except Exception as e:
        log(f"   ⚠️ FAL error: {e}")
    return None


# ═══════════════════════════════════════════════════════════
# PROMPT BUILDER — Flash Lite génère des prompts photo pros
# ═══════════════════════════════════════════════════════════

def build_hero_prompt(article_es: str, title_es: str) -> str:
    """
    Génère un prompt photo professionnel pour le HERO.
    Basé sur le titre + lead + thème global de l'article.
    """
    # Extraire le lead (premier paragraphe après le H1)
    lead_match = re.search(r'^#\s+.+\n+(.+?)(?=\n##|\Z)', article_es, re.MULTILINE | re.DOTALL)
    lead = lead_match.group(1).strip()[:500] if lead_match else ""

    llm_prompt = f"""Eres un fotógrafo documental profesional. 
Genera UN prompt de 60-80 palabras en INGLÉS para generar una FOTOGRAFÍA de PORTADA (hero image) para un artículo periodístico.

TÍTULO DEL ARTÍCULO: {title_es}
ENTRADA: {lead[:400]}

⚠️ REGLA CRÍTICA ANTI-TEXTO:
Describe SOLO la escena visual (paisaje, objetos, luz, composición).
NO copies ni parafrasees el título ni el lead del artículo.
PROHIBIDO nombres propios, topónimos y cualquier palabra que pueda renderizarse como texto. NO citar la entrada ni los temas.

REGLAS ABSOLUTAS:
- Describe la ESCENA VISUAL: sujeto, fondo, luz, colores, composición, atmósfera
- Luz natural mediterránea, composición profesional, profundidad de campo
- PROHIBIDO mencionar "National Geographic", "Getty", "Reuters" o cualquier marca
- PROHIBIDO: logo, marca de agua, TEXT, letters, typography, caption, words, sign, banner
- NO incluyas personas reconocibles o primeros planos de rostros
- Estilo: fotoperiodismo documental, color natural, contrastado
- Formato: UNA SOLA FRASE en inglés

RESPUESTA (solo el prompt visual, sin palabras del título ni del lead):
"""

    prompt_en = _llm(llm_prompt, max_tokens=200, temp=0.4)

    # Nettoyage
    prompt_en = prompt_en.strip().strip('"').strip("'")
    # Virer les résidus de marque
    for banned in ["National Geographic", "Getty", "Reuters", "Magnum", "Leica"]:
        prompt_en = prompt_en.replace(banned, "documentary photography")

    # Anti-texte renforcé : interdire explicitement tout rendu textuel en fin de prompt
    prompt_en = prompt_en.rstrip(' .,')
    if prompt_en and not any(t in prompt_en.lower() for t in ["no text", "no letters", "without text"]):
        prompt_en = f"{prompt_en}, no text, no letters, no typography, no watermark"

    log(f"   Hero prompt: {prompt_en[:100]}...")
    return prompt_en


def build_section_prompt(h2_title: str, section_text: str, index: int) -> str:
    """
    Génère un prompt photo professionnel pour une SECTION H2.
    Basé sur le titre H2 ET le contenu du paragraphe.
    """
    # Nettoyer le texte de la section
    section_clean = re.sub(r'\[\[PHOTO:\d+\]\]', '', section_text[:500])
    section_clean = re.sub(r'!\[.*?\]\(.*?\)', '', section_clean)

    llm_prompt = f"""Eres un fotógrafo documental profesional.
Genera UN prompt de 45-60 palabras en INGLÉS para UNA FOTOGRAFÍA que ilustra ESTA sección.

TÍTULO DE LA SECCIÓN: {h2_title}
CONTENIDO DEL PÁRRAFO: {section_clean[:400]}

⚠️ REGLA CRÍTICA ANTI-TEXTO:
El prompt describe SOLO lo que se VE en la imagen: escena, objetos, materiales, luz, entorno.
NO copies ni parafrasees el título ni el texto del artículo.
NO uses frases literales del TÍTULO o del PÁRRAFO como contenido descriptivo.
El prompt final NO debe contener ninguna palabra en español ni ninguna expresión que pueda renderizarse como texto/lugar/leyenda.

REGLAS ABSOLUTAS:
- Describe la ESCENA VISUAL concreta (materiales, herramientas, objetos en primer plano, fondo, luz, colores, composición)
- PROHIBIDO mencionar "National Geographic", "Getty", "Reuters", "Magnum"
- PROHIBIDO: logo, watermark, TEXT, letters, typography, caption, words, sign, banner, inscription
- PROHIBIDO nombres propios, topónimos y frases — SOLO descripción visual
- NO incluyas personas reconocibles — planos de detalle, paisajes, arquitectura, objetos
- Estilo: fotoperiodismo documental, color natural, luz mediterránea
- Formato: UNA SOLA FRASE en inglés

RESPUESTA (solo el prompt visual, sin palabras del título ni del texto):
"""

    prompt_en = _llm(llm_prompt, max_tokens=180, temp=0.3)

    # Nettoyage
    prompt_en = prompt_en.strip().strip('"').strip("'")
    for banned in ["National Geographic", "Getty", "Reuters", "Magnum", "Leica"]:
        prompt_en = prompt_en.replace(banned, "documentary photography")

    # Anti-texte renforcé : interdire explicitement tout rendu textuel en fin de prompt
    prompt_en = prompt_en.rstrip(' .,')
    if prompt_en and not any(t in prompt_en.lower() for t in ["no text", "no letters", "without text"]):
        prompt_en = f"{prompt_en}, no text, no letters, no typography, no watermark"

    return prompt_en


# ═══════════════════════════════════════════════════════════
# GENERATION — Fonctions principales
# ═══════════════════════════════════════════════════════════

def generate_hero(article_es: str, title_es: str, slug: str) -> Optional[str]:
    """
    Génère l'image HERO 16:9 pour l'article.
    Retourne l'URL publique ou None.
    """
    log("🖼️ HERO — Génération image de couverture")

    prompt = build_hero_prompt(article_es, title_es)
    if not prompt or len(prompt) < 20:
        log("   ⚠️ Prompt hero vide, fallback générique")
        prompt = f"Documentary photograph of {title_es[:80]}, Costa Tropical Granada, natural Mediterranean light, professional composition, no text"

    hero_file = f"journal-{DATE}-hero-{sanitize_filename(slug[:30])}.webp"
    return _generate_fal(prompt, hero_file, width=1024, height=576)


def generate_section_images(article_es: str, slug: str) -> list[dict]:
    """
    Génère UNE image par section H2.
    Le prompt est basé sur le titre H2 ET le paragraphe.

    Retourne: [{"url": "...", "pos": "center 50%"}, ...]
    """
    log("🖼️ SECTIONS — Génération images par H2")

    # Parser les sections H2 avec leur contenu
    sections = _parse_sections(article_es)
    log(f"   {len(sections)} sections H2 détectées")

    gallery = []
    for i, sec in enumerate(sections):
        h2_title = sec["title"]
        section_content = sec["content"]

        log(f"   [{i+1}/{len(sections)}] {h2_title[:60]}...", newline=False)

        # Générer le prompt basé sur titre + paragraphe
        prompt = build_section_prompt(h2_title, section_content, i)
        if not prompt or len(prompt) < 15:
            prompt = f"Documentary photograph of {h2_title[:80]}, Costa Tropical, natural light, no text"

        # Générer l'image
        sf = f"journal-{DATE}-section-{i+1:02d}-{sanitize_filename(slug[:20])}.webp"
        url = _generate_fal(prompt, sf, width=1024, height=576)

        if url:
            gallery.append({"url": url, "pos": "center 50%"})

        time.sleep(3)  # Pause anti-stress entre chaque génération

    log(f"   Total: {len(gallery)}/{len(sections)} images section générées")
    return gallery


# ═══════════════════════════════════════════════════════════
# UTILITAIRES
# ═══════════════════════════════════════════════════════════

def _parse_sections(article: str) -> list[dict]:
    """
    Découpe l'article en sections H2 avec leur contenu.
    Retourne: [{"title": "...", "content": "..."}, ...]
    """
    sections = []
    current_title = None
    current_lines = []

    for line in article.split('\n'):
        if line.startswith('## '):
            if current_title:
                sections.append({
                    "title": current_title,
                    "content": '\n'.join(current_lines).strip()
                })
            current_title = line[3:].strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)

    # Dernière section
    if current_title:
        sections.append({
            "title": current_title,
            "content": '\n'.join(current_lines).strip()
        })

    return sections


def sanitize_filename(name: str) -> str:
    """Nettoie un nom pour usage comme nom de fichier."""
    return re.sub(r'[^a-z0-9_-]', '', name.lower().replace(' ', '-')[:50])


def inject_markers(article_text: str, gallery: list[dict]) -> str:
    """
    Injecte les marqueurs [[PHOTO:N]] dans l'article.
    Ordre: hero avant le premier H2, images section après chaque H2.
    Format IMPÉRATIF: [[PHOTO:N]] — la PWA (ArticleClient.tsx) ne rend QUE ce format,
    résolu via gallery_images. Le HTML <figure> brut N'EST PAS rendu en image.
    """
    lines = article_text.split('\n')
    result = []
    img_idx = 0
    used_hero = False

    for line in lines:
        stripped = line.strip()

        # Hero: avant le premier H2
        if not used_hero and stripped.startswith('## '):
            if img_idx < len(gallery):
                result.append(f'[[PHOTO:{img_idx}]]')
                result.append("")
                img_idx += 1
                used_hero = True

        result.append(line)

        # Image section: après chaque H2
        if stripped.startswith('## ') and used_hero:
            if img_idx < len(gallery):
                result.append("")
                result.append(f'[[PHOTO:{img_idx}]]')
                img_idx += 1

    return '\n'.join(result)
