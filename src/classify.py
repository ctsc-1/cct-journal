"""classify.py — Classification de profondeur du sujet."""

def classify_topic(topic: dict):
    """Classifie le sujet et retourne (level, target_words, reason)."""
    # Par défaut : article standard 4000 mots
    return ("article", 4000, "Classification par défaut (module minimal)")
