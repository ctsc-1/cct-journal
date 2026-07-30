# Profil Hermes : `alejandro-journal`

## Mission
Agent IA autonome qui produit **un article de journalisme d'investigation de 10 000+ mots par jour** pour le Club Costa Tropical.
L'article est publié en 3 langues (ES/FR/EN) avec images professionnelles.

## Architecture du pipeline (9 phases)

| Phase | Composant | Modèle | Description |
|-------|-----------|--------|-------------|
| **Phase 0** | `phase0_evaluator.py` | Flash Lite | Sondage (3 candidats) → Scoring → Sélection (≥7/10) → Anti-doublon SQL 30j + vectoriel pgvector (cosine > 0.85) |
| **Phase 1** | `act1_es.py` (DeepSearch) | SearXNG + Flash Lite | 7 requêtes SearXNG, synthèse 1500-2500 mots, sleep 5s |
| **Phase 2** | `act1_es.py` (Planification) | Flash Lite | Plan éditorial : titre H1 + lead GEO + 10-15 sections H2 |
| **Phase 3** | `act1_es.py` (Génération H2×H2) | Gemini 3.6 Flash | Un appel par H2 (900-1200 mots), cache inter-H2 pour cohérence, sleep 5s |
| **Phase 4** | `act1_es.py` (FastCheck) | DeepSeek V4 + Gemini | Double vérification anti-hallucinations, score ≥ 8/10, max 3 retries |
| **Phase 5** | `act1_es.py` (Humanisation) | Flash Lite | Section par section, hash factuel (MD5) pour garantir zéro modification |
| **Phase 6** | `act2_fr.py` | Flash Lite | Traduction FR : titre → intro GEO → 11+ H2, sleep 5s |
| **Phase 7** | `act3_en.py` | Flash Lite | Traduction EN : title → GEO intro → 11+ H2 sections, sleep 5s |
| **Phase 8** | `photo_studio.py` | Flash Lite + FAL.ai | Hero pro (titre+lead) + 1 image/H2 (titre+paragraphe). PNG→WebP + upload GDrive |
| **Phase 9** | `run_pipeline.py` (DB) | PostgreSQL | INSERT articles avec featured_image_url + gallery_images + marqueurs [[PHOTO:N]] |

## Gestion des quotas

Tous les modèles sont bridés par la **Gateway LLM** (127.0.0.1:4000) :

| Modèle | Usage/Jour max | Coût |
|--------|----------------|------|
| Gemini 2.5 Flash Lite | **Illimité** (RPD) | 0€ |
| Gemini 3.6 Flash | 100 000 tokens | ~0.05€/article |
| Gemini Embedding 2 | Illimité (RPD) | ~0.00015€/article |
| DeepSeek V4 Flash | Via API externe | ~0.01€/article |

**Budget mensuel :** ~1.5€ pour 30 articles. Kill-switch Gateway à 24€ (jamais atteint).

## Règles éditoriales (SOUL.md)

- Ton Chaves Nogales : journalisme humaniste, précis, ironie fine
- **ZÉRO narration à la première personne** ("j'ai vu", "je me suis rendu")
- **ZÉRO personnages inventés** — chaque personne nommée doit exister
- **ZÉRO cliché touristique** — pas de "soleil", "paradis", "rêve", "magie"
- **GEO obligatoire** : données chiffrées dans les 200 premiers caractères
- **Titres ≤ 50 caractères**, directs, sans ponctuation interne
- **10-15 sections H2**, 900-1200 mots chacune
- **Cible : 10 000-16 000 mots par article**

## Fichiers clés

| Fichier | Lignes | Rôle |
|---------|--------|------|
| `src/run_pipeline.py` | 240 | Orchestrateur : Phase 0 → Actes 1-3 → Images → DB |
| `src/phase0_evaluator.py` | 423 | Sondage + scoring + anti-doublon double couche |
| `src/act1_es.py` | 496 | DeepSearch + planification + génération H2×H2 + FastCheck + Humanisation |
| `src/act2_fr.py` | 110 | Traduction FR (titre + intro + H2 par H2) |
| `src/act3_en.py` | 110 | Traduction EN (title + intro + H2 by H2) |
| `src/photo_studio.py` | 295 | Studio photo : hero pro + 1 image/H2 |
| `src/image_postprocess.py` | 165 | PNG→WebP + upload GDrive + suppression locale |
| `src/pipeline_cache.py` | 44 | Cache inter-actes (JSON, /tmp/cache/journal-cache/) |
| `scripts/backfill_embeddings.py` | 165 | Backfill pgvector embedding_gemini (768d) |

## Services systemd

| Service | Timer | Description |
|---------|-------|-------------|
| `cct-journal.service` | `cct-journal.timer` (07h05 Madrid) | Lance `run_pipeline.py` — article quotidien |
| `cct-journal-skill-b.service` | Inactif | Variante skill B (désactivée) |

## Mémoire vectorielle (anti-doublon)

- Colonne : `articles.embedding_gemini` (768 dimensions, Gemini Embedding 2)
- 147 articles backfillés (30/07/2026)
- Requête pgvector : `cosine similarity > 0.85` → doublon rejeté
- Script : `scripts/backfill_embeddings.py --dry-run` pour audit

## Workflow manuel

```bash
cd /srv/cct-journal && source .venv/bin/activate
python3 run_pipeline.py                          # Mode rotor automatique + Phase 0
python3 run_pipeline.py <category_id>            # Catégorie forcée
python3 run_pipeline.py --topic "Sujet"          # Sujet forcé (skip Phase 0)
```

## Dépendances

- **Gateway LLM** : 127.0.0.1:4000 (Gemini via AI Studio Pro)
- **SearXNG** : 127.0.0.1:8888 (DeepSearch)
- **FAL.ai** : 127.0.0.1:8700 (génération images, gratuit)
- **PostgreSQL** : alejandro_db, socket postgres@/
- **Valkey** : 127.0.0.1:6379 (quotas Gateway)
- **rclone** : gdrive:Journal-CCT/PNG/ (archivage PNG)
- **GDrive** : 4 To disponibles, ~490 Go utilisés

## Auteur

Profil créé par Marc Cochet pour le Club Costa Tropical.
Développé et maintenu par Zambra (agent Hermes VPS2).
Dernière mise à jour : 30 juillet 2026.
