#!/usr/bin/env python3
"""Iconos de las apps NEURAL (SVG con el estilo HUD y los colores del tema activo).

Escribe ~/.local/share/icons/hicolor/scalable/apps/org.local.Neural*.svg, que es
lo que piden los .desktop (Icon=org.local.NeuralTerm…). Se vuelven a generar al
cambiar de tema (app.py, NEURAL//AJUSTES > APARIENCIA).

Uso:  iconos.py            genera con el tema actual
      iconos.py TEMA       genera con ese tema (id de themes.js)"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board import load_board  # noqa: E402

DESTINO = Path.home() / ".local/share/icons/hicolor/scalable/apps"

# Dibujo de cada app dentro de un lienzo de 128×128 (el marco va aparte).
# {a} = acento del tema, {b} = color secundario (alt), {t} = texto.
GLIFOS = {
    "org.local.NeuralTerm": """
  <polyline points="32,44 54,64 32,84" fill="none" stroke="{a}" stroke-width="8" stroke-linecap="square"/>
  <line x1="62" y1="86" x2="96" y2="86" stroke="{b}" stroke-width="8"/>""",
    "org.local.NeuralFiles": """
  <path d="M26 40 h26 l8 8 h42 v44 h-76 z" fill="none" stroke="{a}" stroke-width="7" stroke-linejoin="miter"/>
  <line x1="26" y1="58" x2="102" y2="58" stroke="{b}" stroke-width="5"/>""",
    "org.local.NeuralDisco": """
  <circle cx="64" cy="64" r="32" fill="none" stroke="{a}" stroke-width="7"/>
  <path d="M64 32 A32 32 0 0 1 96 64" fill="none" stroke="{b}" stroke-width="7"/>
  <circle cx="64" cy="64" r="8" fill="{b}"/>""",
    "org.local.NeuralPak": """
  <polygon points="64,28 98,44 98,84 64,100 30,84 30,44" fill="none" stroke="{a}" stroke-width="7"
           stroke-linejoin="miter"/>
  <polyline points="30,44 64,60 98,44" fill="none" stroke="{a}" stroke-width="5"/>
  <line x1="64" y1="60" x2="64" y2="100" stroke="{b}" stroke-width="5"/>""",
    "org.local.NeuralVM": """
  <rect x="26" y="32" width="76" height="52" fill="none" stroke="{a}" stroke-width="7"/>
  <rect x="40" y="45" width="30" height="24" fill="none" stroke="{b}" stroke-width="5"/>
  <line x1="50" y1="98" x2="78" y2="98" stroke="{a}" stroke-width="7"/>
  <line x1="64" y1="84" x2="64" y2="98" stroke="{a}" stroke-width="5"/>""",
    "org.local.NeuralBIA": """
  <g stroke="{a}" stroke-width="4">
    <line x1="64" y1="64" x2="64" y2="30"/><line x1="64" y1="64" x2="96" y2="50"/>
    <line x1="64" y1="64" x2="90" y2="94"/><line x1="64" y1="64" x2="38" y2="94"/>
    <line x1="64" y1="64" x2="32" y2="50"/>
    <polyline points="64,30 96,50 90,94 38,94 32,50 64,30" fill="none" stroke-width="3" opacity="0.6"/>
  </g>
  <g fill="{b}"><circle cx="64" cy="30" r="6"/><circle cx="96" cy="50" r="6"/><circle cx="90" cy="94" r="6"/>
    <circle cx="38" cy="94" r="6"/><circle cx="32" cy="50" r="6"/></g>
  <circle cx="64" cy="64" r="11" fill="{a}"/>""",
    "org.local.NeuralAjustes": """
  <g transform="translate(64 64)" fill="{a}">
    <rect x="-6" y="-38" width="12" height="16"/><rect x="-6" y="22" width="12" height="16"/>
    <rect x="-38" y="-6" width="16" height="12"/><rect x="22" y="-6" width="16" height="12"/>
    <g transform="rotate(45)"><rect x="-6" y="-38" width="12" height="16"/><rect x="-6" y="22" width="12" height="16"/>
      <rect x="-38" y="-6" width="16" height="12"/><rect x="22" y="-6" width="16" height="12"/></g>
  </g>
  <circle cx="64" cy="64" r="24" fill="none" stroke="{a}" stroke-width="8"/>
  <circle cx="64" cy="64" r="8" fill="{b}"/>""",
    "org.local.NeuralWeb": """
  <circle cx="64" cy="64" r="34" fill="none" stroke="{a}" stroke-width="7"/>
  <ellipse cx="64" cy="64" rx="14" ry="34" fill="none" stroke="{b}" stroke-width="5"/>
  <line x1="30" y1="64" x2="98" y2="64" stroke="{b}" stroke-width="5"/>
  <line x1="36" y1="46" x2="92" y2="46" stroke="{a}" stroke-width="3"/>
  <line x1="36" y1="82" x2="92" y2="82" stroke="{a}" stroke-width="3"/>""",
    "org.local.NeuralCVE": """
  <path d="M64 26 L96 38 V64 C96 84 82 96 64 104 C46 96 32 84 32 64 V38 Z" fill="none" stroke="{a}"
        stroke-width="7" stroke-linejoin="miter"/>
  <polyline points="48,64 60,76 82,52" fill="none" stroke="{b}" stroke-width="8" stroke-linecap="square"/>""",
    "org.local.NeuralNexo": """
  <g fill="none" stroke-linecap="square">
    <polyline points="28,50 96,50" stroke="{a}" stroke-width="7"/>
    <polyline points="80,34 98,50 80,66" stroke="{a}" stroke-width="7"/>
    <polyline points="100,78 32,78" stroke="{b}" stroke-width="7"/>
    <polyline points="48,62 30,78 48,94" stroke="{b}" stroke-width="7"/>
  </g>""",
    "org.local.NeuralRespaldo": """
  <path d="M34 72 a30 30 0 1 1 8 20" fill="none" stroke="{a}" stroke-width="7"/>
  <polyline points="22,62 34,76 48,64" fill="none" stroke="{a}" stroke-width="7" stroke-linecap="square"/>
  <polyline points="64,48 64,70 80,78" fill="none" stroke="{b}" stroke-width="7" stroke-linecap="square"/>""",
    "org.local.NeuralBoveda": """
  <rect x="30" y="58" width="68" height="46" fill="none" stroke="{a}" stroke-width="7"/>
  <path d="M44 58 V44 a20 20 0 0 1 40 0 V58" fill="none" stroke="{a}" stroke-width="7"/>
  <circle cx="64" cy="78" r="7" fill="{b}"/>
  <line x1="64" y1="80" x2="64" y2="94" stroke="{b}" stroke-width="5"/>""",
    "org.local.NeuralVisor": """
  <rect x="26" y="34" width="76" height="60" fill="none" stroke="{a}" stroke-width="7"/>
  <polyline points="34,86 54,62 68,76 80,64 96,86" fill="none" stroke="{b}" stroke-width="6"/>
  <circle cx="84" cy="50" r="6" fill="{b}"/>""",
    "org.local.NeuralPulso": """
  <polyline points="22,66 44,66 54,40 68,92 78,66 106,66" fill="none" stroke="{a}" stroke-width="7" stroke-linejoin="miter"/>
  <circle cx="106" cy="66" r="6" fill="{b}"/>""",
    "org.local.NeuralEscudo": """
  <path d="M64 24 L98 36 V64 C98 86 83 98 64 106 C45 98 30 86 30 64 V36 Z" fill="none" stroke="{a}" stroke-width="7"/>
  <line x1="46" y1="52" x2="82" y2="52" stroke="{b}" stroke-width="5"/>
  <line x1="46" y1="66" x2="82" y2="66" stroke="{b}" stroke-width="5"/>
  <line x1="52" y1="80" x2="76" y2="80" stroke="{b}" stroke-width="5"/>""",
}

PLANTILLA ="""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <filter id="brillo" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="2.2" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <pattern id="lineas" width="4" height="4" patternUnits="userSpaceOnUse">
      <rect width="4" height="1" fill="{a}" opacity="0.07"/>
    </pattern>
  </defs>
  <polygon points="22,4 124,4 124,106 106,124 4,124 4,22" fill="#05070a"/>
  <polygon points="22,4 124,4 124,106 106,124 4,124 4,22" fill="url(#lineas)"/>
  <polygon points="22,4 124,4 124,106 106,124 4,124 4,22" fill="none" stroke="{a}" stroke-width="3"
           filter="url(#brillo)"/>
  <polyline points="10,32 10,10 32,10" fill="none" stroke="{b}" stroke-width="3"/>
  <polyline points="118,96 118,118 96,118" fill="none" stroke="{b}" stroke-width="3"/>
  <g filter="url(#brillo)">{glifo}
  </g>
</svg>
"""


def _hex(c) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(v * 255))) for v in (c.red, c.green, c.blue)))


def generar(tema: str | None = None) -> int:
    """Escribe los SVG; devuelve cuántos cambiaron."""
    from estilo import load_themes
    temas = load_themes()
    t = temas.get(tema or load_board().get("theme", "red")) or next(iter(temas.values()))
    colores = {"a": _hex(t["accent"]), "b": _hex(t["alt"]), "t": _hex(t["text"])}
    DESTINO.mkdir(parents=True, exist_ok=True)
    cambiados = 0
    for nombre, glifo in GLIFOS.items():
        svg = PLANTILLA.replace("{glifo}", glifo.format(**colores)).format(**colores)
        ruta = DESTINO / f"{nombre}.svg"
        try:
            if ruta.read_text() == svg:
                continue
        except OSError:
            pass
        ruta.write_text(svg)
        cambiados += 1
    if cambiados:
        # la fecha de la carpeta avisa a GTK de que hay iconos nuevos
        raiz = DESTINO.parents[2]
        if not (raiz / "index.theme").exists() and Path("/usr/share/icons/hicolor/index.theme").exists():
            (raiz / "index.theme").write_text(Path("/usr/share/icons/hicolor/index.theme").read_text())
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", "-q", str(raiz)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    return cambiados


if __name__ == "__main__":
    print(f"{generar(sys.argv[1] if len(sys.argv) > 1 else None)} iconos actualizados en {DESTINO}")
