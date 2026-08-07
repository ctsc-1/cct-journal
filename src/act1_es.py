#!/usr/bin/env python3
"""
act1_es.py — Acte 1 v2: Article ES 10 000+ mots par génération H2 par H2.

Pipeline:
  1a. DeepSearch → synthèse 2000 mots
  1b. PLANIFICATION → 12-15 H2 + contexte spécifique par H2
  1c. GÉNÉRATION SÉQUENTIELLE — un appel Gemini 3.6 Flash par H2 (900-1200 mots)
      Cohérence préservée via cache: résumé des H2 précédents en contexte
  1d. FASTCHECK ES — DeepSeek + Gemini cross-check sur l'article assemblé
  1e. HUMANISATION ES — nettoyage chirurgical, hash factuel

Usage: python3 act1_es.py <categorie_id> [--topic "sujet"] [--date YYYY-MM-DD]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from pipeline_cache import save_step, load_step

# ─── CONFIG ─────────────────────────────────────────────────
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DATE = datetime.now().strftime("%Y-%m-%d")

MODEL_HEAVY = "deepseek-v4-flash"       # Génération article ES (appels H2) — RÈGLE MARC: JAMAIS deepseek-v4-pro, JAMAIS deepseek-chat (déprécié 24/07)
MODEL_LIGHT = "deepseek-v4-flash"  # DeepSearch, planification, résumés

TIMEOUT_SECTION = 300  # Génération d'un H2 (900-1200 mots) — pas de contrainte de temps
TIMEOUT_LIGHT = 120
MIN_WORDS_H2 = 800
MAX_WORDS_H2 = 1200
MIN_H2_SECTIONS = 10
MAX_H2_SECTIONS = 15

ANDALUSIA_CONSTRAINT = (
    "IMPORTANTE: Busca UNICAMENTE fuentes andaluzas (Ideal.es, Granada Hoy, "
    "Junta de Andalucía, Diputación de Granada, AEMET, IECA, etc.). "
    "Ignora resultados que no conciernen a Andalucía, provincia de Granada "
    "o Costa Tropical."
)


def log(msg: str, newline: bool = True):
    t = datetime.now().strftime("%H:%M:%S")
    if newline:
        print(f"[{t}] {msg}", flush=True)
    else:
        print(f"[{t}] {msg}", end=" ", flush=True)


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
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    return DEEPSEEK_API_KEY


def _llm(prompt: str, model: str = MODEL_LIGHT, max_tokens: int = 4096,
         temp: float = 0.3, timeout: int = 120) -> str:
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
                "reasoning_effort": "none",  # ponytail: désactive le mode thinking DeepSeek (04/08/2026)
            },
            timeout=timeout,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = msg.get("content", "")
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


def _deepseek_call(prompt: str, max_tokens: int = 4096, temp: float = 0.1,
                   thinking: bool = True) -> str:
    """Appel DeepSeek V4 Flash pour FastCheck.

    thinking=True (défaut) : active le raisonnement DeepSeek. Le resultat final
    est dans `content`, mais si vide le raisonnement est dans `reasoning_content`
    (piège deepseek-hub-design). On lit content puis fallback reasoning_content.
    """
    import subprocess
    try:
        r = subprocess.run(
            ["grep", "-A3", "deepseek:", "/root/.hermes/config.yaml"],
            capture_output=True, text=True, timeout=5
        )
        m = re.search(r"api_key:\s*(\S+)", r.stdout)
        key = m.group(1) if m else os.environ.get("DEEPSEEK_API_KEY", "")
    except Exception:
        key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("Clé DeepSeek introuvable")

    body = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temp,
    }
    if not thinking:
        # Ponytail: raisonnement coûteux inutile pour des tâches simples (04/08/2026)
        body["reasoning_effort"] = "none"

    r = httpx.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = msg.get("content", "")
    if not content.strip():
        content = msg.get("reasoning_content", " ")
    return content.strip()


# ─── OPENROUTER (Qwen thinking) ─────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
QWEN_THINKING_MODEL = "qwen/qwen-plus-2025-07-28:thinking"


def _openrouter_call(prompt: str, max_tokens: int = 4096, temp: float = 0.1) -> str:
    """Appel Qwen Plus :thinking via OpenRouter (API directe).

    Isolation profils (07/08/2026 — Marc) : la clé OpenRouter est lue UNIQUEMENT
    depuis le contexte du profil alejandro-journal.
    1. Variable d'environnement OPENROUTER_API_KEY — injectée par systemd via
       l'EnvironmentFile=/root/.hermes/profiles/alejandro-journal/.env.
    2. Fallback : relecture du .env PROPRE du profil journal (source de vérité
       interne), jamais d'un profil partagé ni du config root.
    Le suffixe `:thinking` active le mode raisonnement de Qwen ; la réponse
    finale est dans `content` (OpenRouter ne pollue pas `content` avec le
    raisonnement).
    """
    PROFILE_ENV = "/root/.hermes/profiles/alejandro-journal/.env"
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        try:
            for line in open(PROFILE_ENV):
                if line.strip().startswith("OPENROUTER_API_KEY"):
                    v = line.split("=", 1)[1].strip()
                    if v:
                        key = v
                        break
        except Exception:
            key = ""
    if not key:
        raise RuntimeError("Clé OpenRouter introuvable (profil alejandro-journal)")

    r = httpx.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://clubcostatropical.es",
            "X-Title": "CCT Journal FastCheck",
        },
        json={
            "model": QWEN_THINKING_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temp,
        },
        timeout=120,
    )
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return (msg.get("content", "") or "").strip()


# ─── SEARXNG ────────────────────────────────────────────────
def search_searxng(query: str, lang: str = "es", max_results: int = 5) -> list[dict]:
    for port in [8888, 8889]:
        try:
            r = httpx.get(
                f"http://127.0.0.1:{port}/search",
                params={"q": query, "format": "json", "language": lang,
                        "categories": "general,news"},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                if results:
                    return [
                        {"title": r["title"], "url": r["url"],
                         "snippet": r.get("content", "")[:300]}
                        for r in results[:max_results]
                    ]
        except Exception:
            continue
    return []


# ─── 1a. DEEPSEARCH ─────────────────────────────────────────
def deepsearch(topic: dict, date_str: str = DATE) -> str:
    log("📡 1a. DeepSearch (SearXNG + Flash Lite)")
    domain = topic.get("domain", "")
    title = topic["title"]

    query_prompt = (
        f"Eres un documentalista. Genera 7-10 busquedas web para investigar: {title}\n"
        f"Dominio: {domain}\n{ANDALUSIA_CONSTRAINT}\n"
        f"Formato: UNA busqueda por linea. Sin operadores site: — usa palabras clave naturales.\n"
        f"Incluye datos, cifras, nombres de lugares, fechas recientes (2025-2026).\n"
        f"Ej: Salobreña castillo nazarí historia 2025\n"
        f"Variar los terminos: mezcla aspectos historicos, economicos, culturales, geograficos."
    )
    queries_raw = _llm(query_prompt, max_tokens=500, temp=0.3)
    queries = [q.strip() for q in queries_raw.splitlines() if q.strip() and len(q) > 10][:7]
    if not queries:
        queries = [
            f"{title} Costa Tropical Granada {date_str[:4]}",
            f"site:ideal.es {title} {date_str[:4]}",
        ]

    log(f"   {len(queries)} requêtes")
    all_sources = []
    for i, q in enumerate(queries):
        results = search_searxng(q)
        all_sources.extend(results)
        log(f"   [{i+1}/{len(queries)}] {q[:60]}... → {len(results)} sources")
        time.sleep(5)

    log(f"   Total: {len(all_sources)} sources brutes")
    sources_text = "\n\n".join(
        [f"FUENTE: {s['title']}\n{s['snippet']}" for s in all_sources[:20]]
    )
    synthesis = _llm(
        f"Eres un periodista. Sintetiza estas fuentes en 1500-2500 palabras "
        f"con datos concretos, cifras, fechas y lugares. "
        f"Responde en español. No inventes nada que no este en las fuentes.\n\n"
        f"{sources_text[:8000]}",
        model=MODEL_LIGHT, max_tokens=3000, temp=0.1, timeout=180,
    )
    save_step("act1_deepsearch", {"context": synthesis, "sources_count": len(all_sources)})
    return synthesis


# ─── 1b. PLANIFICATION ──────────────────────────────────────
def plan_article(topic: dict, context: str) -> dict:
    """Génère le plan éditorial: titre, lead, 12-15 H2 avec contexte spécifique."""
    log("📋 1b. Planification (Flash Lite)")

    title = topic["title"]
    angle = topic.get("angle", "")

    prompt = f"""Eres el redactor jefe. Planifica un articulo de INVESTIGACION de mas de 10 000 palabras.

TEMA: {title}
ANGULO: {angle}

CONTEXTO (hechos verificados):
{context[:4000]}

Genera un plan editorial COMPLETO con {MIN_H2_SECTIONS}-{MAX_H2_SECTIONS} secciones H2. CADA H2 debe ser un angulo DISTINTO del tema, con materia para 900-1200 palabras.

Devuelve SOLO un JSON:
{{
  "title": "Titulo final (max 55 caracteres, DIRECTO)",
  "lead": "Lead de 150-200 caracteres con localidad concreta + dato numerico",
  "h2s": [
    {{
      "title": "Titulo H2 (40-60 caracteres, un angulo especifico)",
      "context_specific": "2-3 frases con los datos y fuentes del DeepSearch que alimentan ESTA seccion especifica"
    }}
  ]
}}

REGLAS:
- CADA H2 debe ser un angulo DISTINTO. No repitas angulos.
- Ordenalas logicamente: lo general primero, lo especifico despues.
- El context_specific NO es el contenido de la seccion — son los DATOS del DeepSearch que serviran para escribirla.
- PROHIBIDO: Rules, sequia, polemica politica."""

    raw = _llm(prompt, max_tokens=3000, temp=0.3, timeout=TIMEOUT_LIGHT)
    # Extraction JSON robuste — compteur d'accolades (ponytail: anti-regex-gourmande)
    plan = None
    json_start = raw.find('{')
    if json_start >= 0:
        depth = 0
        json_end = json_start
        for i, ch in enumerate(raw[json_start:], json_start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break
        if json_end > json_start:
            try:
                plan = json.loads(raw[json_start:json_end])
            except json.JSONDecodeError:
                pass
    # Fallback: si pas de JSON, extraire les H2 du texte brut (ponytail: résilience LLM)
    if not plan:
        import re as _re2
        h2_matches = _re2.findall(r'^##\s+(.+?)$|^\*\*(.+?)\*\*$|^\d+\.\s+(.+?)$', raw, _re2.MULTILINE)
        h2_titles = []
        for m in h2_matches:
            t = m[0] or m[1] or m[2]
            if t and len(t) > 10:
                h2_titles.append(t.strip())
        if len(h2_titles) >= 3:
            plan = {
                "title": topic["title"],
                "lead": context[:200] if context else "",
                "h2s": [{"title": t, "context_specific": context[:300] if context else ""} for t in h2_titles[:MAX_H2_SECTIONS]]
            }
            log(f"   \u2705 {len(h2_titles)} H2 extraits du texte brut (fallback)")
    if not plan:
        raise RuntimeError(f"Planification: JSON introuvable dans la réponse ({len(raw)} chars)")

    h2s = plan.get("h2s", [])
    if len(h2s) < MIN_H2_SECTIONS:
        log(f"   ⚠️ Seulement {len(h2s)} H2, on complète avec des H2 génériques")
        # Fallback: générer des H2 supplémentaires
        for i in range(len(h2s), MIN_H2_SECTIONS):
            h2s.append({
                "title": f"Aspecto {i+1}: {title}",
                "context_specific": context[:300]
            })

    log(f"   ✅ {len(h2s)} sections H2 planifiées")
    save_step("act1_plan", plan)
    return plan


# ─── 1c. GÉNÉRATION H2 PAR H2 ───────────────────────────────
def _summarize_h2(content: str) -> str:
    """Résumé ultra-court (1-2 phrases) d'un H2 pour contexte inter-sections."""
    words = content.split()[:50]
    return " ".join(words) + "..." if len(content.split()) > 50 else content


def generate_h2(h2: dict, h2_index: int, total_h2s: int,
                topic: dict, deepsearch_context: str,
                previous_h2s: list[dict], plan_title: str) -> str:
    """Génère UNE section H2 (900-1200 mots) avec contexte des H2 précédents."""
    title = topic["title"]
    h2_title = h2["title"]
    context_specific = h2.get("context_specific", "")

    # Construire le contexte des H2 précédents (résumé)
    prev_context = ""
    if previous_h2s:
        prev_summaries = [
            f"- {h['title']}: {_summarize_h2(h['content'])[:150]}"
            for h in previous_h2s[-3:]  # 3 derniers max
        ]
        prev_context = (
            "SECCIONES YA ESCRITAS (NO las repitas — solo como referencia para evitar "
            "redundancias):\n" + "\n".join(prev_summaries) + "\n\n"
        )

    prompt = f"""Eres Alejandro Ortega, periodista andaluz. Escribe UNA seccion de un articulo mas largo.

ARTICULO COMPLETO: {plan_title}
ESTA SECCION: {h2_title}
SECCION {h2_index+1} de {total_h2s}

CONTEXTO ESPECIFICO PARA ESTA SECCION (datos del DeepSearch):
{context_specific[:600]}

CONTEXTO GLOBAL DEL ARTICULO:
{deepsearch_context[:1500]}

{prev_context}
REGLAS ABSOLUTAS:
- OBLIGATOIRE: Inclus au moins UN TABLEAU MARKDOWN de donnees chiffrees comparatives dans cet article.
- PROHIBIDO repetir conectores usados previamente (evita 'Por otro lado', 'En este sentido', 'Cabe destacar').
- Escribe 900-1200 palabras SOLO para esta seccion (la seccion #{h2_index+1}).
- Empieza DIRECTAMENTE con el contenido, NO repitas el titulo H2.
- NO escribas otros H2, solo ESTA seccion.
- NO resumas las secciones anteriores — escribe contenido NUEVO y especifico.
- USA los datos del contexto especifico. NO inventes personas, citas, anecdotas.
- Estilo: periodismo documental, frases de longitud variable, detalles concretos.
- NO uses "sin duda", "cabe destacar", "es importante señalar".
- NO escribas primera persona narrativa.
- NO escribas cierres ni despedidas — esto es una seccion intermedia.

ESCRIBE AHORA el contenido de la seccion ## {h2_title}"""

    content = _llm(prompt, model=MODEL_HEAVY, max_tokens=4096, temp=0.3,
                   timeout=TIMEOUT_SECTION)
    words = len(content.split())
    log(f"   [{h2_index+1}/{total_h2s}] {h2_title[:50]}... → {words} mots")
    return content


def generate_article_h2_by_h2(topic: dict, plan: dict, deepsearch_context: str) -> str:
    """Génère l'article complet H2 par H2, avec cache progressif."""
    log("✍️ 1c. Génération H2 par H2 (Gemini 3.6 Flash)")
    plan_title = plan.get("title", topic["title"])
    lead = plan.get("lead", "")
    h2s = plan.get("h2s", [])

    log(f"   {len(h2s)} H2 à générer, ~{len(h2s) * 900}-{len(h2s) * 1200} mots cibles")

    # Cache progressif
    progress = load_step("act1_h2s_progress")
    generated_h2s: list = progress.get("h2s", []) if progress else []
    start_idx = len(generated_h2s)

    for i in range(start_idx, len(h2s)):
        h2 = h2s[i]
        content = generate_h2(
            h2, i, len(h2s), topic, deepsearch_context,
            generated_h2s, plan_title
        )

        generated_h2s.append({
            "title": h2["title"],
            "content": content,
            "order": i,
        })
        # Sauvegarde progressive
        save_step("act1_h2s_progress", {"h2s": generated_h2s})
        time.sleep(5)  # Pause anti-saturation: 5s entre chaque H2

    # Assemblage
    article = f"# {plan_title}\n\n{lead}\n\n"
    for h2 in generated_h2s:
        article += f"## {h2['title']}\n\n{h2['content']}\n\n"

    total_words = len(article.split())
    log(f"   ✅ Article assemblé: {total_words} mots, {len(generated_h2s)} sections")

    # Nettoyer le cache de progression
    progress_path = Path("/tmp/cache/journal-cache/act1_h2s_progress.json")
    if progress_path.exists():
        progress_path.unlink()

    return article


# ─── 1d. FASTCHECK ES ───────────────────────────────────────
def _extract_factual_core(text: str) -> str:
    numbers = re.findall(r'\d+[\d\s.,%€]*\d*', text)
    proper_nouns = re.findall(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b', text)
    dates = re.findall(r'\b\d{1,2}\s+de\s+\w+\s+(?:de\s+)?\d{4}\b', text)
    core = " ".join(numbers + proper_nouns + dates)
    return hashlib.md5(core.encode()).hexdigest()


def fastcheck_es(article: str) -> tuple[bool, int, str]:
    log("🔍 1d. FastCheck ES (DeepSeek V4 thinking + Qwen Plus:thinking)")
    log("   V1: DeepSeek V4 Flash...", newline=False)
    v1_prompt = f"""Verificador de datos. Analiza este articulo periodistico (>10 000 palabras).
Verifica: personas inventadas, cifras incoherentes, lugares falsos, escenas narrativas inventadas, 
primera persona narrativa.

Responde: HALLUC|tipo|gravedad(1-5)|extracto|razon (una linea por hallazgo)
Si todo correcto: PROPRE

ARTICULO:
{article[:15000]}
"""
    try:
        v1_raw = _deepseek_call(v1_prompt, max_tokens=1000, temp=0.1, thinking=True)
        v1_hallucs = [l for l in v1_raw.splitlines() if l.startswith("HALLUC|")]
        v1_score = max(0, 10 - min(10, len(v1_hallucs) * 2))
        log(f"{v1_score}/10 ({len(v1_hallucs)} problemes)")
    except Exception as e:
        log(f"❌ DeepSeek HS: {e}")
        return False, 0, f"FastCheck V1 plante: {e}"

    log("   V2: Qwen Plus :thinking (OpenRouter)...", newline=False)
    v2_prompt = f"""Editor conservador. Contra-verifica:
- Contradicciones internas entre secciones?
- Tono panfletario/acusador?
- Frases genericas IA?
- Datos aproximados?

HALLUC|tipo|gravedad|extracto|razon o PROPRE

ARTICULO:
{article[:15000]}
"""
    try:
        v2_raw = _openrouter_call(v2_prompt, max_tokens=1000, temp=0.1)
        v2_hallucs = [l for l in v2_raw.splitlines() if l.startswith("HALLUC|")]
        v2_score = max(0, 10 - min(10, len(v2_hallucs) * 2))
        log(f"{v2_score}/10 ({len(v2_hallucs)} problemes)")
    except Exception as e:
        log(f"❌ Qwen HS: {e}")
        return False, 0, f"FastCheck V2 plante: {e}"

    score = int((v1_score + v2_score) / 2)
    feedback = "\n".join(v1_hallucs + v2_hallucs)
    passed = score >= 8
    log(f"   {'✅' if passed else '⚠️'} Score: {score}/10")
    return passed, score, feedback


# ─── 1e. HUMANISATION ES ────────────────────────────────────
def humanize_es(article: str) -> str:
    log("🖋️ 1e. Humanisation ES (Flash Lite)")
    hash_before = _extract_factual_core(article)

    # Humaniser section par section (les articles de 10K mots sont trop longs d'un coup)
    sections = article.split("\n## ")
    humanized_sections = [sections[0]]  # H1 + lead

    for i, section in enumerate(sections[1:], 1):
        prompt = f"""Editor de estilo. AJUSTA el ritmo y fluidez. NO cambies hechos.
VARIA longitud de frases. MEJORA transiciones.
ELIMINA: "sin duda", "cabe destacar", "es importante señalar", "en conclusion".
MANTEN tono Alejandro Ortega: humano, preciso, ironia fina.

TEXTO:
## {section[:3000]}
"""
        result = _llm(prompt, max_tokens=4096, temp=0.3, timeout=TIMEOUT_LIGHT)
        humanized_sections.append(result.replace("## ", ""))
        time.sleep(1)

    article_h = "\n## ".join(humanized_sections)
    hash_after = _extract_factual_core(article_h)
    if hash_before != hash_after:
        log("   ⚠️ Humanisation a modifié les faits — rejeté, original conservé")
        return article
    log(f"   ✅ Humanisation OK (hash factuel préservé)")
    return article_h


# ─── ORCHESTRATEUR ACTE 1 ───────────────────────────────────
def run(topic: dict, date_str: str = DATE) -> bool:
    log("═══ ACTE 1: Article ES 10K+ mots (H2 par H2) ═══")

    # 1a. DeepSearch
    context = deepsearch(topic, date_str)

    # 1b. Planification
    plan = plan_article(topic, context)

    # 1c. Génération H2 par H2
    article = generate_article_h2_by_h2(topic, plan, context)
    if len(article) < 5000:
        log(f"❌ Article trop court ({len(article)} chars)")
        return False

    # 1d. FastCheck (avec retry)
    score = 0
    original_article = article  # garde-fou anti-troncature de la correction ciblée
    for attempt in range(1, 4):
        passed, score, feedback = fastcheck_es(article)
        if passed:
            break
        if attempt == 3:
            log(f"❌ FastCheck échoué après 3 tentatives (score {score}/10)")
            return False
        if score >= 5:
            log(f"   🔄 Correction ciblée par section (tentative {attempt+1})...")
            # Règle anti-méta-discours (07/08/2026, bug n°1) : la 1-passe entière
            # avec max_tokens=8192 tronquait l'article à 2 sections et injectait
            # "He revisado el artículo..." dans le contenu publié. Correction :
            # (a) ne JAMAIS renvoyer de préface/méta-texte, (b) traiter chaque H2
            # séparément avec un budget suffisant, (c) garde-fou longueur finale.
            fix_preamble = (
                "Eres un corrector editorial. CORRIGE los errores indicados SIN redactar "
                "prefacio ni introducción alguna. Devuelve SOLO el texto corregido, sin "
                "\"He revisado\", sin \"A continuación\", sin meta-comentarios. Conserva "
                "TODOS los encabezados H2 y su orden exactos. No añadas ni elimines secciones.\n"
            )
            corrected_sections = []
            # Découper l'article en sections H2 pour corriger chaque bloc isolément
            sections = re.split(r'(\n##\s+[^\n]+\n)', article)
            for seg in sections:
                if seg.startswith('\n## '):  # en-tête H2 → à conserver tel quel
                    corrected_sections.append(seg)
                    continue
                body = seg.strip()
                if len(body) < 200:  # lead très court / séparateur → conserver
                    corrected_sections.append(seg)
                    continue
                if body:
                    sp = (
                        f"{fix_preamble}PROBLEMAS (solo aplica los relevantes a este fragmento):\n"
                        f"{feedback}\n\nFRAGMENTO A CORREGIR:\n{body[:8000]}\n"
                    )
                    fixed = _llm(sp, model=MODEL_HEAVY, max_tokens=9000, temp=0.2, timeout=300)
                    corrected_sections.append(f"\n{fixed.strip()}\n")
            article = "".join(corrected_sections) if corrected_sections else article
            # Garde-fou anti-troncature : si la correction a raccourci sous 10000 mots,
            # revenir à l'original et laisser le verdict au re-fastcheck (ou rejeter).
            if len(article.split()) < 10000:
                log(f"   ⚠️ Correction ciblée a tronqué l'article ({len(article.split())} mots) — original conservé")
                article = original_article
        else:
            log(f"❌ Score < 5/10 — article rejeté")
            return False

    # 1e. Humanisation
    article = humanize_es(article)

    # Sauvegarde finale
    total_words = len(article.split())
    h2_count = len(re.findall(r'^##\s+', article, re.MULTILINE))
    save_step("act1_es_validated", {
        "article_es": article,
        "title_es": plan.get("title", topic["title"]),
        "date": date_str,
        "fastcheck_score": score,
        "word_count": total_words,
        "h2_count": h2_count,
        "hash": _extract_factual_core(article),
    })

    log(f"✅ ACTE 1 TERMINÉ — {total_words} mots, {h2_count} sections H2, score {score}/10")
    return True


if __name__ == "__main__":
    cat_id = sys.argv[1] if len(sys.argv) > 1 else "047d7527-d161-4c25-a948-3e6f88aa8a9e"
    topic_override = None
    if "--topic" in sys.argv:
        idx = sys.argv.index("--topic")
        topic_override = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

    topic = {
        "id": cat_id,
        "title": topic_override or "Gastronomía y vino de la Costa Tropical",
        "domain": "gastronomía",
        "angle": "productos locales, denominaciones de origen y enoturismo",
    }
    success = run(topic)
    sys.exit(0 if success else 1)
