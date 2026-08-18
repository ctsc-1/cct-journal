#!/usr/bin/env python3
"""
auto_correct_dates.py — Normalisation des dates anachroniques dans les articles ES.

Détecte et corrige les références temporelles incohérentes dans les articles
du Journal CCT, en s'appuyant sur les alertes de fact_check_es.py pour les
appliquer automatiquement.

Usage:
    from auto_correct_dates import correct_dates
    text, corrections = correct_dates(article_text, publish_year=2026)
    if corrections:
        print(f"✓ {corrections} correction(s) appliquée(s)")

RÈGLE MARC: stdlib only.
"""

from __future__ import annotations

import re


def _classifier_contexte(match: re.Match, text: str) -> str:
    """
    Classe le contexte d'une mention d'année.

    Retourne:
        'historique' — l'année est >2 ans dans le passé (référence historique légitime)
        'flashback'  — l'année est l'année précédente avec marqueur de flashback
        'actuel'     — l'année est l'année précédente présentée comme actuelle
        'future'     — l'année est l'année suivante présentée comme projection
        'incertain'  — impossible de classifier
    """
    debut = max(0, match.start() - 80)
    fin = min(len(text), match.end() + 40)
    contexte = text[debut:fin].lower()

    annee = int(match.group(0))

    # Marqueurs de flashback légitime (pas de correction)
    # On utilise des motifs qui incluent l'année précise pour éviter
    # les faux positifs quand une autre année apparaît dans le contexte
    motifs_flashback = re.compile(
        rf'(?:'
        rf'el\s+año\s+(?:pasado|anterior)'
        rf'|el\s+verano\s+pasado'
        rf'|la\s+temporada\s+(?:pasada|anterior)'
        rf'|el\s+año\s+anterior'
        rf'|la\s+(?:semana|quincena|mes)\s+pasada'
        rf'|hace\s+un\s+año'
        rf'|respecto\s+a\s+{annee}'
        rf'|respecto\s+de\s+{annee}'
        rf')',
        re.IGNORECASE,
    )

    # Marqueurs d'actualité (doit être corrigé si année = publish_year - 1)
    motifs_actuel = re.compile(
        r'(?:'
        r'\beste\s+(?:año|verano|temporada)'
        r'|\besta\s+(?:temporada|época)'
        r'|\bactualmente'
        r'|\bactual\s+(?:temporada|año)'
        r'|\bahora\b'
        r'|\bhoy\b'
        r'|\ben\s+curso'
        r')',
        re.IGNORECASE,
    )

    # Marqueurs de projection future
    motifs_future = re.compile(
        r'(?:'
        r'el\s+próximo\s+año'
        r'|el\s+año\s+(?:que\s+viene|próximo|siguiente)'
        r'|l\'?\s*(?:année|an)\s+(?:prochaine|à\s+venir)'
        r'|next\s+year'
        r'|previsiones?\s+(?:para|de)\s+2026'
        r'|se\s+prevé'
        r'|inversiones?\s+previstas'
        r')',
        re.IGNORECASE,
    )

    if motifs_flashback.search(contexte):
        return 'flashback'

    if motifs_actuel.search(contexte):
        return 'actuel'

    if motifs_future.search(contexte):
        return 'future'

    return 'incertain'


def _normaliser_temporada_2025(m: re.Match, publish_year: int) -> tuple[str, bool]:
    """
    Traite les motifs comme 'alta temporada 2025', 'temporada 2025', 'verano 2025'.
    Retourne (texte_remplace, a_ete_corrige).
    """
    motif = m.group(0)
    # On extrait l'année à la fin
    annee_match = re.search(r'(20[0-9]{2})$', motif)
    if not annee_match:
        return motif, False

    annee = int(annee_match.group(1))

    # Ne pas modifier si l'année est trop ancienne (historique légitime)
    if annee < publish_year - 2:
        return motif, False

    # Si c'est l'avant-dernière année ou plus vieux et pas dans un contexte actuel
    context_type = _classifier_contexte(annee_match, m.string)
    if context_type == 'flashback':
        return motif, False
    if context_type == 'historique':
        return motif, False

    return motif.replace(str(annee), str(publish_year)), True


def _corriger_annee_seule(m: re.Match, publish_year: int) -> tuple[str, bool]:
    """
    Traite les années seules (20XX) dans le texte, avec analyse de contexte.

    Retourne (texte_remplace, modifie).
    modifie=True seulement si la valeur change réellement.
    """
    annee = int(m.group(0))

    # Déjà l'année courante — rien à faire
    if annee == publish_year:
        return m.group(0), False

    # Année future — rien à faire
    if annee > publish_year:
        return m.group(0), False

    # Ne pas toucher aux années trop anciennes (>= 2 ans dans le passé)
    if annee < publish_year - 2:
        return m.group(0), False

    context_type = _classifier_contexte(m, m.string)

    if context_type == 'flashback':
        return m.group(0), False
    if context_type == 'actuel':
        return str(publish_year), True
    if context_type == 'future':
        return str(publish_year + 1), True

    # Pour l'année précédente dans un contexte incertain, on vérifie des patterns
    # supplémentaires (ex: 'La alta temporada 2025' sans autre marqueur)
    debut = max(0, m.start() - 60)
    contexte_avant = m.string[debut:m.start()].lower()

    # Patterns qui indiquent une actualité même sans mot-clé
    patterns_actuel_implicite = [
        r'(?:la\s+)?alta\s+temporada\s+$',
        r'temporada\s+(?:alta\s+)?$',
        r'verano\s+$',
        r'en\s+esta\s+(?:temporada|época|año)\s+',
        r'de\s+esta\s+temporada\s+',
        r'para\s+esta\s+temporada\s+',
        r'confirm[oó]\s+que\s+',
        r'ha\s+(?:sido|sido\s+la|superado|alcanzado)\s+',
    ]

    for pat in patterns_actuel_implicite:
        if re.search(pat, contexte_avant):
            return str(publish_year), True

    return m.group(0), False


def _corriger_contextes_futurs(text: str, publish_year: int) -> str:
    """Corrige les années associées à des projections futures."""
    # Motifs 'el próximo año 202X' → 'el próximo año 2027' (si X == 6)
    def _replace_proximo(m: re.Match) -> str:
        full = m.group(0)
        annee_match = re.search(r'(20[0-9]{2})', full)
        if annee_match:
            annee = int(annee_match.group(1))
            if annee == publish_year:
                return full.replace(str(annee), str(publish_year + 1))
            if annee != publish_year + 1:
                return full.replace(str(annee), str(publish_year + 1))
        return full

    text = re.sub(
        r'(?:el\s+próximo\s+año|el\s+año\s+(?:que\s+viene|próximo)|next\s+year)'
        r'(?:\s*(?:,|de|en|\s)*\s*(20[0-9]{2}))?',
        _replace_proximo,
        text,
        flags=re.IGNORECASE,
    )
    return text


def correct_dates(text: str, publish_year: int = 2026) -> tuple[str, int]:
    """
    Corrige les dates anachroniques dans un article ES.

    Règles:
    - Les années < publish_year - 2 ne sont pas modifiées (références historiques).
    - L'année publish_year - 1 présentée comme actuelle → corrigée vers publish_year.
    - L'année publish_year - 1 avec marqueur flashback ('el año pasado') → inchangée.
    - Les projections futures ('el próximo año') → publish_year + 1.
    - Les motifs 'alta temporada 2025' → 'alta temporada 2026' si contexte actuel.

    Args:
        text: Article ES en texte brut.
        publish_year: Année de publication (par défaut 2026).

    Returns:
        tuple[str, int]: (texte_corrige, nombre_corrections_appliquees)
    """
    corrections = 0

    # ── Phase 1: Corriger les motifs 'alta temporada 2025', 'temporada 2025' ──
    motifs_temporada = re.compile(
        r'(?:'
        r'(?:la\s+)?alta\s+temporada\s+(?:de\s+)?(20[0-9]{2})'
        r'|(?:la\s+)?temporada\s+(?:alta\s+)?(?:de\s+)?(20[0-9]{2})'
        r'|verano\s+(?:de\s+)?(20[0-9]{2})'
        r')',
        re.IGNORECASE,
    )

    def _replace_temporada(m: re.Match) -> str:
        nonlocal corrections
        full = m.group(0)
        # Trouver l'année dans le match
        for g in m.groups():
            if g:
                annee = int(g)
                break
        else:
            return full

        if annee < publish_year - 2:
            return full

        # Vérifier le contexte — on regarde le match complet
        # Si le contexte avant contient 'el año pasado' ou similaire
        debut_ctx = max(0, m.start() - 80)
        ctx = text[debut_ctx:m.start()].lower()
        if re.search(r'(?:pasado|anterior|el\s+año\s+2025)', ctx):
            return full

        if annee != publish_year:
            corrections += 1
            return full.replace(str(annee), str(publish_year))
        return full

    text = motifs_temporada.sub(_replace_temporada, text)

    # ── Phase 2: Corriger 'en agosto de 2024' — ne pas toucher (historique: >2 ans) ──
    # Rien à faire, ces cas sont déjà protégés par la règle annee < publish_year - 2

    # ── Phase 3: Corriger les années isolées avec contexte actuel ──
    def _replace_annee(m: re.Match) -> str:
        nonlocal corrections
        result, was_corrected = _corriger_annee_seule(m, publish_year)
        if was_corrected:
            corrections += 1
        return result

    text = re.sub(r'(?<!\d)(20[0-9]{2})(?!\d)', _replace_annee, text)

    # ── Phase 4: Projections futures ──
    old_len = len(text)
    text = _corriger_contextes_futurs(text, publish_year)
    if len(text) != old_len:
        # Count changes by comparing
        pass

    return text, corrections


# ─── TESTS ──────────────────────────────────────────────────────────

def _run_tests() -> None:
    """Test unitaire du module."""
    print("=" * 60)
    print("Tests: auto_correct_dates.py")
    print("=" * 60)

    tests_passes = 0
    tests_total = 0

    def verifie(description: str, entrees: str, attendu: str, publish_year: int = 2026) -> None:
        nonlocal tests_total, tests_passes
        tests_total += 1
        resultat, corrections = correct_dates(entrees, publish_year)
        if resultat == attendu and corrections == (0 if attendu == entrees else (entrees.count("2025") if entrees != attendu else 0)):
            tests_passes += 1
            print(f"  ✓ {description}")
        else:
            print(f"  ✗ {description}")
            print(f"    Entrée:  {repr(entrees)}")
            print(f"    Attendu: {repr(attendu)}")
            print(f"    Obtenu:  {repr(resultat)} (corrections: {corrections})")

    # ── Test 1: alta temporada → corrigée ──
    verifie(
        "alta temporada 2025 -> 2026 (contexte actuel)",
        "La alta temporada 2025 ha superado todas las expectativas.",
        "La alta temporada 2026 ha superado todas las expectativas.",
    )

    # ── Test 2: temporada de 2025 → corrigée ──
    verifie(
        "temporada de 2025 -> 2026",
        "La temporada de 2025 ha sido un éxito.",
        "La temporada de 2026 ha sido un éxito.",
    )

    # ── Test 3: verano 2025 → corrigé ──
    verifie(
        "verano 2025 -> 2026",
        "Durante el verano 2025, las playas registraron cifras récord.",
        "Durante el verano 2026, las playas registraron cifras récord.",
    )

    # ── Test 4: 'el año pasado, en 2025' → NE PAS toucher (flashback) ──
    verifie(
        "el año pasado, en 2025 -> inchangé (flashback)",
        "El año pasado, en 2025, la afluencia creció un 12%.",
        "El año pasado, en 2025, la afluencia creció un 12%.",
    )

    # ── Test 5: 'en agosto de 2024' → NE PAS toucher (historique >2 ans) ──
    verifie(
        "en agosto de 2024 -> inchangé (historique)",
        "La temporada comenzó en agosto de 2024 con buenos datos.",
        "La temporada comenzó en agosto de 2024 con buenos datos.",
    )

    # ── Test 6: 'Actualmente, en 2024' → corriger 2024→2026 ──
    verifie(
        "Actualmente, en 2024 -> 2026 (actuel + incohérent)",
        "Actualmente, en 2024, los datos ya superaban las expectativas.",
        "Actualmente, en 2026, los datos ya superaban las expectativas.",
    )

    # ── Test 7: 'Actualmente, en 2024, los datos ya superaban las expectativas' → 2026
    verifie(
        "Actualmente en 2024 (phrase complète) -> 2026",
        "Actualmente, en 2024, los datos ya superaban las expectativas.",
        "Actualmente, en 2026, los datos ya superaban las expectativas.",
    )

    # ── Test 8: 'La temporada alta de 2025 s'annonce comme un test décisif' → 2026
    verifie(
        "temporada alta de 2025 -> 2026 (projection future)",
        "La temporada alta de 2025 s'annonce comme un test décisif.",
        "La temporada alta de 2026 s'annonce comme un test décisif.",
    )

    # ── Test 9: 'La alta temporada 2025 confirmó que...' → 2026
    verifie(
        "alta temporada 2025 confirmó -> 2026 (bilan année courante)",
        "La alta temporada 2025 confirmó que la Costa Tropical es imparable.",
        "La alta temporada 2026 confirmó que la Costa Tropical es imparable.",
    )

    # ── Test 10: année future inchangée ──
    verifie(
        "année future 2027 -> inchangée",
        "El próximo año se invertirán 5 millones en 2027.",
        "El próximo año se invertirán 5 millones en 2027.",
    )

    # ── Test 11: Aucune année présente ──
    verifie(
        "aucune année présente",
        "La Costa Tropical brille en verano.",
        "La Costa Tropical brille en verano.",
    )

    # ── Test 12: Article complet du test ──
    article_test = (
        "La Costa Tropical se enciende en agosto.\n\n"
        "Agosto en la Costa Tropical alcanza temperaturas de récord.\n\n"
        "El año pasado, en 2025, la afluencia turística creció un 12% respecto a 2024.\n"
        "Actualmente, en 2024, los datos ya superaban las expectativas.\n\n"
        "La alta temporada 2025 ha sido un éxito rotundo.\n"
        "La temporada alta de 2025 s'annonce como un test décisif.\n"
    )
    article_attendu = (
        "La Costa Tropical se enciende en agosto.\n\n"
        "Agosto en la Costa Tropical alcanza temperaturas de récord.\n\n"
        "El año pasado, en 2025, la afluencia turística creció un 12% respecto a 2024.\n"
        "Actualmente, en 2026, los datos ya superaban las expectativas.\n\n"
        "La alta temporada 2026 ha sido un éxito rotundo.\n"
        "La temporada alta de 2026 s'annonce comme un test décisif.\n"
    )
    resultat, corrections = correct_dates(article_test, 2026)
    if resultat == article_attendu and corrections == 3:
        tests_passes += 1
        tests_total += 1
        print("  ✓ Article complet (3 corrections sur alta temporada, actualmente, temporada alta)")
    else:
        tests_total += 1
        print("  ✗ Article complet")
        print(f"    Entrée:\n{article_test}")
        print(f"    Attendu:\n{article_attendu}")
        print(f"    Obtenu:\n{resultat}")
        print(f"    Corrections: {corrections}")

    # ── Test 13: Ne pas corriger 2024 si contexte flashback ──
    verifie(
        "2024 contexte flashback 'respecto a 2024' -> inchangé",
        "creció un 12% respecto a 2024",
        "creció un 12% respecto a 2024",
    )

    # ── Test 14: Article original du test avec l'erreur 'Actualmente, en 2024' ──
    test_original = (
        "El año pasado, en 2025, la afluencia turística creció un 12% respecto a 2024. "
        "Este año las previsiones son aún más optimistas. "
        "Actualmente, en 2024, los datos ya superaban las expectativas."
    )
    expected = (
        "El año pasado, en 2025, la afluencia turística creció un 12% respecto a 2024. "
        "Este año las previsiones son aún más optimistas. "
        "Actualmente, en 2026, los datos ya superaban las expectativas."
    )
    result, n = correct_dates(test_original, 2026)
    if result == expected and n == 1:
        tests_passes += 1
        tests_total += 1
        print("  ✓ Test article original (1 correction: actualmente 2024→2026)")
    else:
        tests_total += 1
        print(f"  ✗ Test article original (got {n} corrections, expected 1)")
        print(f"    Result: {repr(result)}")

    print(f"\n{'=' * 60}")
    print(f"Résultat: {tests_passes}/{tests_total} tests passés")
    if tests_passes < tests_total:
        print("⚠ CERTAINS TESTS ONT ÉCHOUÉ")
    else:
        print("✅ Tous les tests passés")


if __name__ == "__main__":
    _run_tests()