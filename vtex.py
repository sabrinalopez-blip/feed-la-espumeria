"""Lectura del catálogo público de VTEX (sin credenciales)."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import requests

STORE = "https://www.laespumeria.com"
SEARCH = STORE + "/api/catalog_system/pub/products/search"
PAGE = 50          # VTEX devuelve hasta 50 por request
MAX_FROM = 2500    # límite duro de paginación de VTEX


@dataclass
class Sku:
    id: str                    # itemId (SKU) = id del producto en Meta
    product_id: str
    title: str
    description: str
    link: str
    image_url: str | None
    list_price: float
    price: float
    available: bool
    brand: str
    product_type: str
    ref_id: str
    n_cuotas: int | None
    cuota_value: float | None
    clusters: dict = field(default_factory=dict)


def _clean(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:4900]


def _best_installment(offer: dict) -> tuple[int | None, float | None]:
    """Máxima cantidad de cuotas sin interés con tarjeta de crédito."""
    best = None
    for i in offer.get("Installments") or []:
        if i.get("InterestRate", 1) == 0 and i.get("PaymentSystemGroupName") == "creditCardPaymentGroup":
            if best is None or i["NumberOfInstallments"] > best["NumberOfInstallments"]:
                best = i
    if not best or best["NumberOfInstallments"] < 2:
        return None, None
    return best["NumberOfInstallments"], best["Value"]


def fetch_products(session: requests.Session | None = None) -> list[dict]:
    s = session or requests.Session()
    out, start = [], 0
    while start < MAX_FROM:
        r = s.get(SEARCH, params={"_from": start, "_to": start + PAGE - 1}, timeout=60)
        if r.status_code not in (200, 206):
            r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < PAGE:
            break
        start += PAGE
    return out


def to_skus(products: list[dict]) -> list[Sku]:
    skus: dict[str, Sku] = {}
    for p in products:
        cats = p.get("categories") or []
        product_type = max(cats, key=len).strip("/").replace("/", " - ") if cats else ""
        for it in p.get("items") or []:
            seller = next((s for s in it.get("sellers") or [] if s.get("sellerDefault")), None) \
                or (it.get("sellers") or [None])[0]
            if not seller:
                continue
            o = seller["commertialOffer"]
            price, list_price = float(o.get("Price") or 0), float(o.get("ListPrice") or 0)
            if price <= 0:
                continue
            n, v = _best_installment(o)
            imgs = it.get("images") or []
            skus[it["itemId"]] = Sku(
                id=it["itemId"],
                product_id=p["productId"],
                title=it.get("nameComplete") or p.get("productName", ""),
                description=_clean(p.get("description")) or p.get("productName", ""),
                link=p.get("link") or f"{STORE}/{p.get('linkText')}/p",
                image_url=imgs[0]["imageUrl"] if imgs else None,
                list_price=max(list_price, price),
                price=price,
                available=bool(o.get("IsAvailable")) and (o.get("AvailableQuantity") or 0) > 0,
                brand=p.get("brand") or "La Espumería",
                product_type=product_type,
                ref_id=next((r["Value"] for r in it.get("referenceId") or [] if r.get("Key") == "RefId"), ""),
                n_cuotas=n,
                cuota_value=v,
                clusters=p.get("productClusters") or {},
            )
    return list(skus.values())
