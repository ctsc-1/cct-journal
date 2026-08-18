#!/usr/bin/env python3
"""
fact_check_es.py — Vérification factuelle des articles ES du Journal CCT.
Détecte les hallucinations sur les entités nommées (maires, lieux, géographie).

Usage:
    from fact_check_es import fact_check
    alerts = fact_check(article_text)
    if alerts:
        for a in alerts:
            print(f"[{a['type']}] {a['entite']}: trouvé '{a['valeur_trouvee']}', attendu '{a['valeur_attendue']}'")

RÈGLE MARC: stdlib only, pas d'appel LLM ni de dépendances externes.
"""

from __future__ import annotations

import re
from typing import Any

# ─── BASE DE CONNAISSANCE LOCALE ──────────────────────────────
# Sources vérifiées : BOJA, sites municipaux, Mancomunidad (août 2026)

ALCALDES: dict[str, dict[str, str]] = {
    "motril": {
        "nom_complet": "Luisa García Chamorro",
        "partido": "PP",
        "nota": "Réélue en 2023. Aucun 'José García' n'est ou n'a été maire de Motril.",
    },
    "salobreña": {
        "nom_complet": "Julián Lozano",
        "partido": "PSOE",
        "nota": "",
    },
    "almuñécar": {
        "nom_complet": "Juan José Ruiz Joya",
        "partido": "PP",
        "nota": "",
    },
    "granada": {
        "nom_complet": "Marifrán Carazo",
        "partido": "PP",
        "nota": "Alcaldesa de Granada capital.",
    },
}

PRESIDENTES_MANCOMUNIDAD: dict[str, str] = {
    "nombre": "Rafael Caballero Jiménez",
    "nota": "Président de la Mancomunidad de Municipios de la Costa Tropical.",
}

PLAYAS: dict[str, dict[str, Any]] = {
    "tesorillo": {
        "municipio": "Almuñécar",
        "nota": "Playa de El Tesorillo (Taramay/Velilla) — PAS Salobreña.",
        "secteur": "Taramay / Velilla",
        "confusions_communes": ["Salobreña", "salobrena"],
    },
    "charca": {
        "municipio": "Salobreña",
        "nota": "Playa de la Charca / Salomar.",
    },
}

LUGARES: dict[str, dict[str, str]] = {
    "peña_escrita": {
        "municipio": "Almuñécar / Otívar",
        "nota": "Massif montagneux à plusieurs dizaines de km de Salobreña — NON visible depuis la Plaza del Pilar au cœur du vieux Salobreña.",
    },
    "criée": {
        "municipio": "Motril",
        "nota": "La SEULE criée (lonja) de toute la Costa Tropical est au Port de Motril. Aucune criée à Marina del Este / La Herradura.",
    },
    "marina_del_este": {
        "municipio": "Almuñécar (Punta de la Mona)",
        "nota": "Port de plaisance uniquement. Pas de criée aux poissons.",
    },
    "feria_motril": {
        "lugares_reales": ["Cortijo del Conde", "Parque de los Pueblos de América", "Plaza de Toros"],
        "nota": "Les grands concerts de feria à Motril ne se tiennent PAS sur une 'place de la Liberté' (inexistante).",
    },
}

# ─── PATTERNS DE DÉTECTION ───────────────────────────────────

def _extraire_noms_propres(text: str) -> list[tuple[str, int]]:
    """Extrait les noms propres (prénom + nom) avec leur position."""
    # Patrons comme "José García", "Luisa García Chamorro", "Rafael Cabrera"
    patron_noms = re.finditer(
        r'(?:[Dd]on\s+)?(?:[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)+)(?:\s+(?:[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+))?',
        text,
    )
    resultats = [(m.group(), m.start()) for m in patron_noms]
    return resultats


def _extraire_contextes_maires(text: str) -> list[dict]:
    """Extrait les mentions potentielles de maires avec leur contexte."""
    patterns = [
        # Pattern 1: "alcalde de Motril, José García" (ville AVANT, nom APRÈS virgule)
        # Group 1 = ville (lowercase), Group 2 = nom complet
        r'(?:maire|alcalde|alcaldesa|alcadesa)\s+(?:de\s+)?([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\s*[,;:]?\s*([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)+)',
        # Pattern 2: "alcalde José García de Motril" (nom AVANT, ville APRÈS "de")
        r'(?:maire|alcalde|alcaldesa|alcadesa)\s+([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)+)\s*(?:,\s*)?(?:de\s+)([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)',
    ]
    resultats = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            groups = m.groups()
            # Pattern 1: groups[0]=ville (usually lowercase or single-word city), groups[1]=nom
            # Pattern 2: groups[0]=nom (multi-word, uppercase), groups[1]=ville
            # Heuristic: si groups[0] contient un espace → c'est un nom complet → Pattern 2
            # Si groups[0] est un mot simple → c'est une ville → Pattern 1
            if ' ' in groups[0].strip() or groups[0][0].isupper() and len(groups) >= 2 and ' ' not in groups[1].strip():
                # Pattern 2: nom d'abord, ville ensuite
                resultats.append({
                    "match": m.group(),
                    "municipio": groups[1].lower() if len(groups) >= 2 else "",
                    "nombre": groups[0],
                    "position": m.start(),
                })
            else:
                # Pattern 1: ville d'abord, nom ensuite
                resultats.append({
                    "match": m.group(),
                    "municipio": groups[0].lower(),
                    "nombre": groups[1],
                    "position": m.start(),
                })
    return resultats


def _extraire_contextes_presidentes(text: str) -> list[str]:
    """Extrait les mentions de président de la Mancomunidad, y compris le nom."""
    patterns = [
        # Pattern 1: "presidente de la Mancomunidad, Rafael Cabrera" (avec/sans virgule)
        r'(?:president|presidente|director)\s+(?:de\s+(?:la\s+)?)?(?:mancomunidad)\s*[,;:]?\s*[a-záéíóúüñ]*(?:\s+)?([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)+)',
        # Pattern 2: "mancomunidad ... cuyo presidente es Rafael Cabrera"
        r'(?:mancomunidad)\s*.{0,80}(?:president|presidente).{0,40}([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)+)',
        # Pattern 3: fallback — capture large context around "presidente ... mancomunidad"
        r'(?:president|presidente|director)\s+(?:de\s+(?:la\s+)?)?(?:mancomunidad|mancomunidad\s+de\s+municipios)',
    ]
    resultats = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            resultats.append(m.group())
    return resultats


def _extraire_playas(text: str) -> list[dict]:
    """Détecte les mentions de plages avec leur commune associée."""
    playas_connues = {
        "el tesorillo": "tesorillo",
        "tesorillo": "tesorillo",
        "la charca": "charca",
        "charca": "charca",
    }
    resultats = []
    for nom_plage, key in playas_connues.items():
        # Capture le mot après la plage, en sautant les prépositions (en, de, a, en)
        # Ex: "El Tesorillo, en Salobreña" -> Salobreña
        # Ex: "El Tesorillo (Salobreña)" -> Salobreña
        pattern = rf'{nom_plage}\s*[\(,;:]?\s*(?:(?:en|de|a|por|desde|hasta|entre)\s+)?([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)'
        for m in re.finditer(pattern, text, re.IGNORECASE):
            municipio_mentionne = m.group(1).lower() if m.lastindex and m.group(1) else ""
            # Filtre les mots qui ne sont clairement pas des noms de communes
            mots_stop = {"en", "de", "a", "el", "la", "los", "las", "con", "por", "entre", "del", "que", "una", "un", "y"}
            if municipio_mentionne in mots_stop:
                # Si le mot capturé est une préposition, cherche le mot suivant
                reste = text[m.end():].strip()
                m2 = re.match(r'([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)', reste)
                if m2:
                    municipio_mentionne = m2.group(1).lower()
            resultats.append({
                "playa": nom_plage,
                "key": key,
                "municipio_mentionne": municipio_mentionne,
                "match": m.group(),
            })
    return resultats


def _extraire_mentions_geographiques(text: str) -> list[dict]:
    """Détecte les mentions de lieux spécifiques (Peña Escrita, criée, etc.)."""
    resultats = []

    # Peña Escrita + Salobreña (dans les DEUX ordres)
    for m in re.finditer(
        r'(?:peña\s+escrita|pena\s+escrita).{0,100}(?:salobreña|salobrena)',
        text, re.IGNORECASE,
    ):
        resultats.append({
            "lieu": "peña_escrita",
            "contexte": m.group()[:120],
            "alerte": "Peña Escrita mentionnée avec Salobreña — ces deux lieux sont distants de +30 km",
        })
    # Ordre inverse: Salobreña ... Peña Escrita
    for m in re.finditer(
        r'(?:salobreña|salobrena).{0,100}(?:peña\s+escrita|pena\s+escrita)',
        text, re.IGNORECASE,
    ):
        resultats.append({
            "lieu": "peña_escrita",
            "contexte": m.group()[:120],
            "alerte": "Peña Escrita mentionnée avec Salobreña — ces deux lieux sont distants de +30 km",
        })

    # Criée / lonja / cofradía + La Herradura / Marina del Este
    for m in re.finditer(
        r'(?:cri[eé]e|lonja|subasta|cofrad[íi]a|d[áa]rsena|pescadores).{0,80}(?:herradura|marina\s+del\s+este)',
        text, re.IGNORECASE,
    ):
        resultats.append({
            "lieu": "criée_herradura",
            "contexte": m.group()[:120],
            "alerte": "Aucune criée aux poissons à La Herradura/Marina del Este — la seule criée est au Port de Motril",
        })

    # Place de la Liberté à Motril
    for m in re.finditer(r'(?:place|plaza)\s+(?:de\s+)?(?:la\s+)?[Ll]ibertad\s*(?:,|\.|\s)', text):
        resultats.append({
            "lieu": "place_libertad_motril",
            "contexte": m.group()[:120],
            "alerte": "'Place de la Liberté' n'existe pas à Motril — les concerts se tiennent au Cortijo del Conde ou au Parque de los Pueblos de América",
        })

    return resultats


def _extraire_dates(text: str, publish_year: int = 2026) -> list[dict]:
    """Vérifie la cohérence temporelle des dates mentionnées."""
    resultats = []

    # Détecte les années dans le texte
    for m in re.finditer(r'(20[0-9]{2})', text):
        annee = int(m.group(1))
        # Contexte autour de l'année
        debut = max(0, m.start() - 60)
        fin = min(len(text), m.end() + 60)
        contexte = text[debut:fin]

        # Si l'année est dans le passé (>= 2 ans avant publish_year), vérifie le contexte
        if annee < publish_year - 1:
            # Vérifie si l'année est présentée comme l'année en cours/actuelle
            # (pas un flashback historique légitime)
            patterns_actuel = [
                r'cet(?:te)?\s+(?:année|año|temporada|verano)',
                r'este\s+(?:año|verano|temporada)',
                r'la\s+(?:temporada|alta\s+temporada|próxima\s+temporada)',
                r'en\s+esta\s+(?:temporada|época)',
                r'actual\s+(?:temporada|año)',
                r'cours\s+de\s+cette',
                r'durante\s+el\s+verano',
                r'de\s+la\s+high\s+season',
                r'de\s+la\s+alta\s+temporada',
            ]
            est_actuel = any(
                re.search(p, contexte, re.IGNORECASE) for p in patterns_actuel
            )
            if est_actuel:
                resultats.append({
                    "type": "date_anachronique",
                    "match": m.group(),
                    "contexte": contexte[:120],
                    "annee": annee,
                    "suggestion": f"L'article est en {publish_year} mais mentionne '{contexte[:80]}' comme actuel — vérifier si c'est un flashback ou une erreur",
                })

    # "cette année 2025", "temporada 2025", "verano 2025" directement
    for m in re.finditer(
        r'(?:cet(?:te)?\s+(?:année|año|year|temporada|verano)|'
        r'este\s+(?:año|verano|temporada)|'
        r'la\s+(?:temporada|alta\s+temporada)|'
        r'high\s+season)\s+(?:de\s+)?(20[0-9]{2})',
        text, re.IGNORECASE,
    ):
        annee = int(m.group(1))
        if annee != publish_year:
            resultats.append({
                "type": "date_anachronique",
                "match": m.group()[:80],
                "contexte": m.group()[:120],
                "annee": annee,
                "suggestion": f"L'article mentionne '{m.group()[:60]}' mais on est en {publish_year} — corriger en {publish_year} sauf flashback explicite",
            })

    # "l'année prochaine" devrait être publish_year + 1
    for m in re.finditer(
        r'(?:l\'(?:année|an)\s+(?:prochaine|qui\s+vient|suivant)|'
        r'el\s+(?:año\s+)?(?:próximo|que\s+viene|siguiente)|'
        r'next\s+year)',
        text, re.IGNORECASE,
    ):
        debut = max(0, m.start() - 40)
        fin = min(len(text), m.end() + 40)
        contexte = text[debut:fin]
        annee_match = re.search(r'(20[0-9]{2})', contexte)
        if annee_match:
            annee = int(annee_match.group(1))
            if annee != publish_year + 1:
                resultats.append({
                    "type": "date_anachronique",
                    "match": m.group()[:60],
                    "contexte": contexte[:120],
                    "annee": annee,
                    "suggestion": f"Projection future incohérente: 'l'année prochaine' devrait être {publish_year + 1}",
                })

    return resultats


# ─── FONCTION PRINCIPALE DE VÉRIFICATION ────────────────────

def fact_check(text: str, publish_year: int = 2026) -> list[dict]:
    """
    Vérifie les faits dans un article ES.
    Retourne une liste d'alertes, vide si tout est OK.

    Chaque alerte est un dict:
        type: str — catégorie (maire, president, geographie, date)
        entite: str — ce qui a été vérifié
        valeur_trouvee: str — ce que l'article dit
        valeur_attendue: str — la vérité
        suggestion: str — action corrective proposée
    """
    alerts: list[dict] = []
    text_lower = text.lower()

    # ── 1. VÉRIFICATION DES MAIRES ──
    mentions_maires = _extraire_contextes_maires(text)
    for mention in mentions_maires:
        municipio = mention.get("municipio", "")
        nombre_mentionne = mention["nombre"]

        for ville, info in ALCALDES.items():
            if ville in municipio or municipio in ville:
                alcalde_reel = info["nom_complet"]
                # Vérifie si le nom mentionné correspond
                nom_normalise = nombre_mentionne.lower().replace("á", "a").replace("é", "e").replace("í", "i")
                reel_normalise = alcalde_reel.lower().replace("á", "a").replace("é", "e").replace("í", "i")
                # Extrait le prénom+premier nom du réel
                reel_parts = reel_normalise.split()
                if len(reel_parts) >= 2:
                    reel_court = f"{reel_parts[0]} {reel_parts[1]}"
                else:
                    reel_court = reel_normalise

                if nom_normalise != reel_normalise and nom_normalise != reel_court:
                    alerts.append({
                        "type": "maire",
                        "entite": f"Maire de {ville.capitalize()}",
                        "valeur_trouvee": nombre_mentionne,
                        "valeur_attendue": alcalde_reel,
                        "suggestion": f"Remplacer '{nombre_mentionne}' par '{alcalde_reel}' ({info['partido']})",
                    })

    # ── 2. VÉRIFICATION DU PRÉSIDENT DE LA MANCOMUNIDAD ──
    mentions_president = _extraire_contextes_presidentes(text)
    for mention in mentions_president:
        # Cherche un nom propre dans la mention
        m = re.search(
            r'([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)+)',
            mention,
        )
        if m:
            nom_president = m.group(1)
            # Ne pas confondre avec "Mancomunidad" ou "Costa Tropical" comme nom
            if nom_president.lower() in ("mancomunidad", "costa tropical", "municipios", "la costa tropical"):
                continue
            nom_normalise = nom_president.lower().replace("á", "a").replace("é", "e").replace("í", "i")
            reel = PRESIDENTES_MANCOMUNIDAD["nombre"]
            reel_normalise = reel.lower().replace("á", "a").replace("é", "e").replace("í", "i")

            if nom_normalise != reel_normalise:
                alerts.append({
                    "type": "president_mancomunidad",
                    "entite": "Président de la Mancomunidad Costa Tropical",
                    "valeur_trouvee": nom_president,
                    "valeur_attendue": reel,
                    "suggestion": f"Remplacer '{nom_president}' par '{reel}'",
                })

    # ── 3. VÉRIFICATION GÉOGRAPHIQUE : PLAGES ──
    mentions_playas = _extraire_playas(text)
    for p in mentions_playas:
        key = p["key"]
        ville_mentionnee = p.get("municipio_mentionne", "")
        info_playa = PLAYAS.get(key, {})
        vrai_municipio = info_playa.get("municipio", "").lower()

        # Vérifie si la plage est attribuée à la mauvaise commune
        confusions = info_playa.get("confusions_communes", [])
        for confusion in confusions:
            if confusion.lower() in ville_mentionnee or ville_mentionnee in confusion.lower():
                alerts.append({
                    "type": "geographie_playa",
                    "entite": f"Playa de {p['playa'].title()}",
                    "valeur_trouvee": f"Attribuée à {ville_mentionnee.title()}",
                    "valeur_attendue": f"Commune de {vrai_municipio.title()}",
                    "suggestion": f"'{p['playa'].title()}' est sur la commune de {vrai_municipio.title()}, pas {ville_mentionnee.title()}",
                })

    # ── 4. VÉRIFICATION GÉOGRAPHIQUE : LIEUX SPÉCIFIQUES ──
    for geo_alert in _extraire_mentions_geographiques(text):
        alerts.append({
            "type": "geographie_lieu",
            "entite": geo_alert.get("lieu", "lieu inconnu"),
            "valeur_trouvee": geo_alert.get("contexte", "?"),
            "valeur_attendue": "correction geographique",
            "suggestion": geo_alert.get("alerte", "verifier les coordonnees geographiques"),
        })

    # ── 5. VÉRIFICATION TEMPORELLE ──
    for date_alert in _extraire_dates(text, publish_year):
        alerts.append({
            "type": date_alert.get("type", "date_anachronique"),
            "entite": f"date {date_alert.get('annee', '?')}",
            "valeur_trouvee": date_alert.get("contexte", "?"),
            "valeur_attendue": f"coherence avec {publish_year}",
            "suggestion": date_alert.get("suggestion", "verifier la coherence temporelle"),
        })

    return alerts


def format_report(alerts: list[dict]) -> str:
    """Formate les alertes en texte lisible."""
    if not alerts:
        return "Vert Aucune anomalie factuelle detectee."

    lines = ["**Rapport de verification factuelle**"]
    for i, a in enumerate(alerts, 1):
        lines.append(
            f"{i}. [{a['type']}] {a['entite']} : "
            f"trouve '{a['valeur_trouvee']}', attendu '{a['valeur_attendue']}'. "
            f"Suggestion: {a['suggestion']}"
        )
    return "\n".join(lines)


# ─── TESTS (si exécuté directement) ──────────────────────────
if __name__ == "__main__":
    print("=== Test 1: Article défectueux (courte version) ===")
    test_text = """
    La Costa Tropical se enciende en agosto.
    El alcalde de Motril, José García, inauguró la temporada.
    El presidente de la Mancomunidad, Rafael Cabrera, destacó...
    La playa de El Tesorillo, en Salobreña, recibió...
    Un restaurante con vistas a la Peña Escrita desde la Plaza del Pilar en Salobreña.
    La cofradía de pescadores comparte dársena con embarcaciones de recreo en La Herradura.
    """

    alerts = fact_check(test_text)
    print(format_report(alerts))
    print(f"\nTotal alertes: {len(alerts)}")

    print("\n\n=== Test 2: Article réel 'la-costa-tropical-se-enciende-en-agosto' ===")
    real_text = """
    La Costa Tropical se enciende en agosto. La alta temporada 2025 ha superado todas las
    expectativas, según el alcalde de Motril, José García, quien inauguró la temporada estival
    en la playa de El Tesorillo, en Salobreña, acompañado del presidente de la Mancomunidad,
    Rafael Cabrera. Ambos coincidieron en que la afluencia turística ha sido excepcional.

    La cofradía de pescadores de La Herradura comparte dársena con embarcaciones de recreo,
    mientras que los puestos de la lonja de La Herradura registran cifras récord de ventas.
    Un restaurante con vistas a la Peña Escrita desde el casco antiguo de Salobreña ofrece
    una experiencia gastronómica única. La plaza de la Libertad de Motril acoge esta semana
    los conciertos de la feria.
    """

    real_alerts = fact_check(real_text)
    print(format_report(real_alerts))
    print(f"\nTotal alertes: {len(real_alerts)}")

    print("\n\n=== Test 3: Dates anachroniques ===")
    date_text = """
    La alta temporada 2025 ha superado todas las expectativas.
    Esta temporada 2025 ha sido la mejor de la década.
    El próximo año 2028 será aún mejor.
    Durante el verano de 2025, las playas registraron...
    """
    date_alerts = fact_check(date_text, publish_year=2026)
    print(format_report(date_alerts))
    print(f"Total alertes dates: {len(date_alerts)}")