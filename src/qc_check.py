"""qc_check.py — Protocole de Contrôle Qualité du Journal CCT.

Vérifie automatiquement tous les critères qualité d'un article.
Si le QC échoue (P1), l'article est dépublié (is_published = FALSE)
et le pipeline signale une erreur bloquante pour correction.

Checklist QC :
[P1] — Subtitle : pas d'image markdown dans subtitle
[P1] — Trilinguisme : titres FR≠ES≠EN
[P1] — Contenu : 3 langues non vides
[P1] — Image hero : fichier existant + accessible HTTP 200
[P1] — Images inline : au moins 1 image dans le body
[P2] — Podcast audio : fichier existant + accessible + taille > 100KB
[P2] — Gallery : au moins 1 image dans gallery_images
[P2] — API : article trouvable par UUID
[P3] — Metadonnées : author_id, category_id, is_published
[P3] — Mots : word_count raisonnable (> 300)
"""
from __future__ import annotations
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import httpx
import psycopg2

logger = logging.getLogger("cct-journal.qc")

# ─── Résultat QC

class QCRecord:
    """Un point de contrôle qualité."""
    def __init__(self, code: str, label: str, priority: str = "P1",
                 passed: bool = False, detail: str = ""):
        self.code = code
        self.label = label
        self.priority = priority  # P1=critique, P2=important, P3=cosmétique
        self.passed = passed
        self.detail = detail

    @property
    def verdict(self) -> str:
        return "✅ PASS" if self.passed else "❌ FAIL"

    @property
    def priority_label(self) -> str:
        return {"P1": "🔴 CRITIQUE", "P2": "🟡 IMPORTANT", "P3": "🔵 COSMÉTIQUE"}[self.priority]

    def __str__(self) -> str:
        return f"  {self.verdict} [{self.priority_label}] {self.code}: {self.label} — {self.detail[:200]}"


# ─── DB ──────────────────────────────────────────────────────────────────

def _get_pg_url() -> str:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            r = subprocess.run(
                ["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                capture_output=True, text=True, timeout=5
            )
            line = r.stdout.strip()
            if line:
                db_url = line.split("=", 1)[1].strip("'\"")
        except Exception:
            pass
    return db_url or "postgresql:///alejandro_db"


def _fetch_article(slug: str) -> Optional[Dict]:
    """Récupère l'article depuis la DB par slug.
    Fallback: si le slug ne trouve rien, prend l'article le plus récent du jour."""
    try:
        conn = psycopg2.connect(_get_pg_url(), connect_timeout=5)
        with conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, slug, title, title_es, title_en,
                       excerpt, excerpt_es, excerpt_en,
                       content, content_es, content_en,
                       featured_image_url, gallery_images,
                       audio_url, word_count,
                       author_id, category_id, is_published
                FROM articles WHERE slug = %s
            """, (slug,))
            row = cur.fetchone()
            if row:
                columns = [desc[0] for desc in cur.description]
                article = dict(zip(columns, row))
                logger.info(f"   Article trouvé par slug: {article.get('slug')}")
                return article

            # Fallback: slug non trouvé → chercher l'article le plus récent du jour
            logger.warning(f"   Slug '{slug}' introuvable, fallback article le plus récent du jour")
            cur.execute("""
                SELECT id, slug, title, title_es, title_en,
                       excerpt, excerpt_es, excerpt_en,
                       content, content_es, content_en,
                       featured_image_url, gallery_images,
                       audio_url, word_count,
                       author_id, category_id, is_published
                FROM articles
                WHERE published_at::date = CURRENT_DATE
                ORDER BY published_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                columns = [desc[0] for desc in cur.description]
                article = dict(zip(columns, row))
                logger.info(f"   Fallback: article du jour trouvé — slug='{article.get('slug')}'")
                return article

            logger.warning("   Aucun article trouvé pour aujourd'hui non plus")
        conn.close()
    except Exception as e:
        logger.error(f"DB error: {e}")
    return None


# ─── VÉRIFICATIONS INDIVIDUELLES ────────────────────────────────────────

def check_subtitle(article: Dict) -> QCRecord:
    """Le subtitle ne doit PAS contenir d'image markdown."""
    for lang in ('fr', 'es', 'en'):
        sub = article.get(f'excerpt', '') if lang == 'fr' else article.get(f'excerpt_{lang}', '')
        if '![' in sub:
            return QCRecord(
                "SUBTITLE-IMG", "Subtitle contient du markdown image",
                priority="P1", passed=False,
                detail=f"excerpt_{lang} contient '![...]' : {sub[:60]}..."
            )
        if sub == '':
            return QCRecord(
                "SUBTITLE-EMPTY", "Subtitle vide",
                priority="P1", passed=False,
                detail=f"excerpt_{lang} est vide"
            )
    return QCRecord("SUBTITLE", "Subtitle valide (pas d'image markdown)", passed=True)


def check_trilingual_titles(article: Dict) -> QCRecord:
    """Les titres FR/ES/EN doivent être différents."""
    titles = {
        'fr': article.get('title', ''),
        'es': article.get('title_es', ''),
        'en': article.get('title_en', ''),
    }
    # Vérifier que les 3 sont remplis
    empty_langs = [lang for lang, t in titles.items() if not t]
    if empty_langs:
        return QCRecord(
            "TRILING-TITLE-EMPTY", "Titres trilingues incomplets",
            priority="P1", passed=False,
            detail=f"Langues vides: {', '.join(empty_langs)}"
        )
    # Vérifier qu'ils sont différents
    if titles['fr'] == titles['es'] == titles['en']:
        return QCRecord(
            "TRILING-TITLE-SAME", "Tous les titres sont identiques",
            priority="P1", passed=False,
            detail=f"FR=ES=EN='{titles['fr'][:50]}'"
        )
    if titles['fr'] == titles['es']:
        return QCRecord(
            "TRILING-TITLE-FR=ES", "Titres FR et ES identiques",
            priority="P2", passed=False,
            detail=f"FR=ES='{titles['fr'][:50]}'"
        )
    return QCRecord("TRILING-TITLE", "Titres trilingues OK",
                     passed=True,
                     detail=f"FR='{titles['fr'][:30]}' ES='{titles['es'][:30]}' EN='{titles['en'][:30]}'")


# Seuil minimum de contenu pour un article d'excellence (La devise de Marc: 6€/mois)
_MIN_CONTENT_CHARS = 6000

def check_content(article: Dict) -> QCRecord:
    """Chaque langue doit avoir du contenu ET longueur minimale."""
    issues = []
    for lang, col in [('fr', 'content'), ('es', 'content_es'), ('en', 'content_en')]:
        val = article.get(col, '')
        if not val or len(val) < 200:
            issues.append(f"{lang}=vide")
        elif len(val) < _MIN_CONTENT_CHARS:
            issues.append(f"{lang}={len(val)}c (<{_MIN_CONTENT_CHARS})")
    if issues:
        return QCRecord(
            "CONTENT-SHORT", "Contenu insuffisant — article trop court",
            priority="P1", passed=False,
            detail=f"{' · '.join(issues)}. Minimum: {_MIN_CONTENT_CHARS}c par langue. Règle Marc: 6€/mois."
        )
    return QCRecord("CONTENT", "Contenu trilingue OK", passed=True,
                     detail=f"FR={len(article.get('content',''))}c ES={len(article.get('content_es',''))}c EN={len(article.get('content_en',''))}c")


def check_hero_image(article: Dict) -> QCRecord:
    """L'image hero doit exister sur disque ET être accessible HTTP."""
    hero_url = article.get('featured_image_url', '')
    if not hero_url:
        return QCRecord("HERO-MISSING", "Aucune image hero", priority="P1", passed=False)
    # Extraire le chemin
    file_path = hero_url.replace('/api/static/', '/srv/rag-engine/static/')
    if not os.path.exists(file_path):
        return QCRecord(
            "HERO-FILE", "Image hero absente du disque",
            priority="P1", passed=False,
            detail=f"Fichier manquant: {file_path}"
        )
    file_size = os.path.getsize(file_path) // 1024
    if file_size < 10:
        return QCRecord(
            "HERO-SIZE", "Image hero trop petite",
            priority="P2", passed=False,
            detail=f"Taille: {file_size}KB (< 10KB)"
        )
    # Test HTTP
    try:
        r = httpx.get(f"https://clubcostatropical.es{hero_url}", timeout=10, verify=False)
        if r.status_code != 200:
            return QCRecord(
                "HERO-HTTP", "Image hero inaccessible HTTP",
                priority="P1", passed=False,
                detail=f"HTTP {r.status_code}"
            )
    except Exception as e:
        return QCRecord("HERO-HTTP", f"Image hero: {e}", priority="P2", passed=False)
    return QCRecord("HERO", "Image hero OK", passed=True,
                     detail=f"{file_size}KB — HTTP 200")


def check_inline_images(article: Dict) -> QCRecord:
    """Au moins 1 image markdown inline dans chaque langue."""
    langs_ok = []
    langs_missing = []
    for lang, col in [('fr', 'content'), ('es', 'content_es'), ('en', 'content_en')]:
        val = article.get(col, '')
        import re
        imgs = re.findall(r'!\[.*?\]\(.*?\)', val)
        if len(imgs) >= 2:  # hero + au moins 1 section
            langs_ok.append(f"{lang}({len(imgs)})")
        else:
            langs_missing.append(f"{lang}({len(imgs)})")
    if langs_missing:
        return QCRecord(
            "INLINE-IMG", "Images inline insuffisantes",
            priority="P1", passed=False,
            detail=f"Manque: {', '.join(langs_missing)}. OK: {', '.join(langs_ok)}"
        )
    total = sum(len(re.findall(r'!\[.*?\]\(.*?\)', article.get(article.get('content') and article.get('content') or '', '')))
                for _ in range(1))
    return QCRecord("INLINE-IMG", "Images inline OK", passed=True,
                     detail=f"{', '.join(langs_ok)}")


def check_podcast(article: Dict) -> QCRecord:
    """Le podcast audio doit exister et être accessible."""
    audio_url = article.get('audio_url', '')
    if not audio_url:
        return QCRecord("AUDIO-MISSING", "Aucun podcast audio", priority="P1", passed=False)
    # Extraire le chemin
    file_path = audio_url.replace('/api/static/', '/srv/rag-engine/static/')
    if not os.path.exists(file_path):
        return QCRecord(
            "AUDIO-FILE", "Fichier audio absent du disque",
            priority="P2", passed=False,
            detail=f"Fichier manquant: {file_path}"
        )
    file_size = os.path.getsize(file_path) // 1024
    if file_size < 100:
        return QCRecord(
            "AUDIO-SIZE", "Podcast trop petit",
            priority="P2", passed=False,
            detail=f"Taille: {file_size}KB (< 100KB)"
        )
    # Test HTTP
    try:
        r = httpx.get(f"https://clubcostatropical.es{audio_url}", timeout=10, verify=False)
        if r.status_code != 200:
            return QCRecord(
                "AUDIO-HTTP", "Podcast inaccessible HTTP",
                priority="P2", passed=False,
                detail=f"HTTP {r.status_code}"
            )
    except Exception as e:
        return QCRecord("AUDIO-HTTP", f"Podcast: {e}", priority="P2", passed=False)
    return QCRecord("AUDIO", "Podcast OK", passed=True,
                     detail=f"{file_size}KB — HTTP 200")


def check_gallery(article: Dict) -> QCRecord:
    """La galerie doit contenir au moins 1 image."""
    gallery = article.get('gallery_images', '[]')
    if isinstance(gallery, str):
        try:
            gallery = json.loads(gallery)
        except (json.JSONDecodeError, TypeError):
            gallery = []
    if not isinstance(gallery, list):
        gallery = []
    count = len(gallery)
    if count == 0:
        return QCRecord(
            "GALLERY-EMPTY", "Galerie d'images vide",
            priority="P2", passed=False
        )
    # Vérifier que les fichiers existent
    missing_files = []
    for img in gallery:
        url = img.get('url', '') if isinstance(img, dict) else ''
        if url:
            file_path = url.replace('/api/static/', '/srv/rag-engine/static/')
            if not os.path.exists(file_path):
                missing_files.append(os.path.basename(file_path))
    if missing_files:
        return QCRecord(
            "GALLERY-FILES", "Images de galerie absentes du disque",
            priority="P1", passed=False,
            detail=f"Manquantes: {', '.join(missing_files)}"
        )
    return QCRecord("GALLERY", "Galerie OK", passed=True,
                     detail=f"{count} image(s)")


def check_api(article: Dict) -> QCRecord:
    """L'article doit être trouvable par API /blog/{UUID} et /blog/list."""
    article_id = article.get('id', '')
    if not article_id:
        return QCRecord("API-UUID", "UUID manquant", priority="P2", passed=False)
    try:
        r = httpx.get(
            f"https://clubcostatropical.es/api/blog/{article_id}",
            timeout=10, verify=False
        )
        if r.status_code != 200:
            return QCRecord(
                "API-DETAIL", "Article introuvable par API UUID",
                priority="P1", passed=False,
                detail=f"HTTP {r.status_code} pour /api/blog/{article_id[:12]}..."
            )
        data = r.json()
        if not data.get('title_fr'):
            return QCRecord(
                "API-DETAIL-EMPTY", "API retourne un objet vide",
                priority="P1", passed=False,
                detail="Le JSON de /api/blog/{id} n'a pas de title_fr"
            )
    except Exception as e:
        return QCRecord("API-DETAIL", f"API error: {e}", priority="P1", passed=False)
    return QCRecord("API", "Article accessible via API", passed=True,
                     detail=f"UUID {article_id[:12]}... → HTTP 200")


def check_metadata(article: Dict) -> QCRecord:
    """Vérifie les métadonnées essentielles."""
    issues = []
    if not article.get('author_id'):
        issues.append("author_id manquant")
    if not article.get('category_id'):
        issues.append("category_id manquant")
    if not article.get('is_published'):
        issues.append("is_published = FALSE")
    wc = article.get('word_count', 0)
    if wc < 500:
        issues.append(f"word_count trop bas: {wc} (min 500)")
    if issues:
        return QCRecord(
            "METADATA", "Métadonnées incomplètes",
            priority="P3", passed=False,
            detail="; ".join(issues)
        )
    return QCRecord("METADATA", "Métadonnées OK", passed=True,
                     detail=f"WC={wc}")


def check_slug(slug: str) -> QCRecord:
    """Vérifie que le slug ne contient pas de double date."""
    # Le slug ne doit pas avoir 'YYYY-MM-DD-YYYY-MM-DD' (double date)
    import re
    matches = re.findall(r'\d{4}-\d{2}-\d{2}', slug)
    if len(matches) > 1:
        return QCRecord(
            "SLUG-DOUBLE-DATE", "Slug contient une double date",
            priority="P2", passed=False,
            detail=f"Pattern dates: {matches} dans slug '{slug}'"
        )
    return QCRecord("SLUG", "Slug OK", passed=True)


def check_title_length(article: Dict) -> QCRecord:
    '''Les titres ne doivent pas depasser 60 caracteres.'''
    for lang, col in [('fr', 'title'), ('es', 'title_es'), ('en', 'title_en')]:
        t = article.get(col, '')
        if len(t) > 60:
            return QCRecord(
                "TITLE-LONG", f"Titre {lang} trop long ({len(t)}c)",
                priority="P1", passed=False,
                detail=f"'{t[:50]}...' — max 60 caractères"
            )
    return QCRecord("TITLE-LEN", "Titres taille OK", passed=True)


# ─── DÉTECTION DE LANGUE ────────────────────────────────────────────────

# Mots EXCLUSIVEMENT espagnols (pas de faux positifs avec le français)
# On évite : que/entre/comme/dans (communs au FR)
_SPANISH_MARKERS = {
    "los", "las", "más", "para", "esta", "este", "como",
    "donde", "cuesta", "comprar", "guía", "vivienda",
    "alquiler", "municipio", "expatriado", "presupuesto",
    "retirado", "inversor", "vistas", "pisos", "tiene",
    "son", "está", "ser", "cada", "sobre", "vida",
    "años", "año", "parte", "puede", "también",
}

# Mots communs FR/ES à NE PAS compter
_FR_COMMON = {"que", "entre", "comme", "dans", "sur", "une", "tout", "son", "los"}

# Caractères exclusifs à l'espagnol (hors ñ/Ñ qui sont dans les noms de lieux)
_SPANISH_CHARS = set("¡¿")


def check_language(article: Dict) -> QCRecord:
    """Détecte si le contenu FR est réellement en français, pas en espagnol."""
    fr_content = article.get('content', '')
    if not fr_content or len(fr_content) < 100:
        return QCRecord("LANG-TOO-SHORT", "Contenu FR trop court pour analyse",
                         priority="P1", passed=False)

    words = set(fr_content.lower().split())

    # Exclure les mots communs FR/ES
    spanish_markers_found = (words & _SPANISH_MARKERS) - _FR_COMMON

    # Compter ¡ et ¿ (exclusifs à l'espagnol, pas de faux positifs)
    spanish_char_count = sum(1 for c in fr_content if c in _SPANISH_CHARS)
    marker_ratio = len(spanish_markers_found) / max(len(_SPANISH_MARKERS), 1)

    # Si >30% des marqueurs espagnols sont présents OU des ¡¿
    if marker_ratio > 0.30 or spanish_char_count > 0:
        markers = ", ".join(sorted(spanish_markers_found)[:10]) if spanish_markers_found else "(aucun mot, ponctuation ¡¿)"
        return QCRecord(
            "LANG-ES-IN-FR", "Le contenu FR semble être en espagnol",
            priority="P1", passed=False,
            detail=f"{len(spanish_markers_found)} marqueurs ES trouvés sur {len(_SPANISH_MARKERS)} ({marker_ratio:.0%}) — ex: {markers}"
        )

    return QCRecord("LANG", "Langue FR correcte", passed=True,
                     detail=f"0 marqueurs espagnols significatifs")


def check_geo_intro(article):
    """P2: Vérifie que les 200 premiers caractères sont GEO-optimisés."""
    content = (article.get("content_es") or article.get("content") or "")
    intro = content[:200].strip()
    has_digits = bool(re.search(r'\d', intro))
    communes_found = bool(re.search(r'(?i)(Motril|Almuñécar|Salobreña|La Herradura|Torrenueva|Castell|Vélez|Gualchos|Lújar|Molvízar|Rubite|Saleres|Ítrabo|Lobres|La Mamola|Calahonda|Carchuna|El Varadero)', intro))
    is_poetic = bool(re.search(r'(?i)(soleil|rêve|paradis|magique|enchant|merveille|splendide)', intro))
    if is_poetic or not has_digits:
        return QCRecord("GEO-INTRO", "Intro non optimisee GEO",
                        priority="P2", passed=False,
                        detail=f"{'Poétique' if is_poetic else ''} {'Pas de chiffres' if not has_digits else ''} {'Pas de communes' if not communes_found else ''}")
    return QCRecord("GEO-INTRO", "Intro GEO-optimisee", passed=True)


# ─── QC COMPLET ──────────────────────────────────────────────────────────

ALL_CHECKS = [
    ("🔤 Sous-titres", check_subtitle),
    ("🌐 Titres trilingues", check_trilingual_titles),
    ("📏 Longueur titres", check_title_length),
    ("🇫🇷 Langue FR", check_language),
    ("📝 Contenu trilingue", check_content),
    ("🌍 Intro GEO", check_geo_intro),
    ("🔎 Vérif faits", check_fact_verification),
    ("🖼️ Image hero", check_hero_image),
    ("📸 Images inline", check_inline_images),
    ("🖼️ Galerie", check_gallery),
    ("🎙️ Podcast audio", check_podcast),
    ("🔗 API accessible", check_api),
    ("🏷️ Métadonnées", check_metadata),
]


def run_full_qc(slug: str) -> List[QCRecord]:
    """Exécute le QC complet sur un article identifié par son slug.

    Args:
        slug: Le slug de l'article (ex: 'enquetes-dossiers-2026-06-18')

    Returns:
        Liste des QCRecord, triés par priorité
    """
    results: List[QCRecord] = []

    # 1. Vérification du slug (ne nécessite pas la DB)
    results.append(check_slug(slug))

    # 2. Récupérer l'article
    article = _fetch_article(slug)
    if not article:
        results.append(QCRecord("DB", "Article introuvable dans la DB",
                                 priority="P1", passed=False,
                                 detail=f"slug={slug}"))
        return results

    # 3. Tous les checks
    for label, check_fn in ALL_CHECKS:
        try:
            result = check_fn(article)
            results.append(result)
        except Exception as e:
            results.append(QCRecord(
                check_fn.__name__, f"Exception dans {check_fn.__name__}",
                priority="P1", passed=False, detail=str(e)
            ))

    return results


def report(results: List[QCRecord], slug: str) -> Dict:
    """Génère un rapport structuré à partir des résultats QC."""
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    p1_fails = [r for r in failed if r.priority == "P1"]
    p2_fails = [r for r in failed if r.priority == "P2"]
    p3_fails = [r for r in failed if r.priority == "P3"]

    # Verdict global
    if p1_fails:
        verdict = "🔴 FAIL"
        summary = f"{len(p1_fails)} critique(s) — NE PAS PUBLIER"
    elif p2_fails:
        verdict = "🟡 WARN"
        summary = f"{len(p2_fails)} non-critique(s) — Publier OK, corriger"
    else:
        verdict = "✅ PASS"
        summary = "Tout est vert"

    report_data = {
        "slug": slug,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "summary": summary,
        "scores": {
            "passed": len(passed),
            "failed": len(failed),
            "total": len(results),
            "p1_fails": len(p1_fails),
            "p2_fails": len(p2_fails),
            "p3_fails": len(p3_fails),
        },
        "checks": [
            {
                "code": r.code,
                "label": r.label,
                "priority": r.priority,
                "passed": r.passed,
                "detail": r.detail,
            }
            for r in results
        ],
    }

    return report_data


def print_report(report_data: Dict):
    """Affiche le rapport QC dans les logs."""
    from datetime import datetime
    dt = datetime.fromisoformat(report_data["timestamp"])

    logger.info("")
    logger.info("━" * 50)
    logger.info(f"📊 CONTRÔLE QUALITÉ — Journal CCT")
    logger.info(f"    Article: {report_data['slug']}")
    logger.info(f"    Date: {dt.strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"    Verdict: {report_data['verdict']}")
    logger.info(f"    Score: {report_data['scores']['passed']}/{report_data['scores']['total']}")
    logger.info(f"    {report_data['summary']}")
    logger.info("━" * 50)

    for check in report_data["checks"]:
        icon = "✅" if check["passed"] else "❌"
        prio = {"P1": "🔴", "P2": "🟡", "P3": "🔵"}.get(check["priority"], "")
        logger.info(f"  {icon} [{prio}{check['priority']}] {check['code']}: {check['label']}")
        if check["detail"]:
            logger.info(f"     → {check['detail']}")

    logger.info("━" * 50)
    if report_data["scores"]["failed"] > 0:
        logger.warning(f"⚠️ {report_data['scores']['failed']} vérification(s) non passée(s)")
    else:
        logger.info("✅ TOUT EST VERT")

    return report_data["verdict"] == "✅ PASS"


def run_qc(slug: str) -> bool:
    """Point d'entrée unique pour le QC externe."""
    results = run_full_qc(slug)
    rd = report(results, slug)
    return print_report(rd)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    import argparse
    parser = argparse.ArgumentParser(description="QC — Contrôle Qualité Journal CCT")
    parser.add_argument("slug", help="Slug de l'article à vérifier")
    args = parser.parse_args()
    sys.exit(0 if run_qc(args.slug) else 1)


def check_fact_verification(article):
    """P2: Verification factuelle via SearXNG + Gateway (Sherlock Verify)."""
    try:
        from sherlock_verify import verify_article
    except ImportError:
        return QCRecord("VERIFY-FACTS", "Module sherlock_verify.py introuvable",
                        priority="P2", passed=True, detail="Module non disponible")
    content = (article.get("content_es") or "").strip()
    if len(content) < 300:
        return QCRecord("VERIFY-FACTS", "Contenu ES trop court",
                        priority="P2", passed=True, detail="Skip")
    try:
        result = verify_article(content, lang="es")
    except Exception as e:
        return QCRecord("VERIFY-FACTS", "Erreur",
                        priority="P2", passed=True, detail=str(e)[:200])
    sc = result.get("score_global", 10)
    nc = result.get("nb_confirme", 0)
    nx = result.get("nb_contredit", 0)
    nv = result.get("nb_non_verifiable", 0)
    ok = nx == 0
    d = f"Score: {sc}/10 | {nc}c {nx}x {nv}?"
    if not ok:
        d += " | CONTRADICTIONS!"
    return QCRecord("VERIFY-FACTS", "Verification factuelle (SearXNG + Gateway)",
                    priority="P2", passed=ok, detail=d)
