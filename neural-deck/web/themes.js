"use strict";
// Catalogo de temas. Agregar uno = agregar un objeto aqui; todo lo demas
// (barra superior, selector de temas, paneles, fondo y colores de la
// terminal) se adapta solo.
//
// style: "grid"  -> fondo negro con rejilla y paquetes (como 2077)
//        "synth" -> sol, horizonte y piso synthwave
//        "plain" -> negro liso, sin rejilla ni paquetes
// colors: bg/bg2 fondo, accent color principal (bordes, titulos), alt
//   secundario (detalles, cursor), warn avisos, text texto, muted texto
//   apagado, panel fondo de los paneles, floor (solo synth) piso,
//   line (opcional) color de las lineas y bordes si no deben ser del acento.
// glow (opcional): intensidad del brillo de los marcos (0..1).
// term (opcional): colores fijos de la terminal en vez de los calculados,
//   p. ej. { "ansi-2": "#00ff00", fg: "#ffffff", cursor: "#ff0000" }.
const THEMES = [
  {
    id: "red", name: "2077", desc: "Negro con rojo en líneas y bordes", style: "grid",
    colors: { bg: "#020203", bg2: "#08080a", accent: "#ff2d3f", alt: "#00eaff", warn: "#fcee0a", text: "#e9e4e4", muted: "#8a5458", panel: "rgba(5, 5, 7, 0.94)" },
  },
  {
    id: "synth", name: "SYNTH", desc: "Synthwave magenta y menta con sol al horizonte", style: "synth", glow: 0.5,
    colors: { bg: "#0b0418", bg2: "#1a0730", accent: "#ff4fd8", alt: "#3dffc8", warn: "#ffe066", text: "#ffe3fb", muted: "#a77bbf", panel: "rgba(22, 6, 34, 0.84)", floor: "#12052a" },
  },
  {
    id: "matrix", name: "MATRIX", desc: "Verde fósforo con detalles ámbar", style: "grid",
    colors: { bg: "#010604", bg2: "#04100a", accent: "#39ff14", alt: "#ffb000", warn: "#ff3d00", text: "#d8ffd0", muted: "#4f8a4a", panel: "rgba(2, 10, 5, 0.93)" },
  },
  {
    id: "amber", name: "ÁMBAR", desc: "Monitor retro de fósforo ámbar", style: "grid",
    colors: { bg: "#060300", bg2: "#0e0800", accent: "#ffb000", alt: "#ff6a1a", warn: "#ff3b30", text: "#ffe8c2", muted: "#8a6a3a", panel: "rgba(10, 6, 0, 0.93)" },
  },
  {
    id: "arctic", name: "ÁRTICO", desc: "Azul hielo con menta", style: "grid",
    colors: { bg: "#01060b", bg2: "#041320", accent: "#1fb6ff", alt: "#7dffe3", warn: "#ffd23f", text: "#def3ff", muted: "#4d7489", panel: "rgba(2, 9, 16, 0.92)" },
  },
  {
    id: "violet", name: "VIOLETA", desc: "Púrpura eléctrico con turquesa", style: "grid",
    colors: { bg: "#04020a", bg2: "#0c0618", accent: "#a259ff", alt: "#00ffd5", warn: "#ffd60a", text: "#ece2ff", muted: "#7a62a3", panel: "rgba(7, 4, 14, 0.93)" },
  },
  {
    id: "mono", name: "MONO", desc: "Blanco y negro con un toque rojo", style: "grid",
    colors: { bg: "#050505", bg2: "#0b0b0c", accent: "#ececec", alt: "#ff2d3f", warn: "#fcee0a", text: "#f5f5f5", muted: "#7c7c80", panel: "rgba(8, 8, 9, 0.94)" },
  },
  {
    id: "outrun", name: "OUTRUN", desc: "Atardecer naranja con cian, estilo synthwave", style: "synth", glow: 0.45,
    colors: { bg: "#0f030c", bg2: "#2a0718", accent: "#ff6b35", alt: "#00d4ff", warn: "#ffe066", text: "#fff0e6", muted: "#b07a6a", panel: "rgba(30, 6, 18, 0.84)", floor: "#22051a" },
  },
  {
    id: "vapor", name: "VAPOR", desc: "Vaporwave cian y rosa sobre índigo", style: "synth", glow: 0.45,
    colors: { bg: "#070521", bg2: "#1b0f47", accent: "#01cdfe", alt: "#ff71ce", warn: "#fffb96", text: "#e8f9ff", muted: "#8b7fb8", panel: "rgba(16, 10, 48, 0.84)", floor: "#140a3a" },
  },
  {
    id: "negro", name: "NEGRO", desc: "Negro puro, letras blancas y bordes casi invisibles", style: "plain", glow: 0,
    colors: { bg: "#000000", bg2: "#000000", accent: "#ffffff", alt: "#ffffff", warn: "#ffffff", text: "#ffffff", muted: "#8c8c8c", panel: "rgba(0, 0, 0, 1)", line: "#3a3a3a" },
  },
];
const DEFAULT_THEME_BAR = ["red", "synth"];
const MAX_THEME_BAR = 6;
