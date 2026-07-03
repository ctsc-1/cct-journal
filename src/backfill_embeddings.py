"""
Script one-shot : rattrape les embeddings manquants pour les documents
insérés sans embedding (table embeddings_doc vide pour certains doc_ids).

Usage:  sudo -u cct-journal .venv/bin/python src/backfill_embeddings.py
"""
import logging
from publish import _embed, _pg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cct-journal.backfill")

with _pg() as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT d.id, d.langue, d.titre, d.contenu
        FROM documents d
        LEFT JOIN embeddings_doc e ON e.document_id = d.id
        WHERE e.document_id IS NULL
          AND d.meta->>'pipeline' IN ('cct-journal', 'cct-revue-presse')
        ORDER BY d.id DESC
    """)
    rows = cur.fetchall()
    log.info(f"{len(rows)} docs à backfiller")
    for doc_id, langue, titre, contenu in rows:
        log.info(f"Embed doc_id={doc_id} ({langue}) — {titre[:50]}")
        emb = _embed(f"{titre}\n\n{contenu}", caller=f"backfill-{langue}")
        if emb:
            cur.execute("""
                INSERT INTO embeddings_doc (document_id, embedding)
                VALUES (%s, %s::halfvec)
                ON CONFLICT (document_id) DO UPDATE SET embedding = EXCLUDED.embedding
            """, (doc_id, emb))
            log.info(f"  ✓ inséré ({len(emb)} dims)")
        else:
            log.error(f"  ✗ embedding KO")
    conn.commit()
log.info("Backfill done.")
