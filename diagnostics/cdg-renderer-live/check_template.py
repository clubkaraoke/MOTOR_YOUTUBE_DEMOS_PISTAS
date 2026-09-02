#!/usr/bin/env python3
"""
Comprueba si una imagen sirve como plantilla de portada CD+G.

    python check_template.py assets/intro_djgabo.png [-o informe.png]

CD+G no es un formato de imagen. La pantalla se divide en bloques de 6x12
píxeles y **cada bloque admite exactamente dos colores**. Cuando un bloque
tiene más, el generador elige dos y descarta el resto: de ahí que los logos
bonitos salgan hechos pedazos.

Lo que rompe una plantilla:
  · antialiasing (cada borde suave inventa colores intermedios)
  · contornos alrededor del texto (relleno + contorno + fondo = 3 colores)
  · líneas finas que cruzan otros elementos
  · degradados y fotografías

Lo que funciona:
  · colores planos, sin suavizado
  · texto grande y sólido, sin contorno
  · formas separadas entre sí por al menos un bloque
  · bandas de color alineadas a la rejilla de 12 píxeles de alto
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

W, H, TW, TH = 300, 216, 6, 12


def analyse(path: Path):
    im = Image.open(path).convert("RGB")
    if im.size != (W, H):
        print(f"aviso: la imagen mide {im.size[0]}x{im.size[1]}; se espera {W}x{H}")
        im = im.resize((W, H), Image.LANCZOS)
    a = np.array(im)

    bad, worst, colors_total = [], 0, set()
    for ty in range(H // TH):
        for tx in range(W // TW):
            tile = a[ty*TH:(ty+1)*TH, tx*TW:(tx+1)*TW].reshape(-1, 3)
            uniq = np.unique(tile, axis=0)
            for c in uniq:
                colors_total.add(tuple(int(v) for v in c))
            if len(uniq) > 2:
                bad.append((tx, ty, len(uniq)))
            worst = max(worst, len(uniq))
    return im, a, bad, worst, colors_total


def main() -> int:
    ap = argparse.ArgumentParser(description="Valida una plantilla de portada CD+G.")
    ap.add_argument("image", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="guarda un mapa de los bloques problemáticos")
    args = ap.parse_args()

    im, a, bad, worst, colors = analyse(args.image)
    total = (W // TW) * (H // TH)
    pct = 100 * len(bad) / total

    print(f"bloques de {TW}x{TH}: {total}")
    print(f"con más de 2 colores: {len(bad)} ({pct:.0f} %)")
    print(f"peor bloque: {worst} colores")
    print(f"colores distintos en toda la imagen: {len(colors)}  (la paleta CD+G tiene 16)")
    print()

    if not bad and len(colors) <= 16:
        print("APTA. Se verá igual en el CDG.")
    elif pct < 3:
        print("CASI APTA. Se perderá algún detalle suelto, pero se reconocerá.")
    else:
        print("NO APTA. Saldrá rota.")
        print("Rehaz la plantilla con colores planos, sin antialiasing, sin")
        print("contornos en el texto, y sin líneas finas que crucen otras formas.")

    if len(colors) > 16:
        print(f"\nAdemás hay {len(colors)} colores y la paleta sólo admite 16,")
        print("de los cuales el texto de la letra ya gasta unos cuantos.")

    if args.out:
        vis = im.convert("RGB").copy()
        px = vis.load()
        for tx, ty, _ in bad:
            for y in range(ty*TH, (ty+1)*TH):
                for x in range(tx*TW, (tx+1)*TW):
                    r, g, b = px[x, y]
                    px[x, y] = (min(255, r+90), g//3, b//3)
        vis.resize((W*2, H*2), Image.NEAREST).save(args.out)
        print(f"\nmapa guardado en {args.out} (en rojo, los bloques que se romperán)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
