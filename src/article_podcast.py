"""article_podcast.py — Podcast monologue pour les articles du Journal CCT.

Génère un script radio adapté (pas une lecture mot à mot) avec une seule voix
féminine (Sofía/Leda), convertit en MP3 via Gemini TTS, et stocke l'URL
dans articles.audio_url.

Flux :
1. Reçoit le texte de l'article (ES) + slug + titre
2. Appelle Gateway pour générer un script radio adapté (~2-3 min)
3. Convertit le script en audio via Gemini TTS (voix Leda)
4. Sauvegarde MP3 et met à jour audio_url en DB
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Dict

import httpx
import psycopg2
import sys; sys.path.insert(0, "/srv/rag-engine")
from pipeline.model_env import get_model

logger = logging.getLogger("cct-journal.podcast")

# ─── Chemins ─────────────────────────────────────────────────────────────
RAG_ENGINE_PATH = "/srv/rag-engine"
if RAG_ENGINE_PATH not in sys.path:
    sys.path.insert(0, RAG_ENGINE_PATH)

STATIC_AUDIO_DIR = "/srv/rag-engine/static/audio/articles"
os.makedirs(STATIC_AUDIO_DIR, exist_ok=True)

# ─── Gateway ─────────────────────────────────────────────────────────────
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")

# ─── Prompts du script radio par langue ─────────────────────────────────

SYSTEM_PROMPTS = {
    "es": (
        "Eres **Sofía**, periodista y redactora del Club Costa Tropical. "
        "Eres la voz diaria de la Costa Tropical — cercana, profesional, con acento andaluz sutil. "
        "Tu tono es el de una amiga culta que cuenta una historia fascinante, no el de una locutora de radio leyendo un boletín.\n\n"
        "REGLAS ABSOLUTAS:\n"
        "1. Escribe un **monólogo radiofónico** para ti sola (una voz, femenina, Sofía).\n"
        "2. **NO es una lectura del artículo**. Es una adaptación oral : más corta, más conversacional, con frases más breves.\n"
        "3. Duración: **2-3 minutos** (250-400 palabras).\n"
        "4. Estructura:\n"
        "   - SALUDO (15s): 'Hola, soy Sofía, y esto es el Club Costa Tropical...' + frase d'accroche\n"
        "   - RELATO (90-150s): cuenta el tema con gancho, datos concretos, lugares reales. Sin enumeraciones. Con alma.\n"
        "   - CIERRE (15s): 'Hasta mañana, y recuerda: la Costa Tropical te espera.'\n"
        "5. Usa frases cortas (<20 palabras cada una). Ritmo vivo. Pausas naturales.\n"
        "6. Menciona lugares reales (Motril, Almuñécar, Salobreña, La Herradura...) cuando el artículo los cite.\n"
        "7. **Prohibido** : leer cifras secas, enumerar datos, lenguaje administrativo.\n"
        "8. **Prohibido** : usar [HOST], [GUEST], o cualquier etiqueta de diálogo.\n"
        "9. **Prohibido** : incluir indicaciones técnicas (música, efectos, tono).\n"
        "10. El texto debe ser ORAL, no escrito. Como si se lo contaras a un amigo en una terraza.\n"
        "11. **Termina siempre** con el cierre (punto 3). No cortes el texto.\n"
        "12. NO hagas referencia a que es una 'grabación', un 'podcast' o un 'programa'. Es Sofía que HABLA.\n"
        "13. Escribe ÚNICAMENTE en español. Ni una palabra en otro idioma.\n"
        "Entrega SOLO el texto del monólogo, sin introducción, sin notas, sin metadatos."
    ),
    "fr": (
        "Tu es **Sofía**, journaliste et rédactrice du Club Costa Tropical. "
        "Tu es la voix quotidienne de la Costa Tropical — proche, professionnelle, chaleureuse. "
        "Ton ton est celui d'une amie cultivée qui raconte une histoire fascinante.\n\n"
        "RÈGLES ABSOLUES :\n"
        "1. Écris un **monologue radiophonique** pour toi seule (une voix, féminine, Sofía).\n"
        "2. **CE N'EST PAS une lecture de l'article**. C'est une adaptation orale : plus courte, plus conversationnelle.\n"
        "3. Durée : **2-3 minutes** (250-400 mots).\n"
        "4. Structure :\n"
        "   - ACCROCHE (15s) : 'Bonjour, je suis Sofía, et voici le Club Costa Tropical...' + phrase d'accroche\n"
        "   - RÉCIT (90-150s) : raconte le sujet avec des détails concrets, des lieux réels. Pas d'énumérations. Avec de l'âme.\n"
        "   - CONCLUSION (15s) : 'À demain, et souvenez-vous : la Costa Tropical vous attend.'\n"
        "5. Phrases courtes (<20 mots chacune). Rythme vivant. Pauses naturelles.\n"
        "6. Mentionne les lieux réels (Motril, Almuñécar, Salobreña, La Herradura...) quand l'article les cite.\n"
        "7. **Interdit** : lire des chiffres secs, énumérer des données, langage administratif.\n"
        "8. **Interdit** : utiliser [HOST], [GUEST] ou toute balise de dialogue.\n"
        "9. **Interdit** : inclure des indications techniques (musique, effets, ton).\n"
        "10. Le texte doit être ORAL, pas écrit. Comme si tu racontais à un ami en terrasse.\n"
        "11. **Termine toujours** par la conclusion (point 3). Ne coupe pas le texte.\n"
        "12. Ne fais PAS référence à un 'enregistrement', un 'podcast' ou une 'émission'. C'est Sofía qui PARLE.\n"
        "13. Écris UNIQUEMENT en français. Pas un mot dans une autre langue.\n"
        "Livrer UNIQUEMENT le texte du monologue, sans introduction, sans notes, sans métadonnées."
    ),
    "en": (
        "You are **Sofía**, journalist and writer for the Club Costa Tropical. "
        "You are the daily voice of the Costa Tropical — warm, professional, with a subtle Andalusian accent. "
        "Your tone is that of a cultured friend telling a fascinating story.\n\n"
        "ABSOLUTE RULES:\n"
        "1. Write a **radio monologue** for yourself alone (one voice, female, Sofía).\n"
        "2. **This is NOT a reading of the article**. It's an oral adaptation: shorter, more conversational.\n"
        "3. Duration: **2-3 minutes** (250-400 words).\n"
        "4. Structure:\n"
        "   - HOOK (15s): 'Hello, I'm Sofía, and this is the Club Costa Tropical...' + hook phrase\n"
        "   - STORY (90-150s): tell the subject with compelling details, real places. No lists. With soul.\n"
        "   - CLOSING (15s): 'See you tomorrow, and remember: the Costa Tropical awaits you.'\n"
        "5. Short sentences (<20 words each). Lively rhythm. Natural pauses.\n"
        "6. Mention real places (Motril, Almuñécar, Salobreña, La Herradura...) when the article cites them.\n"
        "7. **Forbidden**: reading dry figures, listing data, administrative language.\n"
        "8. **Forbidden**: using [HOST], [GUEST], or any dialogue tags.\n"
        "9. **Forbidden**: including technical directions (music, effects, tone).\n"
        "10. The text must be ORAL, not written. Like telling a friend on a terrace.\n"
        "11. **Always finish** with the closing (point 3). Don't cut the text short.\n"
        "12. Do NOT refer to a 'recording', 'podcast' or 'show'. Sofía is SPEAKING.\n"
        "13. Write ONLY in English. Not a word in another language.\n"
        "Deliver ONLY the monologue text, no introduction, no notes, no metadata."
    ),
}

def _get_system_prompt(lang: str = "es") -> str:
    """Retourne le prompt système adapté à la langue."""
    return SYSTEM_PROMPTS.get(lang, SYSTEM_PROMPTS["es"])

def _get_pg_url() -> str:
    """Récupère DATABASE_URL comme publish.py."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            import subprocess as _sp
            r = _sp.run(
                ["grep", "^DATABASE_URL=", "/srv/rag-engine/.env"],
                capture_output=True, text=True, timeout=5
            )
            line = r.stdout.strip()
            if line:
                db_url = line.split("=", 1)[1].strip("'\"")
        except Exception:
            pass
    if not db_url:
        db_url = "postgresql:///alejandro_db"
    return db_url


USER_PROMPTS = {
    "es": "Escribe un monólogo radiofónico de Sofía (2-3 minutos) sobre este tema.\nNO leas el artículo. Cuéntalo. Como si hablaras con un amigo.",
    "fr": "Écris un monologue radiophonique de Sofía (2-3 minutes) sur ce sujet.\nNe lis PAS l'article. Raconte-le. Comme si tu parlais avec un ami.",
    "en": "Write a Sofía radio monologue (2-3 minutes) about this topic.\nDo NOT read the article. Tell it. Like talking to a friend.",
}

def _build_prompt(article_text: str, title: str, lang: str = "es") -> str:
    """Construit le prompt utilisateur pour générer le script radio dans la langue demandée."""
    text = article_text[:5000]
    label_titre = {"es": "TÍTULO DEL ARTÍCULO", "fr": "TITRE DE L'ARTICLE", "en": "ARTICLE TITLE"}
    label_texte = {"es": "TEXTO DEL ARTÍCULO", "fr": "TEXTE DE L'ARTICLE", "en": "ARTICLE TEXT"}
    lt = label_titre.get(lang, "TÍTULO DEL ARTÍCULO")
    lx = label_texte.get(lang, "TEXTO DEL ARTÍCULO")
    instruction = USER_PROMPTS.get(lang, USER_PROMPTS["es"])
    return (
        f"{lt}: {title}\n\n"
        f"{lx}:\n{text}\n\n---\n\n"
        f"{instruction}"
    )


def _generate_script(text: str, title: str, lang: str = "es") -> Optional[str]:
    """Génère le script radio via Gateway LLM (gemini-2.5-flash, gratuit) dans la langue demandée."""
    system_prompt = _get_system_prompt(lang)
    user_prompt = _build_prompt(text, title, lang)
    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/generate",
            json={
                "model": get_model("TTS", "gemini-3.1-flash-tts-preview"),
                "contents": f"{system_prompt}\n\n{user_prompt}",
                "caller": "cct-journal-podcast",
            },
            timeout=120,
        )
        r.raise_for_status()
        script = r.json().get("text", "").strip()
        if script and len(script) > 50:
            logger.info(f"📝 Script podcast: {len(script)} chars ({len(script.split())} mots)")
            return script
        logger.warning(f"⚠️ Script vide ou trop court ({len(script or '')}c)")
        return None
    except Exception as e:
        logger.error(f"❌ Script generation error: {e}")
        return None


def _tts(text: str, voice: str = "Leda") -> Optional[bytes]:
    """Appelle le module TTS unifié (MCP quotas + fallback automatique)."""
    import asyncio
    from pipeline.tts_unified import tts as unified_tts
    try:
        return asyncio.get_event_loop().run_until_complete(
            unified_tts(text, voice, api_key_path="/etc/cct-journal/gemini.key")
        )
    except RuntimeError:
        # Pas d'event loop → en créer une
        return asyncio.run(unified_tts(text, voice, api_key_path="/etc/cct-journal/gemini.key"))
    except Exception as e:
        logger.error(f"❌ TTS unified error: {e}")
        return None


def _pcm_to_mp3(pcm_data: bytes) -> Optional[str]:
    """Convertit le PCM brut en MP3 via ffmpeg. Retourne le chemin du fichier temp."""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
             "-i", "-", "-c:a", "libmp3lame", "-b:a", "64k", tmp_path],
            input=pcm_data, capture_output=True, timeout=30,
        )
        if os.path.getsize(tmp_path) > 1000:
            return tmp_path
        logger.warning(f"⚠️ MP3 trop petit: {os.path.getsize(tmp_path or 0)}B")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return None
    except Exception as e:
        logger.error(f"❌ PCM→MP3 error: {e}")
        return None


def _save_audio_url(slug: str, audio_url: str):
    """Met à jour articles.audio_url dans la DB."""
    try:
        conn = psycopg2.connect(_get_pg_url(), connect_timeout=5)
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET audio_url = %s, updated_at = NOW() "
                "WHERE slug = %s",
                (audio_url, slug)
            )
            if cur.rowcount > 0:
                logger.info(f"💾 audio_url saved: {audio_url}")
            else:
                logger.warning(f"⚠️ Slug '{slug}' introuvable dans articles")
        conn.close()
    except Exception as e:
        logger.error(f"❌ DB update error: {e}")


def generate_article_podcast(article_text: str, title: str, slug: str,
                             lang: str = "es") -> Optional[Dict]:
    """Point d'entrée principal. Génère le podcast pour un article.

    Args:
        article_text: Texte complet de l'article (ES, markdown)
        title: Titre de l'article
        slug: Slug unique de l'article
        lang: Langue (es par défaut)

    Returns:
        {"url": str, "size": int, "duration_s": float} ou None si échec
    """
    logger.info(f"🎙️ Génération podcast pour '{title[:60]}'")

    if not article_text or len(article_text) < 200:
        logger.warning(f"⚠️ Texte trop court ({len(article_text or '')}c) — podcast ignoré")
        return None

    # 1. Générer le script radio dans la langue demandée
    script = _generate_script(article_text, title, lang)
    if not script:
        logger.warning("⚠️ Aucun script généré — podcast abandonné")
        return None

    # 2. TTS du script complet
    logger.info("🔊 TTS...")
    pcm = _tts(script, voice="Leda")
    if not pcm or len(pcm) < 100:
        logger.warning(f"⚠️ PCM vide ({len(pcm or b'')}B)")
        return None

    # 3. PCM → MP3
    tmp_mp3 = _pcm_to_mp3(pcm)
    if not tmp_mp3:
        return None

    # 4. Normaliser le volume
    filename = f"{slug}-{lang}.mp3"
    output_path = os.path.join(STATIC_AUDIO_DIR, filename)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_mp3,
             "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
             "-c:a", "libmp3lame", "-b:a", "64k", output_path],
            capture_output=True, timeout=30,
        )
    except Exception as e:
        logger.error(f"❌ Normalisation: {e}")
        try:
            os.unlink(tmp_mp3)
        except Exception:
            pass
        return None
    finally:
        try:
            os.unlink(tmp_mp3)
        except Exception:
            pass

    if not os.path.exists(output_path) or os.path.getsize(output_path) < 2000:
        logger.warning(f"⚠️ Fichier final invalide")
        return None

    size_kb = os.path.getsize(output_path) // 1024
    # Durée approximative (script ~200 mots/min, ~chars/sec)
    duration_s = len(script) / 15  # ~15 chars/sec pour du parlé
    url = f"/api/static/audio/articles/{filename}"

    logger.info(f"✅ Podcast: {filename} ({size_kb}KB, ~{duration_s:.0f}s)")

    # 5. Sauvegarder l'URL en DB (uniquement pour l'ES — langue source)
    # FR/EN suivent la convention de nommage {slug}-{lang}.mp3
    if lang == "es":
        _save_audio_url(slug, url)

    return {
        "url": url,
        "size": size_kb,
        "duration_s": int(duration_s),
        "script_len": len(script),
    }
