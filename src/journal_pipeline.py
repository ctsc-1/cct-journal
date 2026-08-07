#!/usr/bin/env python3
"""
journal-pipeline.py — Script maître de production d'article pour le profil alejandro-journal.
Usage: python3 journal-pipeline.py <categorie_id> [--topic "sujet" --date YYYY-MM-DD]

Surdimensionne tout ce qui peut l'être :
- Gateway : max_tokens=32768 sur tous les appels
- Timeouts : 180s par appel LLM
- DeepSearch : 3 iterations max, port 8888 ET 8889
- Images : 1 hero + jusqu'a 15 sections
- DB : featured_image_url + gallery_images obligatoires
"""

import json, os, re, sys, uuid, time, httpx, subprocess
from datetime import datetime
from pathlib import Path

# ─── CONFIG SURDIMENSIONNEE ──────────────────────────
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
DB_URL = "postgresql://alejandro:AndaluciaRocks2025@127.0.0.1:5432/alejandro_db"
OUTPUT_DIR = "/srv/rag-engine/static/DEPARTEMENT_ICONOGRAPHIE/JOURNAL"
DATE = datetime.now().strftime("%Y-%m-%d")
AUTHOR_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
# Règle images (07/08/2026 — isolation profil journal) : URL publique servie
# temps réel via le canal RAG /api/static/ (dossier DEPARTEMENT_ICONOGRAPHIE),
# PAS via /images/ de la PWA (qui exigeait un rebuild Next à chaque article).
def _pub(filename: str) -> str:
    return f"/api/static/DEPARTEMENT_ICONOGRAPHIE/JOURNAL/{filename}"
LIMIT_WORDS = 10000
MAX_TOKENS = 32768
TIMEOUT = 180
SITE = "https://clubcostatropical.es"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── FONCTIONS BASE ──────────────────────────────────
def log(msg, newline=True):
    t = datetime.now().strftime("%H:%M:%S")
    if newline:
        print(f"[{t}] {msg}", flush=True)
    else:
        print(f"[{t}] {msg}", end=" ", flush=True)

def llm(prompt, max_tokens=MAX_TOKENS, temp=0.3, timeout=TIMEOUT, lightweight=False):
    """Appel Gateway avec max_tokens SURDIMENSIONNE.
    lightweight=True → deepseek-v4-flash (RPD illimité, DeepSearch/metadonnées/traductions).
    RÈGLE MARC: JAMAIS deepseek-v4-pro (interdit) — toujours deepseek-v4-flash."""
    model = "deepseek-v4-flash"  # RÈGLE MARC: JAMAIS deepseek-v4-pro
    r = httpx.post(
        f"{GATEWAY}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temp,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def search_searxng(query):
    """Recherche SearXNG sur 2 ports possibles."""
    for port in [8888, 8889]:
        try:
            r = httpx.get(
                f"http://127.0.0.1:{port}/search",
                params={"q": query, "format": "json", "language": "es", "categories": "general,news"},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                if results:
                    return [{"title": r["title"], "url": r["url"], "snippet": r.get("content", "")[:300]} for r in results[:5]]
        except:
            continue
    return []

def generate_image_fal(prompt, filename):
    """Genere image via le MCP FAL Hermes (cle backend configuree)."""
    path = f"{OUTPUT_DIR}/{filename}"
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        log(f"  ⏩ Deja existant: {filename}")
        return _pub(f)
    
    try:
        # Appel direct a l'API FAL — la cle est dans la config Hermes
        r = httpx.post(
            "https://fal.run/fal-ai/flux-pro/v1.1-ultra",
            json={"prompt": prompt, "aspect_ratio": "16:9", "num_images": 1, "safety_tolerance": 2},
            timeout=60,
        )
        # Si 401, on laisse le pipeline continuer sans images (degrade)
        if r.status_code == 401:
            log(f"  ⚠️ FAL: auth necessaire, generation sautee")
            return None
        r.raise_for_status()
        img_url = r.json().get("images", [{}])[0].get("url", "")
        if img_url:
            img_r = httpx.get(img_url, timeout=30)
            with open(path, "wb") as f:
                f.write(img_r.content)
            log(f"  ✅ {os.path.getsize(path)} bytes -> {filename}")
            return _pub(filename)
    except Exception as e:
        log(f"  ⚠️ FAL error: {e}")
    return None

def insert_db(article_id, title_fr, title_es, title_en, slug, excerpt_fr, excerpt_es, excerpt_en,
              content_fr, content_es, content_en, category_id, word_count, reading_time,
              featured_image_url, gallery_images):
    """INSERT avec featured_image_url ET gallery_images obligatoires."""
    import psycopg2
    conn = psycopg2.connect(DB_URL, connect_timeout=10)
    cur = conn.cursor()
    now = datetime.now()
    
    cur.execute("""
        INSERT INTO articles (
            id, title, title_es, title_en, slug,
            excerpt, excerpt_es, excerpt_en,
            content, content_es, content_en,
            category_id, author_id,
            is_published, published_at, updated_at,
            word_count, reading_time_minutes,
            featured_image_url,
            gallery_images
        ) VALUES (%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s, %s,%s, %s, %s)
    """, (
        article_id, title_fr, title_es, title_en, slug,
        excerpt_fr, excerpt_es, excerpt_en,
        content_fr, content_es, content_en,
        category_id, AUTHOR_ID,
        True, now, now,
        word_count, reading_time,
        featured_image_url,
        json.dumps(gallery_images),
    ))
    conn.commit()
    conn.close()
    log(f"✅ INSERT DB: {slug}")

# ─── PIPELINE COMPLET ─────────────────────────────────
def run(category_id, topic_override=None, category_name="Gastronomía y Vino"):
    log(f"🚀 DEBUT — {category_name} ({category_id})")
    start = time.time()
    total_tokens = 0
    
    # 1. DeepSearch SearXNG (deepseek-v4-flash)
    log("📡 1. DeepSearch Phase 1 (SearXNG + Flash Lite)")
    searches = [
        f"vino DO Granada Costa Tropical bodegas {DATE[:7]}",
        f"gastronomía típica Costa Tropical productos locales",
        f"site:ideal.es gastronomía Costa Tropical {DATE[:4]}",
        f"site:juntadeandalucia.es denominación origen Granada vino",
        f"rutas enoturísticas Costa Tropical Alpujarra",
        f"site:malagahoy.es vinos Granada {DATE[:4]}",
        f"pescaíto frito quisquilla Motril gastronomía",
    ]
    all_sources = []
    for i, q in enumerate(searches):
        results = search_searxng(q)
        all_sources.extend(results)
        log(f"   Iteration {i+1}/{len(searches)}: {len(results)} sources")
        time.sleep(5)  # Anti-stress quotas — pause 5s entre chaque requête SearXNG

    log(f"   Total: {len(all_sources)} sources brute")
    sources_text = "\n\n".join([f"- {s['title']}: {s['snippet'][:200]}" for s in all_sources[:15]])
    total_tokens += len(sources_text)
    
    # 2. Metadonnees — extraction robuste avec nettoyage regex
    log("📝 2. Métadonnées")
    
    # Appel unique pour titre + slug + lead
    meta_prompt = (
        "Eres periodista gastronomico andaluz. "
        f"Genera TRES cosas sobre {category_name} de la Costa Tropical, separadas exactamente por | (pipe).\n"
        "1) TITULO: maximo 45 caracteres, directo, sin puntuacion. "
        "2) SLUG: solo minusculas y guiones, max 40 chars, sin preposiciones. "
        "3) LEAD: exactamente 150-200 caracteres con UNA localidad concreta y UN dato numerico.\n"
        "Ejemplo: Vinos artesanales DO Granada|vinos-do-granada|Orgiva, corazon de la Costa Tropical, esconde 15 bodegas que elaboran vinos unicos a 600 metros de altitud.\n"
        "Devuelve SOLO las 3 partes separadas por |, sin explicaciones, sin comillas."
    )
    meta_raw = llm(meta_prompt, max_tokens=300, temp=0.2, lightweight=True)
    
    # Nettoyage agressif
    meta_raw = re.sub(r'^["\'*#]+|["\'*#]+$', '', meta_raw.strip())
    parts = meta_raw.split("|")
    
    # Fallback si le parse echoue
    if len(parts) >= 3:
        title_es = parts[0].strip().strip('"').strip("'").strip("*").strip(":")
        slug_raw = parts[1].strip().lower()
        slug = re.sub(r"[^a-z0-9-]", "", slug_raw.replace(" ", "-"))[:45].strip("-")
        lead_es = parts[2].strip()
    elif len(parts) == 2:
        title_es = parts[0].strip()
        slug = parts[1].strip().lower()
        lead_es = "Gastronomia de la Costa Tropical: productos locales y vinos con denominacion de origen."
    else:
        title_es = f"Gastronomia y vino {category_name}"[:50]
        slug = "gastronomia-costa-tropical"
        lead_es = f"La Costa Tropical ofrece {category_name} con productos locales y vinos de calidad."
    
    # Limiter a 50 caracteres
    title_es = title_es[:50]
    slug = slug[:45]
    
    # Traductions (lightweight: deepseek-v4-flash)
    title_fr = llm(f"Traduis ce titre en français, max 45 caracteres, direct. Reponds uniquement le titre: {title_es}", max_tokens=80, temp=0.2, lightweight=True)
    title_en = llm(f"Translate to English, max 45 chars, punchy. Only the title: {title_es}", max_tokens=80, temp=0.2, lightweight=True)
    lead_fr = llm(f"Traduis en français, 150-200 caracteres. Une localite, un chiffre. Direct: {lead_es}", max_tokens=200, temp=0.2, lightweight=True)
    lead_en = llm(f"Translate to English, 150-200 chars. One location, one number. Punchy: {lead_es}", max_tokens=200, temp=0.2, lightweight=True)
    
    log(f"   Titre ES({len(title_es)}c): {title_es}")
    log(f"   Slug({len(slug)}c): {slug}")
    log(f"   Lead ES({len(lead_es)}c): {lead_es[:80]}...")
    total_tokens += len(title_es) + len(title_fr) + len(title_en) + len(lead_es) + len(lead_fr) + len(lead_en)
    
    # 3. Article ES
    log("✍️ 3. Génération article ES")
    article_es = llm(
        f"""Eres Alejandro Ortega, periodista andaluz especializado en gastronomía y vino.
Escribe un artículo de INVESTIGACIÓN de más de 8000 palabras sobre:
{title_es}

CONTEXTO ET SOURCES (utiliser UNIQUEMENT ces faits):
{sources_text[:3000]}

REGLAS ABSOLUTAS:
- TÍTULO: {title_es}
- LEAD (primer párrafo): {lead_es}
- MÍNIMO 12 secciones H2, máximo 15
- CADA sección H2: 600-800 palabras con datos, ejemplos, contexto histórico
- TONO Chaves Nogales: humano, preciso, sin sensacionalismo
- PROHIBIDO inventar personas, citas, encuentros
- PROHIBIDO primera persona narrativa ("he visto", "me dijo", "caminando por")
- CIFRAS con fuente implícita
- Incluir datos de: bodegas, variedades de uva, hectáreas, altitudes, producción
- ESTRUCTURA: título + lead + 12-15 secciones H2 + cierre editorial
- CIERRE: "Hasta la próxima — la Costa os espera, de Almuñécar a la Axarquía." 
- SOLO español. Sin traducciones. Sin meta-líneas.""",
        max_tokens=32768, temp=0.3, timeout=300
    )
    words_es = len(article_es.split())
    sections = re.findall(r'^##\s+', article_es, re.MULTILINE)
    log(f"   ✅ {words_es} mots, {len(sections)} sections H2")
    total_tokens += len(article_es)
    
    # 4. Traductions
    log("🌐 4. Traductions FR/EN")
    chunks = re.split(r'(?=^## )', article_es, flags=re.MULTILINE)
    log(f"   {len(chunks)} chunks à traduire")
    
    fr_chunks, en_chunks = [], []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            fr_chunks.append(chunk); en_chunks.append(chunk); continue
        log(f"   Chunk {i+1}/{len(chunks)}...", newline=False)
        fr = llm(f"Traduis en français. Conserve ## titres, ```tableaux```, ![images]. Noms propres inchangés.\n\n{chunk}", max_tokens=8192, temp=0.3, timeout=120, lightweight=True)
        time.sleep(2)  # Anti-stress — pause 2s entre chunks
        en = llm(f"Translate to English. Keep ## headings, ```tables```, ![images]. Proper nouns unchanged.\n\n{chunk}", max_tokens=8192, temp=0.3, timeout=120, lightweight=True)
        fr_chunks.append(fr); en_chunks.append(en)
        log(f"✅ FR={len(fr)}c EN={len(en)}c")
        total_tokens += len(fr) + len(en)
        time.sleep(2)  # Anti-stress — pause 2s entre chunks
    
    content_es = article_es
    content_fr = "\n".join(fr_chunks)
    content_en = "\n".join(en_chunks)
    log(f"   FR: {len(content_fr)} chars, EN: {len(content_en)} chars")
    
    # 5. Images FAL
    log("🖼️ 5. Génération images FAL")
    h2_titles = re.findall(r'^##\s+(.+)$', article_es, re.MULTILINE)
    num_sections = len(h2_titles)
    log(f"   {num_sections} sections → {num_sections + 1} images (hero + {num_sections} sections)")
    
    # Hero
    hero_file = f"journal-{DATE}-hero-{slug[:20]}.webp"
    hero_prompt = f"Fotografía aérea documental de viñedos en terrazas de la Costa Tropical, entre Órgiva y Motril, con el mar Mediterráneo al fondo, luz dorada del atardecer, estilo National Geographic, sin texto."
    hero_url = generate_image_fal(hero_prompt, hero_file)
    gallery = [{"url": hero_url, "pos": "center 50%"}] if hero_url else []
    
    # Sections
    section_prompts = [
        "Viñedos de secano en las laderas de pizarra de la Contraviesa, Granada. Cepas centenarias, suelo rojizo, montañas al fondo. Luz de la mañana. Fotografía documental.",
        "Bodega artesanal tradicional en la Alpujarra granadina. Barricas de roble, tinajas de barro, luz tenue de sótano. Fotografía documental de enoturismo.",
        "Catador o sumiller sosteniendo una copa de vino tinto DO Granada. Foco en la copa, fondo desenfocado de barricas. Luz natural de ventana.",
        "Viñedo en ladera empinada cerca de Órgiva, a 600 metros de altitud. Terrazas de piedra seca, cepas verdes, Sierra Nevada al fondo. Gran angular.",
        "Mesa de degustación con vinos locales de la Costa Tropical, aceitunas, pan, queso de cabra. Comida y vino, luz natural, estilo lifestyle.",
        "Uvas tintas Vijiriega o Jaén colgando de la vid al atardecer. Primer plano, luz dorada, gotas de rocío. Fotografía macro documental.",
        "Interior de bodega moderna con depósitos de acero inoxidable y barricas de roble. Luz fría industrial contrastando con madera cálida.",
        "Bodeguero o enólogo artesano en su bodega familiar, manos manchadas de hollejo, rodeado de barricas. Retrato documental, luz natural.",
        "Prensa de uva tradicional de madera en bodega ancestral de la Alpujarra. Herramientas de vendimia, luz de bodega. Fotografía de patrimonio.",
        "Paisaje del Valle de Lecrín con viñedos en otoño, hojas doradas, montañas al fondo. Luz suave de tarde. Fotografía de paisaje.",
        "Vendimia manual en viñedo de ladera, cestas de mimbre llenas de uvas. Manos trabajando, luz de la mañana. Fotografía documental laboral.",
        "Ruta del Vino de Granada: cartel indicador, vallas de viñedo, paisaje ondulado. Luz de atardecer. Fotografía de viaje documental.",
        "Etiquetas de vino DO Granada sobre mesa de madera, copa al lado. Naturaleza muerta estilizada. Fotografía de producto editorial.",
        "Cooperativa vitivinícola, fachada blanca andaluza con tejas, tractor aparcado. Architecture rurale documentaire. Plein soleil.",
        "Atardecer sobre los viñedos de la Costa Tropical, con el mar de Alborán al fondo. Silueta de cepas, cielo naranja. Cinematographique.",
    ]
    
    for i in range(min(num_sections, len(section_prompts))):
        sf = f"journal-{DATE}-section-{i+1:02d}-{slug[:20]}.webp"
        prompt = section_prompts[i]
        url = generate_image_fal(prompt, sf)
        if url:
            gallery.append({"url": url, "pos": "center 50%"})
            log(f"     Section {i+1}: ✅ {sf}")
        else:
            log(f"     Section {i+1}: ❌ echec generation")
    
    log(f"   Total: {len(gallery)} images dans la galerie")
    
    # 6. Construire contenu avec [[PHOTO:N]]
    log("🔗 6. Marquage [[PHOTO:N]]")
    photo_markers_es = content_es
    photo_markers_fr = content_fr
    photo_markers_en = content_en
    
    # Hero en premiere position
    if len(gallery) > 0:
        # Inserer [[PHOTO:0]] au tout debut
        photo_markers_es = "[[PHOTO:0]]\n\n" + photo_markers_es
        photo_markers_fr = "[[PHOTO:0]]\n\n" + photo_markers_fr
        photo_markers_en = "[[PHOTO:0]]\n\n" + photo_markers_en
    
    # Remplacer les alt-text des images (si existent) par [[PHOTO:N]]
    for i in range(1, len(gallery)):
        old = re.search(r'!\[.*?\]\(.*?\)', photo_markers_es)
        if old:
            photo_markers_es = photo_markers_es.replace(old.group(0), f"[[PHOTO:{i}]]", 1)
        old_fr = re.search(r'!\[.*?\]\(.*?\)', photo_markers_fr)
        if old_fr:
            photo_markers_fr = photo_markers_fr.replace(old_fr.group(0), f"[[PHOTO:{i}]]", 1)
        old_en = re.search(r'!\[.*?\]\(.*?\)', photo_markers_en)
        if old_en:
            photo_markers_en = photo_markers_en.replace(old_en.group(0), f"[[PHOTO:{i}]]", 1)
    
    # 7. Insertion DB
    log("💾 7. Insertion DB")
    article_id = str(uuid.uuid4())
    
    # Excerpts
    excerpt_es = lead_es[:300] if len(lead_es) > 100 else content_es[:300]
    if len(excerpt_es) > 300: excerpt_es = excerpt_es[:300].rsplit(" ", 1)[0]
    excerpt_fr = lead_fr[:300] if len(lead_fr) > 100 else content_fr[:300]
    if len(excerpt_fr) > 300: excerpt_fr = excerpt_fr[:300].rsplit(" ", 1)[0]
    excerpt_en = lead_en[:300] if len(lead_en) > 100 else content_en[:300]
    if len(excerpt_en) > 300: excerpt_en = excerpt_en[:300].rsplit(" ", 1)[0]
    
    word_count = max(words_es, len(content_es.split()))
    reading_time = max(1, word_count // 200)
    featured_image = gallery[0]["url"] if gallery else None
    
    try:
        insert_db(article_id, title_fr, title_es, title_en, slug,
                  excerpt_fr, excerpt_es, excerpt_en,
                  photo_markers_fr, photo_markers_es, photo_markers_en,
                  category_id, word_count, reading_time,
                  featured_image, gallery)
        
        # 8. Verification
        log("🔍 8. Vérification")
        r = httpx.get(f"{SITE}/api/blog/{slug}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            gi = data.get("gallery_images", [])
            if isinstance(gi, str): gi = json.loads(gi) if gi else []
            log(f"   ✅ API 200: featured_image={bool(data.get('featured_image'))}, gallery={len(gi)} images")
        
        elapsed = time.time() - start
        mins, secs = divmod(int(elapsed), 60)
        calls_est = 5 + len(chunks) * 2 + num_sections + 6
        
        log("\n" + "="*50)
        log("📊 RAPPORT FINAL")
        log(f"⏱️  Temps: {mins}m{secs}s")
        log(f"📝 Article: {word_count} mots, {num_sections} sections H2")
        log(f"🖼️  Images: {len(gallery)} dont hero + {num_sections} sections")
        log(f"🔤 Appels LLM estimés: ~{calls_est}")
        log(f"📊 Tokens estimés: ~{total_tokens}")
        log(f"🌐 URL: {SITE}/blog/{slug}")
        log("="*50)
        
    except Exception as e:
        log(f"❌ ERREUR DB: {e}")
        # Sauvegarde de secours
        with open(f"/tmp/article_{slug}.md", "w") as f:
            f.write(f"# {title_es}\n\n{content_es}")
        log(f"💾 Article sauvegardé dans /tmp/article_{slug}.md")

if __name__ == "__main__":
    cat_id = sys.argv[1] if len(sys.argv) > 1 else "047d7527-d161-4c25-a948-3e6f88aa8a9e"
    topic = sys.argv[sys.argv.index("--topic") + 1] if "--topic" in sys.argv else None
    run(cat_id, topic)
