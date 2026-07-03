"""
images.py — Studio photo du Journal CCT.

Génère des images via Gemini Nano Banana (gratuit, 50K RPD).
Remplace les marqueurs [[IMG:...]] par les <img> réelles.
Badge ✨ IA appliqué via ImageFactory.
WebP Q80 optimisé, original supprimé.

Deux modes :
1. Planifié : reçoit un plan narratif (prompts spécifiques)
2. Manuel : génère des prompts génériques (rattrapage)
"""
from __future__ import annotations
import base64
import logging
import os
import re
from typing import Dict, List, Optional, Tuple
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("cct-journal.images")

# ─── Chemins ─────────────────────────────────────────────────────────────────
RAG_ENGINE_PATH = "/srv/rag-engine"
if RAG_ENGINE_PATH not in sys.path:
    sys.path.insert(0, RAG_ENGINE_PATH)

JOURNAL_IMAGE_DIR = "/srv/rag-engine/static/DEPARTEMENT_ICONOGRAPHIE/JOURNAL"
os.makedirs(JOURNAL_IMAGE_DIR, exist_ok=True)

FLUX_API_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

HERO_TIMEOUT = 90
SECTION_TIMEOUT = 60
MIN_PROMPT_LENGTH = 20  # Un prompt valide a au moins 20 caractères


def _get_hf_token() -> Optional[str]:
    """Récupère le token HuggingFace."""
    token = os.getenv("HUGGINGFACE_API_KEY") or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not token:
        try:
            from dotenv import load_dotenv
            load_dotenv("/srv/rag-engine/.env")
            token = os.getenv("HUGGINGFACE_API_KEY") or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
        except Exception:
            pass
    return token


def _generate_image(prompt: str, timeout: int = 90, max_retries: int = 3) -> Optional[bytes]:
    """Appelle FLUX.1-schnell (HuggingFace), retourne les bytes de l'image."""
    hf_token = _get_hf_token()
    if not hf_token:
        logger.error("❌ HuggingFace token inaccessible")
        return None
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                FLUX_API_URL,
                headers={"Authorization": f"Bearer {hf_token}"},
                json={
                    "inputs": prompt[:1500],
                    "parameters": {"num_inference_steps": 8, "guidance_scale": 7.5},
                },
                timeout=timeout,
            )
            if resp.status_code != 200:
                logger.warning(f"⚠️ FLUX {resp.status_code} (tentative {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    continue
                return None
            img_bytes = resp.content
            logger.info(f"✅ FLUX image: {len(img_bytes)//1024}KB")
            return img_bytes
        except httpx.TimeoutException:
            if attempt < max_retries - 1:
                logger.warning(f"   ⚠️ Tentative {attempt+1}/{max_retries}: timeout, nouveau départ...")
                continue
            logger.error(f"❌ FLUX timeout après {max_retries} tentatives")
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"   ⚠️ Tentative {attempt+1}/{max_retries}: {e}")
                continue
            logger.error(f"❌ FLUX error: {e}")
            return None


def _apply_badge(filepath: str):
    """Ajoute le watermark IA à une image."""
    try:
        from services.image_factory import ImageFactory
        factory = ImageFactory()
        factory._add_ai_watermark(filepath)
        logger.info(f"✨ Badge IA: {os.path.basename(filepath)}")
    except Exception as e:
        logger.warning(f"⚠️ Badge IA: {e}")


def _optimize(original_bytes: bytes, output_path_no_ext: str,
              max_width: int = 1920) -> Optional[str]:
    """Redimensionne + convertit en WebP Q80 + badge IA. Supprime l'original."""
    try:
        from PIL import Image as PILImage
        from io import BytesIO

        img = PILImage.open(BytesIO(original_bytes))
        if img.width > max_width:
            ratio = max_width / float(img.width)
            new_h = int(float(img.height) * ratio)
            img = img.resize((max_width, new_h), PILImage.Resampling.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        webp_path = f"{output_path_no_ext}.webp"
        img.save(webp_path, "WEBP", quality=80, optimize=True)
        orig_kb = len(original_bytes) // 1024
        webp_kb = os.path.getsize(webp_path) // 1024
        logger.info(f"   Optimisé: {orig_kb}KB → {webp_kb}KB WebP ({webp_kb/orig_kb*100:.0f}%)")

        _apply_badge(webp_path)
        return webp_path
    except Exception as e:
        logger.error(f"❌ Optimisation: {e}")
        return None


def generate_article_images(
    text_with_markers: str,
    plan: List[Dict],
    slug: str,
) -> Tuple[str, str, str]:
    """Génère hero + section images depuis un plan narratif.

    Args:
        text_with_markers: Texte avec marqueurs [[IMG:...]] (sortie de narrative_planner)
        plan: Plan narratif [{"section", "prompt", "type", "marker"}, ...]
        slug: Slug unique pour les fichiers

    Returns:
        (hero_url, gallery_json, text_with_images)
        text_with_images = marqueurs [[IMG]] remplacés par les <img> réelles
    """
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
        img_bytes = _generate_image(prompt, timeout=timeout)
        if not img_bytes:
            logger.warning(f"   ⚠️ Échec génération: {section[:40]}")
            continue

        # Optimisation WebP
        suffix = "hero" if ptype == "hero" else f"section-{len(section_images) + 1}"
        output_base = os.path.join(JOURNAL_IMAGE_DIR, f"{slug}-{suffix}")
        webp_path = _optimize(img_bytes, output_base,
                              max_width=1920 if ptype == "hero" else 1200)
        if not webp_path:
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

    # Remplacer les marqueurs par les <img>
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
            logger.info(f"   📄 Marqueur {marker} → ![]() hero")
        elif ptype == "section" and marker_idx < len(section_images):
            img = section_images[marker_idx]
            img_tag = f'![{img["alt"]}]({img["url"]})'
            text_with_images = text_with_images.replace(marker, img_tag, 1)
            logger.info(f"   📄 Marqueur {marker} → ![]() section")
            marker_idx += 1

    gallery_json = json.dumps(section_images) if section_images else "[]"
    if not hero_url:
        logger.error("🔴 CRITICAL — Aucune image hero générée après toutes les tentatives")
    else:
        logger.info(f"   Hero: ✅ | Sections: {len(section_images)} | Texte: {'✅' if marker_idx > 0 else '⏳'}")
    return hero_url, gallery_json, text_with_images


# ─── Mode Manuel (rattrapage d'articles existants sans plan narratif) ──────

def _generate_generic_prompt(title: str, section: str = "", ptype: str = "section") -> str:
    """Génère un prompt générique quand le plan narratif n'est pas disponible."""
    if ptype == "hero":
        return (
            f"Fotografía de prensa para artículo '{title[:80]}' en la Costa Tropical. "
            f"Escena realista y luminosa, estilo documental National Geographic. "
            f"Luz mediterránea natural, composición profesional. Sin texto."
        )
    return (
        f"Fotografía documental para sección '{section[:60]}' del artículo '{title[:60]}' "
        f"en la Costa Tropical. Estilo National Geographic. Luz mediterránea. Sin texto."
    )


def generate_article_images_manual(
    article_text: str,
    title: str,
    slug: str,
    category_name: str = "Costa Tropical",
) -> Tuple[str, str, str]:
    """Mode manuel (rattrapage) — génère prompts génériques + images.

    Utilisé pour les articles existants qui n'ont pas de plan narratif.
    Retourne (hero_url, gallery_json, text_with_images).
    """
    # Extraire les sections H2
    sections = [l.replace("## ", "").strip()[:80]
                for l in article_text.split("\n")
                if l.startswith("## ") and not l.startswith("### ")]

    # Construire un plan générique
    plan = []
    # Hero
    plan.append({
        "section": "hero",
        "prompt": _generate_generic_prompt(title, ptype="hero"),
        "type": "hero",
        "marker": "[[IMG:hero]]",
    })
    # Sections
    text_with_markers = article_text
    for i, sec in enumerate(sections):
        marker = f"[[IMG:section-{i+1}]]"
        plan.append({
            "section": sec,
            "prompt": _generate_generic_prompt(title, sec, "section"),
            "type": "section",
            "marker": marker,
        })
        # Insérer les marqueurs
        pattern = re.compile(
            r'(^|\n)(##\s*' + re.escape(sec) + r'\s*\n\n)',
            re.IGNORECASE | re.MULTILINE
        )
        text_with_markers = pattern.sub(r'\1\2' + marker + r'\n\n', text_with_markers, count=1)

    # Marqueur hero après le titre
    h1 = re.search(r'^#\s+(.+)$', text_with_markers, re.MULTILINE)
    if h1:
        text_with_markers = text_with_markers.replace(
            h1.group(0) + "\n\n", h1.group(0) + "\n\n[[IMG:hero]]\n\n", 1
        )

    return generate_article_images(text_with_markers, plan, slug)
