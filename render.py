"""Renderiza la imagen de catálogo con el marco AO de La Espumería.

Replica el layout de las imágenes que generaba Orka (1080x1080):
- foto del producto en la ventana transparente del marco (cover-fit)
- % OFF dentro del globo naranja
- precio de lista tachado + precio final en la píldora
- "N Cuotas sin interés de $X" debajo
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent
SIZE = 1080
WINDOW = (140, 215, 941, 864)  # ventana transparente del marco (x0, y0, x1, y1)
NAVY = (8, 14, 92)
WHITE = (255, 255, 255)

# Posiciones calibradas contra las imágenes de Orka
PCT_CENTER = (872, 132)
PILL_CENTER_X = 538
PILL_INNER_W = 650          # ancho máximo para el texto dentro de la píldora
LINE1_CENTER_Y = 954
LINE2_CENTER_Y = 1017
LINE1_GAP = 32

# Nunito (SIL Open Font License, ver OFL-Nunito.txt): fuente libre parecida a Gotham Rounded.
FONT_FILE = ASSETS / "Nunito.ttf"
FONT_BOLD = 800      # peso para % y precio final
FONT_MED = 600       # peso para tachado y cuotas


@lru_cache
def font(weight: int, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT_FILE), size)
    f.set_variation_by_axes([weight])
    return f


@lru_cache
def frame(with_badge: bool) -> Image.Image:
    fr = Image.open(ASSETS / "frame_ao.png").convert("RGBA")
    if with_badge:
        return fr
    # Variante sin descuento: se borra el globo naranja y el "OFF".
    a = np.array(fr)
    x0, y0, x1, y1 = 715, 35, 1045, 265
    reg = a[y0:y1, x0:x1].astype(int)
    dist = np.abs(reg[..., :3] - np.array(NAVY)).sum(axis=2)
    hit = (dist > 25) & (reg[..., 3] > 0)
    # dilatar 3px para levantar los bordes suavizados
    for _ in range(3):
        h = hit.copy()
        h[1:, :] |= hit[:-1, :]; h[:-1, :] |= hit[1:, :]
        h[:, 1:] |= hit[:, :-1]; h[:, :-1] |= hit[:, 1:]
        hit = h
    ys, xs = np.nonzero(hit)
    gx, gy = xs + x0, ys + y0
    inside = (gx >= WINDOW[0]) & (gx < WINDOW[2]) & (gy >= WINDOW[1]) & (gy < WINDOW[3])
    a[gy[inside], gx[inside], 3] = 0                     # dentro de la ventana: transparente
    a[gy[~inside], gx[~inside], :3] = NAVY               # fuera: fondo azul
    a[gy[~inside], gx[~inside], 3] = 255
    return Image.fromarray(a)


def ars(value: float) -> str:
    return "$" + f"{round(value):,}".replace(",", ".")


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    nw, nh = round(img.width * s), round(img.height * s)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _draw_centered(d: ImageDraw.ImageDraw, text: str, f, cx: float, cy: float):
    b = f.getbbox(text)
    x = cx - (b[0] + b[2]) / 2
    y = cy - (b[1] + b[3]) / 2
    d.text((x, y), text, font=f, fill=WHITE)
    return b


def render(product_img: Image.Image, list_price: float, price: float,
           n_cuotas: int | None, cuota_value: float | None) -> Image.Image:
    canvas = Image.new("RGB", (SIZE, SIZE), NAVY)
    x0, y0, x1, y1 = WINDOW
    canvas.paste(cover(product_img, x1 - x0, y1 - y0), (x0, y0))

    has_disc = list_price and list_price > price * 1.005
    pct = round((1 - price / list_price) * 100) if has_disc else 0
    if pct < 5:
        has_disc = False
    canvas.paste(frame(has_disc), (0, 0), frame(has_disc))
    d = ImageDraw.Draw(canvas)

    if has_disc:
        _draw_centered(d, f"{pct}%", font(FONT_BOLD, 82), *PCT_CENTER)

    # Línea 1: tachado + precio final (se achica si no entra)
    sale_txt, strike_txt = ars(price), ars(list_price)
    scale = 1.0
    while True:
        fs = font(FONT_BOLD, round(62 * scale))
        fm = font(FONT_MED, round(45 * scale))
        sw = fs.getbbox(sale_txt)
        wsale = sw[2] - sw[0]
        if has_disc:
            st = fm.getbbox(strike_txt)
            wstrike = st[2] - st[0]
            total = wstrike + LINE1_GAP * scale + wsale
        else:
            total = wsale
        if total <= PILL_INNER_W or scale < 0.6:
            break
        scale -= 0.04
    left = PILL_CENTER_X - total / 2
    if has_disc:
        b = _draw_centered(d, strike_txt, fm, left + wstrike / 2, LINE1_CENTER_Y)
        # línea del tachado
        ly = LINE1_CENTER_Y + 2
        d.line([(left - 6, ly), (left + wstrike + 6, ly)], fill=WHITE, width=max(3, round(4 * scale)))
        left += wstrike + LINE1_GAP * scale
    _draw_centered(d, sale_txt, fs, left + wsale / 2, LINE1_CENTER_Y)

    # Línea 2: cuotas
    if n_cuotas and n_cuotas > 1 and cuota_value:
        _draw_centered(d, f"{n_cuotas} Cuotas sin interés de {ars(cuota_value)}",
                       font(FONT_MED, 33), PILL_CENTER_X, LINE2_CENTER_Y)
    return canvas


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()
