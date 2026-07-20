#!/usr/bin/env python3
"""
images.py — Moteur d'images du Journal CCT.
Délègue tout à pipeline.mcp_image (FAL.ai → Replicate fallback).
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, "/srv/rag-engine")
from pipeline.mcp_image import generate_hero, generate_square, generate_and_save  # noqa: E402

logger = logging.getLogger("cct-journal.images")

JOURNAL_IMAGE_DIR = "/srv/rag-engine/static/DEPARTEMENT_ICONOGRAPHIE/JOURNAL"
os.makedirs(JOURNAL_IMAGE_DIR, exist_ok=True)

HERO_TIMEOUT = 120
SECTION_TIMEOUT = 120
MIN_PROMPT_LENGTH = 20


def _save_webp(img_bytes: bytes, output_base: str) -> None:
    path = output_base + ".webp"
    with open(path, "wb") as f:
        f.write(img_bytes)


async def _generate_and_save_one(prompt: str, output_base: str, ptype: str,
                                  timeout: int = 120) -> Optional[str]:
    """Génère via mcp_image (FAL→Replicate) et sauvegarde en WebP."""
    slug_part = os.path.basename(output_base)

    gen = generate_hero if ptype == "hero" else generate_square
    img_bytes = await gen(prompt=prompt, timeout=timeout)

    if img_bytes and len(img_bytes) > 100:
        _save_webp(img_bytes, output_base)
        logger.info(f"   ✅ {ptype}: {len(img_bytes)//1024}KB")
        return output_base + ".webp"

    logger.error(f"   ❌ Échec pour {ptype}")
    return None


async def generate_article_images(
    text_with_markers: str,
    plan: List[Dict],
    slug: str,
) -> Tuple[str, str, str]:
    """Génère hero + section images depuis un plan narratif."""
    logger.info(f"📸 Studio photo: {len(plan)} image(s) planifiée(s)")
    hero_url = ""
    section_images = []

    for item in plan:
        ptype = item.get("type", "section")
        prompt = item.get("prompt", "")
        marker = item.get("marker", "")
        section = item.get("section", "")

        if not prompt or len(prompt) < MIN_PROMPT_LENGTH:
            logger.warning(f"   ⚠️ Prompt trop court pour {marker or section}")
            continue

        timeout = HERO_TIMEOUT if ptype == "hero" else SECTION_TIMEOUT
        suffix = "hero" if ptype == "hero" else f"section-{len(section_images) + 1}"
        output_base = os.path.join(JOURNAL_IMAGE_DIR, f"{slug}-{suffix}")

        webp_path = await _generate_and_save_one(prompt, output_base, ptype, timeout)
        if not webp_path:
            logger.warning(f"   ⚠️ Échec génération: {section[:40]}")
            continue

        url = f"/api/static/DEPARTEMENT_ICONOGRAPHIE/JOURNAL/{os.path.basename(webp_path)}"
        kb = os.path.getsize(webp_path) // 1024

        if ptype == "hero":
            hero_url = url
            logger.info(f"   🖼️ Hero: {os.path.basename(webp_path)} ({kb}KB)")
        else:
            alt = section or f"Section {len(section_images) + 1}"
            section_images.append({
                "url": url, "alt": alt, "section": section,
                "type": "section", "kb": kb
            })
            logger.info(f"   🖼️ Section: {os.path.basename(webp_path)} ({kb}KB)")

    text_with_images = text_with_markers
    marker_idx = 0

    for item in plan:
        marker = item.get("marker", "")
        if not marker or marker not in text_with_images:
            continue

        ptype = item.get("type", "section")
        if ptype == "hero" and hero_url:
            img_tag = f'![{item.get("section", slug)}]({hero_url})'
            text_with_images = text_with_images.replace(marker, img_tag, 1)
        elif ptype == "section" and marker_idx < len(section_images):
            img = section_images[marker_idx]
            img_tag = f'![{img["alt"]}]({img["url"]})'
            text_with_images = text_with_images.replace(marker, img_tag, 1)
            marker_idx += 1

    gallery_json = json.dumps(section_images) if section_images else "[]"
    if not hero_url:
        logger.error("🔴 CRITICAL — Aucune image hero générée")
    else:
        logger.info(f"   Hero: ✅ | Sections: {len(section_images)} | Texte: {'✅' if marker_idx > 0 else '⏳'}")
    return hero_url, gallery_json, text_with_images


def _generate_generic_prompt(title: str, section: str = "", section_content: str = "", ptype: str = "section") -> str:
    if ptype == "hero":
        return (
            f"Fotografía de prensa para artículo '{title[:80]}' en la Costa Tropical. "
            f"Escena realista y luminosa, estilo documental National Geographic. "
            f"Luz mediterránea natural, composición profesional. Sin texto."
        )
    content_hint = f" La sección habla de: {section_content[:200]}." if section_content else ""
    return (
        f"Fotografía documental para ilustrar el párrafo sobre '{section[:60]}' del artículo '{title[:60]}' "
        f"en la Costa Tropical.{content_hint} Estilo National Geographic. Luz mediterránea. Sin texto."
    )


async def generate_article_images_manual(
    article_text: str, title: str, slug: str, category_name: str = "Costa Tropical",
) -> Tuple[str, str, str]:
    sections = []
    section_contents = {}
    current_section = None
    current_content = []

    for l in article_text.split("\n"):
        stripped = l.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            if current_section:
                section_contents[current_section] = " ".join(current_content)[:300]
            sec = stripped.replace("## ", "").strip()[:80]
            sections.append(sec)
            current_section = sec
            current_content = []
        elif stripped.startswith("### "):
            if current_section:
                section_contents[current_section] = " ".join(current_content)[:300]
            sec = stripped.replace("### ", "").strip()[:80]
            sections.append(sec)
            current_section = sec
            current_content = []
        elif current_section and stripped and not stripped.startswith("#"):
            current_content.append(stripped)
    if current_section:
        section_contents[current_section] = " ".join(current_content)[:300]

    plan = [{
        "section": "hero", "prompt": _generate_generic_prompt(title, ptype="hero"),
        "type": "hero", "marker": "[[IMG:hero]]",
    }]

    text_with_markers = article_text
    for i, sec in enumerate(sections):
        marker = f"[[IMG:section-{i+1}]]"
        plan.append({
            "section": sec, "prompt": _generate_generic_prompt(title, sec, section_contents.get(sec, ""), "section"),
            "type": "section", "marker": marker,
        })
        pat = re.compile(r'(^|\n)(##\s*' + re.escape(sec) + r'\s*\n\n)', re.IGNORECASE | re.MULTILINE)
        text_with_markers = pat.sub(r'\1\2' + marker + r'\n\n', text_with_markers, count=1)

    h1 = re.search(r'^#\s+(.+)$', text_with_markers, re.MULTILINE)
    if h1:
        text_with_markers = text_with_markers.replace(h1.group(0) + "\n\n", h1.group(0) + "\n\n[[IMG:hero]]\n\n", 1)

    return await generate_article_images(text_with_markers, plan, slug)


def generate_article_images_sync(text_with_markers, plan, slug):
    return asyncio.run(generate_article_images(text_with_markers, plan, slug))


def generate_article_images_manual_sync(article_text, title, slug, category_name="Costa Tropical"):
    return asyncio.run(generate_article_images_manual(article_text, title, slug, category_name))
