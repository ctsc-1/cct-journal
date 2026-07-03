#!/usr/bin/env python3
"""Pipeline mascotas article - ES generation, FR/EN translation, images, publish."""
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
logger = logging.getLogger("generate-mascotas")

GATEWAY_URL = "http://127.0.0.1:4000"
GEN_MODEL = "gemini-3-flash-preview"
TRANS_MODEL = "gemini-3.1-flash-lite-preview"

GATEWAY_KEY = os.environ.get("GATEWAY_KEY")
if not GATEWAY_KEY:
    try:
        GATEWAY_KEY = open("/etc/gateway-secrets/gemini-paid.key").read().strip()
    except Exception:
        pass

HEADERS = {"Authorization": f"Bearer {GATEWAY_KEY}"} if GATEWAY_KEY else {}

CATEGORY_ID = "4a45150c-8404-4598-ba31-abee29ab40d8"

DEEP_CONTEXT = """## INFORME DE INVESTIGACION: Mascotas y animales de compania en la Costa Tropical (Granada)

### Microchip e identificacion obligatoria (REIAC/RCEE)
- **Identificacion obligatoria** en Espana: perros, gatos y hurones deben llevar microchip (ISO 11784/11785).
- Registro en el REIAC o RCEE autonomico. En Andalucia: RIA (Registro de Identificacion Animal de Andalucia).
- El microchip debe implantarlo un veterinario colegiado. Coste: 30-50 euros.
- Multas por no identificar: hasta 3.000 euros en Andalucia (Ley 11/2003 de Proteccion de Animales).

### Vacunas obligatorias
- **Rabia**: obligatoria en Andalucia (Decreto 65/2012). Anual. Primera dosis a partir de los 3 meses.
- Calendario: primeras vacunas a las 6-8 semanas, refuerzo a las 12-16 semanas.
- Precio consulta + vacuna: 40-70 euros.

### Pasaporte Europeo para Animales de Compania
- Formato oficial UE (Reglamento 576/2013). Azul, expedido por veterinarios autorizados.
- Requisitos: microchip ISO, vacuna antirrabica vigente (min. 21 dias antes del viaje), tratamiento antiequinococo.
- Precio: 30-50 euros.

### Transporte aereo a Espana
- **Iberia**: mascotas en cabina (hasta 8kg, transportin rigido 45x39x21cm, max 1 por pasajero, 100-150 euros). Bodega para perros >8kg.
- **Vueling**: mascotas en cabina (hasta 8kg, 45x39x21cm, max 2 por vuelo). 50-70 euros online.
- **Air France**: mascotas en cabina (hasta 8kg, 55x40x24cm, ~60 euros). Bodega con previo aviso.
- **Ryanair**: SOLO en bodega (hasta 75kg viajero+perro). NO cabina excepto perros guia. ~50 euros por trayecto.
- **Restricciones por raza**: muchas aerolineas no aceptan bulldogs, carlinos, gatos persas (razas braquicefalicas).

### Viaje en coche desde Francia a Espana
- Autopistas de peaje en Francia y Espana (AP-7, AP-4).
- Obligatorio sistema de retencion para perros (transportin, rejilla, arnes). Multas: 200-500 euros.
- Paradas cada 2-3 horas.
- Itinerario: Perpinan - Barcelona - Valencia - Almeria - Costa Tropical (10-12h desde frontera francesa).

### Veterinarios en la Costa Tropical
**Motril:**
- Clinica Veterinaria Motril: Av. de Salobrena, 21 | Tel: 958 82 32 88
- Clinica Veterinaria Taoro: C. Londres, 1 | C. Obispo, 6 | Tel: 958 06 90 54
- Clinica Veterinaria Talisman: C. Victoria, 8 | Tel: 958 05 64 39
- Clinica Veterinaria Canisur: Cam. de San Antonio, 5 | Tel: 610 79 00 79
- Clinica Veterinaria Animal's: C. Cruces, 2 | Tel: 958 56 82 89
- Centro Veterinario Velez: Av. Rambla de los Alamos, 8 | Tel: 634 41 93 35
- Telemascota: C. Rodriguez Acosta, 4 | Tel: 687 57 62 12
- SPA MASCOTAS: Poligono El Vadillo, C. Rio de Janeiro, 1 | Tel: 958 83 66 59

**Almunecar:**
- Clinica Veterinaria Kelibia: Pl. Madrid, 1 | Tel: 958 88 00 22
- SOS Animales Cantalobos: Cortijo de Cantalobos | Web: sosanimalessalobrena.com

**La Herradura:**
- MUNAi-VET (veterinaria a domicilio): Tel: 678 82 17 74
- Guarderia canina: Tel: 622 09 60 74

**Castell de Ferro:**
- Veterinario Castell: Carretera Malaga, 30

### Refugios y asociaciones
- SOS Animales Cantalobos (Almunecar): Refugio y protectora. sosanimalessalobrena.com
- SOS Animales Motril: Asociacion protectora

### Playas para perros (playas caninas)
- Playa Canina de Motril (Playacan): Puerto, 11, Motril
- Playa del Cable (Almunecar): zona habilitada para perros
- En verano (junio-septiembre) muchas playas prohiben perros

### Reglamentacion municipal
- Ley 11/2003 de Proteccion de Animales de Andalucia
- Recogida obligatoria de excrementos (multas 300-750 euros)
- Correa obligatoria en espacios publicos
- Razas PPP: bozal obligatorio, licencia administrativa, seguro"""


def gateway_call(model: str, system: str, user: str, timeout: int = 180) -> str:
    combined = f"{system}\n\n---\n\n{user}" if system else user
    try:
        r = httpx.post(
            f"{GATEWAY_URL}/v1/generate",
            json={"model": model, "contents": combined, "caller": "generate-mascotas"},
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


SYSTEM_ES = """Eres **Alejandro Ortega**, periodista andaluz y redactor jefe del Club Costa Tropical.
Escribes en la tradicion de **Manuel Chaves Nogales**: humanista, preciso, sin sensacionalismo, con un toque de ironia fina.

Hoy escribes un **articulo de guia practica** -- no una cronica personal, sino un dossier util, completo y bien documentado para familias, expatriados y residentes que quieren entender todo sobre las mascotas y animales de compania en la Costa Tropical.

**REGLAS ABSOLUTAS:**
1. Escribe SOLO en espanol. No produzcas version FR/EN. No traduzcas nada.
2. **ESTRUCTURA OBLIGATORIA:**
   - Titulo H1: `# Mascotas en la Costa Tropical: guia completa`
   - Chapo GEO-first (40-80 palabras) que empieza con respuesta directa con datos y cifras clave
   - Secciones H2 para cada tema
   - Cierre editorial
3. **LONGITUD: 1500-2000 palabras** -- debe ser exhaustivo.
4. **SECCIONES H2 OBLIGATORIAS** (en este orden):
   - `## Microchip e identificacion obligatoria`
   - `## Vacunas y calendario veterinario`
   - `## Pasaporte europeo para animales de compania`
   - `## Viajar con mascotas: avion y coche`
   - `## Veterinarios en la Costa Tropical` (con direcciones, telefonos, subsecciones por municipio)
   - `## Refugios, asociaciones y protectoras`
   - `## Guarderia, pet sitting y cuidados`
   - `## Playas para perros en la Costa Tropical`
   - `## Normativa municipal sobre mascotas`
5. **DATOS CONCRETOS:** usa direcciones exactas, telefonos, webs de los veterinarios reales de la Costa Tropical.
6. **MAPAS PWA:** Integra enlaces al mapa PWA con el formato: punto-rojo [Nombre](/map?lat=LAT&lng=LNG&name=Nombre&article=mascotas-costa-tropical)
   Coordenadas a usar:
   - Clinica Veterinaria Motril: 36.7470, -3.5185
   - Clinica Veterinaria Taoro: 36.7455, -3.5210
   - Clinica Veterinaria Talisman: 36.7460, -3.5195
   - Clinica Veterinaria Canisur: 36.7465, -3.5170
   - Centro Veterinario Velez: 36.7458, -3.5200
   - SOS Animales Cantalobos: 36.7300, -3.6940
   - Clinica Veterinaria Kelibia: 36.7345, -3.6885
   - Playa Canina Motril: 36.7400, -3.5050
7. **CIERRE EDITORIAL OBLIGATORIO:** *"Hasta la proxima -- la Costa os espera, de Almunecar a la Axarquia."*
8. Sin meta-lineas de traduccion al final."""

USER_ES = f"""Fecha: {datetime.now(timezone.utc).strftime('%-d de %B de %Y').lower()}
Tema: Mascotas en la Costa Tropical: guia completa
Categoria: Vie Pratique (Guia practica para residentes y expatriados)

**Contexto de investigacion:**
{DEEP_CONTEXT[:8000]}

Escribe el articulo completo en markdown. Minimo 1500 palabras, maximo 2000. Incluye los enlaces punto-rojo a los mapas PWA en cada veterinario y refugio. Se exhaustivo pero practico -- esta guia sera leida por familias que estan considerando mudarse a la Costa Tropical con sus mascotas o que ya viven alli."""


SYSTEM_TRANS_FR = """Eres traductor profesional de cultura andaluza.

Traduce **integramente** el texto markdown siguiente del espanol al frances, preservando:
- La estructura markdown (H1/H2/H3, parrafos, italicas, negritas)
- Los nombres propios (Motril, Almunecar, Salobrena, La Herradura, etc.)
- Los enlaces a mapas punto-rojo (no los traduzcas ni modifiques)
- El tono practico de guia util

ADAPTA:
- El titulo H1: `# Animaux de Compagnie sur la Costa Tropical : guide complet`
- El cierre editorial: *"A la prochaine -- la Costa vous attend, d'Almunecar a la Axarquia."*

Traduce SOLO el texto. No anadas ni quites nada."""

SYSTEM_TRANS_EN = """Eres traductor profesional de cultura andaluza.

Traduce **integramente** el texto markdown siguiente del espanol al ingles, preservando:
- La estructura markdown (H1/H2/H3, parrafos, italicas, negritas)
- Los nombres propios (Motril, Almunecar, Salobrena, La Herradura, etc.)
- Los enlaces a mapas punto-rojo (no los traduzcas ni modifiques)
- El tono practico de guia util

ADAPTA:
- El titulo H1: `# Pets on the Costa Tropical: Complete Guide`
- El cierre editorial: *"Until next time -- the Costa awaits you, from Almunecar to the Axarquia."*

Traduce SOLO el texto. No anadas ni quites nada."""


def strip_trilingual_tail(text: str) -> str:
    patterns = [
        r"\n\s*---\s*\n\s*###?\s*(Traducciones?|Translations?|FR|EN|Francais|English|Anglais)\b.*",
        r"\n\s*###?\s*(Traducciones?|Translations?|FR|EN|Francais|English|Anglais)\s*:?\s*\n.*",
    ]
    for pat in patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def main():
    logger.info("=" * 60)
    logger.info("ETAPE 1/4 : Generation de l'article en espagnol")
    logger.info("=" * 60)

    es_text = gateway_call(GEN_MODEL, SYSTEM_ES, USER_ES, timeout=300)
    es_text = strip_trilingual_tail(es_text)
    es_words = len(es_text.split())
    logger.info(f"OK ES genere: {len(es_text)} chars ({es_words} mots)")
    
    with open("/tmp/mascotas-es.md", "w") as f:
        f.write(es_text)
    logger.info("   Sauvegarde dans /tmp/mascotas-es.md")

    logger.info("=" * 60)
    logger.info("ETAPE 2/4 : Traduction FR et EN")
    logger.info("=" * 60)

    fr_text = gateway_call(TRANS_MODEL, SYSTEM_TRANS_FR, es_text, timeout=300)
    fr_text = strip_trilingual_tail(fr_text)
    fr_words = len(fr_text.split())
    logger.info(f"OK FR traduit: {len(fr_text)} chars ({fr_words} mots)")
    with open("/tmp/mascotas-fr.md", "w") as f:
        f.write(fr_text)

    en_text = gateway_call(TRANS_MODEL, SYSTEM_TRANS_EN, es_text, timeout=300)
    en_text = strip_trilingual_tail(en_text)
    en_words = len(en_text.split())
    logger.info(f"OK EN traduit: {len(en_text)} chars ({en_words} mots)")
    with open("/tmp/mascotas-en.md", "w") as f:
        f.write(en_text)

    # Etape 3 : Planification narrative + Images
    logger.info("=" * 60)
    logger.info("ETAPE 3/4 : Planification narrative et images")
    logger.info("=" * 60)

    sys.path.insert(0, "/srv/cct-journal/src")
    from narrative_planner import plan_images
    from images import generate_article_images

    title_es = "Mascotas en la Costa Tropical: guia completa"
    text_with_markers, plan, raw_llm = plan_images(es_text, title_es)
    logger.info(f"   Plan: {len(plan)} image(s) planifiee(s)")

    slug = "mascotas-costa-tropical-guia-completa"

    hero_url, gallery_json, text_with_images = generate_article_images(
        text_with_markers, plan, slug
    )
    logger.info(f"   Hero: {hero_url}")
    logger.info(f"   Gallery: {gallery_json[:100]}...")

    from narrative_planner import plan_images_for_lang
    fr_with_images = plan_images_for_lang(text_with_images, fr_text)
    en_with_images = plan_images_for_lang(text_with_images, en_text)

    translations = {
        "es": text_with_images,
        "fr": fr_with_images,
        "en": en_with_images,
    }

    with open("/tmp/mascotas-es-final.md", "w") as f:
        f.write(text_with_images)
    with open("/tmp/mascotas-fr-final.md", "w") as f:
        f.write(fr_with_images)
    with open("/tmp/mascotas-en-final.md", "w") as f:
        f.write(en_with_images)

    # Etape 4 : Publication
    logger.info("=" * 60)
    logger.info("ETAPE 4/4 : Publication")
    logger.info("=" * 60)

    from publish import publish_trilingual
    
    topic = {
        "id": slug,
        "domain": "vida-practica",
        "title": title_es,
        "angle": "Guia practica completa sobre mascotas y animales de compania en la Costa Tropical: microchip, vacunas, pasaporte europeo, veterinarios, refugios, playas caninas y normativa municipal.",
        "context": "Articulo detallado para familias y expatriados que necesitan informacion practica sobre tenencia de mascotas en la Costa Tropical, incluyendo veterinarios locales con direcciones y telefonos.",
        "tags": ["mascotas", "animales", "veterinarios", "perros", "gatos", "playas-caninas", "guia-practica", "vida-practica", "expatriados"],
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
        logger.info(f"OK Publication reussie: {result}")
    else:
        logger.error("KO Echec de la publication")
        return 1

    # Verification DB
    logger.info("=" * 60)
    logger.info("VERIFICATION DB")
    logger.info("=" * 60)
    import subprocess
    check = subprocess.run(
        ["sudo", "-u", "alejandro", "psql", "-d", "alejandro_db", "-c",
         "SELECT id, title_fr, slug, word_count, published_at::date FROM articles WHERE slug LIKE '%%mascotas%%' OR slug LIKE '%%animaux%%' OR slug LIKE '%%pets%%' ORDER BY published_at DESC LIMIT 3;"],
        capture_output=True, text=True, timeout=10,
    )
    if check.returncode == 0:
        logger.info(f"Data Verification DB:\n{check.stdout}")
    else:
        logger.warning(f"Warning DB check: {check.stderr[:200]}")

    logger.info("Pipeline termine avec succes !")
    return 0


if __name__ == "__main__":
    sys.exit(main())
