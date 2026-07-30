#!/usr/bin/env python3
"""
image_postprocess.py — Post-processing des images FAL:
1. Conversion PNG → WebP (PIL)
2. Déplacement du PNG original sur Google Drive
3. Nettoyage du fichier temporaire
"""

import os
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:
    subprocess.check_call(["pip", "install", "-q", "Pillow"])
    from PIL import Image

# ─── CONFIG GDRIVE ──────────────────────────────────────────
GDRIVE_PARENT_ID = "1k8ZHtBdMNPmhYUgKmLrOXX0J-mPbTeh0"  # Dossier Journal CCT
RCLONE_REMOTE = "gdrive:"
RCLONE_JOURNAL = f"{RCLONE_REMOTE}Journal-CCT/"

# ─── CONVERSION ──────────────────────────────────────────────
def convert_to_webp(png_path: Path) -> Optional[Path]:
    """
    Convertit un PNG en WebP à partir du chemin physique.
    Le fichier source PEUT être un PNG avec l'extension .webp (bug FAL).
    Retourne le chemin du WebP ou None.
    """
    if not png_path.exists():
        return None

    webp_path = png_path.with_suffix(".webp")

    # Si déjà un vrai WebP
    try:
        if png_path.stat().st_size < 50:
            return None
        with open(png_path, "rb") as f:
            header = f.read(12)
            if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
                # Déjà un vrai WebP — rien à faire
                return png_path
    except Exception:
        return None

    # Conversion PNG → WebP
    try:
        img = Image.open(png_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Sauvegarder en WebP (qualité 85%)
        img.save(webp_path, "WEBP", quality=85)
        print(f"   ✅ PNG→WebP: {png_path.name} ({png_path.stat().st_size} → {webp_path.stat().st_size} bytes)")
        return webp_path
    except Exception as e:
        print(f"   ⚠️ Conversion échouée: {e}")
        return None


def convert_to_webp_bytes(png_data: bytes) -> Optional[bytes]:
    """
    Convertit des bytes PNG en bytes WebP.
    """
    try:
        img = Image.open(BytesIO(png_data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        output = BytesIO()
        img.save(output, "WEBP", quality=85)
        return output.getvalue()
    except Exception:
        return None


# ─── UPLOAD GDRIVE ──────────────────────────────────────────
def upload_to_gdrive(local_path: Path, remote_subdir: str = "PNG") -> bool:
    """
    Copie un fichier PNG vers Google Drive via rclone.
    Retourne True si succès, False sinon.
    """
    if not local_path.exists():
        return False

    remote_path = f"{RCLONE_JOURNAL}{remote_subdir}/"

    try:
        result = subprocess.run(
            ["rclone", "copy", str(local_path), remote_path,
             "--drive-chunk-size", "256M", "--transfers", "1", "--retries", "2", "-q"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            print(f"   📤 GDrive: {remote_subdir}/{local_path.name}")
            return True
        else:
            print(f"   ⚠️ rclone: {result.stderr[:80]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"   ⚠️ rclone timeout (60s)")
        return False
    except Exception as e:
        print(f"   ⚠️ GDrive error: {e}")
        return False


# ─── WORKFLOW COMPLET ───────────────────────────────────────
def process_fal_output(png_path: Path, upload_to_drive: bool = True) -> Optional[Path]:
    """
    Post-process complet d'une sortie FAL:
    1. Convertir PNG → WebP
    2. Si upload_to_drive: déplacer le PNG sur GDrive
    3. Supprimer le PNG local (sauf si conversion échoue)

    Retourne le chemin du WebP final.
    """
    if not png_path.exists():
        return None

    # 1. Conversion
    webp_path = convert_to_webp(png_path)
    if not webp_path:
        return None

    # 2. Upload GDrive du PNG original
    uploaded = False
    if upload_to_drive:
        uploaded = upload_to_gdrive(png_path)

    # 3. Supprimer le PNG local UNIQUEMENT si upload réussi
    if uploaded and png_path != webp_path and png_path.exists():
        try:
            size_kb = png_path.stat().st_size // 1024
            png_path.unlink()
            print(f"   🗑️ PNG supprimé: {png_path.name} ({size_kb} Ko libérés)")
        except Exception as e:
            print(f"   ⚠️ Suppression échouée: {e}")
    elif not uploaded and png_path != webp_path:
        # Upload échoué → on garde le PNG local (ne pas perdre l'original)
        print(f"   ⚠️ PNG conservé localement (upload GDrive échoué)")

    return webp_path


# ─── BATCH ──────────────────────────────────────────────────
def batch_process_journal(pub_dir: str = "/srv/pwa/public/images/journal"):
    """
    Convertit tous les PNG en WebP dans le répertoire journal et déplace
    les originaux sur GDrive. À lancer ponctuellement ou en cron nocturne.
    """
    pub = Path(pub_dir)
    if not pub.exists():
        return

    png_files = list(pub.glob("*.png"))
    # Aussi les .webp qui sont en réalité du PNG
    potential_png = list(pub.glob("*.webp"))

    total = 0
    for f in png_files + potential_png:
        result = process_fal_output(f, upload_to_drive=True)
        if result:
            total += 1

    print(f"✅ {total} fichiers traités dans {pub_dir}")
    return total
