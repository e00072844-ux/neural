#!/usr/bin/env python3
"""Tema NEURAL para Firefox (Flatpak o normal): genera en el perfil
  chrome/userChrome.css   interfaz (pestañas, barra, menús) con los colores del tema
  chrome/neural-inicio.html  página de inicio NEURAL
  user.js                 preferencias (entre marcas NEURAL; lo demás del archivo se respeta)
y copia las fuentes Rajdhani / Share Tech Mono a ~/.local/share/fonts/neural.

Va en ~/neural-deck (lee web/themes.js y web/fonts). Tema = el de board.json, o --tema ID.
Solo usa la biblioteca estándar de Python.

Uso:  python3 ~/neural-deck/neural-firefox.py [--tema synth] [--perfil DIR] [--sin-https-only]
      (Firefox tiene que haberse abierto una vez antes, para que exista el perfil;
       después de generar, cierra y vuelve a abrir Firefox)
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DECK = Path(__file__).resolve().parent
WEB = DECK / "web"
BOARD = Path(os.environ.get("LIVE_WALLPAPER_CONFIG_DIR", Path.home() / ".config" / "live-wallpaper")) / "board.json"
BASES = [  # dónde puede estar profiles.ini (Flatpak primero)
    Path.home() / ".var/app/org.mozilla.firefox/config/mozilla/firefox",
    Path.home() / ".var/app/org.mozilla.firefox/.mozilla/firefox",
    Path.home() / ".config/mozilla/firefox",
    Path.home() / ".mozilla/firefox",
]
MARCA_INI, MARCA_FIN = "// ---- NEURAL (generado por neural-firefox.py) ----", "// ---- /NEURAL ----"


# ---------- temas ----------
def load_themes() -> dict[str, dict]:
    text = (WEB / "themes.js").read_text()
    themes = {}
    for chunk in re.split(r"\{\s*\n\s*id:", text)[1:]:
        tid = re.match(r'\s*"([^"]+)"', chunk)
        cols = re.search(r"colors:\s*\{([^}]*)\}", chunk)
        if tid and cols:
            name = re.search(r'name:\s*"([^"]+)"', chunk)
            glow = re.search(r"glow:\s*([\d.]+)", chunk)
            style = re.search(r'style:\s*"(\w+)"', chunk)
            t = dict(re.findall(r'(\w+):\s*"([^"]+)"', cols.group(1)))
            t["_name"] = name.group(1) if name else tid.group(1)
            t["_style"] = style.group(1) if style else "grid"
            t["_glow"] = float(glow.group(1)) if glow else (0.5 if t["_style"] == "synth" else 0.25)
            themes[tid.group(1)] = t
    return themes


def board_theme() -> str | None:
    try:
        return json.loads(BOARD.read_text()).get("theme")
    except (OSError, json.JSONDecodeError):
        return None


# ---------- perfil ----------
def find_profile() -> Path | None:
    for base in BASES:
        ini = base / "profiles.ini"
        if not ini.exists():
            continue
        cp = configparser.ConfigParser()
        cp.read(ini)
        rel = None
        for sec in cp.sections():  # el perfil que usa esta instalación
            if sec.startswith("Install") and cp[sec].get("Default"):
                rel = cp[sec]["Default"]
                break
        if rel is None:
            for sec in cp.sections():
                if sec.startswith("Profile") and cp[sec].get("Default") == "1":
                    rel = cp[sec]["Path"]
                    break
        if rel is None:
            for sec in cp.sections():
                if sec.startswith("Profile"):
                    rel = cp[sec]["Path"]
                    break
        if rel:
            p = Path(rel)
            return p if p.is_absolute() else base / p
    return None


# ---------- CSS de la interfaz ----------
def mix(var: str, pct: int) -> str:
    return f"color-mix(in srgb, var({var}) {pct}%, transparent)"


def user_chrome(t: dict) -> str:
    g = t["_glow"]
    dark = "#0a0204"
    return f"""/* NEURAL//FOX — tema {t['_name']}. Generado por neural-firefox.py: NO editar a mano
   (los cambios se pierden al regenerar). Copia del original en userChrome.css.antes-neural */
:root {{
  --nw-acc: {t['accent']}; --nw-alt: {t['alt']}; --nw-warn: {t['warn']};
  --nw-text: {t['text']}; --nw-muted: {t['muted']};
  --nw-bg: {t['bg']}; --nw-bg2: {t['bg2']}; --nw-panel: {t['panel']};
  --nw-dark: {dark};
  --nw-glow: 0 0 {round(14 * g + 4)}px {mix('--nw-acc', round(70 * g + 20))};
  --nw-cut: polygon(9px 0, 100% 0, 100% calc(100% - 9px), calc(100% - 9px) 100%, 0 100%, 0 9px);

  /* variables de Firefox: casi todo sale de aquí */
  --lwt-accent-color: var(--nw-bg) !important;
  --lwt-accent-color-inactive: var(--nw-bg) !important;
  --lwt-text-color: var(--nw-text) !important;
  --toolbox-bgcolor: var(--nw-bg) !important;
  --toolbox-bgcolor-inactive: var(--nw-bg) !important;
  --toolbox-textcolor: var(--nw-text) !important;
  --toolbar-bgcolor: var(--nw-bg2) !important;
  --toolbar-color: var(--nw-text) !important;
  --tab-selected-bgcolor: {mix('--nw-acc', 18)} !important;
  --tab-selected-textcolor: var(--nw-text) !important;
  --tab-border-radius: 0 !important;
  --tab-min-height: 30px !important;
  --toolbarbutton-border-radius: 0 !important;
  --toolbarbutton-hover-background: var(--nw-acc) !important;
  --toolbarbutton-active-background: var(--nw-alt) !important;
  --toolbar-field-background-color: rgba(0, 0, 0, 0.6) !important;
  --toolbar-field-focus-background-color: rgba(0, 0, 0, 0.75) !important;
  --toolbar-field-color: var(--nw-text) !important;
  --toolbar-field-focus-color: var(--nw-text) !important;
  --toolbar-field-border-color: {mix('--nw-acc', 55)} !important;
  --toolbar-field-focus-border-color: var(--nw-alt) !important;
  --urlbar-box-bgcolor: rgba(0, 0, 0, 0.6) !important;
  --urlbar-box-focus-bgcolor: rgba(0, 0, 0, 0.75) !important;
  --urlbar-box-hover-bgcolor: {mix('--nw-acc', 20)} !important;
  --urlbar-box-text-color: var(--nw-text) !important;
  --arrowpanel-background: var(--nw-bg2) !important;
  --arrowpanel-color: var(--nw-text) !important;
  --arrowpanel-border-color: var(--nw-acc) !important;
  --arrowpanel-border-radius: 0 !important;
  --arrowpanel-dimmed: {mix('--nw-acc', 15)} !important;
  --arrowpanel-dimmed-further: {mix('--nw-acc', 28)} !important;
  --panel-separator-color: {mix('--nw-acc', 35)} !important;
  --panel-item-hover-bgcolor: {mix('--nw-acc', 22)} !important;
  --focus-outline-color: var(--nw-alt) !important;
  --button-primary-bgcolor: var(--nw-acc) !important;
  --button-primary-color: var(--nw-dark) !important;
  --border-radius-small: 0 !important;
  --border-radius-medium: 0 !important;
  --chrome-content-separator-color: var(--nw-acc) !important;
  --sidebar-background-color: var(--nw-bg) !important;
  --sidebar-text-color: var(--nw-text) !important;
}}

/* --- marco general: línea de acento con brillo bajo la caja de herramientas --- */
#navigator-toolbox {{
  background: var(--nw-bg) !important;
  border-bottom: 1px solid var(--nw-acc) !important;
  box-shadow: var(--nw-glow) !important;
  font-family: "Rajdhani", sans-serif !important;
  font-weight: 600 !important;
}}
#nav-bar {{
  background: linear-gradient({mix('--nw-acc', 7)}, transparent), var(--nw-bg2) !important;
  border-top: 1px solid {mix('--nw-acc', 25)} !important;
}}

/* --- marca a la izquierda de las pestañas --- */
#TabsToolbar .toolbar-items::before {{
  content: "NEURAL//FOX";
  align-self: center;
  margin: 0 12px 0 10px;
  font: 700 15px "Rajdhani", sans-serif;
  letter-spacing: 2px;
  color: var(--nw-acc);
  text-shadow: 0 0 8px {mix('--nw-acc', 70)};
}}

/* --- pestañas: esquinas cortadas, borde y brillo en la activa --- */
.tab-background {{
  border-radius: 0 !important;
  background: rgba(0, 0, 0, 0.45) !important;
  outline: 1px solid {mix('--nw-acc', 30)} !important;
  outline-offset: -1px !important;
  clip-path: var(--nw-cut);
  margin-block: 4px !important;
}}
.tabbrowser-tab:hover > .tab-stack > .tab-background:not([selected]) {{
  background: {mix('--nw-acc', 14)} !important;
}}
.tab-background:is([selected], [multiselected]) {{
  background: linear-gradient({mix('--nw-acc', 30)}, {mix('--nw-acc', 10)}) !important;
  outline: 1px solid var(--nw-acc) !important;
  box-shadow: inset 0 -2px 0 var(--nw-acc) !important;
}}
.tab-label {{
  font-family: "Rajdhani", sans-serif !important;
  font-weight: 600 !important;
  font-size: 14px !important;
  letter-spacing: 0.5px !important;
}}
.tabbrowser-tab[selected] .tab-label {{ color: var(--nw-text) !important; }}
.tabbrowser-tab:not([selected]) .tab-label {{ color: var(--nw-muted) !important; }}
.tab-close-button {{ border-radius: 0 !important; }}
.tab-close-button:hover {{ background: var(--nw-warn) !important; fill: #000 !important; }}
.tab-throbber, .tab-icon-overlay {{ fill: var(--nw-alt) !important; }}
.tab-loading-burst {{ display: none !important; }}

/* --- barra de direcciones: monoespaciada, esquinas cortadas, cian al enfocar --- */
#urlbar, #urlbar-background, #urlbar-input-container, #searchbar {{
  border-radius: 0 !important;
}}
#urlbar-background {{
  clip-path: var(--nw-cut);
}}
#urlbar[focused] > #urlbar-background {{
  box-shadow: 0 0 10px {mix('--nw-alt', 50)} !important;
  outline: 1px solid var(--nw-alt) !important;
}}
#urlbar-input, .urlbarView-title, .urlbarView-url, #searchbar .searchbar-textbox {{
  font-family: "Share Tech Mono", monospace !important;
}}
#urlbar-input::selection {{ background: var(--nw-acc) !important; color: var(--nw-dark) !important; }}
.urlbarView-row[selected] {{ background: {mix('--nw-acc', 25)} !important; }}
.urlbarView-row[selected] .urlbarView-title {{ color: var(--nw-text) !important; }}
#identity-box[pageproxystate="valid"].verifiedDomain #identity-icon {{ fill: var(--nw-alt) !important; }}
#identity-box.notSecure #identity-icon, #identity-box.mixedActiveContent #identity-icon {{ fill: var(--nw-warn) !important; }}

/* --- botones --- */
toolbarbutton:hover > :is(.toolbarbutton-icon, .toolbarbutton-badge-stack, .toolbarbutton-text) {{
  fill: var(--nw-dark) !important;
  color: var(--nw-dark) !important;
}}
#nav-bar toolbarbutton > :is(.toolbarbutton-icon, .toolbarbutton-badge-stack) {{ border-radius: 0 !important; }}

/* --- menús y paneles --- */
menupopup, panel {{
  --panel-border-radius: 0 !important;
  --panel-background: var(--nw-bg2) !important;
  --panel-color: var(--nw-text) !important;
  --panel-border-color: var(--nw-acc) !important;
  font-family: "Rajdhani", sans-serif !important;
  font-weight: 600 !important;
}}
menupopup menuitem:hover, menupopup menu:hover, menupopup menuitem[_moz-menuactive],
menupopup menu[_moz-menuactive] {{
  background: var(--nw-acc) !important;
  color: var(--nw-dark) !important;
}}

/* --- marcadores y barra lateral --- */
#PersonalToolbar {{ background: var(--nw-bg2) !important; border-top: 1px solid {mix('--nw-acc', 20)} !important; }}
#sidebar-box, #sidebar-main {{ background: var(--nw-bg) !important; }}

/* --- borde de la página: escuadras de acento --- */
#tabbrowser-tabbox {{ outline: 1px solid {mix('--nw-acc', 35)} !important; outline-offset: -1px; }}
"""


def user_content(t: dict) -> str:
    """Solo para páginas internas (about:*) y barras de desplazamiento."""
    return f"""/* NEURAL//FOX — generado por neural-firefox.py */
@-moz-document url-prefix("about:") {{
  :root {{
    --in-content-page-background: {t['bg']} !important;
    --in-content-box-background: {t['bg2']} !important;
    --in-content-text-color: {t['text']} !important;
    --in-content-primary-button-background: {t['accent']} !important;
    --in-content-primary-button-text-color: #0a0204 !important;
    --in-content-accent-color: {t['accent']} !important;
    --in-content-border-radius: 0 !important;
    --color-accent-primary: {t['accent']} !important;
    --newtab-background-color: {t['bg']} !important;
    --newtab-text-primary-color: {t['text']} !important;
  }}
}}
:root {{ scrollbar-color: {t['accent']} {t['bg']}; }}
"""


def inicio_html(t: dict) -> str:
    links = [("DuckDuckGo", "https://duckduckgo.com"), ("YouTube", "https://www.youtube.com"),
             ("Wikipedia", "https://es.wikipedia.org"), ("BLFS", "https://www.linuxfromscratch.org/blfs/"),
             ("GitHub", "https://github.com"), ("Arch Wiki", "https://wiki.archlinux.org")]
    tiles = "".join(f'<a href="{u}"><b>{i + 1:02d}</b>{n}</a>' for i, (n, u) in enumerate(links))
    a, alt, text, muted, bg = t["accent"], t["alt"], t["text"], t["muted"], t["bg"]
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>NEURAL//FOX</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center;
  background: {bg}; color: {text}; font-family: "Rajdhani", sans-serif;
  background-image: repeating-linear-gradient(0deg, rgba(255,255,255,0.025) 0 1px, transparent 1px 4px); }}
h1 {{ font-size: 64px; letter-spacing: 8px; margin: 0; color: {a}; text-shadow: 0 0 18px {a}; }}
h1 span {{ color: {alt}; text-shadow: 0 0 18px {alt}; }}
.sub {{ font-family: "Share Tech Mono", monospace; color: {muted}; letter-spacing: 3px; font-size: 12px; margin: 6px 0 34px; }}
form {{ width: min(640px, 86vw); }}
input {{ width: 100%; box-sizing: border-box; background: rgba(0,0,0,0.6); color: {text}; border: 1px solid {a};
  padding: 12px 16px; font: 18px "Share Tech Mono", monospace; outline: none;
  clip-path: polygon(14px 0, 100% 0, 100% calc(100% - 14px), calc(100% - 14px) 100%, 0 100%, 0 14px); }}
input:focus {{ border-color: {alt}; box-shadow: 0 0 14px {alt}; }}
.grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; width: min(640px, 86vw); margin-top: 28px; }}
a {{ display: block; padding: 12px 14px; border: 1px solid {a}; color: {text}; text-decoration: none; font-weight: 600;
  letter-spacing: 1px; background: rgba(0,0,0,0.45);
  clip-path: polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px); }}
a:hover {{ background: {a}; color: #0a0204; }}
a b {{ font-family: "Share Tech Mono", monospace; font-weight: 400; color: {muted}; margin-right: 10px; font-size: 12px; }}
a:hover b {{ color: #0a0204; }}
</style></head><body>
<h1>NEURAL<span>//</span>FOX</h1>
<div class="sub">ENLACE NEURAL ESTABLE · TEMA {t['_name']}</div>
<form action="https://duckduckgo.com/" method="get"><input name="q" placeholder="&gt; buscar" autofocus></form>
<div class="grid">{tiles}</div>
</body></html>"""


def prefs(inicio: Path, https_only: bool) -> str:
    p = {
        "toolkit.legacyUserProfileCustomizations.stylesheets": True,  # sin esto no lee userChrome.css
        "extensions.activeThemeID": "firefox-compact-dark@mozilla.org",  # base oscura bajo el CSS
        "layout.css.prefers-color-scheme.content-override": 0,  # webs en modo oscuro si lo tienen
        "browser.theme.content-theme": 0,
        "browser.theme.toolbar-theme": 0,
        "browser.startup.homepage": inicio.as_uri(),
        "browser.startup.page": 1,
        "browser.compactmode.show": True,
        "browser.uidensity": 1,
        "browser.tabs.inTitlebar": 1,
        "browser.contentblocking.category": "strict",  # protección contra rastreo estricta
        "dom.security.https_only_mode": https_only,     # avisa antes de entrar a webs http://
    }
    lines = [MARCA_INI]
    for k, v in p.items():
        val = json.dumps(v)
        lines.append(f'user_pref("{k}", {val});')
    lines.append(MARCA_FIN)
    return "\n".join(lines) + "\n"


def write_user_js(profile: Path, block: str) -> None:
    f = profile / "user.js"
    old = f.read_text() if f.exists() else ""
    old = re.sub(re.escape(MARCA_INI) + r".*?" + re.escape(MARCA_FIN) + r"\n?", "", old, flags=re.S)
    f.write_text(old.rstrip("\n") + ("\n" if old.strip() else "") + block)


def install_fonts() -> None:
    dst = Path.home() / ".local/share/fonts/neural"
    dst.mkdir(parents=True, exist_ok=True)
    for f in (WEB / "fonts").glob("*.ttf"):
        shutil.copy2(f, dst / f.name)
    if shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f", str(dst)], check=False)
    print(f"fuentes  → {dst}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Tema NEURAL para Firefox")
    ap.add_argument("--tema", help="id del tema (por defecto el de board.json)")
    ap.add_argument("--perfil", type=Path, help="carpeta del perfil (por defecto la detecta)")
    ap.add_argument("--sin-https-only", action="store_true", help="no activar el modo solo-HTTPS")
    ap.add_argument("--sin-fuentes", action="store_true", help="no copiar las fuentes")
    a = ap.parse_args()

    themes = load_themes()
    tid = a.tema or board_theme() or "red"
    if tid not in themes:
        print(f"ERROR: tema '{tid}' no existe. Hay: {', '.join(themes)}")
        return 1
    t = themes[tid]
    profile = a.perfil or find_profile()
    if profile is None or not profile.is_dir():
        print("ERROR: no encuentro el perfil de Firefox. Ábrelo una vez, ciérralo y repite.")
        print("       Buscado en:\n         " + "\n         ".join(map(str, BASES)))
        return 1

    chrome = profile / "chrome"
    chrome.mkdir(exist_ok=True)
    uc = chrome / "userChrome.css"
    if uc.exists() and not (chrome / "userChrome.css.antes-neural").exists() \
            and "neural-firefox.py" not in uc.read_text():
        shutil.copy2(uc, chrome / "userChrome.css.antes-neural")
    uc.write_text(user_chrome(t))
    (chrome / "userContent.css").write_text(user_content(t))
    inicio = chrome / "neural-inicio.html"
    inicio.write_text(inicio_html(t))
    write_user_js(profile, prefs(inicio, not a.sin_https_only))
    if not a.sin_fuentes:
        install_fonts()
    print(f"perfil   → {profile}")
    print(f"tema     → {t['_name']} ({tid})")
    print("LISTO. Cierra Firefox del todo y vuelve a abrirlo para ver el tema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
