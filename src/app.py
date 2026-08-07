#!/usr/bin/env python3
"""
app.py — Entrypoint de transition pour le service systemd cct-journal.
Délègue au nouveau pipeline 3 actes (run_pipeline.py).

Transition 30/07/2026: remplace l'ancien pipeline monolithique par
l'architecture 3 actes avec FastCheck + Humanisation au fil de l'eau.

Fix 04/08/2026 (ponytail): fallback DeepSeek direct quand Gateway 500.
"""
import sys
import os
import httpx

# Ajouter le répertoire courant au path pour les imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_pipeline import run as run_pipeline

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
FALLBACK_CATEGORY_ID = "047d7527-d161-4c25-a948-3e6f88aa8a9e"  # Alpujarra
FALLBACK_TOPIC = "Actualité de la Costa Tropical"


def _get_deepseek_key():
    import re
    try:
        with open("/root/.hermes/config.yaml", "r") as f:
            config = f.read()
        m = re.search(r'deepseek:\s*\n\s+api_key:\s*(\S+)', config)
        if m:
            return m.group(1)
    except Exception:
        pass
    return os.environ.get("DEEPSEEK_API_KEY", "")


def select_topic_fallback():
    """Essaie rotor.select_topic(), puis DeepSeek direct si Gateway down."""
    try:
        from rotor import select_topic
        topic = select_topic()
        # topic["category_id"] = UUID PostgreSQL, topic["id"] = identifiant domaine
        cat_id = topic.get("category_id", FALLBACK_CATEGORY_ID) if isinstance(topic, dict) else FALLBACK_CATEGORY_ID
        title = topic.get("title", FALLBACK_TOPIC) if isinstance(topic, dict) else FALLBACK_TOPIC
        return cat_id, title
    except Exception as e:
        reason = str(e)[:120]
        print(f"⚠️ Topic generation error: {reason}, trying DeepSeek fallback...")

        # Fallback: DeepSeek direct pour choisir une catégorie
        try:
            from rotor import CATEGORIES
            cats_list = "\n".join([f"- {c['name_es']} (id: {c['id']})" for c in CATEGORIES])
            key = _get_deepseek_key()
            r = httpx.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-v4-pro",
                    "messages": [{"role": "user", "content": f"""Choisis UNE catégorie d'article parmi cette liste pour le journal quotidien de la Costa Tropical aujourd'hui. 
Réponds UNIQUEMENT avec l'ID de la catégorie, rien d'autre.

Catégories:
{cats_list}

Catégorie la plus pertinente aujourd'hui:"""}],
                    "max_tokens": 80,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            r.raise_for_status()
            chosen = r.json()["choices"][0]["message"]["content"].strip()
            # Extraire l'UUID
            import re as _re
            m = _re.search(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', chosen)
            if m:
                print(f"✅ DeepSeek fallback — catégorie: {m.group(0)}")
                return m.group(0), FALLBACK_TOPIC
        except Exception as e2:
            print(f"⚠️ DeepSeek fallback aussi en échec: {e2}")

        return FALLBACK_CATEGORY_ID, FALLBACK_TOPIC


if __name__ == "__main__":
    category_id, topic_title = select_topic_fallback()
    print(f"📋 Catégorie: {topic_title}")
    success = run_pipeline(category_id, topic_title)
    sys.exit(0 if success else 1)