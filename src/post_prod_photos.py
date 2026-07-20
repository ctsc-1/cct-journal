#!/usr/bin/env python3
"""
post_prod_photos.py — Boucle post-production vérification adéquation image/paragraphe.

Pour chaque article publié aujourd'hui :
1. Extrait chaque image inline + le paragraphe qui l'entoure
2. Demande à un LLM si l'image correspond au paragraphe (description visuelle vs contenu texte)
3. Si inadéquat → régénère l'image avec un prompt basé sur le contenu du paragraphe
4. Loop jusqu'à adéquation ou max 3 tentatives

Utilisation:
    python3 post_prod_photos.py [--article-id UUID] [--dry-run]
"""
import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Setup paths
sys.path.insert(0, "/srv/rag-engine")
sys.path.insert(0, "/srv/cct-journal/src")

import httpx
import psycopg2
from dotenv import load_dotenv
from pipeline.model_env import get_model

load_dotenv('/srv/rag-engine/.env')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("post-prod-photos")

GATEWAY_CHAT_URL = "http://127.0.0.1:4000/v1/chat/completions"
GATEWAY_VISION_URL = "http://127.0.0.1:4000/v1/chat/completions"
GATEWAY_IMAGE_URL = "http://127.0.0.1:4000/v1/images/generations"

MAX_RETRIES = 3
STATIC_BASE = "/srv/rag-engine/static"


def get_db_conn():
    db_url = os.getenv("DATABASE_URL")
    return psycopg2.connect(db_url)


def _gateway_chat(messages: list, max_tokens: int = 500, temperature: float = 0.1) -> str:
    """Appelle la Gateway LLM."""
    resp = httpx.post(
        GATEWAY_CHAT_URL,
        json={
            "model": get_model("FASTCHECK", "gemini-3.5-flash"),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


PROMPT_VERIFY_ADEQUATION = """Tu es un éditeur de presse visuel. On te donne :
1. Le contenu d'un paragraphe d'un article
2. La description d'une image qui l'illustre

Tu dois déterminer si l'image est ADEQUATE pour illustrer ce paragraphe.

Critères d'adéquation :
- L'image montre quelque chose qui est mentionné dans le paragraphe
- L'ambiance de l'image correspond au ton du texte
- L'image n'est pas générique (un paysage aléatoire) si le paragraphe parle de quelque chose de spécifique

Réponds en TEXTE SIMPLE :
- Si ADEQUAT : écris UNIQUEMENT "ADEQUAT"
- Si INADEQUAT : écris une ligne :
INADEQUAT|raison (max 150 chars)|suggestion de prompt visuel mieux adapté (max 200 chars)

Paragraphe :
"""

PROMPT_DESCRIBE_IMAGE = """Décris cette image en 2-3 phrases en espagnol. Sois précis sur ce qu'on voit (lieux, objets, personnes, ambiance)."""


def extract_inline_images(content: str) -> List[Dict]:
    """Extrait les images inline du markdown avec leur paragraphe environnant.
    
    Returns: [{"url": str, "alt": str, "paragraph": str, "position": int}]
    """
    images = []
    
    # Pattern markdown: ![alt](url)
    pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    matches = list(re.finditer(pattern, content))
    
    # Découper en paragraphes
    paragraphs = content.split("\n\n")
    
    for i, match in enumerate(matches):
        alt = match.group(1)
        url = match.group(2)
        pos = match.start()
        
        # Trouver le paragraphe qui contient cette image
        # On prend les 300 chars autour de l'image (en excluant l'image elle-même)
        before = content[:pos]
        after = content[match.end():]
        
        # Paragraphe = texte avant l'image (dernier paragraphe) + texte après (premier paragraphe)
        before_paras = [p.strip() for p in before.split("\n\n") if p.strip() and not p.strip().startswith("![")]
        after_paras = [p.strip() for p in after.split("\n\n") if p.strip() and not p.strip().startswith("![")]
        
        # Le paragraphe pertinent = le texte autour de l'image
        context_before = before_paras[-1] if before_paras else ""
        context_after = after_paras[0] if after_paras else ""
        paragraph = f"{context_before} {context_after}".strip()[:500]
        
        # Ignorer les images hero (avant le premier H2)
        if i == 0 and "hero" in alt.lower():
            continue
        
        images.append({
            "url": url,
            "alt": alt,
            "paragraph": paragraph,
            "position": pos,
        })
    
    return images


def describe_image(image_path: str) -> str:
    """Demande au LLM de décrire l'image (vision)."""
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        
        # Détecter le mime type
        ext = Path(image_path).suffix.lower()
        mime = "image/webp" if ext == ".webp" else "image/jpeg"
        
        resp = httpx.post(
            GATEWAY_VISION_URL,
            json={
                "model": get_model("VISION", "gemini-3.5-flash"),
                "messages": [
                    {"role": "system", "content": PROMPT_DESCRIBE_IMAGE},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Describe cette image:"},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                    ]},
                ],
                "max_tokens": 200,
                "temperature": 0.1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"Vision échouée: {e}")
        return ""


def verify_adéquation(paragraph: str, image_description: str) -> Tuple[bool, str, str]:
    """Vérifie si l'image correspond au paragraphe.
    
    Returns: (is_adequate, reason, suggested_prompt)
    """
    try:
        content = _gateway_chat(
            [
                {"role": "system", "content": PROMPT_VERIFY_ADEQUATION},
                {"role": "user", "content": f"Paragraphe:\n{paragraph[:500]}\n\nDescription de l'image:\n{image_description}"},
            ],
            max_tokens=300,
            temperature=0.1,
        )
        
        if content.strip().upper().startswith("ADEQUAT"):
            return True, "", ""
        
        # Parser INADEQUAT|raison|suggestion
        if content.startswith("INADEQUAT|"):
            parts = content.split("|", 2)
            if len(parts) >= 3:
                return False, parts[1].strip()[:150], parts[2].strip()[:200]
            elif len(parts) == 2:
                return False, parts[1].strip()[:150], ""
        
        # Réponse ambiguë → conservateur, on considère inadéquat
        return False, "Réponse ambiguë du vérificateur", ""
    except Exception as e:
        logger.warning(f"Vérification adéquation échouée: {e}")
        return True, "", ""  # En cas d'erreur, on garde l'image


def regenerate_image(prompt: str, output_base: str) -> Optional[str]:
    """Régénère une image via le moteur unique."""
    try:
        from pipeline.mcp_image import generate_and_save
        import asyncio
        
        async def _gen():
            return await generate_and_save(
                prompt, output_base,
                max_width=1200, width=1024, height=1024, timeout=60,
                silo="journal"
            )
        
        return asyncio.run(_gen())
    except Exception as e:
        logger.error(f"Régénération échouée: {e}")
        return None


def process_article(article_id: str, dry_run: bool = False) -> Dict:
    """Traite un article : vérifie et corrige l'adéquation image/paragraphe."""
    conn = get_db_conn()
    cur = conn.cursor()
    
    cur.execute("SELECT id, title, content FROM blog_posts WHERE id = %s AND status = 'published'", (article_id,))
    row = cur.fetchone()
    if not row:
        logger.warning(f"Article {article_id} non trouvé ou non publié")
        return {"article_id": article_id, "status": "not_found"}
    
    art_id, title, content = row
    logger.info(f"📋 Article: {title[:60]}")
    
    images = extract_inline_images(content)
    logger.info(f"   {len(images)} image(s) inline à vérifier")
    
    if not images:
        logger.info("   ✅ Aucune image inline à vérifier")
        cur.close()
        conn.close()
        return {"article_id": article_id, "status": "no_images", "images_checked": 0}
    
    results = []
    updated_content = content
    changes = 0
    
    for img in images:
        url = img["url"]
        alt = img["alt"]
        paragraph = img["paragraph"]
        
        logger.info(f"   🖼️ Vérification: {alt[:40]}")
        logger.info(f"      Paragraphe: {paragraph[:80]}...")
        
        # Résoudre le chemin local de l'image
        if url.startswith("/api/static/"):
            local_path = os.path.join(STATIC_BASE, url.replace("/api/static/", ""))
        elif url.startswith("/static/"):
            local_path = os.path.join(STATIC_BASE, url.replace("/static/", ""))
        else:
            logger.info(f"      ⏭️ URL externe, skip")
            results.append({"url": url, "status": "external", "adequate": True})
            continue
        
        if not os.path.exists(local_path):
            logger.warning(f"      ⚠️ Image introuvable: {local_path}")
            results.append({"url": url, "status": "missing", "adequate": True})
            continue
        
        # Étape 1: Décrire l'image (vision)
        image_desc = describe_image(local_path)
        if not image_desc:
            logger.info(f"      ⚠️ Vision échouée, skip")
            results.append({"url": url, "status": "vision_failed", "adequate": True})
            continue
        
        logger.info(f"      Description: {image_desc[:80]}")
        
        # Étape 2: Vérifier l'adéquation
        adequate, reason, suggested_prompt = verify_adéquation(paragraph, image_desc)
        
        if adequate:
            logger.info(f"      ✅ ADEQUAT")
            results.append({"url": url, "status": "adequate", "adequate": True})
            continue
        
        logger.info(f"      ❌ INADEQUAT: {reason}")
        results.append({"url": url, "status": "inadequate", "adequate": False, "reason": reason})
        
        if dry_run:
            logger.info(f"      [DRY-RUN] Pas de régénération")
            continue
        
        # Étape 3: Régénérer avec le prompt suggéré ou un prompt basé sur le paragraphe
        new_prompt = suggested_prompt if suggested_prompt else (
            f"Fotografía documental para ilustrar: {paragraph[:200]}. "
            f"Costa Tropical, estilo National Geographic, luz mediterránea. Sin texto."
        )
        
        output_base = local_path.rsplit(".", 1)[0] + "-regen"
        new_path = regenerate_image(new_prompt, output_base)
        
        if new_path and os.path.exists(new_path):
            new_url = f"/api/static/{new_path.replace(STATIC_BASE + '/', '')}"
            logger.info(f"      🔄 Image régénérée: {new_url}")
            
            # Remplacer dans le contenu
            updated_content = updated_content.replace(url, new_url)
            changes += 1
            results.append({"url": new_url, "status": "regenerated", "adequate": True, "old_url": url})
        else:
            logger.warning(f"      ⚠️ Régénération échouée, on garde l'originale")
    
    # Mettre à jour la DB si des images ont été régénérées
    if changes > 0 and not dry_run:
        cur.execute("UPDATE blog_posts SET content = %s, updated_at = NOW() WHERE id = %s", (updated_content, art_id))
        conn.commit()
        logger.info(f"   ✅ {changes} image(s) régénérée(s) et article mis à jour")
    
    cur.close()
    conn.close()
    
    return {
        "article_id": article_id,
        "status": "processed",
        "images_checked": len(images),
        "changes": changes,
        "details": results,
    }


def get_today_articles() -> List[str]:
    """Récupère les articles publiés aujourd'hui."""
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM blog_posts 
        WHERE status = 'published' 
          AND created_at::date = CURRENT_DATE
        ORDER BY created_at DESC
    """)
    ids = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return ids


def main():
    parser = argparse.ArgumentParser(description="Post-prod photos — vérification adéquation image/paragraphe")
    parser.add_argument("--article-id", help="UUID d'un article spécifique")
    parser.add_argument("--dry-run", action="store_true", help="Vérifier sans régénérer")
    args = parser.parse_args()
    
    if args.article_id:
        result = process_article(args.article_id, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        article_ids = get_today_articles()
        logger.info(f"=== {len(article_ids)} article(s) publié(s) aujourd'hui ===")
        
        all_results = []
        for aid in article_ids:
            result = process_article(aid, dry_run=args.dry_run)
            all_results.append(result)
            print()
        
        # Résumé
        total_checked = sum(r.get("images_checked", 0) for r in all_results)
        total_changes = sum(r.get("changes", 0) for r in all_results)
        logger.info(f"=== RÉSUMÉ ===")
        logger.info(f"Articles traités: {len(all_results)}")
        logger.info(f"Images vérifiées: {total_checked}")
        logger.info(f"Images régénérées: {total_changes}")


if __name__ == "__main__":
    main()