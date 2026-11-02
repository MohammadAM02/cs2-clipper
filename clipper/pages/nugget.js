// Nugget, the war-correspondent chicken, in pixels. CS maps are full of chickens; this one lived, put on a PRESS
// helmet and films your Highlights. He is a 20 x 20 sprite, drawn here as an SVG of square cells so every page can
// have him at any size and he stays sharp, in four moods:
//   "rendering"  filming: the REC light on his helmet camera blinks
//   "idle"       asleep beside his egg, helmet down over his eyes
//   "problem"    feathers flying, helmet knocked askew
//   "ace"        crowing, wings up
// NUGGET.svg(mood) is the whole bird, NUGGET.head(mood) his bust for small spots, NUGGET.burst(word) a pixel sound
// effect. The rooftop he used to film from is pixelscene.js now: NUGGET.scene() is kept for old callers and draws
// nothing, and NUGGET.follow() (his eye used to follow the cursor) does nothing.
"use strict";

const NUGGET = (() => {
  const K = "#0D0A08";
  const MOODS = ["rendering", "idle", "problem", "ace"];
  const moodOf = m => (MOODS.includes(m) ? m : "idle");
  // the same palette as the pixel scene: ink, cream, tan, gunmetal, the oranges, the REC red
  const COL = {w: "#F2E7CF", W: "#D6AE78", h: "#47403F", H: "#262224", c: "#F2E7CF", o: "#FF5500", b: "#FF8A3D", B: "#FF5500", r: "#FF3D2E", e: "#0D0A08", E: "#F2E7CF", l: "#FF8A3D", m: "#262224", n: "#7D736F", R: "#FF3D2E", z: "#F2E7CF", f: "#F2E7CF", s: "#FFB27A", S: "#FF5500", g: "#F2E7CF", G: "#D6AE78", d: "#0D0A08"};
  const BODY = "wWhHcobBreElmngGd";          // outlined; z, f, s, S and R are loose pixels around him
  const LOOSE = {z: "n-zz", f: "n-fe", s: "n-st", S: "n-st", R: "n-rec"};
  const MAP = {
    rendering: [
      "........oo..........",
      ".......oooo.....R...",
      "......hhhhhh..mmm...",
      ".....hhcccchhhmmmn..",
      ".....hhhhhhhhhmmm...",
      "....HHHHHHHHHH......",
      "......wEewwww.......",
      "......wwwwwwbb......",
      "......wwwwwwrB......",
      "..w...wwwwwwr.......",
      ".ww..wwwwwww........",
      ".wwwwwwwwwwww.......",
      ".wwwwWWWWwwwww......",
      "..wwwwWWWwwwww......",
      "..wwwwwwwwwwww......",
      "...wwwwwwwwww.......",
      ".....wwwwwww........",
      "......l...l.........",
      ".....ll..ll.........",
      "...................."
    ],
    idle: [
      "...............zzz..",
      ".................z..",
      "........oo......z...",
      ".......oooo.....zzz.",
      "......hhhhhh..mmm...",
      ".....hhcccchhhmmmn..",
      ".....hhhhhhhhhmmm...",
      "....HHHHHHHHHH......",
      "......wwwwwwbb......",
      "......wwwwwwrB......",
      "..w...wwwwwwr.......",
      ".ww..wwwwwww........",
      ".wwwwwwwwwwww.......",
      ".wwwwWWWWwwwww...gg.",
      "..wwwwWWWwwwww..gggg",
      "..wwwwwwwwwwww..gggG",
      "...wwwwwwwwww...gggG",
      "....wwwwwwwww....gG.",
      "....................",
      "...................."
    ],
    problem: [
      "f.......oo........f.",
      "f.....oooo.........f",
      ".....hhhhhh.mmm.....",
      "....hhcccchhmmmn....",
      "....hhhhhhhhmmm.....",
      "...HHHHHHHHHH.......",
      "......EewEeww....f..",
      "......wwwwwwbb...f..",
      "..f...wwwwwwBB......",
      "..f...wwwwwwr.......",
      ".WW..wwwwwww...WW...",
      "WWwwwwwwwwwww.WWW...",
      ".wwwwwwwwwwwwwW.....",
      "..wwwwwwwwwwww......",
      "..wwwwwwwwwwww......",
      "...wwwwwwwwww.......",
      ".....wwwwwww........",
      "......l...l.........",
      ".....ll..ll.........",
      "...................."
    ],
    ace: [
      "S.......oo........s.",
      ".s.....oooo......S..",
      "......hhhhhh..mmm...",
      "..W..hhcccchhhmmmn..",
      ".WW..hhhhhhhhhmmm.W.",
      ".WW.HHHHHHHHHH...WW.",
      ".WW...wdddwwww...WW.",
      ".WWw..wwwwwwbbb.WW..",
      "..WW..wwwwwwBB..W...",
      "..w...wwwwwwbbb.....",
      ".ww..wwwwwwwr.......",
      ".wwwwwwwwwwww.......",
      ".wwwwwwwwwwwww......",
      "..wwwwwwwwwwww......",
      "..wwwwwwwwwwww......",
      "...wwwwwwwwww.......",
      ".....wwwwwww........",
      "......l...l.........",
      ".....ll..ll.........",
      "s..................S"
    ]
  };
  // where his bust sits in each mood's sprite (x, y, size), outline included
  const BUST = {rendering: [3, 0, 17], idle: [3, 3, 17], problem: [2, 0, 17], ace: [2, 0, 17]};

  // One mood's cells as SVG rects, outline first: runs of one colour on a row become one rect.
  const cells = {};
  function draw(m) {
    if (cells[m]) return cells[m];
    const rows = MAP[m], h = rows.length, w = rows[0].length;
    const at = (x, y) => (x < 0 || y < 0 || x >= w || y >= h ? "." : rows[y][x]);
    const grid = [];                       // (w + 2) x (h + 2), padded for the outline
    for (let y = -1; y <= h; y++) {
      let line = "";
      for (let x = -1; x <= w; x++) {
        const ch = at(x, y);
        const inBody = BODY.includes(ch);
        const edge = !inBody && [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => BODY.includes(at(x + dx, y + dy)));
        line += ch !== "." ? ch : edge ? "k" : ".";
      }
      grid.push(line);
    }
    let body = "", loose = {};
    grid.forEach((line, y) => {
      for (let x = 0; x < line.length;) {
        const ch = line[x];
        let n = 1;
        while (x + n < line.length && line[x + n] === ch) n++;
        if (ch !== ".") {
          const rect = `<rect x="${x}" y="${y}" width="${n}" height="1" fill="${ch === "k" ? K : COL[ch]}"/>`;
          if (LOOSE[ch]) (loose[LOOSE[ch]] = loose[LOOSE[ch]] || []).push(rect); else body += rect;
        }
        x += n;
      }
    });
    const extras = Object.entries(loose).map(([cls, r]) => `<g class="${cls}">${r.join("")}</g>`).join("");
    return (cells[m] = `<g class="n-body">${body}</g>${extras}`);
  }

  function svg(mood = "idle", label = "Nugget") {
    const m = moodOf(mood);
    return `<svg class="nugget n-${m}" viewBox="0 0 22 22" shape-rendering="crispEdges" role="img" aria-label="${label}">${draw(m)}</svg>`;
  }

  // His bust: the same drawing, cropped to the helmet and face.
  function head(mood = "rendering") {
    const m = moodOf(mood), [x, y, s] = BUST[m];
    return `<svg class="nhead n-${m}" viewBox="${x} ${y} ${s} ${s}" shape-rendering="crispEdges" aria-hidden="true">${draw(m)}</svg>`;
  }

  // --- a pixel sound effect: a word on a stepped starburst ----------------------------------------------------------
  // The burst is the old comic one's outline, filled on a 24 x 24 grid and outlined in ink.
  const STAR = [80, 2, 90.2, 35.2, 108.6, 20.5, 108.7, 44, 131.6, 38.8, 121.4, 60, 156, 62.6, 126, 80, 144.3, 94.7, 121.4, 100,
    131.6, 121.2, 108.7, 116, 113.8, 150.3, 90.2, 124.8, 80, 146, 69.8, 124.8, 51.4, 139.5, 51.3, 116, 19, 128.6, 38.6, 100,
    15.7, 94.7, 34, 80, 15.7, 65.3, 38.6, 60, 19, 31.4, 51.3, 44, 51.4, 20.5, 69.8, 35.2];
  const N = 24;
  let burstCells = null;
  function inside(px, py) {
    let hit = false;
    for (let i = 0, j = STAR.length - 2; i < STAR.length; j = i, i += 2) {
      const xi = STAR[i], yi = STAR[i + 1], xj = STAR[j], yj = STAR[j + 1];
      if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) hit = !hit;
    }
    return hit;
  }
  function burstShape() {
    if (burstCells) return burstCells;
    const on = [];
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (inside((x + 0.5) * 160 / N, (y + 0.5) * 160 / N)) on.push(y * N + x);
    const set = new Set(on), ring = [];
    for (let y = -1; y <= N; y++) for (let x = -1; x <= N; x++) {
      if (set.has(y * N + x) && x >= 0 && x < N) continue;
      const near = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => {
        const X = x + dx, Y = y + dy;
        return X >= 0 && Y >= 0 && X < N && Y < N && set.has(Y * N + X);
      });
      if (near) ring.push([x, y]);
    }
    return (burstCells = {fill: on.map(i => [i % N, (i / N) | 0]), ring});
  }
  function burst(word, fill = "#FF5500") {
    const {fill: f, ring} = burstShape();
    const r = (cs, c) => cs.map(([x, y]) => `<rect x="${x + 1}" y="${y + 1}" width="1" height="1" fill="${c}"/>`).join("");
    return `<svg viewBox="0 0 ${N + 2} ${N + 2}" shape-rendering="crispEdges" aria-hidden="true">${r(ring, K)}${r(f, fill)}</svg><b>${word}</b>`;
  }

  const scene = () => "";
  const follow = () => {};
  return {MOODS, svg, head, scene, burst, follow};
})();
