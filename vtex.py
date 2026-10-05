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


# ---------------------------------------------------------------------------
# Fuente principal: XML de la colección "CATALOGO ORKA" que genera VTEX.
# El cliente elige qué productos llevan frame sumándolos/sacándolos de esa colección.
# ---------------------------------------------------------------------------
XML_FEED = "https://laespumeria.vtexcommercestable.com.br/XMLData/feed_prueba.xml"
G = "{http://base.google.com/ns/1.0}"


def _ars(text: str | None) -> float:
    """'ARS 2.526.100,00' -> 2526100.0"""
    t = re.sub(r"[^\d,]", "", text or "").replace(",", ".")
    return float(t) if t else 0.0


def _amount(text: str | None) -> float:
    """'80,677.50 ARS' -> 80677.5"""
    t = re.sub(r"[^\d.]", "", text or "")
    return float(t) if t else 0.0


def fetch_xml_skus(session: requests.Session | None = None, url: str = XML_FEED) -> list[Sku]:
    import xml.etree.ElementTree as ET

    s = session or requests.Session()
    r = s.get(url, timeout=120)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out: list[Sku] = []
    for it in root.iter("item"):
        def g(tag: str) -> str:
            el = it.find(G + tag)
            return (el.text or "").strip() if el is not None else ""

        sku_id = g("id")
        title_el = it.find("title")
        desc_el = it.find("description")
        price = _ars(g("price"))
        sale = _ars(g("sale_price")) or price
        if not sku_id or sale <= 0:
            continue
        inst = it.find(G + "installment")
        n = v = None
        if inst is not None:
            m = inst.find(G + "months")
            a = inst.find(G + "amount")
            n = int(re.sub(r"\D", "", m.text or "") or 0) or None if m is not None else None
            v = _amount(a.text) if a is not None else None
        link = g("link").replace("laespumeria.vtexcommercestable.com.br", "www.laespumeria.com")
        link = re.sub(r"\?idsku=\d+$", "", link)
        out.append(Sku(
            id=sku_id,
            product_id=g("mpn") or sku_id,
            title=(title_el.text or "").strip() if title_el is not None else "",
            description=_clean(desc_el.text if desc_el is not None else ""),
            link=link,
            image_url=g("image_link") or None,
            list_price=max(price, sale),
            price=sale,
            available=True,
            brand=g("brand") or "La Espumería",
            product_type=g("product_type"),
            ref_id=g("mpn"),
            n_cuotas=n if (n or 0) > 1 else None,
            cuota_value=v,
        ))
    return out
