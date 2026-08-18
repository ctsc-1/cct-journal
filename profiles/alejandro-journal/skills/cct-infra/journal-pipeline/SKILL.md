---
name: journal-pipeline
title: Pipeline Journal CCT
description: Use for Journal CCT articles, 9 phases, anti-boucle, cache before DB.
category: cct-infra
tags: [cct, article, pipeline, anti-boucle, validation, cache, images, geo, webp]
---

# 📰 Pipeline Journal CCT — alejandro-journal (profil)

## Architecture
Cache : /tmp/cache/journal-cache/ (fichiers JSON par étape)
Verrou : /tmp/pipeline-journal-cycle.lock ({step, attempt, timestamp})

## Phases

### Phase 1 — DeepSearch
SearXNG + Gemini 3.6 Flash, 3 iters, 60s. Fallback Gemini direct si SearXNG down.
Validation : contenu >= 500 chars ET chiffres OU sources.
Cache: step1_deepsearch.json. Max 2 tentatives.

### Phase 2 — Article ES
DeepSeek V4 Flash. Titre <60 chars. >=5000 chars. Zero markdown.
Cache: step2_article_es.json. Max 2 tentatives.

### Phase 3 — Traductions FR/EN
Gemini 3.6 Flash via Gateway. Titres distincts. Validation trilingue.
Cache: step3_traductions.json. Max 2 tentatives.

### Phase 4 — Images FAL
1 hero + 1 par section H2.
PIEGE: URLs FAL DOIVENT etre telechargees -> WebP (Q85) -> /srv/rag-engine/static/
URL DB = /api/static/... PAS l'URL FAL.
Cache: step4_images.json.

### Phase 5 — Galerie inline
Injecter URLs gallery apres chaque H2 (![alt](url)).

### Phase 6 — QC final
Score >= 70% (5000 chars, trilingue, hero+gallery, pas de markdown). Max 2.

### Phase 7 — Publication DB
INSERT dans articles avec category_id UUID. Validation SELECT.

### Phase 8 — Nettoyage
Purge cache, supression verrou.

## Anti-boucle
1. Max 2 tentatives par phase
2. Max 1 cycle complet
3. Fichier verrou : /tmp/pipeline-journal-cycle.lock
4. Pas de redemarrage apres echec definitif

## Pieges (29/07/2026)
- URLs FAL -> locales : PWA attend /api/static/... pas v3b.fal.media
- Cache avant DB : jamais d'insertion avant QC final
- Route PWA : articles servis sur /blog/[slug]
- category_id NOT NULL : recuperer UUID depuis categories
- images inline : injecter apres chaque H2
