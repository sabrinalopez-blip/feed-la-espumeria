"""Feed de catálogo con marco AO para La Espumería (reemplazo del "New Feed Orka").

Flujo (corre 1 vez por hora o por día con Cloud Scheduler):
1. Lee todos los SKUs de la API pública de VTEX (precio, precio de lista, cuotas, foto).
2. Para cada SKU arma la imagen 1080x1080 con el marco AO, % OFF, precios y cuotas.
   Solo re-renderiza si cambió algo (precio, cuotas o foto) -> rápido y barato.
3. Sube las imágenes a un bucket público de GCS con un nombre que incluye un hash,
   así Meta las vuelve a descargar cuando cambia el precio.
4. Publica feed.csv (mismas columnas e IDs que el feed de Orka) para que Meta lo lea.

Uso local (sin GCS):   python main.py --out-dir ./salida --limit 10
En GCP:                 BUCKET=mi-bucket python main.py
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image

import render
import vtex

TEMPLATE_VERSION = "ao-v1"   # cambiar si se modifica el diseño -> fuerza re-render de todo
PREFIX = os.environ.get("PREFIX", "la-espumeria")
BUCKET = os.environ.get("BUCKET")
OVERRIDES_CSV_URL = os.environ.get("OVERRIDES_CSV_URL")
SOURCE = os.environ.get("SOURCE", "xml")  # "xml" = colección CATALOGO ORKA (default) | "api" = todo el catálogo
GOOGLE_PRODUCT_CATEGORY = "Home & Garden"
FEED_COLUMNS = ["id", "title", "description", "availability", "condition", "price", "sale_price",
                "link", "image_link", "brand", "google_product_category", "product_type",
                "item_group_id", "installment", "custom_label_0", "custom_label_1", "custom_label_2"]

log = logging.getLogger("feed")


# ---------- storage (GCS o carpeta local) ----------
class Storage:
    def __init__(self, bucket: str | None, out_dir: str | None):
        self.local = Path(out_dir) if out_dir else None
        if self.local:
            (self.local / PREFIX / "img").mkdir(parents=True, exist_ok=True)
            # GitHub Pages: URL pública del sitio (ej. https://usuario.github.io/repo)
            self.base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/") or self.local.resolve().as_uri()
        else:
            from google.cloud import storage
            self.bucket = storage.Client().bucket(bucket)
            self.base_url = f"https://storage.googleapis.com/{bucket}"

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path}"

    def read(self, path: str) -> bytes | None:
        if self.local:
            p = self.local / path
            return p.read_bytes() if p.exists() else None
        b = self.bucket.blob(path)
        return b.download_as_bytes() if b.exists() else None

    def write(self, path: str, data: bytes, content_type: str, cache: str = "public, max-age=86400"):
        if self.local:
            (self.local / path).write_bytes(data)
            return
        b = self.bucket.blob(path)
        b.cache_control = cache
        b.upload_from_string(data, content_type=content_type)

    def cleanup(self, keep: set[str], older_than_days: int = 7):
        """Borra imágenes viejas que ya no usa el feed (después de 7 días, por si Meta las cachea)."""
        if self.local:
            return 0
        limit = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        n = 0
        for b in self.bucket.list_blobs(prefix=f"{PREFIX}/img/"):
            if b.name not in keep and b.updated < limit:
                b.delete()
                n += 1
        return n


# ---------- helpers ----------
def money(v: float) -> str:
    return f"{v:.2f} ARS"


def load_overrides(session: requests.Session) -> dict[str, dict]:
    if OVERRIDES_CSV_URL:
        text = session.get(OVERRIDES_CSV_URL, timeout=60).text
    else:
        text = (Path(__file__).parent / "custom_labels.csv").read_text()
    return {r["id"]: r for r in csv.DictReader(io.StringIO(text)) if r.get("id")}


def fingerprint(s: vtex.Sku) -> str:
    raw = json.dumps([TEMPLATE_VERSION, s.image_url, s.list_price, s.price, s.n_cuotas,
                      round(s.cuota_value or 0)])
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def build_image(s: vtex.Sku, session: requests.Session) -> bytes:
    r = session.get(s.image_url, timeout=60)
    if r.status_code != 200 and re.search(r"_\d+$", s.image_url):
        r = session.get(re.sub(r"_\d+$", "", s.image_url), timeout=60)
    r.raise_for_status()
    photo = Image.open(io.BytesIO(r.content))
    img = render.render(photo, s.list_price, s.price, s.n_cuotas, s.cuota_value)
    return render.to_jpeg(img)


# ---------- main ----------
def run(out_dir: str | None, limit: int | None, workers: int) -> int:
    session = requests.Session()
    session.headers["User-Agent"] = "BullMetrix-feed/1.0"
    store = Storage(BUCKET, out_dir)

    if SOURCE == "api":
        skus = vtex.to_skus(vtex.fetch_products(session))
    else:
        skus = vtex.fetch_xml_skus(session)
    skus.sort(key=lambda s: int(s.id) if s.id.isdigit() else s.id)
    if limit:
        skus = skus[:limit]
    log.info("SKUs leídos de VTEX: %d", len(skus))

    state_path = f"{PREFIX}/state.json"
    state = json.loads(store.read(state_path) or b"{}")
    overrides = load_overrides(session)

    def process(s: vtex.Sku):
        if not s.image_url:
            return s, None, "sin foto"
        fp = fingerprint(s)
        path = f"{PREFIX}/img/{s.id}_{fp}.jpg"
        if state.get(s.id) == path:
            return s, path, "sin cambios"
        try:
            store.write(path, build_image(s, session), "image/jpeg")
            return s, path, "renderizada"
        except Exception as e:  # una foto rota no frena el resto del feed
            log.warning("SKU %s: %s", s.id, e)
            return s, state.get(s.id), f"error: {e}"

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(process, skus))

    rows, new_state, stats = [], {}, {}
    for s, path, status in results:
        key = status.split(":")[0]
        stats[key] = stats.get(key, 0) + 1
        if not path:
            continue
        new_state[s.id] = path
        ov = overrides.get(s.id, {})
        has_disc = s.list_price > s.price * 1.005
        rows.append({
            "id": s.id,
            "title": s.title[:150],
            "description": s.description,
            "availability": "in stock" if s.available else "out of stock",
            "condition": "new",
            "price": money(s.list_price),
            "sale_price": money(s.price) if has_disc else "",
            "link": s.link,
            "image_link": store.url(path),
            "brand": s.brand,
            "google_product_category": GOOGLE_PRODUCT_CATEGORY,
            "product_type": s.product_type,
            "item_group_id": s.ref_id or s.product_id,
            "installment": f"{s.n_cuotas}:{round(s.cuota_value)} ARS" if s.n_cuotas else "",
            "custom_label_0": ov.get("custom_label_0", ""),
            "custom_label_1": ov.get("custom_label_1", ""),
            "custom_label_2": s.ref_id,
        })

    if not rows:
        log.error("El feed quedó vacío; no se publica para no vaciar el catálogo.")
        return 1

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FEED_COLUMNS)
    w.writeheader()
    w.writerows(rows)
    store.write(f"{PREFIX}/feed.csv", buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                cache="no-cache, max-age=0")
    store.write(state_path, json.dumps(new_state).encode(), "application/json", cache="no-cache")
    deleted = store.cleanup(set(new_state.values()))

    log.info("Resultado: %s | filas en feed: %d | imágenes viejas borradas: %d", stats, len(rows), deleted)
    log.info("Feed: %s", store.url(f"{PREFIX}/feed.csv"))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", help="Modo local: escribe en esta carpeta en vez de GCS")
    ap.add_argument("--limit", type=int, help="Procesar solo N SKUs (pruebas)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if not a.out_dir and not BUCKET:
        sys.exit("Falta BUCKET (o usá --out-dir para correr local)")
    sys.exit(run(a.out_dir, a.limit, a.workers))
