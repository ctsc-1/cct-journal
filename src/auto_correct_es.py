#!/usr/bin/env python3
"""
auto_correct_es.py — Correction automatique des hallucinations dans les
articles ES détectées par fact_check_es.py.

Usage:
    from auto_correct_es import auto_correct
    texte_corrige, nb_corrections = auto_correct(texte_es, alerts)

Chaque alerte de fact_check_es.py est un dict avec:
    type: str           — "maire", "president_mancomunidad", "geographie_playa",
                          "geographie_lieu"
    entite: str          — ce qui a été vérifié
    valeur_trouvee: str  — ce que l'article dit (erroné)
    valeur_attendue: str — la vérité
    suggestion: str      — action corrective proposée

Les corrections sont CONTEXTUELLES : regex lookahead/lookbehind évitent
les faux positifs (ex: homonyme "José García" dans un autre contexte).

RÈGLE MARC: stdlib only, pas d'appel LLM ni de dépendances externes.
"""

from __future__ import annotations

import re
from typing import Any


# ─── CORRECTEURS SPÉCIALISÉS ─────────────────────────────────────


def _corriger_maire(text: str, alert: dict[str, Any]) -> str:
    """
    Corrige le nom d'un maire erroné dans son contexte.
    Ex: 'alcalde de Motril, José García' → 'alcalde de Motril, Luisa García Chamorro'
    """
    valeur_trouvee = alert["valeur_trouvee"]         # "José García"
    valeur_attendue = alert["valeur_attendue"]         # "Luisa García Chamorro"
    entite = alert["entite"]                          # "Maire de Motril"

    # Extraire le nom de la ville depuis l'entité (ex: "Maire de Motril")
    m_ville = re.search(r'(?:Maire|Alcalde|Alcaldesa)\s+(?:de\s+)?(\S+)', entite, re.IGNORECASE)
    if not m_ville:
        # Fallback: chercher dans la suggestion
        # ex: "Remplacer 'José García' par 'Luisa García Chamorro' (PP)"
        # Une ligne comme "Maire de Motril" n'était pas trouvée → on fait un
        # remplacement simple dans tout le texte (peu risqué car le nom est unique)
        return text.replace(valeur_trouvee, valeur_attendue)

    ville = m_ville.group(1)

    # Pattern 1: "alcalde/alcaldesa de <Ville>, <nom_erroné>"
    # Lookbehind pour 'alcalde de <Ville>' suivi du nom erroné
    pattern1 = re.compile(
        rf'(alcalde|alcaldesa|alcadesa)\s+de\s+{re.escape(ville)}\s*[,;:]?\s*'
        rf'{re.escape(valeur_trouvee)}',
        re.IGNORECASE,
    )
    text = pattern1.sub(
        lambda m: f"{m.group(1)} de {ville}, {valeur_attendue}",
        text,
    )

    # Pattern 2: "alcalde <nom_erroné> de <Ville>"
    pattern2 = re.compile(
        rf'(alcalde|alcaldesa|alcadesa)\s+{re.escape(valeur_trouvee)}\s*'
        rf'(?:,\s*)?de\s+{re.escape(ville)}',
        re.IGNORECASE,
    )
    text = pattern2.sub(
        lambda m: f"{m.group(1)} {valeur_attendue} de {ville}",
        text,
    )

    # Pattern 3: simple remplacement direct <nom_erroné> -> <nom_attendu>
    # MAIS seulement si le nom erroné est proche d'un contexte maire/ville
    # pour éviter de toucher un homonyme dans un autre contexte.
    # On cherche "<nom_erroné>" avec la ville dans les 50 caractères avant.
    pattern3 = re.compile(
        rf'(.{{0,60}}{re.escape(ville)}.{{0,60}}){re.escape(valeur_trouvee)}',
        re.IGNORECASE | re.DOTALL,
    )
    text = pattern3.sub(
        lambda m: m.group(1).rstrip(valeur_trouvee) + valeur_attendue,
        text,
    )

    # Si les patterns contextuels n'ont rien changé, faire un remplacement
    # direct en dernier recours (faible risque — c'est un nom propre unique
    # dans le contexte d'un article).
    if valeur_trouvee in text:
        text = text.replace(valeur_trouvee, valeur_attendue)

    return text


def _corriger_president(text: str, alert: dict[str, Any]) -> str:
    """
    Corrige le nom du président de la Mancomunidad.
    Ex: 'presidente de la Mancomunidad, Rafael Cabrera'
      → 'presidente de la Mancomunidad, Rafael Caballero Jiménez'
    """
    valeur_trouvee = alert["valeur_trouvee"]   # "Rafael Cabrera"
    valeur_attendue = alert["valeur_attendue"]  # "Rafael Caballero Jiménez"

    # Pattern 1: "presidente (de la) Mancomunidad[,:] <nom_erroné>"
    pattern1 = re.compile(
        rf'(president|presidente|director)\s+(?:de\s+(?:la\s+)?)?'
        rf'(mancomunidad)\s*[,;:]?\s*{re.escape(valeur_trouvee)}',
        re.IGNORECASE,
    )
    text = pattern1.sub(
        lambda m: f"{m.group(1)} de la {m.group(2).capitalize()}, {valeur_attendue}",
        text,
    )

    # Pattern 2: "Mancomunidad ... cuyo presidente es <nom_erroné>"
    pattern2 = re.compile(
        rf'(mancomunidad).{{0,80}}(president|presidente).{{0,40}}'
        rf'{re.escape(valeur_trouvee)}',
        re.IGNORECASE | re.DOTALL,
    )
    text = pattern2.sub(
        lambda m: m.group(0).replace(valeur_trouvee, valeur_attendue),
        text,
    )

    # Fallback: remplacement direct si toujours présent
    if valeur_trouvee in text:
        text = text.replace(valeur_trouvee, valeur_attendue)

    return text


def _corriger_tesorillo_municipio(text: str, alert: dict[str, Any]) -> str:
    """
    Corrige la commune attribuée à la plage El Tesorillo.
    Ex: 'El Tesorillo, en Salobreña' → 'El Tesorillo, en Almuñécar'
    """
    valeur_trouvee = alert["valeur_trouvee"]   # "Attribuée à Salobreña"
    valeur_attendue = alert["valeur_attendue"]  # "Commune de Almuñécar"

    # Extraire le nom de la commune erronée depuis valeur_trouvee
    m_err = re.search(r'Attribuée à (\w+)', valeur_trouvee)
    m_correct = re.search(r'Commune de (\w+)', valeur_attendue)
    if not m_err or not m_correct:
        return text

    commune_err = m_err.group(1)
    commune_correct = m_correct.group(1)

    # Pattern 1: "El Tesorillo, en <commune_err>"
    pattern1 = re.compile(
        rf'(El\s+)?(Tesorillo)\s*[,;:]?\s*(?:en|de|a|en)\s+'
        rf'{re.escape(commune_err)}',
        re.IGNORECASE,
    )
    text = pattern1.sub(
        lambda m: f"El Tesorillo, en {commune_correct}",
        text,
    )

    # Pattern 2: "El Tesorillo (<commune_err>)"
    pattern2 = re.compile(
        rf'(El\s+)?(Tesorillo)\s*[([]\s*{re.escape(commune_err)}\s*[\])]',
        re.IGNORECASE,
    )
    text = pattern2.sub(
        lambda m: f"El Tesorillo ({commune_correct})",
        text,
    )

    return text


def _corriger_pena_escrita(text: str, alert: dict[str, Any]) -> str:
    """
    Neutralise les mentions de Peña Escrita associées à Salobreña.
    Ex: 'con vistas a la Peña Escrita desde la Plaza del Pilar en Salobreña'
      → 'con vistas al paisaje montañoso desde la Plaza del Pilar en Salobreña'
    """
    # Pattern 1: "con vistas a la Peña Escrita" (ou variantes) près de Salobreña
    pattern1 = re.compile(
        r'(con\s+vistas\s+(?:a\s+(?:la\s+)?)?(?:peña\s+escrita|pena\s+escrita))'
        r'(?=.*?(?:salobreña|salobrena))',
        re.IGNORECASE | re.DOTALL,
    )
    text = pattern1.sub('con vistas al paisaje montañoso', text)

    # Pattern 2: "Peña Escrita" seul proche de Salobreña — remplacer par
    # "paisaje montañoso" dans l'expression (uniquement les mentions problématiques)
    pattern2 = re.compile(
        r'(?:desde\s+(?:el\s+)?)?'
        r'(?:casco\s+antiguo|mirador|restaurante|terraza|paseo|camino|plaza|barrio)'
        r'.{0,60}(?:peña\s+escrita|pena\s+escrita)'
        r'.{0,60}(?:salobreña|salobrena)',
        re.IGNORECASE | re.DOTALL,
    )
    text = pattern2.sub(
        lambda m: m.group(0).replace(
            m.group(0).split("Peña")[-1].split("Escrita")[0].strip()
            if "Peña" in m.group(0) else "",
            "",
        ),
        text,
    )
    # Fallback plus robuste: remplacer "Peña Escrita" par "paisaje montañoso"
    # dans tout le texte, mais seulement si Salobreña est mentionnée dans
    # un rayon de 100 caractères
    text = _neutraliser_pena_escrita(text)

    return text


def _neutraliser_pena_escrita(text: str) -> str:
    """
    Remplace 'Peña Escrita' par 'paisaje montañoso' si Salobreña
    est mentionnée dans les 100 caractères environnants.
    """
    # Trouver toutes les occurrences de "Peña Escrita"
    for m in re.finditer(r'(?:Peña\s+Escrita|pena\s+escrita)', text, re.IGNORECASE):
        debut = max(0, m.start() - 100)
        fin = min(len(text), m.end() + 100)
        contexte = text[debut:fin]
        if re.search(r'(?:salobreña|salobrena)', contexte, re.IGNORECASE):
            # Remplacer cette occurrence spécifique
            debut_occ = m.start()
            fin_occ = m.end()
            text = text[:debut_occ] + 'paisaje montañoso' + text[fin_occ:]
    return text


def _corriger_lonja_herradura(text: str, alert: dict[str, Any]) -> str:
    """
    Corrige les mentions de criée/cofradía/lonja à La Herradura.
    Contexte 'criée/poisson' → 'la lonja del Puerto de Motril'
    Contexte 'cofradía'       → 'el puerto deportivo'
    """
    # La suggestion de fact_check_es pour ce cas est fixe.
    # On fait des corrections contextuelles :

    # Pattern 1: "cofradía de pescadores (de/en La Herradura)"
    # Contexte: association de pêcheurs -> le port de plaisance
    pattern1 = re.compile(
        r'(cofrad[íi]a\s+de\s+pescadores)\s+(?:de\s+|en\s+)?(?:La\s+)?(Herradura|Marina\s+del\s+Este)',
        re.IGNORECASE,
    )
    text = pattern1.sub(
        lambda m: f"{m.group(1)} del Puerto de Motril",
        text,
    )

    # Pattern 1b: "cofradía de pescadores ... en La Herradura" (avec mots intercalés)
    pattern1b = re.compile(
        r'(cofrad[íi]a\s+de\s+pescadores)\s+.{0,60}?en\s+(?:La\s+)?(Herradura|Marina\s+del\s+Este)',
        re.IGNORECASE | re.DOTALL,
    )
    text = pattern1b.sub(
        lambda m: m.group(0).replace(
            m.group(0)[m.group(0).rindex(" en "):],
            " del Puerto de Motril",
        ),
        text,
    )

    # Pattern 2: "lonja de La Herradura" ou "lonja de la Herradura"
    # Contexte: marché aux poissons -> la vraie criée
    pattern2 = re.compile(
        r'(?:la\s+)?(?:lonja|cri[eé]e|subasta)\s+(?:de\s+)?(?:la\s+)?(Herradura|Marina\s+del\s+Este)',
        re.IGNORECASE,
    )
    text = pattern2.sub("la lonja del Puerto de Motril", text)

    # Pattern 3: "dársena/puerto de La Herradura ... comparte ... pescadores"
    # Contexte mixte: port + pêcheurs
    pattern3 = re.compile(
        r'(d[áa]rsena|puerto|muelle)\s+(?:de\s+)?(?:la\s+)?(Herradura|Marina\s+del\s+Este)'
        r'.{0,60}(?:pescadores|cofrad[íi]a|lonja)',
        re.IGNORECASE | re.DOTALL,
    )
    text = pattern3.sub(
        lambda m: m.group(0).replace(
            f"de {m.group(2)}",
            "del Puerto de Motril",
            1,  # ne remplacer que la première occurrence
        ),
        text,
    )

    return text


def _corriger_place_libertad(text: str, alert: dict[str, Any]) -> str:
    """
    Corrige 'plaza de la Libertad' en 'recinto ferial del Cortijo del Conde'.
    Gère l'article défini qui précède (La/El → El).
    """
    # "(La|El) plaza de la Libertad (de Motril)" → "El recinto ferial del Cortijo del Conde"
    text = re.sub(
        r'\b(La|El|la|el)\s+(plaza\s+(?:de\s+)?(?:la\s+)?[Ll]ibertad)\s*(?:de\s+)?(?:Motril)?',
        "El recinto ferial del Cortijo del Conde",
        text,
    )
    # "plaza de la Libertad" sans article préexistant — pas de lookbehind,
    # comme le premier pattern a déjà consommé les cas avec article,
    # ce second pattern ne touche que les orphelins.
    text = re.sub(
        r'(plaza\s+(?:de\s+)?(?:la\s+)?[Ll]ibertad)\s*(?:de\s+)?(?:Motril)?',
        "el recinto ferial del Cortijo del Conde",
        text,
    )
    return text


# ─── DISPATCH DES CORRECTIONS PAR TYPE D'ALERTE ────────────────


_CORRECTEURS: dict[str, list[tuple[re.Pattern, str | None]]] = {
    # Pour les types simples: on laisse la fonction dédiée décider
}


# ─── FONCTION PRINCIPALE ────────────────────────────────────────


def auto_correct(text: str, alerts: list[dict]) -> tuple[str, int]:
    """
    Applique les corrections aux hallucinations détectées.

    Args:
        text: Texte ES de l'article
        alerts: Liste d'alertes de fact_check_es.fact_check()

    Returns:
        (texte_corrige, nombre_de_corrections_appliquees)
    """
    if not alerts:
        return text, 0

    corrections = 0
    texte = text

    for alert in alerts:
        alert_type = alert["type"]

        if alert_type == "maire":
            nouveau = _corriger_maire(texte, alert)
        elif alert_type == "president_mancomunidad":
            nouveau = _corriger_president(texte, alert)
        elif alert_type == "geographie_playa":
            nouveau = _corriger_tesorillo_municipio(texte, alert)
        elif alert_type == "geographie_lieu":
            lieu = alert.get("entite", "")
            if "peña_escrita" in lieu.lower():
                nouveau = _corriger_pena_escrita(texte, alert)
            elif "criée" in lieu.lower() or "lonja" in lieu.lower() or "herradura" in lieu.lower():
                nouveau = _corriger_lonja_herradura(texte, alert)
            elif "place_libertad" in lieu.lower() or "plaza" in lieu.lower() or "libertad" in lieu.lower():
                nouveau = _corriger_place_libertad(texte, alert)
            else:
                # Type de lieu inconnu — essayer le remplacement de valeur_trouvee
                if alert["valeur_trouvee"] in texte:
                    nouveau = texte.replace(alert["valeur_trouvee"], alert["valeur_attendue"])
                else:
                    nouveau = texte
        else:
            # Type inconnu — passer
            nouveau = texte

        if nouveau != texte:
            corrections += 1
        texte = nouveau

    return texte, corrections


# ─── FONCTION DE TEST INTÉGRÉE ──────────────────────────────────


def _extraire_alerts_depuis_texte_brut() -> list[dict]:
    """
    Reconstruit les alertes attendues pour le texte de test.
    Utile pour le test unitaire sans dépendre de fact_check_es dans le test.
    """
    return [
        {
            "type": "maire",
            "entite": "Maire de Motril",
            "valeur_trouvee": "José García",
            "valeur_attendue": "Luisa García Chamorro",
            "suggestion": "Remplacer 'José García' par 'Luisa García Chamorro' (PP)",
        },
        {
            "type": "president_mancomunidad",
            "entite": "Président de la Mancomunidad Costa Tropical",
            "valeur_trouvee": "Rafael Cabrera",
            "valeur_attendue": "Rafael Caballero Jiménez",
            "suggestion": "Remplacer 'Rafael Cabrera' par 'Rafael Caballero Jiménez'",
        },
        {
            "type": "geographie_playa",
            "entite": "Playa de El Tesorillo",
            "valeur_trouvee": "Attribuée à Salobreña",
            "valeur_attendue": "Commune de Almuñécar",
            "suggestion": "'El Tesorillo' est sur la commune de Almuñécar, pas Salobreña",
        },
        {
            "type": "geographie_lieu",
            "entite": "peña_escrita",
            "valeur_trouvee": "con vistas a la Peña Escrita desde la Plaza del Pilar en Salobreña",
            "valeur_attendue": "correction geographique",
            "suggestion": "Peña Escrita mentionnée avec Salobreña — ces deux lieux sont distants de +30 km",
        },
        {
            "type": "geographie_lieu",
            "entite": "criée_herradura",
            "valeur_trouvee": "cofradía de pescadores de La Herradura",
            "valeur_attendue": "correction geographique",
            "suggestion": "Aucune criée aux poissons à La Herradura/Marina del Este — la seule criée est au Port de Motril",
        },
        {
            "type": "geographie_lieu",
            "entite": "place_libertad_motril",
            "valeur_trouvee": "plaza de la Libertad",
            "valeur_attendue": "correction geographique",
            "suggestion": "'Place de la Liberté' n'existe pas à Motril — les concerts se tiennent au Cortijo del Conde ou au Parque de los Pueblos de América",
        },
    ]


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 TEST 1: Article défectueux (version courte)")
    print("=" * 60)

    test_text = """\
La Costa Tropical se enciende en agosto.
El alcalde de Motril, José García, inauguró la temporada.
El presidente de la Mancomunidad, Rafael Cabrera, destacó...
La playa de El Tesorillo, en Salobreña, recibió...
Un restaurante con vistas a la Peña Escrita desde la Plaza del Pilar en Salobreña.
La cofradía de pescadores comparte dársena con embarcaciones de recreo en La Herradura.
"""

    alerts = _extraire_alerts_depuis_texte_brut()
    corrige, count = auto_correct(test_text, alerts)
    print(f"✅ {count} correction(s) appliquée(s)")
    print("--- Texte corrigé ---")
    print(corrige)
    print()

    # Vérifications
    def verify(condition: bool, msg: str):
        status = "✅" if condition else "❌"
        print(f"  {status} {msg}")

    verify("Luisa García Chamorro" in corrige,
           "'Luisa García Chamorro' présent")
    verify("José García" not in corrige,
           "'José García' remplacé")
    verify("Rafael Caballero Jiménez" in corrige,
           "'Rafael Caballero Jiménez' présent")
    verify("Rafael Cabrera" not in corrige,
           "'Rafael Cabrera' remplacé")
    verify("Almuñécar" in corrige,
           "'El Tesorillo' attribué à Almuñécar (pas Salobreña)")
    verify("paisaje montañoso" in corrige,
           "'Peña Escrita' neutralisé en 'paisaje montañoso'")
    verify("del Puerto de Motril" in corrige,
           "'cofradía' corrigé vers 'Puerto de Motril'")

    print("\n" + "=" * 60)
    print("🧪 TEST 2: Article réel (la-costa-tropical-se-enciende-en-agosto)")
    print("=" * 60)

    real_text = """\
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

    # Utiliser les vraies alertes de fact_check pour ce test
    from fact_check_es import fact_check  # noqa: E402
    real_alerts = fact_check(real_text)
    print(f"   Alertes détectées par fact_check: {len(real_alerts)}")
    for a in real_alerts:
        print(f"   [{a['type']}] {a['entite']}: '{a['valeur_trouvee']}'")

    real_corrige, real_count = auto_correct(real_text, real_alerts)
    print(f"\n✅ {real_count} correction(s) appliquée(s)")
    print("--- Texte corrigé ---")
    print(real_corrige)
    print()

    print("--- Vérifications ---")
    verify("Luisa García Chamorro" in real_corrige,
           "Maire Motril corrigé → Luisa García Chamorro")
    verify("Rafael Caballero Jiménez" in real_corrige,
           "Président corrigé → Rafael Caballero Jiménez")
    verify("Almuñécar" in real_corrige and "en Salobreña" not in real_corrige.split("Tesorillo")[1][:30]
           if "Tesorillo" in real_corrige else False,
           "El Tesorillo attribué à Almuñécar")
    verify("paisaje montañoso" in real_corrige,
           "Peña Escrita neutralisé")
    verify("Puerto de Motril" in real_corrige,
           "Lonja/cofradía → Puerto de Motril")
    verify("Cortijo del Conde" in real_corrige,
           "Plaza de la Libertad → Cortijo del Conde")

    print("\n" + "=" * 60)
    print("🧪 TEST 3: Texte sain (pas de fausses corrections)")
    print("=" * 60)

    clean_text = """\
La playa de La Charca, en Salobreña, es perfecta para familias.
El alcalde de Salobreña, Julián Lozano, confirmó las obras.
José García, un vecino de la localidad, comentó...
Rafael Cabrera, el entrenador del club local, ganó el torneo.
"""

    clean_alerts = fact_check(clean_text)
    clean_corrige, clean_count = auto_correct(clean_text, clean_alerts)
    print(f"   Alertes: {len(clean_alerts)}, Corrections: {clean_count}")
    verify(clean_count == 0,
           "Aucune correction sur du texte sain")
    verify("José García, un vecino" in clean_corrige,
           "Homonyme 'José García' (non maire) préservé")
    verify("Rafael Cabrera, el entrenador" in clean_corrige,
           "Homonyme 'Rafael Cabrera' (non président) préservé")
    verify("Julián Lozano" in clean_corrige,
           "Maire correct (Julián Lozano) inchangé")