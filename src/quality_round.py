#!/usr/bin/env python3
"""
quality_round.py — Ronde qualité nocturne du Journal CCT.

Vérifie les articles publiés le jour même :
  - Fact-check ES via fact_check_es.fact_check()
  - Cohérence FR/EN : fact_check() sur les traductions
  - Sections non traduites dans FR/EN (titres ## en espagnol résiduels)
  - Intégrité des images (featured_image_url, gallery_images)

Usage:
    python3 quality_round.py

Le rapport est imprimé sur stdout.
Cron Hermes prévu : 0 22 * * * (heure Madrid)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras

# ─── Ajout du chemin src pour l'import fact_check ──────────
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from fact_check_es import fact_check  # noqa: E402

# ─── Configuration ──────────────────────────────────────────
PG_PWD_FILE = "/etc/cct-journal/pg.pwd"
PG_HOST = "localhost"
PG_PORT = 5432
PG_DB = "alejandro_db"
PG_USER = "alejandro"

TZ_MADRID = timezone.utc  # We'll use UTC and rely on cron TZ; DB stores UTC


def load_pg_password() -> str:
    """Lit le mot de passe PostgreSQL depuis le fichier sécurisé."""
    try:
        with open(PG_PWD_FILE) as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError) as exc:
        print(f"ERREUR: Impossible de lire {PG_PWD_FILE}: {exc}", file=sys.stderr)
        sys.exit(1)


def connect_db() -> psycopg2.extensions.connection:
    """Retourne une connexion PostgreSQL."""
    pwd = load_pg_password()
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=pwd,
        )
        conn.set_session(readonly=True)
        return conn
    except psycopg2.Error as exc:
        print(f"ERREUR: Connexion PostgreSQL échouée: {exc}", file=sys.stderr)
        sys.exit(1)


def get_today_articles(conn: psycopg2.extensions.connection) -> list[dict[str, Any]]:
    """Récupère les articles publiés aujourd'hui (CURRENT_DATE)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT slug, title, content_es, content_fr, content_en,
                   featured_image_url, gallery_images, published_at
            FROM articles
            WHERE is_published = true
              AND published_at::date = CURRENT_DATE
            ORDER BY published_at ASC
            """
        )
        return list(cur.fetchall())


def detect_untranslated_sections(content: str, lang_label: str) -> list[str]:
    """Détecte les sections '##' qui semblent être en espagnol (non traduites)."""
    suspicious: list[str] = []
    # Mots-clés espagnols typiques dans les titres (vs FR/EN)
    es_markers = [
        r'\bel\b', r'\bla\b', r'\blos\b', r'\blas\b',
        r'\bdel\b', r'\ben\b', r'\bde\b', r'\bpor\b',
        r'\by\b', r'\bque\b', r'\buna\b', r'\bun\b',
        r'\bal\b', r'\blo\b', r'\bse\b', r'\ble\b',
        r'\bcon\b', r'\bpara\b', r'\bentre\b', r'\bsobre\b',
        r'\bhasta\b', r'\bdesde\b', r'\bsin\b',
        # Termes espagnols non ambigus
        r'\baño\b', r'\baños\b', r'\bverano\b',
        r'\btemporada\b', r'\balcalde\b', r'\bmunicipio\b',
        r'\bcomarca\b', r'\bprovincia\b', r'\bturismo\b',
        r'\bcosta\b', r'\btropical\b', r'\bplaza\b',
        r'\bferia\b', r'\bsenderos\b', r'\bmedio\b',
        r'\bagua\b', r'\bmar\b', r'\bplaya\b', r'\bpuerto\b',
        r'\bpueblo\b', r'\bpueblos\b', r'\bgranada\b',
        r'\bandalucía\b', r'\bvega\b', r'\bsierra\b',
        r'\bnaturaleza\b', r'\bdiputación\b',
    ]

    # Détecte les lignes qui sont des titres markdown ##
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('## '):
            title_text = stripped[3:].strip()
            if not title_text:
                continue
            # Compter les marqueurs espagnols dans le titre
            es_count = sum(1 for marker in es_markers if re.search(marker, title_text, re.IGNORECASE))
            # Si >= 2 marqueurs espagnols, probablement non traduit
            if es_count >= 2:
                suspicious.append(title_text)

    return suspicious


def check_images(row: dict[str, Any]) -> dict[str, Any]:
    """Vérifie l'intégrité des images de l'article."""
    result: dict[str, Any] = {"hero_ok": False, "gallery_count": 0}

    # featured_image_url
    fiu = row.get("featured_image_url")
    if fiu and isinstance(fiu, str) and len(fiu.strip()) > 0:
        result["hero_ok"] = True
        result["hero_url"] = fiu

    # gallery_images
    gi = row.get("gallery_images")
    if gi is not None:
        if isinstance(gi, str):
            try:
                gi = json.loads(gi)
            except (json.JSONDecodeError, TypeError):
                gi = []
        if isinstance(gi, list):
            result["gallery_count"] = len(gi)
        else:
            result["gallery_count"] = 0
    else:
        result["gallery_count"] = 0

    return result


def produce_report(articles: list[dict[str, Any]]) -> str:
    """Produit le rapport structuré de la ronde qualité."""
    today_str = date.today().isoformat()
    lines: list[str] = [
        f"--- Ronde Qualité — {today_str} ---",
        "",
    ]

    total_ok = 0
    total_alert = 0
    total_articles = len(articles)

    for row in articles:
        slug = row["slug"]
        title = row["title"] or slug
        es = row.get("content_es") or ""
        fr = row.get("content_fr") or ""
        en = row.get("content_en") or ""

        # ── Fact-check ES ──
        es_alerts = fact_check(es) if es else []
        es_alert_count = len(es_alerts)
        if es_alerts:
            es_detail = "; ".join(
                f"[{a['type']}] {a['entite']}: trouvé '{a['valeur_trouvee'][:40]}', attendu '{a['valeur_attendue'][:40]}'"
                for a in es_alerts[:5]
            )
            if len(es_alerts) > 5:
                es_detail += f" … (+{len(es_alerts) - 5} autres)"
        else:
            es_detail = "Aucune anomalie"

        # ── Cohérence FR ──
        fr_alerts = fact_check(fr) if fr else []
        fr_alert = len(fr_alerts) > 0
        fr_sections = detect_untranslated_sections(fr, "FR") if fr else []
        fr_problematic = fr_alert or len(fr_sections) > 0
        if not fr:
            fr_status = "NON DISPONIBLE"
        elif not fr_problematic:
            fr_status = "OK"
        else:
            parts = []
            if fr_alert:
                parts.append(f"{len(fr_alerts)} alerte(s) factuel(les)")
            if fr_sections:
                parts.append(f"{len(fr_sections)} section(s) suspecte(s)")
            fr_status = f"PROBLÈME ({'; '.join(parts)})"

        # ── Cohérence EN ──
        en_alerts = fact_check(en) if en else []
        en_alert = len(en_alerts) > 0
        en_sections = detect_untranslated_sections(en, "EN") if en else []
        en_problematic = en_alert or len(en_sections) > 0
        if not en:
            en_status = "NON DISPONIBLE"
        elif not en_problematic:
            en_status = "OK"
        else:
            parts = []
            if en_alert:
                parts.append(f"{len(en_alerts)} alerte(s) factuel(les)")
            if en_sections:
                parts.append(f"{len(en_sections)} section(s) suspecte(s)")
            en_status = f"PROBLÈME ({'; '.join(parts)})"

        # ── Images ──
        img = check_images(row)
        hero_status = "OK" if img["hero_ok"] else "NOK"
        img_status = f"hero={hero_status}, galerie={img['gallery_count']} image(s)"

        # ── Verdict ──
        total_problems = es_alert_count + (1 if fr_problematic else 0) + (1 if en_problematic else 0) + (0 if img["hero_ok"] else 1)
        if total_problems == 0:
            verdict = "✅"
            total_ok += 1
        elif total_problems <= 2:
            verdict = "⚠️"
            total_alert += 1
        else:
            verdict = "❌"
            total_alert += 1

        lines.append(f"Article: {title} ({slug})")
        lines.append(f"  Fact-check ES: {es_alert_count} alerte(s) — {es_detail}")
        lines.append(f"  Cohérence FR: {fr_status}")
        lines.append(f"  Cohérence EN: {en_status}")
        lines.append(f"  Images: {img_status}")
        lines.append(f"  Verdict: {verdict}")
        lines.append("")

    if total_articles == 0:
        lines.append("Aucun article publié aujourd'hui.")
        lines.append("")
        total_ok = 0
        total_alert = 0
    else:
        lines.append(f"Résumé: {total_articles} articles vérifiés, {total_ok} ✅ OK, {total_alert} ⚠️/❌ alertes")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    conn = connect_db()
    try:
        articles = get_today_articles(conn)
        report = produce_report(articles)
        print(report)
    finally:
        conn.close()

    # Exit code: 0 si tout OK, 1 si au moins une alerte
    if any(es := a.get("content_es", "") for a in articles):
        # On vérifie le nombre total d'alertes
        total_alerts = 0
        for a in articles:
            es = a.get("content_es", "") or ""
            total_alerts += len(fact_check(es))
        if total_alerts > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()