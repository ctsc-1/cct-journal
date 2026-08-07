#!/usr/bin/env python3
"""
regen_section_images.py — Régénère les images de section du Journal
avec le prompt anti-texte corrigé (photo_studio.MODEL_LIGHT=deepseek-v4-flash).

Supprime les images défectueuses (texte superposé reproduit par le bug
deepseek-v4-pro/reasoning_content) et rappelle generate_section_images,
qui ne re-génère que les fichiers absents (le skip "Déjà existant" garde
les bonnes images). Les noms de fichiers restent identiques => la galerie
DB et les marqueurs [[PHOTO:N]] restent valides.

Usage: python3 regen_section_images.py <slug> [num,...]
  - slug : slug de l'article (pour les noms de fichiers)
  - num : liste optionnelle de numéros de section à régénérer (1-based)
         ; par défaut toutes.
"""
import os, sys, re
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    slug = sys.argv[1]
    targets = None
    if len(sys.argv) >= 3 and sys.argv[2]:
        targets = {int(x) for x in re.findall(r"\d+", sys.argv[2])}

    from photo_studio import generate_section_images, OUTPUT_DIR, _parse_sections, sanitize_filename, DATE

    # Déterminer l'article ES depuis le cache (pour les sections)
    from pipeline_cache import load_step
    es_data = load_step("act1_es_validated")
    if not es_data:
        print("❌ act1_es_validated introuvable"); sys.exit(1)
    article_es = es_data["article_es"]

    # Supprimer les fichiers des sections ciblées
    sf_base = sanitize_filename(slug[:20])
    removed = []
    for i in range(1, 16):
        if targets and i not in targets:
            continue
        f = OUTPUT_DIR / f"journal-{DATE}-section-{i:02d}-{sf_base}.webp"
        if f.exists():
            f.unlink()
            removed.append(i)
    print(f"🗑️  Files supprimés pour régénération: sections {removed}")

    # Régénérer (ne re-génère que les fichiers absents)
    gallery = generate_section_images(article_es, slug)
    print(f"✅ {len(gallery)}/15 images section en galerie (hero non inclus)")

if __name__ == "__main__":
    main()
