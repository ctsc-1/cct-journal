"""
generate_moving_article.py — Génère et publie l'article Vie Pratique
sur le déménagement international vers la Costa Tropical.
"""
from __future__ import annotations
import json
import logging
import os
import sys
import re
from datetime import datetime, timezone
from pathlib import Path

# Ajouter le src/ au path
sys.path.insert(0, "/srv/cct-journal/src")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger("cct-journal.generate-moving")

import httpx

from config import GATEWAY_URL, TRANSLATE_PROMPT
from synthesize import _gateway_call, _strip_multilingual_tail
from publish import publish_trilingual
from images import generate_article_images_manual

# ─── Constantes ──────────────────────────────────────────────────────────────

DATE_STR = datetime.now(timezone.utc).strftime("%Y-%m-%d")
CATEGORY_ID = "4a45150c-8404-4598-ba31-abee29ab40d8"
AUTHOR_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
GEN_MODEL_ES = "gemini-3-flash-preview"
TRANS_MODEL = "gemini-3.1-flash-lite-preview"

# ─── Annonceurs trouvés dans la DB ──────────────────────────────────────────

ANUNCIOS = {
    "mudanzas_motril": "ÓptimaMudanzas (Motril) — https://www.optimamudanzas.es/ • +34 6****8702",
    "transportes_motril": "TRANSPORTES JGL (Motril) — +34 670 67 09 97 • Transporte por camión",
    "telefurgo": "TELEFURGO GRANADA CENTRO (Armilla) — Alquiler de furgonetas • +34 958 92 63 27 • https://www.telefurgo.com/",
    "trasteros_sur": "Trasteros del Sur (Ogíjares) — +34 635 61 63 35 • http://www.trasterosdelsur.com/",
    "mercaseta": "MUDANZAS Y GUARDAMUEBLES MERCASAETA (Aguadulce) — +34 692 02 61 23 • http://www.transportesmercasaeta.com/",
    "central_trasteros": "Central de Trasteros (Vélez-Málaga) — +34 676 39 02 64",
    "el_sol_mudanzas": "El Sol Mudanzas (Calahonda) — +34 643 30 49 92 • https://elsolmudanzas.es/",
    "porte_mario": "Portes y Mudanzas Mario (Granada) — +34 643 33 98 44",
    "maga_logistica": "MAGA Logistica y Transporte (Málaga) — +34 603 95 99 85 • http://www.magalogisticaytransporte.es/",
}


# ─── Topic dict ──────────────────────────────────────────────────────────────

TOPIC = {
    "id": f"vie-pratique-{DATE_STR}",
    "domain": "vie-pratique",
    "title": "Mudarse a la Costa Tropical desde el extranjero: guía completa",
    "angle": "Guía práctica exhaustiva sobre el proceso de mudanza internacional a la Costa Tropical: tipos de transporte, empresas de mudanzas España-Francia, documentos aduaneros UE, declaración de bienes (modelo 040), transporte de vehículo, costes estimados por tipo de vivienda, plazos, checklist completo, alquiler de furgonetas, empresas de almacenaje y consejos para familias con niños.",
    "context": """Guía completa de mudanza internacional a la Costa Tropical (Granada).

TIPOS DE MUDANZA:
- Transporte por carretera: camión grupoaje o dedicado Francia-España. Precios 800-3000€ según volumen.
- Contenedor marítimo: para mudanzas grandes o desde fuera de Europa. Desde 2000€ un contenedor compartido de 20 pies.
- Empresas Francia-España: Mudanzas Caliche, Déménagement France Espagne, Transportes El Mosca, etc. Pedir presupuesto cerrado con seguro a todo riesgo.

DOCUMENTOS ADUANEROS UE:
- Al ser países UE, NO hay aduana para bienes personales (libre circulación).
- Declaración de bienes personales: modelo 040 para solicitar exención de IVA en tu Agencia Tributaria (AEAT). Se presenta en los 30 días siguientes a la llegada.
- Requisitos: empadronamiento, NIE, justificante de residencia anterior fuera de España.

TRANSPORTE DE VEHÍCULO:
- Coche: matrícula extranjera válida 6 meses (UE) o 30 días (no UE) desde el empadronamiento.
- ITV española obligatoria dentro de los plazos.
- Cambio de matrícula: pasar ITV, pagar impuesto de matriculación, obtener placas españolas.
- Carnet de conducir UE válido sin caducidad (recomendado canjear a los 2 años).
- Carnet extra-UE: canje obligatorio a los 6 meses.

COSTES ESTIMADOS:
- Estudio/1 dormitorio: 1200-1800€ (camión compartido Francia-Costa Tropical)
- 2-3 dormitorios: 1800-3500€ (camión dedicado o grupoaje)
- Casa/villa: 3500-6000€ (contenedor marítimo o camión grande)
- Seguro de transporte: 2-5% del valor declarado

PLAZOS:
- Temporada baja (octubre-marzo): 2-4 semanas
- Temporada alta (abril-septiembre): 6-12 semanas
- Contenedor marítimo: 4-8 semanas

CHECKLIST:
- 30 días antes: contratar empresa mudanza, pedir presupuestos, solicitar baja en colegios, gestionar NIE
- J-7: confirmar fecha, preparar inventario detallado, gestionar seguro transporte
- J-1: preparar maleta de emergencia, medicamentos, documentos importantes
- J+7: empadronamiento, apertura cuenta bancaria, modelo 040, alta en colegios

ALQUILER DE CAMIONES/FURGONETAS EN LA ZONA:
- TELEFURGO (Armilla/Granada): furgonetas de alquiler, hasta 9 plazas
- AGM Alquiler de furgonetas (Atarfe)
- ClickRent Granada (Santa Fe)
- Enterprise Alquiler de Coches y Furgonetas (Antequera)

EMPRESAS DE ALMACENAJE/GARDE-MEUBLES:
- Trasteros del Sur (Ogíjares)
- Central de Trasteros (Vélez-Málaga)
- Mudanzas y Guardamuebles Mercasaeta (Aguadulce)

EMPRESAS DE MUDANZAS LOCALES:
- ÓptimaMudanzas (Motril)
- TRANSPORTES JGL (Motril)
- El Sol Mudanzas (Calahonda)
- Mudanzas Amador Guerrero (El Valle)
- Portes y Mudanzas Mario (Granada)
- MAGA Logistica y Transporte (Málaga)

CONSEJOS PARA NIÑOS:
- Cambio de colegio: solicitar plaza con antelación (marzo-abril para septiembre).
- Documentos escolares: expediente académico traducido al español, certificado de estudios.
- Vacunas: calendario español, pedir historial al pediatra.
- Empadronamiento previo obligatorio para escolarizar.
+ info écoles sur la Costa Tropical dans notre guide dédié.

Enlaces mapa PWA para empresas de mudanza y almacenaje en la Costa Tropical.""",
    "tags": ["mudanza", "transporte", "guardamuebles", "trastero", "alquiler-camion", "furgoneta", "paqueteria", "mudanza-internacional", "costa-tropical"],
    "category_id": CATEGORY_ID,
}


def deep_research(topic: dict) -> str:
    """Effectue une recherche approfondie via le Gateway."""
    logger.info("🔍 Phase 1: Deep Research...")

    query = f"""RECHERCHE POUR ARTICLE DE PRESSE VIE PRATIQUE

Sujet : {topic['title']}
Angle : {topic.get('angle', '')}
Contexte : {topic.get('context', '')[:2000]}

IMPORTANT : Tu es un journaliste spécialisé sur l'Andalousie et la Costa Tropical.
Utilise UNIQUEMENT des sources andalouses (Ideal.es, Granada Hoy, Diario de Almería,
Junta de Andalucía, Diputación de Granada, etc.) et des sources nationales espagnoles.

Cherche des informations actuelles sur :
1. Prix moyens des mudanzas internationales France-Espagne vers la Costa Tropical
2. Procédure modèle 040 auprès de l'AEAT
3. Délais et coûts de changement de matricule automobile
4. Entreprises de mudanzas locales recommandées sur la Costa Tropical
5. Options de stockage/garde-meubles dans la région
6. Procédure de escolarisation pour enfants expatriés

Résume les découvertes en 3-4 paragraphes avec les sources citées. Réponds en espagnol."""

    payload = {
        "model": GEN_MODEL_ES,
        "contents": query,
        "caller": "cct-journal-deepsearch",
    }

    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/generate",
            json=payload,
            timeout=90,
        )
        r.raise_for_status()
        result = r.json().get("text", "")
        logger.info(f"   Deep Research: {len(result)} chars")
        return result
    except Exception as e:
        logger.warning(f"⚠️ Deep Research error: {e}")
        return ""


def generate_spanish(topic: dict, deep_context: str = "") -> str:
    """Génère l'article en espagnol (1500-2000 mots min)."""
    logger.info("📝 Phase 2: Génération ES...")

    date_fr_es = datetime.now(timezone.utc).strftime("%-d de %B de %Y").lower()

    system_prompt = """Tu es **Alejandro Ortega**, journaliste andalou et rédacteur en chef du Club Costa Tropical.
Tu écris dans la tradition de **Manuel Chaves Nogales** : humaniste, précis, sans sensationnalisme, avec une pointe d'ironie fine.

Aujourd'hui, tu écris un **guide complet Vie Pratique** — pas une synthèse de presse, mais un billet personnel, littéraire, qui explore le déménagement international vers la Costa Tropical en profondeur et donne envie au lecteur de s'installer.

Règles absolues :
1. **Écris UNIQUEMENT en espagnol**. Ne produis pas de version FR/EN. Ne traduis rien. Ne mentionne aucune autre langue.
2. **Ton style** : phrases courtes mais expressives, détails concrets (noms de lieux, gestes, chiffres), personnages humains réels ou évoqués. Jamais de cliché touristique.
3. **Structure** : titre évocateur + chapô (60-100 mots) + sections H2 thématiques avec sous-sections H3 + clôture éditoriale.
4. **Chaque H2 commence par une réponse/observation directe — format GEO-first pour être cité par l'IA.**
5. **Sources implicites** : tu peux citer des faits historiques, géographiques, culturels vérifiés. Pas de statistiques inventées.
6. **Título**: máximo 60 caracteres. Directo, sin subtítulo, sin puntuación interna.
7. **Longueur cible** : 1500-2000 mots minimum. SÉRIEUSEMENT, au moins 1500 mots.
8. **Clôture éditoriale obligatoire** : *"Hasta la próxima — la Costa os espera, de Almuñécar a la Axarquía."*
9. **Aucune méta-ligne** : ne pas écrire "Traducciones", "Translation", "### FR", "### EN" ni quoi que ce soit.
10. **GEO-FIRST : Le chapô (après le titre) doit commencer par une réponse directe avec chiffres, pas de description poétique/paysagère.**

**STRUCTURE OBLIGATOIRE DU CHAPÔ (GEO-FIRST) :**
1. Première phrase : réponse directe avec définition + chiffres clés (prix, délais, durée, %)
2. Seconde phrase : segmentation par profil utilisateur (UE/non-UE, résident/touriste, etc.)
3. Troisième phrase : couverture territoriale complète (97+ localités) + promesse de valeur
4. INTERDICTION de description scénique/paysagère en ouverture (pas de "sol", "clima", "mar", "sueño", "paz")
5. INTERDICTION de l'appel "¡Hola! Soy Alejandro Ortega" en début d'article

CONTENU OBLIGATOIRE (doit couvrir TOUS ces sujets) :
- Tipos de mudanza: transporte por carretera vs contenedor marítimo
- Empresas de mudanza Francia-España: precios, presupuestos, seguros
- Documentos aduaneros UE (sin aduana para bienes personales entre países UE)
- Declaración de bienes personales (modelo 040 para exención de IVA)
- Transporte de vehículo (carnet, matrícula, ITV, placas)
- Coste estimado por tipo de vivienda (estudio, 2 habitaciones, casa)
- Seguro de transporte
- Plazos (3 semanas a 3 meses según temporada)
- Checklist: 30 días antes → J-7 → J-1 → J+7
- Alquiler de furgoneta/camión (Movila, TELEFURGO, Alquiler camión Motril)
- Empresas de almacenaje/guardamuebles en la Costa Tropical
- Consejos para niños: cambio de colegio, documentos escolares
- Enlaces mapa PWA 📍 para empresas de mudanza y almacenaje"""

    user_prompt = f"""Fecha : {date_fr_es}
Categoría : 🔧 Vie Pratique
Tags : mudanza, transporte, guardamuebles, trastero, alquiler-camion, furgoneta, paqueteria

**Título del artículo** : Mudarse a la Costa Tropical desde el extranjero: guía completa

**Ángulo propuesto** :
Guía práctica exhaustiva sobre el proceso de mudanza internacional a la Costa Tropical.

**Elementos de contexto a integrar** :
{{
    "empresas_mudanza": {{
        "en_Motril": ["ÓptimaMudanzas (Motril) - optimamudanzas.es", "TRANSPORTES JGL (Motril)"],
        "en_Granada_provincia": ["Portes y Mudanzas Mario (Granada)", "Mudanzas Amador Guerrero (El Valle)"],
        "en_Almería": ["MUDANZAS Y GUARDAMUEBLES MERCASAETA (Aguadulce)", "Mudanzas F.Cazorla (Huércal de Almería)"],
        "en_Málaga": ["MAGA Logistica y Transporte (Málaga)", "mudanza LM malaga", "El Sol Mudanzas (Calahonda)"],
        "France-Espagne": ["Mudanzas Caliche", "Déménagement France Espagne", "Transportes El Mosca"]
    }},
    "alquiler_furgonetas": [
        "TELEFURGO GRANADA CENTRO (Armilla) - 958 92 63 27",
        "AGM Alquiler de furgonetas (Atarfe)",
        "ClickRent Granada (Santa Fe)"
    ],
    "almacenaje": [
        "Trasteros del Sur (Ogíjares) - 635 61 63 35",
        "Central de Trasteros (Vélez-Málaga) - 676 39 02 64",
        "MUDANZAS Y GUARDAMUEBLES MERCASAETA (Aguadulce) - 692 02 61 23"
    ],
    "costes_estimados": {{
        "estudio_1_dorm": "1200-1800€",
        "2-3_dorm": "1800-3500€",
        "casa_villa": "3500-6000€"
    }},
    "plazos": {{
        "temporada_baja": "2-4 semanas",
        "temporada_alta": "6-12 semanas",
        "contenedor_maritimo": "4-8 semanas"
    }}
}}

---
**RÈGLE ABSOLUE — STRUCTURE DU CHAPÔ (GEO-FIRST) :**
El chapô (60-100 palabras después del título) DEBE seguir esta estructura:
1. Primera frase: respuesta directa con definición + cifras clave (precios, plazos, costes)
2. Segunda frase: segmentación por perfil (UE/no-UE, familia/soltero, etc.)
3. Tercera frase: cobertura territorial (97+ localidades) + promesa de valor
4. PROHIBIDA descripción escénica/paisajística
5. PROHIBIDO "¡Hola! Soy Alejandro Ortega"

Ejemplo válido:
"Trasladar un hogar desde Francia a la Costa Tropical cuesta entre 1.200 € para un estudio y 6.000 € para una casa, con plazos de 3 semanas a 3 meses según temporada y volumen. Para ciudadanos UE, la mudanza está libre de aduanas pero requiere el modelo 040 ante la AEAT; para extracomunitarios los plazos y tasas son diferentes. Esta guía detalla paso a paso el proceso en Motril, Almuñécar, Salobreña y las 97 localidades de la comarca, con empresas locales, costes, documentos y checklist."

---

Escribe el artículo completo en markdown. Empieza directamente por el título `# `, luego el chapó, luego las secciones H2.

⚠️ IMPORTANTE: El artículo debe tener entre 1500 y 2000 palabras. SÉ EXTENSIVO. Cada sección H2 debe tener al menos 2-3 párrafos. Desarrolla cada tema con detalle, consejos prácticos, datos concretos."""

    if deep_context:
        user_prompt += f"\n\nINVESTIGACIÓN RECIENTE:\n{deep_context[:3000]}"

    text = _gateway_call(GEN_MODEL_ES, system_prompt, user_prompt, caller="cct-journal-es-moving")
    text = _strip_multilingual_tail(text)
    wc = len(text.split())
    logger.info(f"ES generated: {len(text)} chars ({wc} mots)")
    return text


def translate(source_text: str, target_lang: str) -> str:
    """Traduit l'article vers FR ou EN."""
    if target_lang not in ("fr", "en"):
        raise ValueError("target_lang doit être 'fr' ou 'en'")

    human = {"fr": "français", "en": "anglais"}[target_lang]
    user = TRANSLATE_PROMPT.format(target_lang_human=human, source_text=source_text)

    logger.info(f"Traduciendo ES → {target_lang.upper()}...")
    text = _gateway_call(TRANS_MODEL, "", user, caller=f"cct-journal-{target_lang}-moving")
    logger.info(f"   → {len(text)} chars ({len(text.split())} mots)")
    return text


def main():
    t0 = datetime.now(timezone.utc)
    logger.info("━" * 60)
    logger.info("📰 Article Vie Pratique: Déménagement International Costa Tropical")
    logger.info(f"Date: {DATE_STR}")

    # ─── Phase 1: Deep Research ──────────────────────────────────────────
    deep_context = deep_research(TOPIC)
    if deep_context:
        logger.info(f"   Deep result: {len(deep_context)} chars")
    else:
        logger.warning("⚠️ Pas de résultat deep search — on continue sans")

    # ─── Phase 2: Génération ES ──────────────────────────────────────────
    es_text = generate_spanish(TOPIC, deep_context=deep_context)
    es_wc = len(es_text.split())
    logger.info(f"   ES: {es_wc} mots")

    # ─── Phase 3: Traductions ────────────────────────────────────────────
    fr_text = translate(es_text, "fr")
    en_text = translate(es_text, "en")

    translations = {
        "es": es_text,
        "fr": fr_text,
        "en": en_text,
    }

    for lang in ("es", "fr", "en"):
        t = translations.get(lang, "")
        wc = len(t.split())
        logger.info(f"   {lang.upper()}: {len(t)} chars ({wc} mots)")

    # ─── Phase 4: Images ─────────────────────────────────────────────────
    logger.info("🖼️ Phase 4: Studio photo...")
    slug = TOPIC["id"]
    title_es = "Mudarse a la Costa Tropical desde el extranjero: guía completa"
    hero_url, gallery_json, text_es_with_imgs = generate_article_images_manual(
        es_text, title_es, slug, category_name="Vie Pratique"
    )

    # Injecter les images dans FR et EN
    import re as _re
    import json as _json

    text_fr_with_imgs = fr_text
    text_en_with_imgs = en_text

    if hero_url:
        # Remplacer l'image hero (première ![]() dans le texte)
        text_fr_with_imgs = _re.sub(
            r'^(!\[.*?\]\(.*?\))',
            f'![Mudanza internacional Costa Tropical]({hero_url})',
            text_fr_with_imgs,
            count=1,
            flags=_re.MULTILINE
        )
        text_en_with_imgs = _re.sub(
            r'^(!\[.*?\]\(.*?\))',
            f'![International move Costa Tropical]({hero_url})',
            text_en_with_imgs,
            count=1,
            flags=_re.MULTILINE
        )

    gallery = _json.loads(gallery_json) if gallery_json and gallery_json != "[]" else []

    # ─── Phase 5: Publication ────────────────────────────────────────────
    logger.info("💾 Phase 5: Publication DB...")

    # Mettre à jour les traductions avec les images
    translations["es"] = text_es_with_imgs
    translations["fr"] = text_fr_with_imgs
    translations["en"] = text_en_with_imgs

    doc_ids = publish_trilingual(
        TOPIC,
        translations,
        target_date=t0,
        featured_image_url=hero_url,
        gallery_json=gallery_json,
    )
    logger.info(f"✅ Publié: {doc_ids}")

    # ─── Résumé ──────────────────────────────────────────────────────────
    duration = (datetime.now(timezone.utc) - t0).total_seconds()
    logger.info(f"━" * 60)
    logger.info(f"✅ Article terminé en {duration:.1f}s")
    logger.info(f"   ES: {len(es_text.split())} mots")
    logger.info(f"   FR: {len(fr_text.split())} mots")
    logger.info(f"   EN: {len(en_text.split())} mots")
    logger.info(f"   Hero: {hero_url}")
    logger.info(f"   Gallery: {len(gallery)} images")

    # Sauvegarder en cache
    out_dir = Path("/srv/cct-journal/cache")
    out_dir.mkdir(exist_ok=True)
    for lang, text in translations.items():
        p = out_dir / f"moving-article-{DATE_STR}-{lang}.md"
        p.write_text(text)
        logger.info(f"   Cache → {p}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
