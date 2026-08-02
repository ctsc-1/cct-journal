#!/usr/bin/env python3
"""
phase0_evaluator.py — Phase 0 : Évaluation du potentiel d'un sujet avant DeepSearch.

Étapes :
0.1 SONDAGE — Flash Lite génère 3 candidats pour la catégorie
0.2 SCORING — Chaque candidat reçoit un score /10 (potentiel 10 000 mots)
0.3 SÉLECTION — Meilleur candidat ≥ 7/10, génération du sujet complet
0.4 ANTI-DOUBLON — SQL 30 jours + vectoriel (pgvector cosine similarity)

Usage: from phase0_evaluator import evaluate; topic = evaluate(category, date_str)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import psycopg2

# ─── CONFIG SURDIMENSIONNÉE ─────────────────────────────────
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
MODEL_LIGHT = "deepseek-chat"    # Sondage, scoring (DeepSeek direct, fiable — pas Gemini/Gateway)
MODEL_EMBED = "gemini-embedding-2"       # Embeddings via Gateway
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# Timeouts surdimensionnés
TIMEOUT_SONDAGE = 45       # 0.1: 3 candidats, Flash Lite
TIMEOUT_SCORE = 30          # 0.2: scoring unitaire, Flash Lite
TIMEOUT_SELECT = 45         # 0.3: génération sujet, Flash Lite
TIMEOUT_SQL = 10            # 0.4a-b: SQL anti-doublon
TIMEOUT_EMBED = 15          # 0.4c: génération embedding
TIMEOUT_GATEWAY = 120       # fallback général

# Anti-doublon
SIMILARITY_THRESHOLD = 0.85  # cosine > 0.85 → doublon
DAYS_HISTORY = 30
DAYS_CATEGORY_COOLDOWN = 7
MIN_CANDIDATE_SCORE = 7.0   # score minimum pour être viable
MAX_CATEGORY_RETRIES = 2    # nombre de catégories à essayer avant abandon


# ─── LOGGING ────────────────────────────────────────────────
def log(msg: str, newline: bool = True):
    t = datetime.now().strftime("%H:%M:%S")
    prefix = "[Phase0]"
    if newline:
        print(f"[{t}] {prefix} {msg}", flush=True)
    else:
        print(f"[{t}] {prefix} {msg}", end=" ", flush=True)


# ─── GATEWAY (avec fallback DeepSeek direct) ──────────────────
DEEPSEEK_API_KEY = None

def _get_deepseek_key() -> str:
    """Charge la clé API DeepSeek depuis config.yaml (une seule fois)."""
    global DEEPSEEK_API_KEY
    if DEEPSEEK_API_KEY:
        return DEEPSEEK_API_KEY
    try:
        import re
        with open("/root/.hermes/config.yaml", "r") as f:
            config = f.read()
        m = re.search(r'deepseek:\s*\n\s+api_key:\s*(\S+)', config)
        if m:
            DEEPSEEK_API_KEY = m.group(1)
            return DEEPSEEK_API_KEY
    except Exception:
        pass
    # Fallback: depuis les variables d'environnement
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    return DEEPSEEK_API_KEY


def _llm(prompt: str, model: str = MODEL_LIGHT, max_tokens: int = 4096,
         temp: float = 0.3, timeout: int = TIMEOUT_GATEWAY) -> str:
    # Si modèle DeepSeek → appel direct (contourne Gateway qui est Gemini-only)
    if "deepseek" in model.lower():
        api_key = _get_deepseek_key()
        r = httpx.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temp,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = msg.get("content", "")
        # deepseek-v4-flash peut mettre la réponse dans reasoning_content (mode thinking)
        if not content:
            content = msg.get("reasoning_content", " ")
        return content.strip()
    # Sinon → Gateway (Gemini)
    r = httpx.post(
        f"{GATEWAY}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temp,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _get_db_url() -> str:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            result = subprocess.run(
                ["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                db_url = result.stdout.strip().split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    return db_url or "postgresql://postgres@/alejandro_db"


# ─── 0.1 SONDAGE ────────────────────────────────────────────
def sond_candidates(category: dict, date_str: str) -> list[dict]:
    """Génère 3 sujets candidats avec matière suffisante pour 10 000 mots."""
    log("📡 0.1 SONDAGE — Recherche de 3 sujets candidats...")

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    day_name = day_names[dt.weekday()]

    prompt = f"""Eres el redactor jefe del Club Costa Tropical. Hoy es {day_name} {date_str}.

CATEGORÍA: {category['name_es']}
DESCRIPCIÓN: {category['description']}
ÁNGULO: {category['angle']}

Tu tarea: proponer 3 temas concretos para un artículo de MÁS DE 10 000 PALABRAS.

REGLAS:
- Cada tema debe tener SUFICIENTE materia para un artículo largo (10 000+ palabras):
  fuentes web probables, datos numéricos disponibles, contexto histórico, 
  múltiples ángulos de análisis, actores/partes implicadas.
- Ancla CADA tema en la Costa Tropical (Motril, Almuñécar, Salobreña, Alpujarra, etc.)
- NO propongas el tema de la sequía del embalse de Rules
- Los temas deben ser CONCRETOS, no genéricos

Devuelve SOLO un JSON array con 3 objetos:
[
  {{
    "title": "Título propuesto (30-50 caracteres, directo)",
    "angle": "Ángulo narrativo (1-2 frases)",
    "context": "Por qué este tema tiene suficiente profundidad: fuentes probables, datos disponibles, actores implicados (3-5 líneas)",
    "estimated_sources": ["fuente1", "fuente2", "fuente3"]
  }}
]

NO expliques nada. Solo el JSON array."""

    try:
        raw = _llm(prompt, max_tokens=2000, temp=0.5, timeout=TIMEOUT_SONDAGE)
        # Extraire le JSON array
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            candidates = json.loads(match.group(0))
            log(f"   ✅ {len(candidates)} candidats générés")
            return candidates[:3]
    except Exception as e:
        log(f"   ⚠️ Sondage échoué: {e}")

    # Fallback: générer un seul candidat basique
    return [{
        "title": f"{category['name_es']} en la Costa Tropical",
        "angle": category["angle"],
        "context": category["description"],
        "estimated_sources": ["ideal.es", "granadahoy.com"],
    }]


# ─── 0.2 SCORING ────────────────────────────────────────────
def score_candidate(candidate: dict, category: dict) -> float:
    """Évalue le potentiel de recherche d'un candidat. Score /10."""
    title = candidate.get("title", "")
    context = candidate.get("context", "")

    prompt = f"""Eres un evaluador de profundidad periodística. 
Evalúa este tema para un artículo de 10 000+ palabras sobre la Costa Tropical.

TEMA: {title}
CONTEXTO: {context}
CATEGORÍA: {category['name_es']}

Evalúa en 5 criterios (responde SOLO el score, una línea por criterio):

1. FUENTES WEB PROBABLES (0-3): ¿Hay suficientes fuentes andaluzas online para investigar?
2. DATOS CUANTITATIVOS (0-2): ¿Se pueden obtener cifras, estadísticas, precios?
3. ACTUALIDAD (0-2): ¿Es un tema de actualidad en 2026? ¿Interesa ahora?
4. PROFUNDIDAD (0-2): ¿Se puede desarrollar en 10 secciones H2 con contenido único cada una?
5. DEBATE/CONTROVERSIA (0-1): ¿Hay ángulos controvertidos o debates sociales?

SCORE TOTAL (suma de los 5, máximo 10):"""

    try:
        raw = _llm(prompt, max_tokens=200, temp=0.1, timeout=TIMEOUT_SCORE)
        # Extraire tous les nombres et prendre le dernier (score total)
        numbers = re.findall(r'(\d+(?:\.\d+)?)', raw)
        score = float(numbers[-1]) if numbers else 5.0
        score = min(10.0, max(0.0, score))
        log(f"      Score: {score:.1f}/10 — {title[:60]}")
        return score
    except Exception as e:
        log(f"      ⚠️ Scoring échoué: {e}, score par défaut 5.0")
        return 5.0


# ─── 0.3 SÉLECTION ──────────────────────────────────────────
def select_best_topic(candidates: list[dict], scores: list[float],
                      category: dict, date_str: str) -> Optional[dict]:
    """Sélectionne le meilleur candidat et génère le sujet complet."""
    log("🎯 0.3 SÉLECTION — Choix du meilleur candidat...")

    # Trier par score décroissant
    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    best_candidate, best_score = ranked[0]

    if best_score < MIN_CANDIDATE_SCORE:
        log(f"   ❌ Meilleur score {best_score:.1f} < {MIN_CANDIDATE_SCORE}/10")
        return None

    log(f"   🏆 Meilleur candidat: {best_candidate['title'][:60]} ({best_score:.1f}/10)")

    # Générer le sujet complet
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    day_name = day_names[dt.weekday()]

    prompt = f"""Eres el redactor jefe del Club Costa Tropical. Hoy es {day_name} {date_str}.

Has seleccionado este tema tras evaluar su potencial para un artículo de 10 000+ palabras:

TEMA: {best_candidate['title']}
CONTEXTO: {best_candidate['context']}
CATEGORÍA: {category['name_es']}

Genera el encargo periodístico COMPLETO:

Devuelve SOLO un JSON:
{{
  "title": "Título final (30-50 caracteres, máximo 55. DIRECTO. Sin subtítulo. Sin puntuación final.)",
  "angle": "Ángulo narrativo específico para hoy, con enfoque editorial claro (2-3 frases)",
  "context": "Contexto detallado: datos concretos, lugares, cifras, personas, instituciones, fuentes sugeridas (5-8 líneas)"
}}

REGLAS:
- NO menciones fuentes externas como autoridad (Ideal.es, Granada Hoy)
- NO uses "sin duda", "cabe destacar", "es importante señalar"
- NO propongas el tema de la sequía de Rules
- El título debe ser CORTO y DIRECTO (máx 55 caracteres)

NO expliques nada. Solo el JSON."""

    try:
        raw = _llm(prompt, max_tokens=1000, temp=0.3, timeout=TIMEOUT_SELECT)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            topic_data = json.loads(match.group(0))
            topic = {
                "id": f"{category['id']}-{date_str}",
                "domain": category["domain"],
                "title": topic_data.get("title", best_candidate["title"]),
                "angle": topic_data.get("angle", best_candidate.get("angle", "")),
                "context": topic_data.get("context", best_candidate.get("context", "")),
                "tags": category.get("tags", []),
                "category_id": category["category_id"],
                "phase0_score": best_score,
            }
            log(f"   ✅ Sujet validé: {topic['title'][:60]}")
            return topic
    except Exception as e:
        log(f"   ⚠️ Génération sujet échouée: {e}")

    # Fallback avec le candidat brut
    return {
        "id": f"{category['id']}-{date_str}",
        "domain": category["domain"],
        "title": best_candidate.get("title", f"{category['name_es']}"),
        "angle": best_candidate.get("angle", category.get("angle", "")),
        "context": best_candidate.get("context", category.get("description", "")),
        "tags": category.get("tags", []),
        "category_id": category["category_id"],
        "phase0_score": best_score,
    }


# ─── 0.4 ANTI-DOUBLON ───────────────────────────────────────
# Noms de lieux de la Costa Tropical — NE doivent PAS déclencher de doublon seuls
# (un lieu peut être décliné à l'infini : château, sucre, histoire, économie...)
_PLACE_STOPWORDS = {
    "almuñécar", "almunecar", "salobreña", "salobrena", "motril", "orgiva", "torvizcón",
    "torvizcon", "vélez", "velez", "benaudalla", "los", "guájares", "guajares", "molvizar",
    "ítrabo", "itrabo", "jete", "otívar", "otivar", "lújar", "lujar", "gualchos", "castell",
    "ferro", "carchuna", "calahonda", "sorvilán", "sorvilan", "polopos", "rubite", "cádiar",
    "cadiat", "cástaras", "castaras", "juviles", "lobras", "bérchules", "berchules",
    "busquístar", "busquistar", "pórtugos", "portugos", "trévelez", "trevelez", "turón",
    "turon", "válor", "valor", "ugíjar", "ugijar", "murtas", "albondón", "albondon",
    "alpujarra", "maro", "cerro", "gordo", "sexitano", "almuñekar", "río", "rio", "verde",
    "castell", "ferro", "melicena", "nejra", "la", "rax", "cerro", "gordo", "costa",
    "granada", "andalucía", "andalucia", "españa", "espana", "sierra", "contraviesa",
}


def _extract_keywords(title: str) -> list[str]:
    """Extrait les mots-clés thématiques d'un titre (mots > 4 lettres, HORS noms de lieux).
    Les noms de lieux sont exclus : un lieu peut être décliné en plusieurs articles
    (château, sucre, économie...) sans que ce soit un doublon."""
    import re
    stopwords = {"sobre", "para", "como", "entre", "desde", "hacia", "hasta",
                 "durante", "según", "contra", "bajo", "ante", "tras", "pero",
                 "costa", "tropical", "historia", "año", "parte", "nueva", "gran",
                 "más", "del", "los", "las", "una", "que", "por", "con", "sus",
                 "siglo", "secreto", "secreta", "guía", "guia", "ruta", "viaje",
                 "tradición", "tradicion", "cultura", "pasado", "futuro", "pequeña",
                 "pequena", "único", "unico", "especial"}
    stopwords |= _PLACE_STOPWORDS
    words = re.findall(r'[a-záéíóúñü]{4,}', title.lower())
    seen, out = set(), []
    for w in words:
        if w not in stopwords and w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= 10:
            break
    return out


def check_duplicates_thematic(topic_title: str, topic_context: str) -> bool:
    """Vérifie les doublons thématiques par mots-clés sur TOUT l'historique.
    Extrait les mots-clés du titre + contexte, cherche ≥2 matches dans les titres existants."""
    log("   🔍 0.4a Anti-doublon thématique (full-text)...", newline=False)
    try:
        # Extraire du titre ET du contexte pour avoir plus de mots-clés
        keywords = _extract_keywords(topic_title + " " + topic_context)
        if len(keywords) < 2:
            log("✅ OK (pas assez de mots-clés)")
            return False

        conn = psycopg2.connect(_get_db_url(), connect_timeout=TIMEOUT_SQL)
        cur = conn.cursor()
        cur.execute(
            "SELECT title_es, slug, published_at, content_es FROM articles "
            "WHERE is_published = TRUE "
            "ORDER BY published_at DESC",
        )
        all_articles = cur.fetchall()
        cur.close()
        conn.close()

        for title, slug, pub_date, content in all_articles:
            if not title:
                continue
            title_lower = title.lower()
            matches = [kw for kw in keywords if kw in title_lower]
            if len(matches) >= 2:
                log(f"⚠️ DOUBLON ({len(matches)} kw: {matches}): \"{title[:60]}\" ({pub_date.strftime('%d/%m/%Y') if pub_date else '?'})")
                return True
            # Aussi vérifier dans content_es si titre match partiel
            if len(matches) == 1 and content:
                content_lower = content[:5000].lower()
                content_matches = [kw for kw in keywords if kw in content_lower]
                if len(content_matches) >= 3:  # Plus strict: 3 kw dans le contenu
                    log(f"⚠️ DOUBLON CONTENU ({len(content_matches)} kw): \"{title[:60]}\" ({pub_date.strftime('%d/%m/%Y') if pub_date else '?'})")
                    return True

        log("✅ OK")
        return False
    except Exception as e:
        log(f"⚠️ Thématique HS (non bloquant): {e}")
        return False


def check_duplicates_sql(topic_title: str, category_id: str) -> bool:
    """Vérifie les doublons par titre (30j) et par catégorie (7j)."""
    log("   🔍 0.4b Anti-doublon SQL (30 jours)...", newline=False)
    try:
        conn = psycopg2.connect(_get_db_url(), connect_timeout=TIMEOUT_SQL)
        cur = conn.cursor()

        # 4a: titres similaires sur 30 jours
        cur.execute(
            "SELECT title_es FROM articles WHERE published_at > NOW() - INTERVAL '30 days' "
            "AND is_published = TRUE ORDER BY published_at DESC",
        )
        recent_titles = [row[0] for row in cur.fetchall() if row[0]]

        # Comparaison simple: le titre candidat est-il très proche d'un titre existant?
        title_lower = topic_title.lower().strip()
        for rt in recent_titles:
            rt_lower = rt.lower().strip()
            # Même début (>50% commun) ou l'un contient l'autre
            min_len = min(len(title_lower), len(rt_lower))
            common = sum(1 for a, b in zip(title_lower, rt_lower) if a == b)
            if common > min_len * 0.7 or title_lower in rt_lower or rt_lower in title_lower:
                log(f"⚠️ DOUBLON TITRE: \"{rt[:60]}\"")
                cur.close(); conn.close()
                return True

        # 4b: même catégorie dans les 7 derniers jours
        cur.execute(
            "SELECT title_es FROM articles WHERE published_at > NOW() - INTERVAL '7 days' "
            "AND category_id = %s AND is_published = TRUE ORDER BY published_at DESC LIMIT 3",
            (category_id,),
        )
        recent_cat = cur.fetchall()
        if recent_cat:
            titles_str = "', '".join([r[0][:50] for r in recent_cat])
            log(f"⚠️ MÊME CATÉGORIE < 7j: {titles_str}")
            cur.close(); conn.close()
            return True

        cur.close()
        conn.close()
        log("✅ OK")
        return False
    except Exception as e:
        log(f"⚠️ SQL error: {e}")
        return False  # En cas d'erreur, ne pas bloquer


def check_duplicates_vectoriel(topic_title: str, topic_context: str) -> tuple[bool, float]:
    """Vérifie les doublons par similarité sémantique (pgvector cosine)."""
    log("   🔍 0.4b Anti-doublon vectoriel (pgvector cosine)...", newline=False)
    try:
        # Générer l'embedding du sujet candidat via Gateway
        combined = f"{topic_title}. {topic_context}"[:8000]
        emb_r = httpx.post(
            f"{GATEWAY}/v1/embed",
            json={"model": MODEL_EMBED, "contents": combined,
                  "task_type": "RETRIEVAL_DOCUMENT", "output_dimensionality": 768},
            timeout=TIMEOUT_EMBED,
        )
        emb_r.raise_for_status()
        data = emb_r.json()
        # Le format de réponse peut être {"embedding": [...]} ou {"data": [{"embedding": [...]}]}
        if "embedding" in data:
            embedding = data["embedding"]
        elif "data" in data:
            embedding = data["data"][0]["embedding"]
        else:
            raise ValueError(f"Format embedding inconnu: {list(data.keys())}")

        # Requête pgvector
        conn = psycopg2.connect(_get_db_url(), connect_timeout=TIMEOUT_SQL)
        cur = conn.cursor()
        cur.execute(
            "SELECT title_es, published_at, "
            "1 - (embedding_gemini <=> %s::vector) AS similarity "
            "FROM articles "
            "WHERE published_at > NOW() - INTERVAL '30 days' "
            "AND embedding_gemini IS NOT NULL "
            "AND is_published = TRUE "
            "ORDER BY similarity DESC LIMIT 5",
            (embedding,),
        )
        matches = cur.fetchall()
        cur.close()
        conn.close()

        if matches:
            max_sim = float(matches[0][2])
            if max_sim >= SIMILARITY_THRESHOLD:
                log(f"⚠️ DOUBLON SÉMANTIQUE: \"{matches[0][0][:60]}\" ({max_sim:.3f})")
                return True, max_sim
            log(f"✅ OK (max similarity: {max_sim:.3f})")
            return False, max_sim
        else:
            log("✅ OK (aucun embedding comparable)")
            return False, 0.0

    except Exception as e:
        log(f"⚠️ Vectoriel HS (non bloquant): {e}")
        return False, 0.0


# ─── ORCHESTRATEUR PHASE 0 ──────────────────────────────────
def evaluate(category: dict, date_str: str) -> Optional[dict]:
    """
    Évalue le potentiel d'une catégorie. Retourne le topic validé ou None.

    Si la catégorie ne produit aucun sujet viable, l'appelant doit
    essayer une autre catégorie (via rotor avec offset=1).
    """
    log("═══ PHASE 0: ÉVALUATION DU POTENTIEL ═══")
    start = time.time()

    # 0.1: Sondage
    candidates = sond_candidates(category, date_str)
    if not candidates:
        log("❌ Aucun candidat trouvé")
        return None

    # 0.2: Scoring
    log(f"📊 0.2 SCORING — {len(candidates)} candidats à évaluer...")
    scores = []
    for i, c in enumerate(candidates):
        log(f"   Candidat {i+1}/{len(candidates)}: {c.get('title', '?')[:60]}")
        s = score_candidate(c, category)
        scores.append(s)
        time.sleep(2)  # Anti-stress quotas

    # 0.3: Sélection
    topic = select_best_topic(candidates, scores, category, date_str)
    if not topic:
        log("❌ Aucun candidat n'a le score minimum")
        return None

    # 0.4: Anti-doublon
    log("🛡️ 0.4 ANTI-DOUBLON...")
    is_dup_thematic = check_duplicates_thematic(topic["title"], topic["context"])
    is_dup_sql = check_duplicates_sql(topic["title"], topic["category_id"])
    is_dup_vec, max_sim = check_duplicates_vectoriel(topic["title"], topic["context"])

    if is_dup_thematic or is_dup_sql or is_dup_vec:
        log("❌ DOUBLON DÉTECTÉ — sujet rejeté")
        return None

    elapsed = time.time() - start
    log(f"✅ PHASE 0 TERMINÉE ({elapsed:.0f}s) — Sujet: {topic['title'][:60]} (score: {topic.get('phase0_score', '?')}/10)")
    return topic


if __name__ == "__main__":
    # Test rapide
    from rotor import CATEGORIES
    cat = CATEGORIES[3]  # Gastronomía y Vino
    result = evaluate(cat, "2026-07-30")
    if result:
        print(f"\n✅ Topic: {result['title']}")
        print(f"   Score: {result.get('phase0_score', 'N/A')}/10")
        print(f"   Angle: {result['angle']}")
    else:
        print("\n❌ Aucun sujet viable")
