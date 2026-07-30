#!/usr/bin/env python3
"""
app.py — Entrypoint de transition pour le service systemd cct-journal.
Délègue au nouveau pipeline 3 actes (run_pipeline.py).

Transition 30/07/2026: remplace l'ancien pipeline monolithique par
l'architecture 3 actes avec FastCheck + Humanisation au fil de l'eau.
"""
import sys
import os

# Ajouter le répertoire courant au path pour les imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_pipeline import run as run_pipeline

if __name__ == "__main__":
    # Le service systemd passe la catégorie en argument ou utilise la catégorie du jour
    # Pour l'instant: utiliser le rotor existant pour choisir la catégorie
    try:
        from rotor import select_topic
        topic = select_topic()
        category_id = topic.get("id", "047d7527-d161-4c25-a948-3e6f88aa8a9e")
        topic_title = topic.get("title", "Actualité de la Costa Tropical")
    except Exception:
        category_id = "047d7527-d161-4c25-a948-3e6f88aa8a9e"
        topic_title = "Actualité de la Costa Tropical"

    success = run_pipeline(category_id, topic_title)
    sys.exit(0 if success else 1)
