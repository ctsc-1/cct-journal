#!/usr/bin/env python3
"""
Pipeline génération article 'Écoles et Éducation sur la Costa Tropical'.
Étape 1 : Génération ES via Gateway LLM (gemini-2.5-pro)
Étape 2 : Traduction FR/EN via Gateway LLM (gemini-2.5-flash-lite)
Étape 3 : Planification narrative + images
Étape 4 : Publication
"""
import json
import logging
import os
import sys
import re
from datetime import datetime, timezone

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate-education")

GATEWAY_URL = "http://127.0.0.1:4000"
GEN_MODEL = "gemini-3-flash-preview"  # qualité article
TRANS_MODEL = "gemini-2.5-flash-lite" # traduction légère

# Token Bearer
GATEWAY_KEY = os.environ.get("GATEWAY_KEY")
if not GATEWAY_KEY:
    try:
        GATEWAY_KEY = open("/etc/gateway-secrets/gemini-paid.key").read().strip()
    except Exception:
        pass

HEADERS = {"Authorization": f"Bearer {GATEWAY_KEY}"} if GATEWAY_KEY else {}

CATEGORY_ID = "4a45150c-8404-4598-ba31-abee29ab40d8"  # Vie Pratique
AUTHOR_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"    # Alejandro Ortega

# ─── Deep Research Context ─────────────────────────────────────────────────

DEEP_CONTEXT = """## INFORME DE INVESTIGACIÓN: Educación en la Costa Tropical (Granada)

### Procedimiento para Extranjeros
- **NIE (Número de Identidad de Extranjero):** Se solicita en la Oficina de Extranjería de Granada o comisaría de Motril. Formulario EX-15, pasaporte, cita previa.
- **Empadronamiento:** Registro en el ayuntamiento del municipio (Motril, Almuñécar, Salobreña). Necesario pasaporte/NIE y contrato de alquiler o escritura.
- **Homologación de Estudios:** Para ESO y Bachillerato desde sistema no español. Se solicita al Ministerio de Educación. Volante de inscripción condicional para empezar mientras se resuelve.

### Calendario Escolar Andalucía 2025-2026
- Infantil, Primaria y Especial: inicio ~10 septiembre 2025
- ESO, Bachillerato, FP: inicio ~16 septiembre 2025
- Navidad: ~23 dic 2025 - 6 ene 2026
- Semana Blanca: última semana de febrero (Día de Andalucía 28 feb)
- Semana Santa: fechas variables según calendario litúrgico
- Fin de clases: ~23-24 junio 2026

### Centros en Motril
**CEIP (públicos):** Cardenal Belluga (C. Ancha 46, 958 83 85 45), Reina Fabiola (C. Jacinto Benavente 1, 958 83 85 70), Mariana Pineda (C. Pablo Picasso 1, 958 83 85 60)
**IES (públicos):** Giner de los Ríos (Av. Esperanza s/n, Bachillerato + FP, 958 83 85 10), Francisco Javier de Burgos (C. San Andrés 1, 958 83 85 00), La Zafra (C. Río Duero 1, FP Hostelería, 958 83 85 90)
**Concertados:** Santo Rosario (Pl. San Agustín 3, 958 60 15 12), Virgen de la Cabeza (Av. Virgen de la Cabeza 21, Agustinos Recoletos, 958 60 08 97)

### Centros en Almuñécar
**CEIP:** San Miguel (C. Torremar 2, 958 83 89 20), La Noria (C. de la Noria 5, 958 83 89 12)
**IES:** Al-Ándalus (Av. Mediterráneo s/n, 958 83 88 50), Puerta del Mar (Av. de Cala 12, 958 83 88 80)
**Internacional:** Almuñécar International School AIS (C. Almendros 6, Urb. Los Pinos, 958 63 58 14, currículum británico + IB)

### Centros en Salobreña
**CEIP:** Mayor Zaragoza (C. Flores s/n, 958 83 87 25), Segalvina (C. Labradores 1, 958 83 87 35)
**IES:** Mediterráneo (Av. Motril s/n, 958 83 87 00)

### Precios Colegios Internacionales
- AIS Almuñécar: Primaria 6.000-8.000€, Secundaria 8.000-10.000€, IB 10.000-12.000€ anuales
- British School of Granada (alternativa en Granada capital)
- Lycée Français International de Granada

### Formación Profesional
- IES Giner de los Ríos: Sanidad, Informática, Administración, Electricidad
- IES La Zafra: Cocina, Restauración, Turismo
- IES Martín Recuerda: Imagen Personal

### Universidad (UNED)
- Centro Asociado UNED Motril: Casa de la Palma, Av. Andalucía 3, 958 82 10 37
- Grados, Másteres, Acceso mayores 25/45 años
- Tutorías presenciales en Motril, exámenes en el centro asociado"""


def gateway_call(model: str, system: str, user: str, timeout: int = 180) -> str:
    """Appelle le Gateway LLM."""
    combined = f"{system}\n\n---\n\n{user}" if system else user
    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/generate",
            json={"model": model, "contents": combined, "caller": "generate-education"},
            headers=HEADERS,
            timeout=timeout,
        )
        r.raise_for_status()
        return (r.json().get("text") or "").strip()
    except httpx.HTTPStatusError as e:
        logger.error(f"Gateway HTTP {e.response.status_code}: {e.response.text[:300]}")
        raise
    except Exception as e:
        logger.error(f"Gateway error: {e}")
        raise


# ─── Étape 1 : Génération ES ────────────────────────────────────────────────

SYSTEM_ES = """Tu eres **Alejandro Ortega**, periodista andaluz y redactor jefe del Club Costa Tropical.
Escribes en la tradición de **Manuel Chaves Nogales**: humanista, preciso, sin sensacionalismo, con un toque de ironía fina.

Hoy escribes un **artículo de guía práctica** — no una crónica personal, sino un dossier útil, completo y bien documentado para familias, expatriados y residentes que quieren entender el sistema educativo de la Costa Tropical.

**REGLAS ABSOLUTAS:**
1. Escribe SOLO en español. No produzcas versión FR/EN. No traduzcas nada.
2. **ESTRUCTURA OBLIGATORIA:**
   - Título H1: `# Colegios y Educación en la Costa Tropical: guía completa`
   - Subtítulo / lead (40-80 palabras) que resume el artículo
   - Secciones H2 para cada tema (ver más abajo)
   - Subsecciones H3 donde sea necesario
   - Cierre editorial
3. **LONGITUD MÍNIMA: 1800-2200 palabras** — debe ser exhaustivo.
4. **SECCIONES H2 OBLIGATORIAS** (en este orden):
   - `## El sistema educativo andaluz: cómo funciona`
   - `## Tipos de centros educativos`
     - `### Colegios públicos (CEIP e IES)`
     - `### Colegios concertados`
     - `### Colegios internacionales y privados`
   - `## Guía de centros por municipio`
     - `### Motril`
     - `### Almuñécar`
     - `### Salobreña`
   - `## Formación Profesional en la Costa`
   - `## La UNED en Motril: estudios universitarios sin salir de la costa`
   - `## Procedimiento para familias extranjeras`
     - `### Paso 1: Obtener el NIE`
     - `### Paso 2: El empadronamiento`
     - `### Paso 3: Homologación de estudios`
   - `## Calendario escolar andaluz 2025-2026`
   - `## Precios y costes de los colegios internacionales`
5. **DATOS CONCRETOS:** usa direcciones exactas, teléfonos, webs. Integra referencias a lugares reales de la Costa Tropical.
6. **MAPAS PWA:** Integra enlaces al mapa PWA con el formato: 📍 [Nombre del centro](/map?lat=LAT&lng=LNG&name=Nombre&article=educacion-costa-tropical)
   Coordenadas a usar:
   - Motril centro: 36.7467, -3.5175
   - CEIP Cardenal Belluga: 36.7474, -3.5182
   - CEIP Reina Fabiola: 36.7455, -3.5210
   - CEIP Mariana Pineda: 36.7435, -3.5231
   - IES Giner de los Ríos: 36.7498, -3.5145
   - IES Francisco Javier de Burgos: 36.7442, -3.5205
   - IES La Zafra: 36.7410, -3.5260
   - Colegio Santo Rosario: 36.7471, -3.5168
   - Colegio Virgen de la Cabeza: 36.7459, -3.5142
   - Almuñécar centro: 36.7340, -3.6890
   - CEIP San Miguel: 36.7351, -3.6872
   - CEIP La Noria: 36.7368, -3.6860
   - IES Al-Ándalus: 36.7325, -3.6911
   - IES Puerta del Mar: 36.7301, -3.6935
   - Almuñécar International School: 36.7312, -3.6950
   - Salobreña centro: 36.7477, -3.5865
   - CEIP Mayor Zaragoza: 36.7489, -3.5851
   - CEIP Segalvina: 36.7465, -3.5882
   - IES Mediterráneo: 36.7450, -3.5878
   - UNED Motril: 36.7460, -3.5190
7. **CIERRE EDITORIAL OBLIGATORIO:** *"Hasta la próxima — la Costa os espera, de Almuñécar a la Axarquía."*
8. Sin meta-líneas de traducción al final."""

USER_ES = f"""Fecha: {datetime.now(timezone.utc).strftime('%-d de %B de %Y').lower()}
Tema: Colegios y Educación en la Costa Tropical: guía completa
Categoría: Vie Pratique (Guía práctica para residentes y expatriados)

**Contexto de investigación:**
{DEEP_CONTEXT[:6000]}

Escribe el artículo completo en markdown. Mínimo 1800 palabras. Incluye los enlaces 📍 a los mapas PWA en cada centro. Sé exhaustivo pero práctico — esta guía será leída por familias que están considerando mudarse a la Costa Tropical."""


# ─── Étape 2 : Traduction ────────────────────────────────────────────────────

SYSTEM_TRANS_FR = """Eres traductor profesional de cultura andaluza.

Traduce **íntegramente** el texto markdown siguiente del español al francés, preservando:
- La estructura markdown (H1/H2/H3, párrafos, itálicas, negritas)
- Los nombres propios (Motril, Almuñécar, Salobreña, La Herradura, etc.)
- Los enlaces a mapas 📍 (no los traduzcas ni modifiques)
- El tono práctico de guía útil

ADAPTA:
- El título H1: `# Écoles et Éducation sur la Costa Tropical : guide complet`
- El cierre editorial: `*"À la prochaine — la Costa vous attend, d'Almuñécar à l'Axarquía."*`

Traduce SOLO el texto. No añadas ni quites nada."""

SYSTEM_TRANS_EN = """Eres traductor profesional de cultura andaluza.

Traduce **íntegramente** el texto markdown siguiente del español al inglés, preservando:
- La estructura markdown (H1/H2/H3, párrafos, itálicas, negritas)
- Los nombres propios (Motril, Almuñécar, Salobreña, La Herradura, etc.)
- Los enlaces a mapas 📍 (no los traduzcas ni modifiques)
- El tono práctico de guía útil

ADAPTA:
- El título H1: `# Schools and Education on the Costa Tropical: Complete Guide`
- El cierre editorial: `*"Until next time — the Costa awaits you, from Almuñécar to the Axarquía."*`

Traduce SOLO el texto. No añadas ni quites nada."""


# ─── Pipeline ────────────────────────────────────────────────────────────────

def strip_trilingual_tail(text: str) -> str:
    """Coupe les éventuelles méta-lignes de traductions."""
    patterns = [
        r"\n\s*---\s*\n\s*###?\s*(Traducciones?|Translations?|FR|EN|Français|English|Anglais)\b.*",
        r"\n\s*###?\s*(Traducciones?|Translations?|FR|EN|Français|English|Anglais)\s*:?\s*\n.*",
    ]
    for pat in patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def main():
    logger.info("=" * 60)
    logger.info("ÉTAPE 1/4 : Génération de l'article en espagnol")
    logger.info("=" * 60)

    es_text = gateway_call(GEN_MODEL, SYSTEM_ES, USER_ES, timeout=300)
    es_text = strip_trilingual_tail(es_text)
    es_words = len(es_text.split())
    logger.info(f"✅ ES généré: {len(es_text)} chars ({es_words} mots)")
    
    # Sauvegarde
    with open("/tmp/educacion-es.md", "w") as f:
        f.write(es_text)
    logger.info("   Sauvegardé dans /tmp/educacion-es.md")

    logger.info("=" * 60)
    logger.info("ÉTAPE 2/4 : Traduction FR et EN")
    logger.info("=" * 60)

    fr_text = gateway_call(TRANS_MODEL, SYSTEM_TRANS_FR, es_text, timeout=300)
    fr_text = strip_trilingual_tail(fr_text)
    fr_words = len(fr_text.split())
    logger.info(f"✅ FR traduit: {len(fr_text)} chars ({fr_words} mots)")
    with open("/tmp/educacion-fr.md", "w") as f:
        f.write(fr_text)

    en_text = gateway_call(TRANS_MODEL, SYSTEM_TRANS_EN, es_text, timeout=300)
    en_text = strip_trilingual_tail(en_text)
    en_words = len(en_text.split())
    logger.info(f"✅ EN traduit: {len(en_text)} chars ({en_words} mots)")
    with open("/tmp/educacion-en.md", "w") as f:
        f.write(en_text)

    # ─── Étape 3 : Planification narrative + Images ───────────────────────
    logger.info("=" * 60)
    logger.info("ÉTAPE 3/4 : Planification narrative et images")
    logger.info("=" * 60)

    sys.path.insert(0, "/srv/cct-journal/src")
    from narrative_planner import plan_images
    from images import generate_article_images

    # Planification sur le texte ES
    title_es = "Colegios y Educación en la Costa Tropical: guía completa"
    text_with_markers, plan, raw_llm = plan_images(es_text, title_es)
    logger.info(f"   Plan: {len(plan)} image(s) planifiée(s)")

    slug = "colegios-y-educacion-costa-tropical-guia-completa"

    # Génération des images
    hero_url, gallery_json, text_with_images = generate_article_images(
        text_with_markers, plan, slug
    )
    logger.info(f"   Hero: {hero_url}")
    logger.info(f"   Gallery: {gallery_json[:100]}...")

    # Reporter les marqueurs dans les traductions
    from narrative_planner import plan_images_for_lang
    fr_with_images = plan_images_for_lang(text_with_images, fr_text)
    en_with_images = plan_images_for_lang(text_with_images, en_text)

    translations = {
        "es": text_with_images,
        "fr": fr_with_images,
        "en": en_with_images,
    }

    with open("/tmp/educacion-es-final.md", "w") as f:
        f.write(text_with_images)
    with open("/tmp/educacion-fr-final.md", "w") as f:
        f.write(fr_with_images)
    with open("/tmp/educacion-en-final.md", "w") as f:
        f.write(en_with_images)

    # ─── Étape 4 : Publication ─────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("ÉTAPE 4/4 : Publication")
    logger.info("=" * 60)

    from publish import publish_trilingual
    
    topic = {
        "id": slug,
        "domain": "vida-practica",
        "title": title_es,
        "angle": "Guía práctica completa sobre el sistema educativo en la Costa Tropical: colegios públicos, concertados, internacionales, FP, UNED y procedimiento para familias extranjeras.",
        "context": "Artículo detallado para familias y expatriados que necesitan información práctica sobre escolarización en la Costa Tropical.",
        "tags": ["educacion", "colegios", "internacional", "escolarizacion", "expatriados", "guia-practica", "vida-practica"],
        "category_id": CATEGORY_ID,
    }

    result = publish_trilingual(
        topic=topic,
        translations=translations,
        target_date=datetime.now(timezone.utc),
        featured_image_url=hero_url,
        gallery_json=gallery_json,
    )

    if result:
        logger.info(f"✅ Publication réussie: {result}")
    else:
        logger.error("❌ Échec de la publication")
        return 1

    # Vérification DB
    logger.info("=" * 60)
    logger.info("VÉRIFICATION DB")
    logger.info("=" * 60)
    import subprocess
    check = subprocess.run(
        ["sudo", "-u", "alejandro", "psql", "-d", "alejandro_db", "-c",
         "SELECT id, title_fr, slug, word_count, published_at::date FROM articles WHERE slug LIKE '%educacion%' OR slug LIKE '%education%' OR slug LIKE '%ecoles%' ORDER BY published_at DESC LIMIT 3;"],
        capture_output=True, text=True, timeout=10,
    )
    if check.returncode == 0:
        logger.info(f"📊 Vérification DB:\n{check.stdout}")
    else:
        logger.warning(f"⚠️ DB check: {check.stderr[:200]}")

    logger.info("🎉 Pipeline terminé avec succès !")
    return 0


if __name__ == "__main__":
    sys.exit(main())
