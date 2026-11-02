// The pixel scene: a round at night, drawn into a canvas (spec: Pages; the 16-bit Press Box look).
//
// A Terrorist holds his spot, fires two bursts, slings his rifle and plants the C4; an orange wipe says BOMB PLANTED
// and hands over to an SAS Counter-Terrorist, who checks his six, fires, reloads, inspects his rifle and defuses it;
// BOMB DEFUSED, and round again. Nugget pecks about, a camera on a tripod records. Eight settings stand behind them,
// each picked by a demo's map (`PIXELSCENE.themeFor("de_inferno")` is the hill village).
//
//   - Everything is drawn into a frame of palette indices around a fixed 128 x 96 stage. The frame grows to the box's
//     shape (more sky, more city) and is blitted at the largest whole-number scale that keeps the stage in view, so
//     every pixel stays a sharp square and nothing letterboxes.
//   - One 28-colour palette: the app's ink, orange, cream, REC red and blue, plus night sky, kit and foliage tones.
//   - The agent is built from a handful of pose numbers that ease between keyframes, sampled at 12 fps and snapped to
//     the grid, so he moves like a sprite; the rifle is pre-rotated with RotSprite so it stays clean at any angle.
//   - A fixed 60 Hz update with requestAnimationFrame rendering; once running, the loop allocates nothing. It stops
//     while the window is hidden, out of focus (`pauseOnBlur`) or scrolled away, and holds a still frame when the
//     system asks for reduced motion.
//   - The loop is 30 s (15 s a side) and every effect repeats in a period that divides it, so there is no seam.
//
// PIXELSCENE.mount(canvas, {map, side, t, start, pauseOnBlur, fit, lift, stageShare, cast}) animates `canvas` inside its
// parent box (or the window, with fit: "window"); PIXELSCENE.paint(canvas, {map, t, lift}) draws one still frame into
// it, sized as it is laid out. `cast: false` leaves the agent, Nugget and the wipe out: the setting alone.
"use strict";

const PIXELSCENE = (() => {
  const STAGE_W = 128, STAGE_H = 96;        // the composition; the frame grows around it
  const MAX_W = 512, MAX_H = 384;
  const HZ = 60, STEP_MS = 1000 / HZ;       // fixed update rate
  const SEG_S = 15, LOOP_S = 30, SEG_TICKS = SEG_S * HZ, LOOP_TICKS = LOOP_S * HZ;
  const ANIM_FPS = 12, SEG_FRAMES = SEG_S * ANIM_FPS, LOOP_FRAMES = LOOP_S * ANIM_FPS;
  const EMPTY = 255;

  // --- palette --------------------------------------------------------------------------------------------------
  const PAL = [
    "#0D0A08", // K   ink, outlines (app --k)
    "#100A1E", // S0  zenith, near skyline
    "#1A1230", // S1
    "#281A44", // S2
    "#3A2352", // S3
    "#5A2C55", // S4  mauve
    "#8C3A3E", // S5  ember horizon
    "#22152E", // B1  far skyline
    "#3B2A1F", // T0  browns: T kit, wood, boots, sandstone
    "#6E4C30", // T1
    "#A47A4E", // T2
    "#D6AE78", // T3
    "#C98A62", // SK  skin
    "#22304F", // C0  navy: SAS kit
    "#3B5683", // C1
    "#6E93C2", // C2
    "#262224", // M0  gunmetal
    "#47403F", // M1
    "#7D736F", // M2
    "#FF5500", // OR  orange (app --accent)
    "#FF8A3D", // O2  (--accent2)
    "#FFB27A", // O3  (--accent3)
    "#F2E7CF", // CR  cream (--cap)
    "#FF3D2E", // RD  the recording light (--rec)
    "#7CC4FF", // BL  blue (--blue)
    "#16241F", // G0  night greens: foliage, moss, a signal
    "#2B4632", // G1
    "#5F8A4A", // G2
  ];
  const K = 0, S0 = 1, S1 = 2, S2 = 3, S3 = 4, S4 = 5, S5 = 6, B1 = 7, T0 = 8, T1 = 9, T2 = 10, T3 = 11, SK = 12,
    C0 = 13, C1 = 14, C2 = 15, M0 = 16, M1 = 17, M2 = 18, OR = 19, O2 = 20, O3 = 21, CR = 22, RD = 23, BL = 24,
    G0 = 25, G1 = 26, G2 = 27;
  const SKY = [S0, S1, S2, S3, S4, S5], SKY_COLD = [S0, S1, S2, C0, C1, C2];

  // One step lighter (rim light, muzzle flash), inside each ramp.
  const LIGHT = new Uint8Array(256);
  for (let i = 0; i < 256; i++) LIGHT[i] = i;
  for (const [a, b] of [[S0, S1], [S1, S2], [S2, S3], [S3, S4], [S4, S5], [S5, O2], [B1, S3], [T0, T1], [T1, T2],
    [T2, T3], [T3, CR], [SK, O3], [C0, C1], [C1, C2], [C2, BL], [M0, M1], [M1, M2], [M2, CR], [OR, O2], [O2, O3],
    [O3, CR], [BL, CR], [RD, O2], [G0, G1], [G1, G2], [G2, CR]]) LIGHT[a] = b;

  // palette index -> RGBA, in the byte order ImageData wants
  const PAL32 = new Uint32Array(256);
  const LITTLE = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;
  PAL.forEach((hex, i) => {
    const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    PAL32[i] = LITTLE ? ((255 << 24) | (b << 16) | (g << 8) | r) >>> 0 : ((r << 24) | (g << 16) | (b << 8) | 255) >>> 0;
  });

  // --- sprites: rows of letters, a legend per sprite --------------------------------------------------------------
  function sprite(rows, legend) {
    let w = 0;
    for (const r of rows) if (r.length > w) w = r.length;
    const h = rows.length, d = new Uint8Array(w * h).fill(EMPTY);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < rows[y].length; x++) {
        const ch = rows[y][x];
        if (ch === ".") continue;
        const c = legend[ch];
        if (c === undefined) throw new Error(`sprite: no colour for "${ch}"`);
        d[y * w + x] = c;
      }
    }
    return {w, h, d};
  }
  function recolor(s, from, to) {
    const d = s.d.slice();
    for (let i = 0; i < d.length; i++) if (d[i] === from) d[i] = to;
    return {w: s.w, h: s.h, d};
  }
  const filled = (s, x, y) => x >= 0 && y >= 0 && x < s.w && y < s.h && s.d[y * s.w + x] !== EMPTY;

  // The Terrorist: black balaclava, tan jacket, brown chest rig, an orange scarf. AK-47 with an orange magazine.
  const TSIDE = {
    torso: sprite([
      "..oooo.",
      ".Ooooyo",
      "qjvvvVj",
      "qvvVvvJ",
      "qvvVvVJ",
      "qvvvvVj",
      ".qvvvvj",
      ".qjjjJ.",
      ".qjjjj.",
      "xxxxzxx",
    ], {j: T2, J: T3, q: T1, v: T0, V: T1, o: O2, O: OR, y: O3, x: M0, z: M2}),
    headR: sprite([
      "..bbbb..",
      ".bbbbbB.",
      "bbbbbbbB",
      "bbbsesse",
      "bbbrssss",
      "bbbbbbbb",
      ".bbbbbbb",
      "..bbbbb.",
    ], {b: M0, B: M1, s: SK, e: K, r: T1}),
    headF: sprite([
      "..bbbb..",
      ".bbbbbb.",
      "bbbbbbbB",
      "bsessesb",
      "brssssrb",
      "bbbbbbbb",
      ".bbbbbb.",
      "..bbbb..",
    ], {b: M0, B: M1, s: SK, e: K, r: T1}),
    eye: K, lid: T1,
    boot: sprite([".bb..", ".bbbb", "kkkkk"], {b: T0, k: K}),
    hand: sprite(["gg", "gg"], {g: SK}),
    handFar: sprite(["gg", "gg"], {g: T1}),
    sleeve: [T1, T2, T3], sleeveFar: [T0, T1, T2], pants: [T0, T1, T2], pantsFar: [M0, T0, T1],
    gun: sprite([
      "......mmmmm.....h....",
      "...WWWMMMMMhWWWWMm...",
      "WWwwwwMMhMMMwwwwMMMMM",
      "wwwwwqMMMMMMqqqq.....",
      "qqq....wwMggg........",
      ".......ww..gGg.......",
      "......ww....ngG......",
      ".............ngG.....",
    ], {m: M1, M: M0, h: M2, w: T1, W: T2, q: T0, g: OR, G: O2, n: S5}),
    // grip (rear hand's top-left), fore (front hand), muzzle, magwell, bolt, ejection port
    anchors: [7, 4, 13, 3, 21, 2, 10, 4, 9, 1, 9, 1],
    magLetters: [OR, O2, S5], magBox: [10, 4, 15, 7],
  };
  // The classic SAS: black hood, S10-style respirator with round eyepieces and a filter on the cheek, dark kit.
  // M4A4.
  const CTSIDE = {
    torso: sprite([
      "..qjjq.",
      ".qjjjjJ",
      "qvvvvvJ",
      "qvVvVvJ",
      "qvvvvvj",
      "qvVvVvj",
      ".qvvvvj",
      ".qjjjJ.",
      ".qjjjj.",
      "xxxxzxx",
    ], {j: C0, J: C1, q: S1, v: M0, V: M1, x: M1, z: M2}),
    headR: sprite([
      "..hhhh..",
      ".hhhhhH.",
      "hhhgggGg",
      "hhgLlgLl",
      "hhglkgll",
      "hhhgggGg",
      ".hhhgfFf",
      "..hhhfF.",
    ], {h: M0, H: M1, g: M1, G: M2, l: S2, L: BL, k: S2, f: M2, F: M1}),
    headF: sprite([
      "..hhhh..",
      ".hhhhhh.",
      "hhggggGh",
      "hLlggLlh",
      "hllggllh",
      "hhggggGh",
      ".hhffhh.",
      "..hFFh..",
    ], {h: M0, g: M1, G: M2, l: S2, L: BL, f: M2, F: M1}),
    eye: BL, lid: S2,                       // the lens glint goes dark for a frame instead of a blink
    boot: sprite([".bb..", ".bbbb", "kkkkk"], {b: M0, k: K}),
    hand: sprite(["gg", "gg"], {g: M1}),
    handFar: sprite(["gg", "gg"], {g: M0}),
    sleeve: [S1, C0, C1], sleeveFar: [S0, S1, C0], pants: [S1, C0, C1], pantsFar: [S0, S1, C0],
    gun: sprite([
      ".........mmmm...h....",
      "MMMM....m.mm.m..MM...",
      "MmmMMMMMMMMMMMMMMMMMM",
      "MmmM.MMMMMMMMMMM.....",
      "MMMM...MM.Mgg........",
      ".......MM..gG........",
      "......MM...gG........",
      "...........gg........",
    ], {m: M1, M: M0, h: M2, g: M1, G: M2}),
    anchors: [7, 4, 13, 3, 21, 2, 11, 4, 10, 2, 9, 1],
    magLetters: [M1, M2], magBox: [11, 4, 12, 7],
  };
  const SIDES = [TSIDE, CTSIDE];
  for (const sd of SIDES) { sd.headRB = recolor(sd.headR, sd.eye, sd.lid); sd.headFB = recolor(sd.headF, sd.eye, sd.lid); }

  // Nugget, the app's war-correspondent chicken, in his PRESS helmet.
  const NL = {w: CR, W: T3, h: T0, c: OR, e: K, b: O2, r: RD, l: O2};
  const CHICK = [
    sprite(["....hc..", "...hhhh.", "w...wewb", "ww..wwr.", "wwwwwww.", ".wWWwww.", "..wwww..", "...l.l.."], NL),
    sprite(["....hc..", "...hhhh.", "w...wewb", "ww..wwr.", "wwwwwww.", ".wWWwww.", "..wwww..", "..l...l."], NL),
    sprite(["........", "w.......", "ww......", "www..hc.", "wwwwhhhh", ".wWWwwew", "..wwwwrb", "...l.l.."], NL),
    sprite(["..W.hc..", ".WW.hhhh", ".WW.wewb", "ww..wwr.", "wwwwwww.", ".wwwwww.", "..wwww..", "..l..l.."], NL),
  ];
  const CH_STAND = 0, CH_STEP = 1, CH_PECK = 2, CH_FLAP = 3;

  const FL = {C: CR, O: O3, o: O2, r: OR};
  const FLASH = [
    sprite(["..o....", ".oOo.r.", "OCCCOOo", ".oOo.r.", "..o...."], FL),
    sprite([".......", "..or...", "CCOoo..", "..or...", "......."], FL),
  ];
  // the C4: tan charges, a keypad, a light (drawn live at C4_LED)
  const C4 = sprite([
    "..kmmk..",
    "oTTTTTTo",
    "tTtTtTtT",
    "tttttttt",
  ], {k: M0, m: M1, T: T3, t: T2, o: OR});
  const C4_LED = 4;
  const CAMERA = sprite([
    "..MM.....",
    "MMMMMMMm.",
    "MmmmmmMmm",
    "MmhhmmMmb",
    "MmmmmmMmm",
    "MMMMMMMm.",
    "...MM....",
    "...mm....",
  ], {M: M0, m: M1, h: M2, b: BL});
  const CRATE = sprite([
    "kkkkkkkkk",
    "kWwwwwwWk",
    "kwWwwwWwk",
    "kwwWwWwwk",
    "kwwwWwwwk",
    "kwwWwWwwk",
    "kwWwwwWwk",
    "kWwwwwwWk",
    "kkkkkkkkk",
  ], {k: T0, w: T1, W: T2});

  // 5-high letters for the wipe's caption (3 wide, M and N wider), drawn at 2x
  const GLYPHS = {
    B: ["BB.", "B.B", "BB.", "B.B", "BB."], O: ["OOO", "O.O", "O.O", "O.O", "OOO"],
    M: ["M...M", "MM.MM", "M.M.M", "M...M", "M...M"], N: ["N..N", "NN.N", "N.NN", "N..N", "N..N"],
    P: ["PP.", "P.P", "PP.", "P..", "P.."], L: ["L..", "L..", "L..", "L..", "LLL"], A: [".A.", "A.A", "AAA", "A.A", "A.A"],
    T: ["TTT", ".T.", ".T.", ".T.", ".T."], E: ["EEE", "E..", "EE.", "E..", "EEE"],
    D: ["DD.", "D.D", "D.D", "D.D", "DD."], F: ["FFF", "F..", "FF.", "F..", "F.."], U: ["U.U", "U.U", "U.U", "U.U", "UUU"],
    S: [".SS", "S..", ".S.", "..S", "SS."],
  };
  function caption(text) {           // a string, as one bitmap
    const gw = ch => (GLYPHS[ch] ? GLYPHS[ch][0].length : 3);
    let w = -1;
    for (const ch of text) w += gw(ch) + 1;
    const d = new Uint8Array(w * 5);
    let x0 = 0;
    for (const ch of text) {
      const g = GLYPHS[ch];
      if (g) for (let y = 0; y < 5; y++) for (let x = 0; x < g[y].length; x++) if (g[y][x] !== ".") d[y * w + x0 + x] = 1;
      x0 += gw(ch) + 1;
    }
    return {w, h: 5, d};
  }
  const CAPTIONS = [caption("BOMB PLANTED"), caption("BOMB DEFUSED")];

  // --- RotSprite: Scale2x three times, rotate by nearest sampling, read back at 1x ---------------------------------
  function scale2x(s) {
    const w = s.w, h = s.h, o = new Uint8Array(w * h * 4), W2 = w * 2;
    const at = (x, y) => (x < 0 || y < 0 || x >= w || y >= h) ? EMPTY : s.d[y * w + x];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const E = at(x, y), B = at(x, y - 1), D = at(x - 1, y), F = at(x + 1, y), Hh = at(x, y + 1);
        let e0 = E, e1 = E, e2 = E, e3 = E;
        if (B !== Hh && D !== F) {
          if (D === B) e0 = D;
          if (B === F) e1 = F;
          if (D === Hh) e2 = D;
          if (Hh === F) e3 = F;
        }
        o[(y * 2) * W2 + x * 2] = e0; o[(y * 2) * W2 + x * 2 + 1] = e1;
        o[(y * 2 + 1) * W2 + x * 2] = e2; o[(y * 2 + 1) * W2 + x * 2 + 1] = e3;
      }
    }
    return {w: W2, h: h * 2, d: o};
  }
  // Rotates `src` (with `up`, its 8x Scale2x) by `deg`, clockwise on screen, about the centre of its pivot pixel
  // (px, py). The pivot keeps a pixel of its own (pox, poy), and `anch` (pairs of pixel coordinates) come along.
  function rotsprite(src, up, px, py, deg, anch) {
    const S = 8, a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), cx = px + 0.5, cy = py + 0.5;
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (const [X, Y] of [[0, 0], [src.w, 0], [0, src.h], [src.w, src.h]]) {
      const rx = X - cx, ry = Y - cy, X2 = rx * c - ry * s, Y2 = rx * s + ry * c;
      x0 = Math.min(x0, X2); x1 = Math.max(x1, X2); y0 = Math.min(y0, Y2); y1 = Math.max(y1, Y2);
    }
    x0 = Math.floor(x0) - 1; y0 = Math.floor(y0) - 1; x1 = Math.ceil(x1) + 1; y1 = Math.ceil(y1) + 1;
    const w = x1 - x0 + 1, h = y1 - y0 + 1, d = new Uint8Array(w * h).fill(EMPTY);
    for (let j = 0; j < h; j++) {
      for (let i = 0; i < w; i++) {
        const rx = x0 + i, ry = y0 + j;
        const sx = cx + rx * c + ry * s, sy = cy - rx * s + ry * c;
        const ux = Math.floor(sx * S), uy = Math.floor(sy * S);
        if (ux >= 0 && uy >= 0 && ux < up.w && uy < up.h) d[j * w + i] = up.d[uy * up.w + ux];
      }
    }
    const out = new Int16Array(anch.length);
    for (let k = 0; k < anch.length; k += 2) {
      const rx = anch[k] - px, ry = anch[k + 1] - py;
      out[k] = Math.round(rx * c - ry * s); out[k + 1] = Math.round(rx * s + ry * c);
    }
    return {w, h, d, pox: -x0, poy: -y0, a: out};
  }

  const GUN_MIN = -120, GUN_STEP = 3, GUN_N = 56;            // -120 (slung on his back) .. +45 degrees
  // the rifles at every angle, cut once, the first time a scene is made
  let prepared = false;
  function prepare() {
    if (prepared) return;
    prepared = true;
    for (const side of SIDES) {
      const g = side.gun, [mx0, my0, mx1, my1] = side.magBox;
      const inMag = (x, y) => x >= mx0 && x <= mx1 && y >= my0 && y <= my1 && side.magLetters.includes(g.d[y * g.w + x]);
      const bare = {w: g.w, h: g.h, d: g.d.slice()};
      const magRows = [];
      for (let y = my0; y <= my1; y++) {
        let row = "";
        for (let x = mx0; x <= mx1; x++) {
          if (inMag(x, y)) { bare.d[y * g.w + x] = EMPTY; row += String.fromCharCode(97 + g.d[y * g.w + x]); } else row += ".";
        }
        magRows.push(row);
      }
      const legend = {};
      for (let i = 0; i < PAL.length; i++) legend[String.fromCharCode(97 + i)] = i;
      side.mag = sprite(magRows, legend);
      let off = 0;
      while (off < side.mag.w && side.mag.d[off] === EMPTY) off++;
      side.magOff = off;
      const upG = scale2x(scale2x(scale2x(g))), upB = scale2x(scale2x(scale2x(bare)));
      side.rot = [[], []];                                    // [with magazine][angle], [without][angle]
      const [gx, gy] = side.anchors;
      for (let k = 0; k < GUN_N; k++) {
        const deg = GUN_MIN + k * GUN_STEP;
        side.rot[0].push(rotsprite(g, upG, gx, gy, deg, side.anchors));
        side.rot[1].push(rotsprite(bare, upB, gx, gy, deg, side.anchors));
      }
    }
  }

  function create(canvas, opts) {
    prepare();
    const CAST = opts.cast !== false;    // false: the setting alone, for a backdrop behind a page with its own scene
    // --- the frame: it grows around the stage to fill the box ------------------------------------------------------
    let LW = STAGE_W, LH = STAGE_H, SX = 0, SY = 0;          // frame size, and where the stage sits in it
    let AX = 52, AY = 85, ROOF_Y = 86, ROOF_X1 = 96, MX = 104, MY = 18, REC_X = 4, REC_Y = 70;
    let fb = new Uint8Array(LW * LH), bg = new Uint8Array(LW * LH);
    const CW = 84, CH = 60, CX0 = 30, CY0 = 54;              // the agent's own layer; his feet are its (30, 54)
    const cb = new Uint8Array(CW * CH), cb2 = new Uint8Array(CW * CH);
    const BOMB_LX = 10;                                       // where the C4 goes, from his feet

    // --- background themes --------------------------------------------------------------------------------------------
    const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
    function rng(seed) {
      return () => {
        seed = seed + 0x6D2B79F5 | 0;
        let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
        t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
        return ((t ^ t >>> 14) >>> 0) / 4294967296;
      };
    }
    let rand = rng(1);
    const put = (x, y, c) => { if (x >= 0 && y >= 0 && x < LW && y < LH) bg[y * LW + x] = c; };
    const get = (x, y) => (x >= 0 && y >= 0 && x < LW && y < LH) ? bg[y * LW + x] : EMPTY;
    const rect = (x, y, w, h, c) => { for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) put(i, j, c); };
    function line(x0, y0, x1, y1, c) {
      const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
      let err = dx + dy;
      for (;;) {
        put(x0, y0, c);
        if (x0 === x1 && y0 === y1) break;
        const e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
      }
    }
    function disc(cx, cy, r, c) {
      for (let y = -r; y <= r; y++) for (let x = -r; x <= r; x++) if (x * x + y * y <= r * r + r * 0.6) put(cx + x, cy + y, c);
    }
    function dome(cx, base, r, c) {
      for (let y = 0; y <= r; y++) {
        const half = Math.round(Math.sqrt(r * r - y * y) + 0.2);
        for (let x = -half; x <= half; x++) put(cx + x, base - y, c);
      }
    }
    function blitBg(s, x0, y0, outline) {
      if (outline) {
        for (let j = -1; j <= s.h; j++) for (let i = -1; i <= s.w; i++) {
          if (!filled(s, i, j) && (filled(s, i - 1, j) || filled(s, i + 1, j) || filled(s, i, j - 1) || filled(s, i, j + 1))) put(x0 + i, y0 + j, K);
        }
      }
      for (let j = 0; j < s.h; j++) for (let i = 0; i < s.w; i++) if (filled(s, i, j)) put(x0 + i, y0 + j, s.d[j * s.w + i]);
    }

    // blinking lights: x, y, colour, period (ticks), phase (ticks), ticks lit per period
    let LIGHTS = [];
    function lightAt(x, y, c, periodS, phase, duty) {
      LIGHTS.push(x, y, c, Math.round(periodS * HZ), Math.round(phase * periodS * HZ), Math.round(duty * periodS * HZ));
    }
    const PERIODS = [2, 3, 5, 6];             // all divide the 30 s loop

    // the night sky: six bands ordered-dithered where they meet, warmer toward the city under the moon
    function sky(glow, ramp) {
      const reach = Math.max(64, LW * 0.45);
      for (let y = 0; y < LH; y++) {
        const yy = y - SY;
        for (let x = 0; x < LW; x++) {
          let v = yy <= 0 ? 0 : Math.pow(yy / 82, 1.4) * 5.3;
          v += Math.max(0, 1 - Math.abs(x - MX) / reach) * Math.max(0, (yy - 44) / 40) * glow;
          const dm = Math.hypot(x - MX, y - MY);
          if (dm < 22) v += (1 - dm / 22) * 1.5;
          if (v > 5) v = 5;
          const b = Math.floor(v), f = Math.min(1, Math.max(0, (v - b - 0.2) / 0.6));
          bg[y * LW + x] = ramp[Math.min(5, b + (f > (BAYER[(y & 3) * 4 + (x & 3)] + 0.5) / 16 ? 1 : 0))];
        }
      }
      for (let y = 1; y < 34; y += 4) {                                   // the app's halftone corner
        for (let x = 1 + ((y >> 2) & 1) * 2; x < 60; x += 4) if (x + y * 1.5 < 50) put(x, y, ramp[2]);
      }
    }
    function moon(crescent) {
      const R = 7;
      for (let y = MY - R; y <= MY + R; y++) {
        for (let x = MX - R; x <= MX + R; x++) {
          const dx = x - MX, dy = y - MY, d = Math.hypot(dx, dy);
          if (d > R + 0.3) continue;
          if (crescent && Math.hypot(dx + 4, dy - 2) < R - 0.4) continue;
          let c = CR;
          if (!crescent && dx + dy * 0.3 < -2.5 && (y - MY + 30) % 3 === 0) c = T3;
          if (d > R - 0.9 && dx < 0) c = T3;
          put(x, y, c);
        }
      }
      if (!crescent) { put(MX + 2, MY - 3, T3); put(MX + 3, MY - 3, T3); put(MX - 1, MY + 3, T3); put(MX + 3, MY + 2, T3); }
    }
    // a row of buildings from x0 to x1: tops between t0 and t1, with sparse windows, some of them blinking
    function buildings(x0, x1, wMin, wSpan, t0, t1, c, stat, blink, lit, dim, step) {
      let x = x0;
      while (x < x1) {
        const w = wMin + Math.floor(rand() * wSpan), top = t0 + Math.floor(rand() * (t1 - t0));
        rect(x, top, w, LH - top, c);
        for (let wy = top + 2; wy < LH; wy += 3) {
          for (let wx = x + 1; wx < x + w - 1; wx += step) {
            const r = rand();
            if (r < blink) lightAt(wx, wy, lit, PERIODS[Math.floor(rand() * 4)], rand(), 0.4 + rand() * 0.5);
            else if (r < blink + stat) put(wx, wy, dim);
          }
        }
        x += w + (rand() < 0.3 ? 1 : 0);
      }
    }
    function camera() {
      blitBg(CAMERA, SX + 3, SY + 69, true);
      line(SX + 7, SY + 77, SX + 3, SY + 85, M1); line(SX + 7, SY + 77, SX + 7, SY + 85, M0); line(SX + 7, SY + 77, SX + 11, SY + 85, M1);
    }

    // --- shared pieces for the settings -------------------------------------------------------------------------------
    const isSky = v => v === S0 || v === S1 || v === S2 || v === S3 || v === S4 || v === S5 || v === C0 || v === C1 || v === C2;
    // the topmost pixel of colour c in each column between y0 and y1 catches the moon, with probability p
    function rimTop(c, hi, y0, y1, p) {
      for (let x = 0; x < LW; x++) {
        for (let y = Math.max(0, y0); y <= y1 && y < LH; y++) {
          if (get(x, y) === c) { if (rand() < p) put(x, y, hi); break; }
        }
      }
    }
    // a line of tree crowns, filled down to the bottom
    function canopy(base, rMin, rSpan, c, hi) {
      let x = -4;
      while (x < LW + 6) {
        const r = rMin + Math.floor(rand() * rSpan);
        disc(x, base + Math.floor(rand() * 3), r, c);
        x += r + 1 + Math.floor(rand() * r);
      }
      rect(0, base, LW, LH - base, c);
      if (hi >= 0) rimTop(c, hi, base - rMin - rSpan - 3, base + 2, 0.45);
    }
    // a rolling ridge: top(x) from two sine waves, filled down; a lit top row
    function ridge(base, a1, f1, a2, f2, c, top, top2) {
      for (let x = 0; x < LW; x++) {
        const t = Math.round(base + a1 * Math.sin(x / f1) + a2 * Math.sin(x / f2 + 1.3));
        for (let y = t; y < LH; y++) put(x, y, y === t ? top : y === t + 1 && top2 >= 0 && ((x + y) & 1) ? top2 : c);
      }
    }
    function palm(px, base, h, lean, c) {
      let tx = px;
      for (let y = 0; y < h; y++) { tx = px + Math.round(lean * (y * y) / 90); put(tx, base - y, c); if (y < 4) put(tx + 1, base - y, c); }
      const cx = tx, cy = base - h;
      for (const [dx, dy] of [[-6, 2], [-4, -2], [0, -3], [4, -2], [6, 2], [5, 3], [-5, 3]]) {
        line(cx, cy, cx + dx, cy + dy, c);
        put(cx + dx, cy + dy + 1, c);
      }
      put(cx, cy, c); put(cx - 1, cy + 1, c); put(cx + 1, cy + 1, c);
    }
    function cypress(x, base, h, c) {
      put(x, base, c); put(x, base - 1, c);
      for (let y = 2; y < h; y++) {
        const half = Math.round(1.7 * Math.sin(Math.PI * Math.pow((y - 2) / (h - 2), 0.75)));
        for (let i = -half; i <= half; i++) put(x + i, base - y, c);
      }
    }
    // a fir in tiers; `snow` (or -1) lies on top of each tier
    function pine(x, base, h, c, snow) {
      put(x, base, c); put(x, base - 1, c);
      for (let y = 2; y < h; y++) {
        const tier = (y - 2) % 4, half = Math.max(0, Math.round((1 - (y - 2) / (h - 2)) * h * 0.3) - tier);
        for (let i = -half; i <= half; i++) put(x + i, base - y, snow >= 0 && tier === 3 && rand() < 0.75 ? snow : c);
      }
      put(x, base - h, snow >= 0 ? snow : c);
    }
    // a house: walls, a pitched roof lit on its right, windows (1 = one pixel, 2 = two by two), some lit
    function house(x, base, w, h, wall, roof, roofHi, lit, win) {
      rect(x, base - h, w, h, wall);
      const rh = Math.max(2, Math.ceil(w / 3));
      for (let k = 0; k < rh; k++) {
        const inset = Math.round(k * (w / 2 + 1) / rh);
        for (let i = x - 1 + inset; i <= x + w - inset; i++) put(i, base - h - 1 - k, roof);
        put(x + w - inset, base - h - 1 - k, roofHi);
      }
      for (let wy = base - h + 2; wy < base - win - 1; wy += win + 3) {
        for (let wx = x + 2; wx < x + w - win - 1; wx += win + 3) {
          const r = rand(), c = r < lit ? (r < lit * 0.4 ? O3 : O2) : K;
          rect(wx, wy, win, win, c);
          if (win === 2 && c !== K && rand() < 0.3) lightAt(wx, wy, K, PERIODS[Math.floor(rand() * 4)], rand(), 0.25);
        }
      }
    }
    // a stepped temple, with torches by the shrine on top
    function stepped(cx, base, w, tiers, th, c, edge) {
      for (let k = 0; k < tiers; k++) {
        const half = (w >> 1) - k * 3, y = base - (k + 1) * th;
        rect(cx - half, y, half * 2 + 1, th, c);
        for (let x = cx - half; x <= cx + half; x++) put(x, y, edge);
      }
      const top = base - tiers * th;
      for (let y = top; y < base; y++) for (let i = -1; i <= 1; i++) put(cx + i, y, (y & 1) ? edge : c);
      rect(cx - 4, top - 6, 9, 6, c);
      for (let x = cx - 4; x <= cx + 4; x++) put(x, top - 6, edge);
      rect(cx - 1, top - 4, 3, 4, K);
      lightAt(cx - 3, top - 3, O2, 0.5, 0, 0.6); lightAt(cx - 3, top - 4, OR, 0.6, 0.3, 0.4);
      lightAt(cx + 3, top - 3, O2, 0.6, 0.5, 0.6); lightAt(cx + 3, top - 4, OR, 0.5, 0.1, 0.4);
    }
    // a broken wall of blocks with vines down its face
    function ruin(x0, w, top, c, joint, vine) {
      for (let bx = x0; bx < x0 + w; bx += 5) {
        const t = top + Math.floor(rand() * 5);
        for (let x = bx; x < Math.min(bx + 5, x0 + w); x++) {
          for (let y = t; y < LH; y++) {
            const r = y - t;
            put(x, y, r % 4 === 3 || (x - x0 + ((r >> 2) & 1) * 2) % 5 === 4 ? joint : c);
          }
        }
        if (rand() < 0.6) {
          const vx = bx + Math.floor(rand() * 5), len = 3 + Math.floor(rand() * 8);
          for (let y = 0; y < len; y++) put(vx + ((y >> 2) & 1), t + y, vine);
        }
      }
    }
    function campanile(x, base, top, c, edge) {
      rect(x - 3, top, 7, base - top, c);
      for (let y = top; y < base; y++) put(x + 3, y, edge);
      rect(x - 4, top + 8, 9, 1, c); rect(x - 4, top, 9, 1, c);
      rect(x - 2, top + 2, 2, 5, K); rect(x + 1, top + 2, 2, 5, K);
      for (let k = 1; k <= 4; k++) for (let i = -(4 - k); i <= 4 - k; i++) put(x + i, top - k, c);
      put(x, top - 5, c); put(x, top - 6, c); put(x - 1, top - 5, c); put(x + 1, top - 5, c);
      lightAt(x + 1, top + 5, O2, 3, 0, 0.8);
    }
    // a cooling tower, its steam drifting up and left over the sky
    function coolingTower(cx, base, h, w, c, edge) {
      for (let y = 0; y < h; y++) {
        const t = y / (h - 1), half = Math.round(w * Math.sqrt(0.42 + Math.pow((t - 0.78) / 0.62, 2)) / 1.39);
        for (let i = -half; i <= half; i++) put(cx + i, base - y, c);
        put(cx + half, base - y, edge);
      }
      const top = base - h;
      for (let k = 0; k < 4; k++) {
        const px = cx - k * 3, py = top - 3 - k * 4, r = 2 + k;
        for (let j = -r; j <= r; j++) {
          for (let i = -r; i <= r; i++) {
            const d2 = i * i + j * j, X = px + i, Y = py + j;
            if (d2 > r * r || !isSky(get(X, Y))) continue;
            const solid = d2 < (r - 1.5) * (r - 1.5) && k < 2;
            if (solid || ((X + Y) & 1) === 0 && ((X ^ Y) & 2) === 0 || (k < 2 && ((X + Y) & 1) === 0)) put(X, Y, k === 0 ? S4 : S3);
          }
        }
      }
    }
    function pylon(x, base, h, c) {
      for (let y = 0; y < h; y++) {
        const half = Math.round((1 - y / h) * 3);
        put(x - half, base - y, c); put(x + half, base - y, c);
        if (y % 3 === 0) for (let i = -half; i <= half; i++) put(x + i, base - y, c);
      }
      for (let i = -5; i <= 5; i++) put(x + i, base - h + 2, c);
      for (let i = -3; i <= 3; i++) put(x + i, base - h + 5, c);
      put(x, base - h, c); put(x, base - h - 1, c);
    }
    function wire(x0, y0, x1, y1, sag, c) {
      for (let x = x0; x <= x1; x++) {
        const u = (x - x0) / (x1 - x0);
        put(x, Math.round(y0 + (y1 - y0) * u + sag * 4 * u * (1 - u)), c);
      }
    }
    function boxcar(x, base, w, h, c, edge, tank) {
      const b0 = base - h, b1 = base - 3;
      if (tank) {
        for (let y = b0; y < b1; y++) {
          const u = (y - b0) / (b1 - b0 - 1), inset = Math.round(2 * (1 - Math.sin(Math.PI * u)));
          for (let i = x + inset; i < x + w - inset; i++) put(i, y, c);
        }
        for (let i = x + 2; i < x + w - 2; i++) put(i, b0 + 1, edge);
        rect(x + (w >> 1) - 1, b0 - 2, 3, 2, c);
      } else {
        rect(x, b0, w, b1 - b0, c);
        for (let i = x; i < x + w; i++) put(i, b0, edge);
        for (let i = x + 2; i < x + w - 1; i += 3) for (let y = b0 + 2; y < b1 - 1; y++) put(i, y, edge);
        const dx = x + (w >> 1) - 3;
        rect(dx, b0 + 2, 7, b1 - b0 - 3, c);
        for (let y = b0 + 2; y < b1 - 1; y++) { put(dx, y, edge); put(dx + 6, y, edge); }
      }
      rect(x + 1, b1, w - 2, 1, K);
      for (const wx of [x + 3, x + 6, x + w - 9, x + w - 6]) rect(wx, base - 2, 2, 2, K);
      put(x - 1, b1 - 1, c); put(x + w, b1 - 1, c);
    }
    function obelisk(x, base, h, c, edge) {
      for (let y = 0; y < h - 2; y++) {
        const wv = 4 - Math.round(2 * y / (h - 2)), x0 = x - (wv >> 1);
        for (let i = 0; i < wv; i++) put(x0 + i, base - y, c);
        put(x0 + wv - 1, base - y, edge);
      }
      put(x, base - h + 2, c); put(x, base - h + 1, edge);
    }
    // a temple gateway: two sloped towers with a lintel between, torches at the gate
    function gateway(cx, base, h, c, edge) {
      for (let y = 0; y < h; y++) {
        const inset = Math.round(y * 3 / h);
        for (let x = cx - 18 + inset; x <= cx - 5; x++) put(x, base - y, c);
        for (let x = cx + 5; x <= cx + 18 - inset; x++) put(x, base - y, c);
        put(cx + 18 - inset, base - y, edge);
      }
      rect(cx - 17, base - h, 13, 1, edge); rect(cx + 5, base - h, 13, 1, edge);
      rect(cx - 5, base - Math.round(h * 0.6), 11, 2, c);
      for (let y = base - 8; y < base - 2; y += 2) { put(cx - 12, y, S0); put(cx + 12, y, S0); }
      lightAt(cx - 6, base - 6, O2, 0.5, 0, 0.6); lightAt(cx - 6, base - 7, OR, 0.6, 0.4, 0.4);
      lightAt(cx + 6, base - 6, O2, 0.6, 0.2, 0.6); lightAt(cx + 6, base - 7, OR, 0.5, 0.6, 0.4);
    }
    // still water from y0 to y1, ripples, and the moon broken up on it
    function water(y0, y1) {
      for (let y = y0; y < y1; y++) {
        for (let x = 0; x < LW; x++) {
          const v = (y - y0) / (y1 - y0) + (BAYER[(y & 3) * 4 + (x & 3)] / 16 - 0.5) * 0.5;
          put(x, y, v < 0.45 ? S3 : S2);
        }
      }
      for (let x = 0; x < LW; x++) put(x, y0, S4);                         // the far bank's waterline
      for (let k = 0; k < LW * (y1 - y0) / 28; k++) {
        const x = Math.floor(rand() * LW), y = y0 + 1 + Math.floor(rand() * (y1 - y0 - 1)), l = 2 + Math.floor(rand() * 4);
        for (let i = 0; i < l; i++) put(x + i, y, (y - y0) < (y1 - y0) / 2 ? S4 : S3);
      }
      for (let y = y0 + 1; y < y1; y += 2) {                               // the moon, broken up on the water
        const half = 2 + ((y - y0) >> 1);
        for (let k = 0; k < 2; k++) {
          const len = 2 + Math.floor(rand() * 4), x = MX - half + Math.floor(rand() * (2 * half - len + 1));
          for (let i = 0; i < len; i++) put(x + i, y, i === 0 || i === len - 1 ? S4 : T3);
          lightAt(x + (len >> 1), y, CR, PERIODS[Math.floor(rand() * 4)] / 2, rand(), 0.5);
        }
      }
    }
    // the ground he stands on, from ROOF_Y down, across the whole width
    function ground(kind) {
      ROOF_X1 = LW;
      for (let x = 0; x < LW; x++) {
        for (let y = ROOF_Y; y < LH; y++) {
          const r = y - ROOF_Y, d = BAYER[(y & 3) * 4 + (x & 3)] / 16;
          let c = K;
          if (kind === "moss") {
            if (r === 0) c = rand() < 0.3 ? G2 : G1; else if (r === 1) c = G0;
            else { const q = r - 2, course = (q / 4) | 0; c = q % 4 === 3 || (x + course * 5) % 10 === 0 ? K : rand() < 0.12 ? G0 : M0; }
          } else if (kind === "cobble") {
            if (r === 0) c = M1; else if (r === 1) c = M0;
            else { const q = r - 2, course = (q / 3) | 0; c = q % 3 === 2 || (x + course * 2) % 4 === 3 ? K : rand() < 0.3 ? M1 : M0; }
          } else if (kind === "concrete") {
            if (r === 0) c = M1; else if (r === 1) c = M0;
            else if (r < 4) c = (x + y) % 6 < 3 ? T3 : K;                    // a painted hazard band
            else c = rand() < 0.12 ? M0 : K;
          } else if (kind === "rails") {
            if (r === 0) c = M2; else if (r === 1) c = M1;
            else if (r < 4) c = x % 7 < 3 ? T0 : rand() < 0.4 ? M0 : K;       // sleepers under the rail
            else { const n = rand(); c = n < 0.25 ? M1 : n < 0.6 ? M0 : K; }   // ballast
          } else if (kind === "snow") {
            if (r === 0) c = CR; else if (r === 1) c = d < 0.5 ? CR : C2;
            else { const v = (r - 2) / Math.max(1, LH - ROOF_Y - 2) * 3 + d * 0.9; c = rand() < 0.015 ? CR : v < 1 ? C2 : v < 2 ? C1 : C0; }
          } else if (kind === "quay") {
            if (r === 0) c = T2; else if (r === 1) c = T1;
            else { const q = r - 2, course = (q / 3) | 0; c = q % 3 === 2 || (x + course * 4) % 8 === 0 ? K : rand() < 0.12 ? T1 : T0; }
          }
          put(x, y, c);
        }
      }
    }
    let PMODE = 0, PCOUNT = 0;                // particles: 0 embers, 1 fireflies, 2 snow; how many per 128 px

    // --- the settings ---------------------------------------------------------------------------------------------------
    // Rooftop: a city at night, a tower crane over a half-built tower, a domed building, a water tower.
    function buildRooftop() {
      sky(0.9, SKY);
      moon(false);
      buildings(-2, LW, 5, 8, SY + 60, SY + 72, B1, 0.08, 0.02, O2, S5, 2);
      rect(SX + 104, SY + 62, 18, LH, B1);
      dome(SX + 113, SY + 61, 6, B1); put(SX + 113, SY + 54, B1); put(SX + 113, SY + 53, B1); put(SX + 113, SY + 52, O3);
      put(SX + 106, SY + 66, S5); put(SX + 117, SY + 66, S5); put(SX + 108, SY + 70, S5);
      rect(SX + 72, SY + 56, 13, LH, B1);
      for (let y = SY + 50; y < SY + 56; y++) { put(SX + 72, y, B1); put(SX + 84, y, B1); if (y % 2) put(SX + 78, y, B1); }
      for (let x = SX + 72; x <= SX + 84; x++) put(x, SY + 50, B1);
      for (let y = SY + 30; y < SY + 56; y++) { put(SX + 79, y, B1); if (((y - SY) & 1) === 0) put(SX + 80, y, B1); }
      for (let x = SX + 62; x <= SX + 98; x++) { put(x, SY + 30, B1); if (((x - SX) & 1) === 0) put(x, SY + 31, B1); }
      line(SX + 79, SY + 26, SX + 66, SY + 30, B1); line(SX + 79, SY + 26, SX + 96, SY + 30, B1);
      put(SX + 79, SY + 27, B1); put(SX + 79, SY + 28, B1); put(SX + 79, SY + 29, B1);
      rect(SX + 62, SY + 31, 4, 3, B1);
      line(SX + 93, SY + 31, SX + 93, SY + 41, S3); rect(SX + 92, SY + 42, 3, 2, B1);
      lightAt(SX + 79, SY + 25, RD, 1.5, 0, 0.25);
      buildings(0, LW, 8, 9, SY + 69, SY + 79, S0, 0.04, 0.012, O3, S5, 3);
      rect(SX + 100, SY + 63, 6, 5, S0); put(SX + 99, SY + 64, S0); put(SX + 106, SY + 64, S0);
      for (let y = SY + 68; y < SY + 72; y++) { put(SX + 100, y, S0); put(SX + 105, y, S0); }
      put(SX + 101, SY + 62, S0); put(SX + 102, SY + 61, S0); put(SX + 103, SY + 61, S0); put(SX + 104, SY + 62, S0);
      for (let x = 0; x < ROOF_X1; x++) {                                // the roof: a lit lip, a hatched wall
        put(x, ROOF_Y, M1); put(x, ROOF_Y + 1, M0);
        for (let y = ROOF_Y + 2; y < LH; y++) put(x, y, (x + y) % 5 === 0 ? M0 : K);
      }
      rect(SX + 88, SY + 81, 8, 5, K); for (let x = SX + 88; x < SX + 96; x++) { put(x, SY + 81, M1); put(x, SY + 82, M0); }
      for (let y = SY + 81; y < LH; y++) put(ROOF_X1, y, y === SY + 81 ? M1 : (y & 1) ? S5 : K);
      camera();
      PMODE = 0; PCOUNT = 5;
    }

    // Desert: a sandstone town under a crescent moon; domes, a minaret, palms, crates on a flat roof.
    function buildDesert() {
      sky(1.3, SKY);
      moon(true);
      let x = -2;
      while (x < LW) {
        const w = 6 + Math.floor(rand() * 9), top = SY + 60 + Math.floor(rand() * 10);
        if (!(x + w > SX + 108 && x < SX + 118)) {
          rect(x, top, w, LH, B1);
          if (rand() < 0.35 && w >= 8) dome(x + (w >> 1), top - 1, (w >> 1) - 1, B1);
          for (let wy = top + 3; wy < SY + 80; wy += 4) for (let wx = x + 2; wx < x + w - 2; wx += 3) {
            const r = rand();
            if (r < 0.02) lightAt(wx, wy, O2, PERIODS[Math.floor(rand() * 4)], rand(), 0.5); else if (r < 0.1) put(wx, wy, S5);
          }
        }
        x += w + (rand() < 0.4 ? 2 : 0);
      }
      const mx = SX + 113;
      rect(mx - 2, SY + 40, 5, LH, B1);
      rect(mx - 3, SY + 47, 7, 2, B1); rect(mx - 3, SY + 40, 7, 1, B1);
      dome(mx, SY + 39, 2, B1); put(mx, SY + 36, B1); put(mx, SY + 35, B1);
      put(mx, SY + 44, O2); put(mx, SY + 52, S5);
      x = -3;
      while (x < LW) {
        const w = 10 + Math.floor(rand() * 10), top = SY + 70 + Math.floor(rand() * 7);
        rect(x, top, w, LH, S2);
        for (let i = x; i < x + w; i++) put(i, top, S3);
        if (rand() < 0.6) {
          const ax = x + 3 + Math.floor(rand() * Math.max(1, w - 8)), ay = top + 4;
          rect(ax, ay + 1, 3, 5, S0); put(ax + 1, ay, S0);
          if (rand() < 0.25) put(ax + 1, ay + 3, O2);
        }
        x += w;
      }
      for (const [px, h, lean] of [[SX + 66, 22, 1], [SX + 104, 18, -1], [SX - 30, 20, 1], [SX + 150, 21, -1]]) palm(px, SY + 86, h, lean, S0);
      for (let x2 = 0; x2 < ROOF_X1; x2++) {
        put(x2, ROOF_Y, T1); put(x2, ROOF_Y + 1, T0);
        for (let y = ROOF_Y + 2; y < LH; y++) {
          const r = y - ROOF_Y - 2, course = (r / 3) | 0;
          put(x2, y, r % 3 === 2 || (x2 + course * 3) % 6 === 0 ? T0 : K);
        }
      }
      for (let i = 0; i < 3; i++) {
        const bx = SX + 84 + i * 4;
        rect(bx, SY + 82, 3, 4, K); for (let k = 0; k < 3; k++) put(bx + k, SY + 82, T1);
      }
      for (let y = SY + 82; y < LH; y++) put(ROOF_X1, y, (y & 1) ? S5 : K);
      blitBg(CRATE, SX + 79, SY + 77, false);
      blitBg(CRATE, SX + 88, SY + 77, false);
      blitBg(CRATE, SX + 83, SY + 68, false);
      camera();
      PMODE = 0; PCOUNT = 3;
    }

    // Jungle: ruins in the forest, a stepped temple with torches above the trees, fireflies, mossy stone underfoot.
    function buildJungle() {
      sky(0.6, SKY);
      moon(true);
      canopy(SY + 64, 4, 5, B1, S3);
      stepped(SX + 106, SY + 68, 34, 5, 4, B1, S3);
      canopy(SY + 74, 5, 5, G0, G1);
      ruin(SX + 60, 26, SY + 71, S1, S0, G1);
      ruin(SX - 44, 36, SY + 75, S1, S0, G1);
      ruin(SX + 120, 40, SY + 73, S1, S0, G1);
      ground("moss");
      for (let x = 0; x < LW; x++) if (rand() < 0.22) { put(x, ROOF_Y - 1, G1); if (rand() < 0.4) put(x, ROOF_Y - 2, G1); }
      camera();
      PMODE = 1; PCOUNT = 8;
    }

    // Village: a hill town of tiled roofs and cypresses with a bell tower, plastered houses on a cobbled street.
    function buildVillage() {
      sky(0.8, SKY);
      moon(false);
      const hill = x => Math.round(SY + 70 - 15 * Math.exp(-Math.pow((x - (SX + 98)) / 44, 2)));
      for (let x = 0; x < LW; x++) for (let y = hill(x); y < LH; y++) put(x, y, B1);
      rimTop(B1, S3, SY + 50, SY + 72, 0.5);
      campanile(SX + 110, hill(SX + 110) + 2, SY + 38, B1, S3);
      for (let i = 0; i < Math.round(LW / 7); i++) {
        const x = Math.floor(rand() * LW);
        if (Math.abs(x - (SX + 110)) < 6) continue;
        if (rand() < 0.35) cypress(x, hill(x) + 2, 8 + Math.floor(rand() * 7), G0);
        else house(x, hill(x) + 3, 4 + Math.floor(rand() * 4), 3 + Math.floor(rand() * 3), B1, S4, S5, 0.3, 1);
      }
      let x = -3;
      while (x < LW) {
        const w = 12 + Math.floor(rand() * 8), h = 11 + Math.floor(rand() * 6);
        house(x, SY + 86, w, h, T0, S5, T1, 0.35, 2);
        x += w + 1 + (rand() < 0.3 ? 5 : 0);
      }
      ground("cobble");
      camera();
      PMODE = 0; PCOUNT = 0;
    }

    // Plant: a power station at night; cooling towers and their steam, a reactor dome, a stack, pylons, a fence.
    function buildPlant() {
      sky(1.2, SKY);
      moon(false);
      const step = 44;
      let px0 = SX + 8 - step * Math.ceil((SX + 8) / step);
      for (let px = px0; px < LW + step; px += step) {
        pylon(px, SY + 74, 22, B1);
        wire(px, SY + 54, px + step, SY + 54, 4, S3); wire(px, SY + 57, px + step, SY + 57, 4, S3);
      }
      coolingTower(SX + 90, SY + 76, 30, 10, B1, S3);
      coolingTower(SX + 115, SY + 76, 25, 9, B1, S3);
      rect(SX + 14, SY + 64, 21, 20, B1); dome(SX + 24, SY + 63, 10, B1);
      rimTop(B1, S3, SY + 50, SY + 66, 0.6);
      rect(SX + 64, SY + 34, 3, 50, B1);
      lightAt(SX + 65, SY + 33, RD, 1.5, 0, 0.3); lightAt(SX + 65, SY + 52, RD, 1.5, 0.5, 0.3);
      buildings(-2, LW, 14, 10, SY + 72, SY + 78, S0, 0.03, 0.01, O3, S5, 4);
      for (const fx of [SX + 38, SX + 100, SX - 24, SX + 160]) {
        for (let y = SY + 60; y < SY + 86; y++) put(fx, y, S0);
        put(fx - 1, SY + 60, O3); put(fx, SY + 60, O3); put(fx + 1, SY + 60, O3);
        for (let k = 1; k < 5; k++) if (k & 1) { put(fx - k, SY + 60 + k, O2); put(fx + k, SY + 60 + k, O2); }
      }
      for (let x = 0; x < LW; x++) {                                     // the fence
        for (let y = SY + 79; y < SY + 86; y++) if ((x + y) % 3 === 0 || x % 12 === 0) put(x, y, S2);
        put(x, SY + 78, S2);
      }
      ground("concrete");
      camera();
      PMODE = 0; PCOUNT = 0;
    }

    // Railyard: warehouses, a signal gantry, a freight train on the far track, rails and ballast underfoot.
    function buildRailyard() {
      sky(0.9, SKY);
      moon(false);
      buildings(-2, LW, 10, 12, SY + 56, SY + 68, B1, 0.08, 0.02, O2, S5, 3);
      for (const gx of [SX + 30, SX + 118]) for (let y = SY + 54; y < SY + 82; y++) { put(gx, y, S1); put(gx + 1, y, S1); }
      for (let x = SX + 30; x <= SX + 119; x++) { put(x, SY + 54, S1); put(x, SY + 55, S1); }
      for (const sx2 of [SX + 58, SX + 92]) {
        rect(sx2 - 1, SY + 56, 3, 5, S0);
        lightAt(sx2, SY + 57, RD, 6, 0, 0.5); lightAt(sx2, SY + 59, G2, 6, 0.5, 0.5);
      }
      rect(0, SY + 80, LW, 6, S0);
      let x = -10 + Math.floor(rand() * 8), k = 0;
      while (x < LW) {
        const w = k % 3 === 1 ? 22 : 26;
        boxcar(x, SY + 82, w, 13, S0, S1, k % 3 === 1);
        x += w + 3; k++;
      }
      ground("rails");
      camera();
      PMODE = 0; PCOUNT = 0;
    }

    // Snow: a cold blue night, snowy hills with firs, houses with snow on their roofs and warm windows; it snows.
    function buildSnow() {
      sky(0.5, SKY_COLD);
      moon(false);
      ridge(SY + 64, 4, 17, 3, 7, C0, C2, C1);
      for (let i = 0; i < Math.round(LW / 6); i++) {
        const x = Math.floor(rand() * LW), t = Math.round(SY + 64 + 4 * Math.sin(x / 17) + 3 * Math.sin(x / 7 + 1.3));
        pine(x, t + 2, 6 + Math.floor(rand() * 5), S1, C2);
      }
      let x = -4;
      while (x < LW) {
        const w = 12 + Math.floor(rand() * 7), h = 9 + Math.floor(rand() * 4);
        house(x, SY + 85, w, h, S1, C2, CR, 0.4, 2);
        for (let i = x - 1; i <= x + w; i++) put(i, SY + 85 - h - 1, CR);
        x += w + 3 + Math.floor(rand() * 6);
      }
      for (const [px, h] of [[SX + 98, 26], [SX + 120, 20], [SX - 18, 24], [SX + 150, 25]]) pine(px, SY + 86, h, S0, CR);
      ground("snow");
      camera();
      PMODE = 2; PCOUNT = 40;
    }

    // River: a temple gateway, an obelisk and palms on the far bank, the moon on the water, a stone quay.
    function buildRiver() {
      sky(1.0, SKY);
      moon(false);
      buildings(-2, LW, 6, 8, SY + 62, SY + 70, B1, 0.08, 0.01, O2, S5, 3);
      gateway(SX + 98, SY + 73, 20, B1, S3);
      obelisk(SX + 72, SY + 73, 22, B1, S3);
      for (const [px, h, lean] of [[SX + 20, 16, 1], [SX + 124, 14, -1], [SX - 22, 15, 1], [SX + 156, 16, -1]]) palm(px, SY + 73, h, lean, B1);
      rect(0, SY + 71, LW, 2, B1);
      water(SY + 73, SY + 86);
      for (const tx of [SX + 92, SX + 104]) for (let k = 0; k < 4; k++) lightAt(tx, SY + 75 + k * 3, k & 1 ? S5 : O2, 1, k * 0.25, 0.5);
      ground("quay");
      for (const bx of [SX + 34, SX + 112]) { rect(bx, SY + 83, 2, 3, T0); put(bx, SY + 83, T2); put(bx + 1, SY + 83, T1); }
      camera();
      PMODE = 0; PCOUNT = 2;
    }

    const THEMES = [
      {name: "rooftop", build: buildRooftop}, {name: "desert", build: buildDesert}, {name: "jungle", build: buildJungle},
      {name: "village", build: buildVillage}, {name: "plant", build: buildPlant}, {name: "railyard", build: buildRailyard},
      {name: "snow", build: buildSnow}, {name: "river", build: buildRiver},
    ];
    let theme = 0;

    // stars and particles, rebuilt with the background
    const NSTAR_MAX = 120, starX = new Int16Array(NSTAR_MAX), starY = new Int16Array(NSTAR_MAX),
      starP = new Uint16Array(NSTAR_MAX), starPh = new Uint16Array(NSTAR_MAX), starBig = new Uint8Array(NSTAR_MAX);
    const NPART_MAX = 160, partX = new Int16Array(NPART_MAX), partY = new Int16Array(NPART_MAX),
      partP = new Uint16Array(NPART_MAX), partPh = new Uint16Array(NPART_MAX), partSw = new Uint16Array(NPART_MAX);
    const SNOW_PERIODS = [5, 6, 10, 15];      // seconds to fall; all divide the 30 s loop
    let nStar = 0, nPart = 0;
    function buildBackground() {
      rand = rng(0xC52 + theme * 977);
      LIGHTS = [];
      ROOF_X1 = SX + 96; PMODE = 0; PCOUNT = 0;
      THEMES[theme].build();
      // stars: about one per 300 px of open sky
      nStar = 0;
      const want = Math.min(NSTAR_MAX, Math.round(LW * (SY + 52) / 300));
      let guard = 0;
      while (nStar < want && guard++ < 20000) {
        const x = 1 + Math.floor(rand() * (LW - 2)), y = 1 + Math.floor(rand() * (SY + 52));
        const c = get(x, y);
        if (c !== S0 && c !== S1 && c !== S2) continue;
        if (Math.hypot(x - MX, y - MY) < 14) continue;
        starX[nStar] = x; starY[nStar] = y;
        starP[nStar] = PERIODS[Math.floor(rand() * 4)] * HZ;
        starPh[nStar] = Math.floor(rand() * starP[nStar]);
        starBig[nStar] = rand() < 0.12 ? 1 : 0;
        nStar++;
      }
      nPart = Math.min(NPART_MAX, Math.round(PCOUNT * LW / STAGE_W));
      for (let i = 0; i < nPart; i++) {
        partX[i] = Math.floor(rand() * LW);
        partY[i] = SY + 58 + Math.floor(rand() * 24);
        partP[i] = (PMODE === 2 ? SNOW_PERIODS[Math.floor(rand() * 4)] : PERIODS[1 + Math.floor(rand() * 3)]) * HZ;
        partPh[i] = Math.floor(rand() * partP[i]);
        partSw[i] = PERIODS[Math.floor(rand() * 4)] * HZ;
      }
    }

    // --- the action -------------------------------------------------------------------------------------------------
    // Keyframes, a side each, times in seconds within its 15 s: time, crouch, lean, head turn (-1 back .. 1 forward),
    // head dy, head dx, gun angle, gun x, gun y (from the shoulders), and the easing into this key (1 smooth,
    // 2 snap with overshoot, 3 ease in). Gun angle -110 is slung on his back.
    const NKF = 10;
    const KF_T = new Float64Array([
      0.0, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      2.2, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      2.8, 0, 0, -1, 0, -1, 33, 4, 7, 1,     // checks his six
      3.6, 0, 0, -1, 0, -1, 33, 4, 7, 1,
      4.1, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      4.6, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      5.8, 1, 1, 1, 1, 1, -1, 6, 4, 1,       // brings the rifle up, slowly, to his shoulder
      7.5, 1, 1, 1, 1, 1, -1, 6, 4, 1,       // two bursts
      8.1, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      8.3, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      8.9, 0, 0, 1, 0, 0, -110, -2, 4, 1,    // slings it
      9.5, 7, 4, 1, 1, 1, -110, -2, 4, 1,    // crouches over the spot
      11.4, 7, 4, 1, 1, 1, -110, -2, 4, 1,   // plants: the code goes in
      12.0, 0, 0, 1, 0, 0, -110, -2, 4, 1,
      12.6, 0, 0, 1, 0, 0, 30, 4, 7, 1,      // rifle back in his hands
      13.0, 0, 0, 1, 1, 0, 30, 4, 7, 1,      // watches the bomb
      13.9, 0, 0, 1, 1, 0, 30, 4, 7, 1,
      14.3, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      15.0, 0, 0, 1, 0, 0, 30, 4, 7, 1,
    ]);
    const KF_CT = new Float64Array([
      0.0, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      1.0, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      1.4, 0, 0, 1, 1, 1, 30, 4, 7, 1,       // the bomb, ticking at his feet
      2.0, 0, 0, 1, 1, 1, 30, 4, 7, 1,
      2.4, 0, 0, -1, 0, -1, 33, 4, 7, 1,     // checks his six
      3.1, 0, 0, -1, 0, -1, 33, 4, 7, 1,
      3.5, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      3.8, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      5.0, 1, 1, 1, 1, 1, -1, 6, 4, 1,       // up to his shoulder, slowly
      5.6, 1, 1, 1, 1, 1, -1, 6, 4, 1,       // one burst
      6.1, 0, 0, 1, 1, 0, 15, 4, 6, 1,       // tilts it to reload
      8.3, 0, 0, 1, 1, 0, 15, 4, 6, 1,
      8.7, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      8.9, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      9.4, 0, 0, 1, 1, 1, -39, 3, 4, 1,      // inspects it: muzzle up, close
      9.9, 0, 0, 1, 1, 1, -39, 3, 4, 1,
      10.4, 0, 0, 1, 1, 0, 24, 4, 5, 1,      // rolls it down to look along it
      10.8, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      11.0, 0, 0, 1, 0, 0, 30, 4, 7, 1,
      11.5, 0, 0, 1, 0, 0, -110, -2, 4, 1,   // slings it
      12.0, 7, 4, 1, 1, 1, -110, -2, 4, 1,   // kneels at the bomb
      13.6, 7, 4, 1, 1, 1, -110, -2, 4, 1,   // defuses
      14.1, 0, 0, 1, 0, 0, -110, -2, 4, 1,
      15.0, 0, 0, 1, 0, 0, -110, -2, 4, 1,
    ]);
    // Hands: time, anchor, dx, dy, easing. Anchors: on the gun (handguard, magwell, bolt, grip), on the body (vest
    // pouch, hip) and the C4's keypad.
    const NHK = 5, A_FORE = 0, A_MAG = 1, A_POUCH = 2, A_BOLT = 3, A_GRIP = 4, A_HIP = 5, A_BOMB = 6;
    const NEAR_T = new Float64Array([
      0.0, A_FORE, 0, 0, 1,
      8.6, A_FORE, 0, 0, 1,
      8.9, A_POUCH, 2, -1, 1,      // the C4 out of his rig
      9.5, A_BOMB, 0, -2, 1,
      11.4, A_BOMB, 0, -2, 1,
      12.0, A_POUCH, 2, 1, 1,
      12.6, A_FORE, 0, 0, 1,
      15.0, A_FORE, 0, 0, 1,
    ]);
    const FAR_T = new Float64Array([
      0.0, A_GRIP, 0, 0, 1,
      8.9, A_GRIP, 0, 0, 1,
      9.5, A_HIP, 4, 3, 1,         // rests on his knee
      11.4, A_HIP, 4, 3, 1,
      12.0, A_HIP, 1, 3, 1,
      12.6, A_GRIP, 0, 0, 1,
      15.0, A_GRIP, 0, 0, 1,
    ]);
    const NEAR_CT = new Float64Array([
      0.0, A_FORE, 0, 0, 1,
      6.1, A_FORE, 0, 0, 1,
      6.4, A_MAG, 0, 0, 1,         // grabs the magazine
      6.7, A_MAG, 0, 4, 1,
      7.05, A_POUCH, 0, 0, 1,      // stows it
      7.3, A_POUCH, 0, 0, 1,
      7.65, A_MAG, 0, 4, 1,        // the fresh one
      7.85, A_MAG, 0, 0, 1,
      8.1, A_BOLT, 0, 0, 1,        // bolt release
      8.25, A_BOLT, -3, 0, 3,
      8.35, A_BOLT, 0, 0, 2,
      8.7, A_FORE, 0, 0, 1,
      11.2, A_FORE, 0, 0, 1,
      11.5, A_POUCH, 2, -1, 1,
      12.0, A_BOMB, 0, -2, 1,      // the kit, at the wires
      13.6, A_BOMB, 0, -2, 1,
      14.1, A_POUCH, 2, 1, 1,
      15.0, A_POUCH, 2, 1, 1,
    ]);
    const FAR_CT = new Float64Array([
      0.0, A_GRIP, 0, 0, 1,
      11.5, A_GRIP, 0, 0, 1,
      12.0, A_HIP, 4, 3, 1,
      13.6, A_HIP, 4, 3, 1,
      14.1, A_HIP, 1, 3, 1,
      15.0, A_HIP, 1, 3, 1,
    ]);
    const KFS = [KF_T, KF_CT], NEARS = [NEAR_T, NEAR_CT], FARS = [FAR_T, FAR_CT];

    // anim frames (12 fps, over the whole 30 s) that fire, and that blink
    const SHOT_LUT = new Uint8Array(LOOP_FRAMES + 1), BLINK_LUT = new Uint8Array(LOOP_FRAMES + 1);
    const SHOTS = [75, 76, 77, 84, 85, 86, 243, 244, 245];             // T 6.25 s and 7.0 s; CT 5.25 s
    for (const f of SHOTS) SHOT_LUT[f] = 1;
    for (const f of [12, 54, 150, 180 + 30, 180 + 100, 180 + 160]) BLINK_LUT[f] = 1;
    const STARTLE = [6.25, 15 + 5.25];                                 // Nugget jumps, a side each

    // the bomb, in ticks: planted, ticking; the defuse; defused
    const PLANT_AT = Math.round(9.65 * HZ), TYPE_FROM = Math.round(9.8 * HZ), TYPE_TO = Math.round(11.3 * HZ);
    const DEFUSE_FROM = SEG_TICKS + Math.round(12.1 * HZ), DEFUSE_TO = SEG_TICKS + Math.round(13.6 * HZ);

    // The pose, sampled once per anim frame. Fractions live in P and whole numbers in I: typed arrays, so writing
    // them every frame boxes nothing and the loop allocates nothing.
    const CROUCH = 0, LEAN = 1, TURN = 2, HDY = 3, HDX = 4, GUNA = 5, GUNX = 6, GUNY = 7, RECOIL = 8, JX = 9, JY = 10,
      ACC = 11, LAST = 12, U = 13, E = 14, TT = 15, TRK = 16;            // TRK: 2 tracks x (dx0, dy0, dx1, dy1, u)
    const P = new Float64Array(TRK + 10);
    const BOB = 0, MAGON = 1, MAGHELD = 2, SHOT = 3, BLINK = 4, BUMP = 5, GUNPX = 6, GUNPY = 7, MUZX = 8, MUZY = 9,
      CHX = 10, CHY = 11, CHF = 12, TICK = 13, LASTF = 14, SIDE = 15, BEHIND = 16, C4HELD = 17, TAP = 18, CHFLIP = 19,
      HX = 20, HY = 22, TA = 24;                                        // HX/HY: 2 hands; TA: 2 tracks x (a0, a1)
    const I = new Int32Array(TA + 4);

    // eases P[U] into P[E]. Fractions go through P rather than arguments or return values: a fraction crossing a
    // call would be boxed, and the loop is meant to allocate nothing.
    function ease(type) {
      const u = P[U];
      if (type === 2) { const c1 = 1.70158, c3 = c1 + 1, v = u - 1; P[E] = 1 + c3 * v * v * v + c1 * v * v; }
      else if (type === 3) P[E] = u * u;
      else P[E] = 0.5 - 0.5 * Math.cos(Math.PI * u);
    }
    // a hand track at P[TT] into slots k
    function sampleTrack(arr, k) {
      const t = P[TT];
      let h = 0;
      while (h < arr.length / NHK - 2 && arr[(h + 1) * NHK] <= t) h++;
      const c = h * NHK, d = c + NHK, span = arr[d] - arr[c];
      P[U] = span > 0 ? Math.min(1, (t - arr[c]) / span) : 1;
      ease(arr[d + 4]);
      const o = TRK + k * 5;
      I[TA + k * 2] = arr[c + 1]; P[o] = arr[c + 2]; P[o + 1] = arr[c + 3];
      I[TA + k * 2 + 1] = arr[d + 1]; P[o + 2] = arr[d + 2]; P[o + 3] = arr[d + 3]; P[o + 4] = P[E];
    }
    function samplePose(f) {
      const side = f >= SEG_FRAMES ? 1 : 0, sf = f - side * SEG_FRAMES, t = sf / ANIM_FPS, KF = KFS[side];
      I[SIDE] = side;
      let k = 0;
      while (k < KF.length / NKF - 2 && KF[(k + 1) * NKF] <= t) k++;
      const a = k * NKF, b = a + NKF, span = KF[b] - KF[a];
      P[U] = span > 0 ? Math.min(1, (t - KF[a]) / span) : 1;
      ease(KF[b + 9]);
      const e = P[E];
      P[CROUCH] = KF[a + 1] + (KF[b + 1] - KF[a + 1]) * e;
      P[LEAN] = KF[a + 2] + (KF[b + 2] - KF[a + 2]) * e;
      P[TURN] = KF[a + 3] + (KF[b + 3] - KF[a + 3]) * e;
      P[HDY] = KF[a + 4] + (KF[b + 4] - KF[a + 4]) * e;
      P[HDX] = KF[a + 5] + (KF[b + 5] - KF[a + 5]) * e;
      P[GUNA] = KF[a + 6] + (KF[b + 6] - KF[a + 6]) * e;
      P[GUNX] = KF[a + 7] + (KF[b + 7] - KF[a + 7]) * e;
      P[GUNY] = KF[a + 8] + (KF[b + 8] - KF[a + 8]) * e;
      P[TT] = t;
      sampleTrack(NEARS[side], 0);
      sampleTrack(FARS[side], 1);
      if (side === 0) {
        I[MAGON] = 1; I[MAGHELD] = 0; I[BUMP] = 0;
        I[BEHIND] = t >= 8.6 && t < 12.3 ? 1 : 0;
        I[C4HELD] = t >= 8.9 && t < 9.65 ? 1 : 0;
        I[TAP] = t >= 9.8 && t < 11.3 ? 1 : 0;
      } else {
        I[MAGON] = t >= 6.45 && t < 7.85 ? 0 : 1;
        I[MAGHELD] = (t >= 6.45 && t < 7.05) || (t >= 7.2 && t < 7.85) ? 1 : 0;
        I[BUMP] = sf === 94 ? 1 : sf === 100 ? 2 : 0;                // the magazine seats; the bolt goes home
        I[BEHIND] = t >= 11.25 ? 1 : 0;
        I[C4HELD] = 0;
        I[TAP] = t >= 12.1 && t < 13.6 ? 1 : 0;
      }
      I[SHOT] = SHOT_LUT[f];
      P[RECOIL] = I[SHOT] ? 1 : f > 0 && SHOT_LUT[f - 1] ? 0.5 : 0;
      I[BLINK] = BLINK_LUT[f];
      I[BOB] = Math.round(0.5 - 0.5 * Math.cos(2 * Math.PI * t / 3)) | 0;
    }

    // --- the agent's layer --------------------------------------------------------------------------------------------
    const LX = 0.6, LY = -0.8;                // light from the moon, upper right
    function cbSet(x, y, c) {
      const cx = x + CX0, cy = y + CY0;
      if (cx >= 0 && cy >= 0 && cx < CW && cy < CH) cb[cy * CW + cx] = c;
    }
    // A limb segment: a capsule of radius r, shaded across its width. `ringOnly` marks its outline where it overlaps
    // what is already drawn (the silhouette's outline comes later, for everything at once). `half` moves both ends
    // half a pixel right, for limbs an even number of pixels wide.
    function seg(ix0, y0, ix1, y1, r, cd, cm, cl, ringOnly, half) {
      // plain arithmetic, not `half ? x + 0.5 : x`: a ternary that mixes a fresh fraction with an argument is boxed
      const x0 = ix0 + half * 0.5, x1 = ix1 + half * 0.5, R = r + (ringOnly ? 1 : 0);
      const minx = Math.floor(Math.min(x0, x1) - R) | 0, maxx = Math.ceil(Math.max(x0, x1) + R) | 0;
      const miny = Math.floor(Math.min(y0, y1) - R) | 0, maxy = Math.ceil(Math.max(y0, y1) + R) | 0;
      const vx = x1 - x0, vy = y1 - y0, l2 = vx * vx + vy * vy;
      for (let y = miny; y <= maxy; y++) {
        const cy = y + CY0;
        if (cy < 0 || cy >= CH) continue;
        for (let x = minx; x <= maxx; x++) {
          const cx = x + CX0;
          if (cx < 0 || cx >= CW) continue;
          let t = l2 > 0 ? ((x - x0) * vx + (y - y0) * vy) / l2 : 0;
          t = t < 0 ? 0 : t > 1 ? 1 : t;
          const ox = x - (x0 + vx * t), oy = y - (y0 + vy * t), d2 = ox * ox + oy * oy, i = cy * CW + cx;
          if (ringOnly) {
            if (d2 <= R * R && d2 > r * r && cb[i] !== EMPTY) cb[i] = K;
          } else if (d2 <= r * r) {
            const s = (ox * LX + oy * LY) / r;
            cb[i] = s > 0.35 ? cl : s < -0.35 ? cd : cm;
          }
        }
      }
    }
    function blitCb(s, lx, ly, flip, ring) {
      const w = s.w, h = s.h, d = s.d;
      if (ring) {
        for (let y = -1; y <= h; y++) {
          for (let x = -1; x <= w; x++) {
            if (filled(s, x, y)) continue;
            if (!(filled(s, x - 1, y) || filled(s, x + 1, y) || filled(s, x, y - 1) || filled(s, x, y + 1))) continue;
            const cx = lx + (flip ? w - 1 - x : x) + CX0, cy = ly + y + CY0;
            if (cx < 0 || cy < 0 || cx >= CW || cy >= CH) continue;
            const i = cy * CW + cx;
            if (cb[i] !== EMPTY) cb[i] = K;
          }
        }
      }
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const c = d[y * w + x];
          if (c !== EMPTY) cbSet(lx + (flip ? w - 1 - x : x), ly + y, c);
        }
      }
    }
    function blitTorso(s, lx, top, lean) {
      for (let y = 0; y < s.h; y++) {
        const sh = Math.round(lean * (s.h - 1 - y) / (s.h - 1)) | 0;
        for (let x = 0; x < s.w; x++) {
          const c = s.d[y * s.w + x];
          if (c !== EMPTY) cbSet(lx - 3 + x + sh, top + y, c);
        }
      }
    }
    // two-bone IK: the elbow or knee into P[JX], P[JY]. The far segment stretches to the target rather than let go.
    function ik(ax, ay, tx, ty, l1, l2, bend) {
      let dx = tx - ax, dy = ty - ay, d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1e-6) { dx = 0; dy = 1; d = 1e-6; }
      const dc = Math.min(l1 + l2 - 1e-3, Math.max(Math.abs(l1 - l2) + 1e-3, d));
      let c = (l1 * l1 + dc * dc - l2 * l2) / (2 * l1 * dc);
      c = c < -1 ? -1 : c > 1 ? 1 : c;
      const ang = Math.atan2(dy, dx) + bend * Math.acos(c);
      P[JX] = ax + Math.cos(ang) * l1; P[JY] = ay + Math.sin(ang) * l1;
    }
    // where an anchor is this frame, in the agent's coordinates
    function anchorX(id, gs, sx) {
      if (id === A_FORE) return I[GUNPX] + gs.a[2];
      if (id === A_MAG) return I[GUNPX] + gs.a[6];
      if (id === A_BOLT) return I[GUNPX] + gs.a[8];
      if (id === A_GRIP) return I[GUNPX];
      if (id === A_POUCH) return sx + 2;
      if (id === A_HIP) return 0;
      return BOMB_LX + 2;
    }
    function anchorY(id, gs, sy, hipY) {
      if (id === A_FORE) return I[GUNPY] + gs.a[3];
      if (id === A_MAG) return I[GUNPY] + gs.a[7];
      if (id === A_BOLT) return I[GUNPY] + gs.a[9];
      if (id === A_GRIP) return I[GUNPY];
      if (id === A_POUCH) return sy + 5;
      if (id === A_HIP) return hipY;
      return -4;
    }
    function handAt(k, gs, sx, sy, hipY) {
      const o = TRK + k * 5, a0 = I[TA + k * 2], a1 = I[TA + k * 2 + 1];
      const x0 = anchorX(a0, gs, sx) + P[o], y0 = anchorY(a0, gs, sy, hipY) + P[o + 1];
      const x1 = anchorX(a1, gs, sx) + P[o + 2], y1 = anchorY(a1, gs, sy, hipY) + P[o + 3];
      I[HX + k] = Math.round(x0 + (x1 - x0) * P[o + 4]) | 0;
      I[HY + k] = Math.round(y0 + (y1 - y0) * P[o + 4]) | 0;
    }

    let gunSpr = null;
    function drawAgent(tick) {
      const sd = SIDES[I[SIDE]];
      cb.fill(EMPTY);
      const crouch = Math.round(P[CROUCH]) | 0, lean = Math.round(P[LEAN] - P[RECOIL] * 0.6) | 0;
      const hipY = -15 + crouch + I[BOB], sx = lean, sy = hipY - 9;

      // the rifle's pose, with recoil and the knocks of the reload
      let ai = Math.round((P[GUNA] - P[RECOIL] * 6 - GUN_MIN) / GUN_STEP) | 0;
      ai = ai < 0 ? 0 : ai >= GUN_N ? GUN_N - 1 : ai;
      const gs = gunSpr = sd.rot[I[MAGON] ? 0 : 1][ai];
      I[GUNPX] = sx + (Math.round(P[GUNX] - P[RECOIL] * 1.5) | 0) + (I[BUMP] === 2 ? 1 : 0);
      I[GUNPY] = sy + (Math.round(P[GUNY]) | 0) - (I[BUMP] === 1 ? 1 : 0);
      I[MUZX] = I[GUNPX] + gs.a[4]; I[MUZY] = I[GUNPY] + gs.a[5];
      handAt(0, gs, sx, sy, hipY);
      handAt(1, gs, sx, sy, hipY);
      if (I[TAP] && ((tick >> 3) & 1)) I[HY] += 1;                     // keying the code, cutting the wire
      const farOnGrip = I[TA + 2] === A_GRIP && I[TA + 3] === A_GRIP;

      // far leg; the rifle when it is slung on his back; far arm
      const pf = sd.pantsFar, pn = sd.pants;
      ik(-0.5, hipY, -3, -2, 7, 6, -1);
      let kx = Math.round(P[JX]) | 0, ky = Math.round(P[JY]) | 0;
      seg(kx, ky, -3, -2, 1.5, pf[0], pf[1], pf[2], false, 0);
      seg(-1, hipY, kx, ky, 2, pf[0], pf[1], pf[2], false, 1);
      blitCb(sd.boot, -5, -2, false, false);
      if (I[BEHIND]) blitCb(gs, I[GUNPX] - gs.pox, I[GUNPY] - gs.poy, false, true);
      const fsx = sx - 2, fsy = sy + 2, fhx = I[HX + 1], fhy = I[HY + 1], sf = sd.sleeveFar;
      ik(fsx, fsy, fhx, fhy, 5, 5, 1);
      const ex = Math.round(P[JX]) | 0, ey = Math.round(P[JY]) | 0;
      seg(fsx, fsy, ex, ey, 1.5, sf[0], sf[1], sf[2], false, 0);
      seg(ex, ey, fhx, fhy, 1.5, sf[0], sf[1], sf[2], false, 0);
      if (!farOnGrip || I[BEHIND]) blitCb(sd.handFar, fhx, fhy, false, false);

      // near leg
      ik(1.5, hipY, 4, -2, 7, 6, -1);
      kx = Math.round(P[JX]) | 0; ky = Math.round(P[JY]) | 0;
      seg(kx, ky, 4, -2, 1.5, pn[0], pn[1], pn[2], false, 0);
      seg(1, hipY, kx, ky, 2, pn[0], pn[1], pn[2], false, 1);
      blitCb(sd.boot, 2, -2, false, false);

      // torso and head
      blitTorso(sd.torso, 0, sy, lean);
      const turn = P[TURN], headSpr = turn > -0.35 && turn < 0.35 ? sd.headF : sd.headR;
      const hs = I[BLINK] ? (headSpr === sd.headF ? sd.headFB : sd.headRB) : headSpr;
      blitCb(hs, (sx - 4 + Math.round(P[HDX])) | 0, (sy - 8 + Math.round(P[HDY])) | 0, turn <= -0.35, true);

      // the near arm runs under the rifle, so the whole rifle reads
      const nsx = sx + 1, nsy = sy + 2, nhx = I[HX], nhy = I[HY], sl = sd.sleeve;
      ik(nsx, nsy, nhx, nhy, 6, 6, 1);
      const nx = Math.round(P[JX]) | 0, ny = Math.round(P[JY]) | 0;
      seg(nsx, nsy, nx, ny, 1.5, 0, 0, 0, true, 0);
      seg(nx, ny, nhx, nhy, 1.5, 0, 0, 0, true, 0);
      seg(nsx, nsy, nx, ny, 1.5, sl[0], sl[1], sl[2], false, 0);
      seg(nx, ny, nhx, nhy, 1.5, sl[0], sl[1], sl[2], false, 0);

      // the rifle in his hands, a hand on its grip; whatever the near hand holds; the near hand
      if (!I[BEHIND]) blitCb(gs, I[GUNPX] - gs.pox, I[GUNPY] - gs.poy, false, true);
      if (farOnGrip && !I[BEHIND]) blitCb(sd.handFar, fhx, fhy, false, true);
      if (I[MAGHELD]) blitCb(sd.mag, nhx - sd.magOff, nhy + 1, false, true);
      if (I[C4HELD]) blitCb(C4, nhx - 3, nhy + 1, false, true);
      blitCb(sd.hand, nhx, nhy, false, true);

      // light it, outline it, put it in the frame
      const fx = I[MUZX] + CX0, fy = I[MUZY] + CY0;
      cb2.set(cb);
      for (let y = 0; y < CH; y++) {
        for (let x = 0; x < CW; x++) {
          const i = y * CW + x, c = cb[i];
          if (c === EMPTY || c === K) continue;
          const edge = x + 1 >= CW || cb[i + 1] === EMPTY || y === 0 || cb[i - CW] === EMPTY;
          let n = edge ? LIGHT[c] : c;
          if (I[SHOT] && edge) {
            const dx = x - fx, dy = y - fy;
            if (dx * dx + dy * dy < 90) n = LIGHT[n];
          }
          cb2[i] = n;
        }
      }
      for (let y = 0; y < CH; y++) {
        for (let x = 0; x < CW; x++) {
          const i = y * CW + x;
          if (cb2[i] !== EMPTY) { cb[i] = cb2[i]; continue; }
          cb[i] = ((x > 0 && cb2[i - 1] !== EMPTY) || (x + 1 < CW && cb2[i + 1] !== EMPTY) ||
            (y > 0 && cb2[i - CW] !== EMPTY) || (y + 1 < CH && cb2[i + CW] !== EMPTY)) ? K : EMPTY;
        }
      }
      const ox = AX - CX0, oy = AY - CY0;
      for (let y = 0; y < CH; y++) {
        const wy = oy + y;
        if (wy < 0 || wy >= LH) continue;
        for (let x = 0; x < CW; x++) {
          const c = cb[y * CW + x], wx = ox + x;
          if (c !== EMPTY && wx >= 0 && wx < LW) fb[wy * LW + wx] = c;
        }
      }
    }

    // --- things that fly, blink and wipe ------------------------------------------------------------------------------
    function fbPut(x, y, c) { if (x >= 0 && y >= 0 && x < LW && y < LH) fb[y * LW + x] = c; }
    function fbBlit(s, x0, y0, flip, outline) {
      const w = s.w, h = s.h;
      if (outline) {
        for (let y = -1; y <= h; y++) {
          for (let x = -1; x <= w; x++) {
            if (filled(s, x, y)) continue;
            if (filled(s, x - 1, y) || filled(s, x + 1, y) || filled(s, x, y - 1) || filled(s, x, y + 1)) fbPut(x0 + (flip ? w - 1 - x : x), y0 + y, K);
          }
        }
      }
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const c = s.d[y * w + x];
          if (c !== EMPTY) fbPut(x0 + (flip ? w - 1 - x : x), y0 + y, c);
        }
      }
    }

    // every shot's muzzle and ejection port, from the pose at that moment, in the agent's coordinates
    const NSHOT = SHOTS.length, shotMx = new Int16Array(NSHOT), shotMy = new Int16Array(NSHOT),
      shotPx = new Int16Array(NSHOT), shotPy = new Int16Array(NSHOT), shotT = new Uint16Array(NSHOT);
    function bakeShots() {
      for (let i = 0; i < NSHOT; i++) {
        const f = SHOTS[i];
        samplePose(f);
        const sd = SIDES[I[SIDE]];
        const lean = Math.round(P[LEAN] - P[RECOIL] * 0.6) | 0, sy = -15 + Math.round(P[CROUCH]) + I[BOB] - 9;
        const ai = Math.round((P[GUNA] - P[RECOIL] * 6 - GUN_MIN) / GUN_STEP);
        const g = sd.rot[0][ai], gx = lean + Math.round(P[GUNX] - P[RECOIL] * 1.5), gy = sy + Math.round(P[GUNY]);
        shotMx[i] = gx + g.a[4]; shotMy[i] = gy + g.a[5];
        shotPx[i] = gx + g.a[10]; shotPy[i] = gy + g.a[11];
        shotT[i] = Math.round(f / ANIM_FPS * HZ);
      }
    }
    function drawShots(tick) {
      for (let i = 0; i < NSHOT; i++) {
        let s = tick - shotT[i];
        if (s < 0) s += LOOP_TICKS;
        const sec = s / HZ;
        if (sec < 0.12) {                       // tracer: across the city in a few frames, climbing a little
          const head = 6 + Math.round(sec * 1500), climb = 0.03 + (i % 3) * 0.035;
          for (let k = 0; k < 11; k++) {
            const d = head - k;
            if (d < 4) break;
            const x = AX + shotMx[i] + d, y = AY + shotMy[i] - Math.round(d * climb);
            if (x >= LW) continue;
            fbPut(x, y, k < 2 ? CR : k < 5 ? O3 : k < 8 ? O2 : OR);
          }
        }
        if (sec < 1.6) {                        // brass: up and out, one bounce on the roof, then gone
          const vx = 9 + (i % 3) * 5, vy = -48 - (i & 1) * 8, g = 260, floor = ROOF_Y - 1;
          const x0 = AX + shotPx[i], y0 = AY + shotPy[i];
          const t1 = (-vy + Math.sqrt(vy * vy + 2 * g * (floor - y0))) / g;
          let x, y, air = true;
          if (sec < t1) { x = x0 + vx * sec; y = y0 + vy * sec + 0.5 * g * sec * sec; } else {
            const vy1 = -(vy + g * t1) * 0.35, vx1 = vx * 0.45, t2 = 2 * vy1 / g, u = sec - t1, x1 = x0 + vx * t1;
            if (u < t2) { x = x1 + vx1 * u; y = floor - vy1 * u + 0.5 * g * u * u; } else { x = x1 + vx1 * t2; y = floor; air = false; }
          }
          if (sec < 1.1 || (s >> 2) & 1) {
            const px = Math.round(x) | 0, py = Math.round(y) | 0, spin = air && ((s >> 1) & 1);
            fbPut(px, py, spin ? CR : O3);
            if (spin) fbPut(px, py - 1, O2); else fbPut(px + 1, py, O2);
          }
        }
      }
    }
    function drawFlash(af) {
      if (!I[SHOT]) return;
      const f = FLASH[af === 76 || af === 85 || af === 244 ? 1 : 0], mx = AX + I[MUZX], my = AY + I[MUZY];
      fbBlit(f, mx, my - 2, false, false);
      for (let x = mx - 5; x < mx + 6; x++) if (x >= 0 && x < ROOF_X1) fbPut(x, ROOF_Y, (x & 1) ? S5 : O2);
    }
    // the C4 on the roof: its light ticks once a second, fast while it is defused, then shows blue
    function drawBomb(tick) {
      const planted = (tick >= PLANT_AT && tick < SEG_TICKS) || tick >= SEG_TICKS;
      if (!planted) return;
      const bx = AX + BOMB_LX - 3, by = AY - 3;
      fbBlit(C4, bx, by, false, true);
      let on = 0;
      if (tick >= DEFUSE_TO) on = 2;
      else if (tick >= DEFUSE_FROM) on = (tick % 15) < 6 ? 1 : 0;
      else on = (tick % HZ) < 9 ? 1 : 0;
      if (on) { fbPut(bx + C4_LED, by, on === 2 ? BL : RD); if (on === 1) fbPut(bx + C4_LED, by - 1, S5); }
      // the progress bar while he keys the code or works the wires
      let from = -1, to = 0, col = OR;
      if (tick >= TYPE_FROM && tick < TYPE_TO) { from = TYPE_FROM; to = TYPE_TO; }
      else if (tick >= DEFUSE_FROM && tick < DEFUSE_TO) { from = DEFUSE_FROM; to = DEFUSE_TO; col = BL; }
      if (from < 0) return;
      const w = 14, x0 = AX + 12, y0 = AY - 22, fillw = Math.round((tick - from) / (to - from) * (w - 2)) | 0;
      for (let x = -1; x <= w; x++) { fbPut(x0 + x, y0 - 1, K); fbPut(x0 + x, y0 + 2, K); }
      fbPut(x0 - 1, y0, K); fbPut(x0 - 1, y0 + 1, K); fbPut(x0 + w, y0, K); fbPut(x0 + w, y0 + 1, K);
      for (let x = 0; x < w; x++) { const c = x < fillw + 1 ? col : M0; fbPut(x0 + x, y0, c); fbPut(x0 + x, y0 + 1, c); }
    }

    // Nugget: pecks, wanders, jumps at the shots, runs off, pecks, wanders back. Stage coordinates.
    function sampleChicken(f) {
      const side = f >= SEG_FRAMES ? 1 : 0, sf = f - side * SEG_FRAMES, t = sf / ANIM_FPS, ts = side ? 5.25 : 6.25;
      const peck = (sf % 12) >= 7 && (sf % 12) < 10 ? CH_PECK : CH_STAND, walk = (sf >> 1) & 1 ? CH_STEP : CH_STAND;
      I[CHY] = 0; I[CHFLIP] = 0;
      if (t < 3.0) { I[CHX] = 22; I[CHF] = peck; }
      else if (t < 4.5) { I[CHX] = 22 + (Math.round(6 * (t - 3.0) / 1.5) | 0); I[CHF] = walk; }
      else if (t < ts) { I[CHX] = 28; I[CHF] = peck; }
      else if (t < ts + 0.52) {
        const u = (t - ts) / 0.52;
        I[CHX] = 28 - (Math.round(2 * u) | 0); I[CHY] = -(Math.round(Math.sin(Math.PI * u) * 7) | 0); I[CHF] = sf & 1 ? CH_FLAP : CH_STAND;
      } else if (t < ts + 1.72) { I[CHX] = 26 - (Math.round(8 * (t - ts - 0.52) / 1.2) | 0); I[CHF] = sf & 1 ? CH_STEP : CH_FLAP; I[CHFLIP] = 1; }
      else if (t < 12.5) { I[CHX] = 18; I[CHF] = peck; I[CHFLIP] = t < ts + 3 ? 1 : 0; }
      else { I[CHX] = 18 + (Math.round(4 * (t - 12.5) / 2.5) | 0); I[CHF] = walk; }
    }
    function drawFeathers(tick) {
      for (let j = 0; j < 2; j++) {
        let s = tick - Math.round((STARTLE[j] + 0.04) * HZ);
        if (s < 0) s += LOOP_TICKS;
        const sec = s / HZ;
        if (sec >= 2.4) continue;
        for (let i = 0; i < 3; i++) {
          const vx = i === 0 ? -7 : i === 1 ? 2 : 8, vy = i === 0 ? -16 : i === 1 ? -21 : -12;
          const fall = Math.min(sec * vy + 22 * sec * sec, 60);
          const x = (Math.round(SX + 31 + vx * sec + Math.sin(sec * 5 + i * 2) * 1.6) | 0);
          let y = Math.round(SY + 78 + fall) | 0;
          if (y > ROOF_Y - 1) y = ROOF_Y - 1;
          if (sec > 1.9 && (s >> 2) & 1) continue;
          fbPut(x, y, CR); fbPut(x + ((s >> 3) & 1 ? 1 : -1), y, T3);
        }
      }
    }
    function drawSky(tick) {
      for (let i = 0; i < nStar; i++) {
        const p = starP[i], b = 0.5 + 0.5 * Math.sin(((tick + starPh[i]) % p) / p * 2 * Math.PI);
        const x = starX[i], y = starY[i];
        if (b > 0.85) {
          fbPut(x, y, CR);
          if (starBig[i]) { fbPut(x - 1, y, S4); fbPut(x + 1, y, S4); fbPut(x, y - 1, S4); fbPut(x, y + 1, S4); }
        } else if (b > 0.4) fbPut(x, y, starBig[i] ? T3 : S4);
        else if (b > 0.15) fbPut(x, y, S3);
      }
      for (let i = 0; i < LIGHTS.length; i += 6) {
        if ((tick + LIGHTS[i + 4]) % LIGHTS[i + 3] < LIGHTS[i + 5]) fbPut(LIGHTS[i], LIGHTS[i + 1], LIGHTS[i + 2]);
      }
      if (tick % HZ < 36) { fbPut(REC_X, REC_Y, RD); fbPut(REC_X + 1, REC_Y, RD); }
    }
    // Embers rise out of the city, fireflies wander and glow, snow falls (a third of it in front of the agent).
    function drawParticles(tick, front) {
      for (let i = 0; i < nPart; i++) {
        const p = partP[i], u = ((tick + partPh[i]) % p) / p, sp = partSw[i];
        const sw = Math.sin(((tick + partPh[i]) % sp) / sp * 2 * Math.PI);
        if (PMODE === 2) {
          const near = i % 3 === 0;
          if (near !== front) continue;
          const y = (Math.round(u * (ROOF_Y + 6)) | 0) - 6, x = partX[i] + (Math.round(sw * 2) | 0);
          if (y < 0 || y >= ROOF_Y) continue;
          fbPut(x, y, near ? CR : C2);
        } else if (front) {
          continue;
        } else if (PMODE === 1) {
          const g = Math.sin(u * 2 * Math.PI);
          if (g < 0.1) continue;
          const wy = Math.cos(((tick + partPh[i] * 3) % sp) / sp * 2 * Math.PI);
          const x = partX[i] + (Math.round(sw * 7) | 0), y = partY[i] + (Math.round(wy * 3) | 0);
          fbPut(x, y, g > 0.75 ? CR : G2);
        } else {
          if (u > 0.8) continue;
          const y = SY + 92 - (Math.round(u * 56) | 0), x = partX[i] + (Math.round(sw * 2) | 0);
          if (x <= ROOF_X1 && y >= SY + 81) continue;          // behind the roof
          fbPut(x, y, u < 0.3 ? OR : S5);
        }
      }
    }
    // The round-end wipe: the app's slanted orange plate sweeps across, holds with a caption, sweeps off.
    const WIPE_IN = 27, WIPE_HOLD = 15;      // ticks: 0.45 s across, 0.25 s either side of the change held
    function drawWipe(tick) {
      let d = tick - SEG_TICKS, cap = 0;
      if (d <= -(WIPE_IN + WIPE_HOLD) || d >= WIPE_IN + WIPE_HOLD) {
        d = tick < SEG_TICKS ? tick : tick - LOOP_TICKS; cap = 1;
        if (d <= -(WIPE_IN + WIPE_HOLD) || d >= WIPE_IN + WIPE_HOLD) return;
      }
      const slant = (LH * 0.35) | 0, span = LW + slant + 2;
      let lead = LW + slant + 2, trail = -2;                  // the plate covers trail <= x + y*k < lead
      if (d < -WIPE_HOLD) { P[U] = (d + WIPE_IN + WIPE_HOLD) / WIPE_IN; ease(1); lead = Math.round(P[E] * span) | 0; }
      else if (d > WIPE_HOLD) { P[U] = (d - WIPE_HOLD) / WIPE_IN; ease(1); trail = Math.round(P[E] * span) | 0; }
      for (let y = 0; y < LH; y++) {
        const k = Math.round(y * slant / LH) | 0;
        for (let x = 0; x < LW; x++) {
          const v = x + k;
          if (v < trail - 1 || v >= lead + 1) continue;
          const i = y * LW + x;
          if (v === lead || v === trail - 1) fb[i] = K;
          else if (v === lead - 1 || v === trail) fb[i] = O3;
          else fb[i] = (((x >> 2) + (y >> 2)) & 1) === 0 && ((x & 3) === 1) && ((y & 3) === 1) && y > LH * 0.6 ? O2 : OR;
        }
      }
      // the caption, 2x, where the plate is
      const c = CAPTIONS[cap], cw = c.w * 2, x0 = (LW - cw) >> 1, y0 = ((LH - 10) >> 1) - 2;
      for (let y = 0; y < 10; y++) {
        for (let x = 0; x < cw; x++) {
          if (!c.d[(y >> 1) * c.w + (x >> 1)]) continue;
          const px = x0 + x, py = y0 + y, v = px + (Math.round(py * slant / LH) | 0);
          if (v >= trail + 1 && v < lead - 1) fbPut(px, py, K);
        }
      }
    }

    // --- update and render ------------------------------------------------------------------------------------------
    function update() {
      I[TICK] = I[TICK] + 1 === LOOP_TICKS ? 0 : I[TICK] + 1;
      const f = (I[TICK] / (HZ / ANIM_FPS)) | 0;
      if (f !== I[LASTF]) { I[LASTF] = f; samplePose(f); sampleChicken(f); }
    }
    function render() {
      const tick = I[TICK];
      fb.set(bg);
      drawSky(tick);
      drawParticles(tick, false);
      if (CAST) {
        drawBomb(tick);
        fbBlit(CHICK[I[CHF]], SX + I[CHX] - 4, AY - 7 + I[CHY], I[CHFLIP] === 1, true);
        drawFeathers(tick);
        drawAgent(tick);
      }
      drawParticles(tick, true);
      if (CAST) {
        drawShots(tick);
        drawFlash(I[LASTF]);
        drawWipe(tick);
      }
      const n = LW * LH;
      for (let i = 0; i < n; i++) px32[i] = PAL32[fb[i]];
      offCtx.putImageData(img, 0, 0);
      ctx.drawImage(off, 0, 0, LW, LH, ox, oy, LW * scale, LH * scale);
    }

    // --- the canvas -----------------------------------------------------------------------------------------------
    const ctx = canvas.getContext("2d", {alpha: false});
    const off = document.createElement("canvas"), offCtx = off.getContext("2d");
    let img = null, px32 = null, scale = 1, ox = 0, oy = 0;
    // The stage stays whole; the frame grows to the box's shape, and the last part-pixel is cropped evenly. `cw` and
    // `ch` are device pixels. Rows the stage does not need go above it, except `opts.lift` CSS px of them, kept below
    // it as more ground, so a caption along the box's bottom edge does not cover the action. `opts.stageShare` caps
    // the pixel size so the stage takes at most that share of the box's height (a backdrop shows more of the world).
    function resize(cw, ch) {
      cw = Math.max(1, Math.round(cw)); ch = Math.max(1, Math.round(ch));
      canvas.width = cw; canvas.height = ch;
      scale = Math.max(1, Math.min(Math.floor(cw / STAGE_W), Math.floor(ch * (opts.stageShare || 1) / STAGE_H)));
      LW = Math.max(STAGE_W, Math.min(MAX_W, Math.ceil(cw / scale)));
      LH = Math.max(STAGE_H, Math.min(MAX_H, Math.ceil(ch / scale)));
      const spare = LH - STAGE_H, lift = Math.ceil((opts.lift || 0) * (window.devicePixelRatio || 1) / scale);
      SX = (LW - STAGE_W) >> 1; SY = spare - Math.min(spare, lift);
      AX = SX + 52; AY = SY + 85; ROOF_Y = SY + 86; ROOF_X1 = SX + 96; MX = SX + 104; MY = SY + 18;
      REC_X = SX + 4; REC_Y = SY + 70;
      if (fb.length !== LW * LH) { fb = new Uint8Array(LW * LH); bg = new Uint8Array(LW * LH); }
      off.width = LW; off.height = LH;
      img = offCtx.createImageData(LW, LH); px32 = new Uint32Array(img.data.buffer);
      ox = (cw - LW * scale) >> 1; oy = (ch - LH * scale) >> 1;
      buildBackground();
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = PAL[K];
      ctx.fillRect(0, 0, cw, ch);
    }
    function resample() {                   // after a jump in time: sample this moment again
      I[LASTF] = -1; I[TICK] = I[TICK] === 0 ? LOOP_TICKS - 1 : I[TICK] - 1; update();
    }
    function setTime(seconds) {
      I[TICK] = ((Math.round(seconds * HZ) % LOOP_TICKS) + LOOP_TICKS) % LOOP_TICKS;
      resample();
    }
    function setTheme(name) {
      const i = themeIndex(name);
      if (i === theme) return;
      theme = i;
      if (img) buildBackground();
    }
    function setSide(s) {
      if ((I[TICK] >= SEG_TICKS ? 1 : 0) === s) return;
      I[TICK] = (I[TICK] + SEG_TICKS) % LOOP_TICKS;
      resample();
    }

    theme = themeIndex(opts.map);
    bakeShots();
    // `t` is a still's moment; `start` is where a playing loop begins (pages that start it from the clock stay in step)
    setTime(opts.t != null ? opts.t : opts.start != null ? opts.start : opts.side === "ct" ? SEG_S : 0);
    return {
      resize, update, render, setTime, setTheme, setSide,
      side: () => (I[TICK] >= SEG_TICKS ? 1 : 0),
      theme: () => THEME_NAMES[theme],
      nextTheme() { theme = (theme + 1) % THEME_NAMES.length; if (img) buildBackground(); },
    };
  }

  // --- names: a setting by its own name, a demo's map name or its label ("de_inferno", "Inferno") -------------------
  const THEME_NAMES = ["rooftop", "desert", "jungle", "village", "plant", "railyard", "snow", "river"];
  const MAP_THEMES = {dust2: "desert", mirage: "desert", ancient: "jungle", inferno: "village", italy: "village",
    nuke: "plant", train: "railyard", office: "snow", vertigo: "rooftop", anubis: "river", overpass: "river"};
  function themeIndex(name) {
    const n = String(name || "").toLowerCase().replace(/^(de|cs)_/, "");
    const i = THEME_NAMES.indexOf(MAP_THEMES[n] || n);
    return i < 0 ? 0 : i;
  }

  // --- mount: an animated scene that fits its box ----------------------------------------------------------------
  function mount(canvas, opts = {}) {
    const scene = create(canvas, opts);
    const host = opts.fit === "window" ? null : (opts.host || canvas.parentElement);
    const still = opts.t != null || matchMedia("(prefers-reduced-motion: reduce)").matches;
    const T = new Float64Array(2);          // the accumulator and the last frame's time: nothing boxed in the loop
    let raf = 0, running = false, onScreen = true, cssW = 0, cssH = 0, dpr = 0;
    function fit() {
      const w = host ? host.clientWidth : innerWidth, h = host ? host.clientHeight : innerHeight;
      const r = window.devicePixelRatio || 1;
      if (!w || !h || (w === cssW && h === cssH && r === dpr)) return;
      cssW = w; cssH = h; dpr = r;
      const cw = Math.round(w * r), ch = Math.round(h * r);
      canvas.style.width = cw / r + "px"; canvas.style.height = ch / r + "px";
      scene.resize(cw, ch);
      scene.render();
    }
    function frame(now) {
      if (T[1] < 0) T[1] = now;
      let dt = now - T[1];
      T[1] = now;
      if (dt > 250) dt = STEP_MS;           // back from a pause: carry on, do not fast-forward
      T[0] += dt;
      let n = 0;
      while (T[0] >= STEP_MS && n < 5) { scene.update(); T[0] -= STEP_MS; n++; }
      if (n === 5) T[0] = 0;
      scene.render();
      raf = requestAnimationFrame(frame);
    }
    const wanted = () => !still && onScreen && !document.hidden && (!opts.pauseOnBlur || document.hasFocus());
    function sync() {
      const want = wanted();
      if (want && !running) { running = true; T[1] = -1; raf = requestAnimationFrame(frame); }
      else if (!want && running) { running = false; cancelAnimationFrame(raf); }
    }
    const ro = typeof ResizeObserver === "function" && host ? new ResizeObserver(fit) : null;
    if (ro) ro.observe(host);
    const io = typeof IntersectionObserver === "function"
      ? new IntersectionObserver(entries => { onScreen = entries[entries.length - 1].isIntersecting; sync(); }) : null;
    if (io) io.observe(canvas);
    addEventListener("resize", fit);
    document.addEventListener("visibilitychange", sync);
    addEventListener("focus", sync);
    addEventListener("blur", sync);
    fit();
    sync();
    const redraw = () => { if (!running && cssW) scene.render(); };
    return {
      setMap(name) { scene.setTheme(name); redraw(); },
      setSide(s) { scene.setSide(s); redraw(); },
      side: scene.side,
      theme: scene.theme,
      nextTheme() { scene.nextTheme(); redraw(); },
      destroy() {
        running = false; cancelAnimationFrame(raf);
        if (ro) ro.disconnect();
        if (io) io.disconnect();
        removeEventListener("resize", fit);
        document.removeEventListener("visibilitychange", sync);
        removeEventListener("focus", sync);
        removeEventListener("blur", sync);
      },
    };
  }

  // --- paint: one still frame, sized to the canvas as laid out -----------------------------------------------------
  function paint(canvas, opts = {}) {
    const r = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    const scene = canvas._pixelScene || (canvas._pixelScene = create(canvas, opts));
    scene.setTheme(opts.map);
    if (opts.t != null) scene.setTime(opts.t);
    scene.resize(Math.round(w * r), Math.round(h * r));
    scene.render();
  }

  return {mount, paint, themeFor: name => THEME_NAMES[themeIndex(name)], THEMES: THEME_NAMES};
})();
