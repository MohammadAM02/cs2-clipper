// Nugget, the war-correspondent chicken. CS maps are full of chickens; this one lived, put on a PRESS helmet and
// films your Highlights. He is drawn here in code so every page can have him, in four moods:
//   "rendering"  filming: the REC light on his helmet camera blinks and his open eye follows your cursor
//   "idle"       asleep on his egg, helmet down over his eyes
//   "problem"    feathers flying, helmet knocked askew
//   "ace"        crowing, wings up
// NUGGET.svg(mood) is the whole bird, NUGGET.head(mood) his bust for small spots, NUGGET.scene(mood) the rooftop
// he films from on Status (with him on it), NUGGET.burst(word) a comic sound effect, NUGGET.follow(el) points
// his eye at the cursor.
"use strict";

const NUGGET = (() => {
  const K = "#0D0A08", P = "#F2EFEA", C = "#F2E7CF", O = "#FF5500", O2 = "#FF8A3D", O3 = "#FFB27A", RD = "#FF3D2E";
  const MOODS = ["rendering", "idle", "problem", "ace"];
  const moodOf = m => (MOODS.includes(m) ? m : "idle");
  let uid = 0;      // every drawing gets its own ids: several Nuggets can share a page

  const st = w => `stroke="${K}" stroke-width="${w}" stroke-linejoin="round" stroke-linecap="round"`;
  const limb = (d, c, w) => `<path d="${d}" fill="none" stroke="${K}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"/>`
    + `<path d="${d}" fill="none" stroke="${c}" stroke-width="${w - 7}" stroke-linecap="round" stroke-linejoin="round"/>`;
  function star(x, y, r, fill, w = 2) {
    const p = [];
    for (let i = 0; i < 10; i++) {
      const a = -Math.PI / 2 + i * Math.PI / 5, q = i % 2 ? r * 0.45 : r;
      p.push(`${(x + q * Math.cos(a)).toFixed(1)},${(y + q * Math.sin(a)).toFixed(1)}`);
    }
    return `<polygon points="${p.join(" ")}" fill="${fill}" ${w ? st(w) : ""}/>`;
  }
  const zz = (x, y, s, delay) => `<path class="n-zz" style="animation-delay:${delay}s" d="M${x},${y} h${s} l-${s},${s * 1.2} h${s}" fill="none" stroke="${P}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
  const drop = (x, y) => `<path d="M${x},${y} q9,14 0,20 q-9,-6 0,-20z" fill="#BFE3FF" ${st(3)}/>`;
  const feather = (x, y, r, delay) => `<g transform="translate(${x},${y}) rotate(${r})"><g class="n-fe" style="animation-delay:${delay}s">`
    + `<path d="M0,-12 C7,-6 7,6 0,12 C-7,6 -7,-6 0,-12 Z" fill="${P}" ${st(2.5)}/><path d="M0,-9 L0,9" stroke="${K}" stroke-width="1.5"/></g></g>`;

  const BODY = "M120,52 C162,52 186,96 184,134 C182,172 154,192 120,192 C86,192 58,172 56,134 C54,96 78,52 120,52 Z";
  const WINGS = {
    rest: ["M62,126 C44,134 40,160 54,172 C62,160 68,146 68,132 Z", "M178,126 C196,134 200,160 186,172 C178,160 172,146 172,132 Z"],
    up: ["M64,120 C44,104 34,80 42,66 C54,76 64,94 72,112 Z", "M176,120 C196,104 206,80 198,66 C186,76 176,94 168,112 Z"],
    out: ["M62,122 C40,112 24,118 18,128 C34,134 48,136 64,136 Z", "M178,122 C200,112 216,118 222,128 C206,134 192,136 176,136 Z"],
  };
  const HALFTONE = id => `<pattern id="${id}h" width="7" height="7" patternUnits="userSpaceOnUse"><circle cx="3.5" cy="3.5" r="1.5" fill="${K}" opacity=".42"/></pattern>`;

  // The bird in a 240 x 212 box, standing with his feet at y 208. `bust` leaves out what is around him (the
  // shadow, his egg, the zz, the feathers and stars) for the small spots that crop him to his head.
  function bird(m, id, bust = false) {
    const foot = x => limb(`M${x},184 L${x},199 M${x},199 L${x - 11},206 M${x},199 L${x},208 M${x},199 L${x + 11},206`, O2, 10);
    const eyes = {
      rendering: `<circle cx="103" cy="110" r="9" fill="#fff" ${st(3)}/><g class="n-pupil"><circle cx="103" cy="110" r="5" fill="${K}"/>`
        + `<circle cx="101.5" cy="108.5" r="1.6" fill="#fff"/></g><path d="M131,110 L147,107" fill="none" ${st(4)}/>`,
      idle: "",
      problem: `<circle cx="98" cy="106" r="9" fill="#fff" ${st(3)}/><circle cx="100" cy="107" r="3.5" fill="${K}"/>`
        + `<circle cx="142" cy="106" r="9" fill="#fff" ${st(3)}/><circle cx="140" cy="107" r="3.5" fill="${K}"/>`,
      ace: `<path d="M94,113 Q102,102 110,113 M130,113 Q138,102 146,113" fill="none" ${st(4)}/>`,
    }[m];
    const beak = m === "ace" || m === "problem"
      ? `<path d="M108,118 L120,110 L132,118 L120,140 Z" fill="${K}"/><path d="M107,118 L120,109 L133,118 L120,122 Z" fill="${O2}" ${st(3.5)}/>`
        + `<path d="M110,127 L130,127 L120,140 Z" fill="${O2}" ${st(3.5)}/>`
      : `<path d="M109,120 L120,113 L131,120 L120,130 Z" fill="${O2}" ${st(4)}/>`;
    const wattle = `<path d="M117,131 C112,141 116,150 121,148 C126,145 125,137 123,131 Z" fill="${O}" ${st(3)}/>`;
    const helmet = `<path d="M72,84 C72,50 94,36 120,36 C146,36 168,50 168,84 Z" fill="#3D3B2B" ${st(5)}/>`
      + `<path d="M74,72 C100,78 140,78 166,72" fill="none" stroke="#24221A" stroke-width="7"/>`
      + `<path d="M60,84 L180,84 L176,95 L64,95 Z" fill="#2C2A1F" ${st(4)}/>`
      + `<rect x="98" y="48" width="44" height="16" rx="2" fill="${C}" ${st(2.5)}/>`
      + `<text x="120" y="61" text-anchor="middle" font-family="Bangers, Impact, sans-serif" font-size="13" letter-spacing="1" fill="${K}">PRESS</text>`
      + `<path d="M106,40 C100,26 112,18 117,29 C116,13 133,13 131,28 C137,19 149,25 140,41 Z" fill="${O}" ${st(4)}/>`
      + `<rect x="156" y="52" width="30" height="21" rx="4" fill="#2A2422" ${st(4)}/><rect x="184" y="56" width="10" height="13" rx="2" fill="#3E3530" ${st(3)}/>`
      + (m === "rendering" ? `<circle class="n-rec" cx="165" cy="46" r="5" fill="${RD}" ${st(2)}/>` : "");
    const tilt = {rendering: "", idle: "translate(0,14)", problem: "rotate(-14 120 84) translate(-2,-8)", ace: "translate(0,-8)"}[m];
    const around = bust ? "" : {
      rendering: "",
      idle: zz(184, 22, 10, 0) + zz(198, 4, 13, 1.2),
      problem: feather(28, 52, -30, 0) + feather(212, 40, 25, 0.5) + feather(222, 168, 70, 1) + drop(52, 64),
      ace: star(22, 40, 10, O3) + star(218, 36, 12, O) + star(218, 150, 8, P),
    }[m];
    const wings = WINGS[m === "ace" ? "up" : m === "problem" ? "out" : "rest"];
    let s = "";
    if (m === "idle" && !bust) s += `<ellipse cx="178" cy="182" rx="17" ry="21" fill="${C}" ${st(4)}/>`;    // his egg
    s += `<path d="M60,118 L30,92 L46,122 L24,116 L54,142 Z" fill="${P}" ${st(4)}/>`;                         // tail
    if (m !== "idle" && !bust) s += foot(104) + foot(136);
    s += `<path d="${BODY}" fill="${P}" ${st(5)}/><clipPath id="${id}b"><path d="${BODY}"/></clipPath>`
      + `<circle cx="172" cy="176" r="92" fill="url(#${id}h)" clip-path="url(#${id}b)"/><path d="${BODY}" fill="none" ${st(5)}/>`;
    s += wings.map(d => `<path d="${d}" fill="${P}" ${st(4)}/>`).join("");
    s += wattle + beak + eyes + `<g transform="${tilt}">${helmet}</g>`;
    const shadow = bust ? "" : `<ellipse cx="120" cy="204" rx="60" ry="5" fill="#000" opacity=".5"/>`;
    return `${shadow}<g class="n-body">${s}</g>${around}`;
  }

  function svg(mood = "idle", label = "Nugget") {
    const m = moodOf(mood), id = `nug${++uid}`;
    return `<svg class="nugget n-${m}" viewBox="0 0 240 212" role="img" aria-label="${label}"><defs>${HALFTONE(id)}</defs>${bird(m, id)}</svg>`;
  }

  // His bust: the same drawing, cropped to the helmet and face.
  function head(mood = "rendering") {
    const m = moodOf(mood), id = `nug${++uid}`;
    return `<svg class="nhead n-${m}" viewBox="48 8 144 144" aria-hidden="true"><defs>${HALFTONE(id)}</defs>${bird(m, id, true)}</svg>`;
  }

  // --- the rooftop he films from: a skyline in the mood's light, with him standing on the roof ------------------
  function skyline(seed, low, high, fill, windows) {
    let r = seed;
    const rnd = () => (r = (r * 16807) % 2147483647) / 2147483647;
    let x = -20, d = "M-20,330 ", lit = "";
    while (x < 500) {
      const w = 34 + rnd() * 62, h = low + rnd() * (high - low);
      d += `L${x.toFixed(0)},${h.toFixed(0)} `;
      if (rnd() < 0.22) {      // a dome, the way Mirage and Dust2 have them
        const a = x + w * 0.25, b = x + w * 0.75;
        d += `L${a.toFixed(0)},${h.toFixed(0)} A${(w * 0.25).toFixed(0)},${(w * 0.22).toFixed(0)} 0 0 1 ${b.toFixed(0)},${h.toFixed(0)} `;
      }
      d += `L${(x + w).toFixed(0)},${h.toFixed(0)} `;
      if (windows) {
        for (let i = 0; i < 3; i++) {
          if (rnd() < 0.55) lit += `<rect x="${(x + 6 + rnd() * (w - 14)).toFixed(0)}" y="${(h + 10 + rnd() * (300 - h - 20)).toFixed(0)}" width="4" height="6" fill="${O2}" opacity="${(0.45 + rnd() * 0.55).toFixed(2)}"/>`;
        }
      }
      x += w;
    }
    return `<path d="${d}L500,330 Z" fill="${fill}"/>${lit}`;
  }

  function scene(mood = "idle", night = false) {
    const m = moodOf(mood), id = `nug${++uid}`;
    const dark = m === "idle" && night;
    const sky = dark ? ["#050304", "#120A10", "#24131A", "#3A1C1E"]
      : {rendering: ["#1E0905", "#7A3020", "#C86A44", "#D98458"], idle: ["#24160F", "#4E3222", "#8A5E40", "#B98A62"],
        problem: ["#1A0505", "#4A0F08", "#8A2410", "#B8461E"], ace: ["#3A160C", "#9A4A2C", "#E08A54", "#FFC08A"]}[m];
    const far = dark ? "#1C1218" : {rendering: "#6A2A1A", idle: "#5A3A28", problem: "#5A1A0E", ace: "#B0582E"}[m];
    const near = dark ? "#0E090C" : {rendering: "#2C100A", idle: "#24170F", problem: "#2A0806", ace: "#5A2412"}[m];
    let s = `<svg class="scene-art" viewBox="0 0 480 330" preserveAspectRatio="xMinYMax slice" aria-hidden="true"><defs>${HALFTONE(id)}`
      + `<linearGradient id="${id}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${sky[0]}"/><stop offset=".45" stop-color="${sky[1]}"/>`
      + `<stop offset=".8" stop-color="${sky[2]}"/><stop offset="1" stop-color="${sky[3]}"/></linearGradient>`
      + `<pattern id="${id}l" width="6" height="6" patternUnits="userSpaceOnUse"><line x1="0" y1="3" x2="6" y2="3" stroke="#000" stroke-width="1.2" opacity=".35"/></pattern>`
      + `<pattern id="${id}d" width="9" height="9" patternUnits="userSpaceOnUse"><circle cx="4.5" cy="4.5" r="1.5" fill="#000" opacity=".4"/></pattern>`
      + `<pattern id="${id}x" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(-38)"><line x1="0" y1="0" x2="0" y2="9" stroke="${O2}" stroke-width=".8" opacity=".35"/></pattern>`
      + `</defs><rect width="480" height="330" fill="url(#${id}g)"/>`;
    if (dark) {
      for (let i = 0; i < 26; i++) s += `<circle cx="${(i * 97 + 31) % 480}" cy="${(i * 53 + 17) % 150}" r="${i % 3 ? 1 : 1.6}" fill="${P}" opacity="${i % 2 ? 0.45 : 0.8}"/>`;
      s += `<circle cx="380" cy="70" r="34" fill="#E8E2D6"/><circle cx="380" cy="70" r="34" fill="url(#${id}l)"/>`;
    } else {
      const [cx, cy, r] = m === "idle" ? [370, 120, 70] : m === "ace" ? [330, 200, 140] : [340, 215, 120];
      s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${m === "problem" ? "#C2410C" : "#F2BC92"}" opacity=".9"/><circle cx="${cx}" cy="${cy}" r="${r}" fill="url(#${id}l)"/>`;
    }
    if (m === "ace") {
      for (let i = 0; i < 18; i++) {
        const a = i * Math.PI / 9;
        s += `<line x1="330" y1="200" x2="${(330 + 420 * Math.cos(a)).toFixed(0)}" y2="${(200 + 420 * Math.sin(a)).toFixed(0)}" stroke="${P}" stroke-width="${i % 2 ? 2 : 5}" opacity=".22"/>`;
      }
    }
    s += `<rect width="480" height="330" fill="url(#${id}d)" opacity=".5"/>`;
    s += skyline(7, 150, 215, far, false) + skyline(23, 192, 248, near, true);
    s += `<path d="M-10,330 L-10,262 L262,262 L262,252 L334,252 L334,330 Z" fill="#000"/>`
      + `<path d="M-10,262 L262,262 M262,252 L334,252" stroke="${O2}" stroke-width="2" opacity=".7"/>`
      + `<rect x="-10" y="262" width="344" height="68" fill="url(#${id}x)"/>`;
    if (m === "rendering" || m === "ace") {
      for (let i = 0; i < 12; i++) {
        s += `<circle class="ember" cx="${(i * 61 + 140) % 470}" cy="${200 + (i * 37) % 120}" r="${1.5 + (i % 3) * 0.6}" fill="${O2}" style="animation-delay:-${(i * 0.83).toFixed(1)}s;animation-duration:${(7 + (i % 5)).toFixed(0)}s"/>`;
      }
    }
    s += `<g transform="translate(34,58)">${bird(m, id)}</g>`;
    return s + "</svg>";
  }

  // --- a comic sound effect: a word on a starburst --------------------------------------------------------------
  const BURST = "80,2 90.2,35.2 108.6,20.5 108.7,44 131.6,38.8 121.4,60 156,62.6 126,80 144.3,94.7 121.4,100 131.6,121.2 108.7,116 "
    + "113.8,150.3 90.2,124.8 80,146 69.8,124.8 51.4,139.5 51.3,116 19,128.6 38.6,100 15.7,94.7 34,80 15.7,65.3 38.6,60 19,31.4 51.3,44 51.4,20.5 69.8,35.2";
  function burst(word, fill = O) {
    return `<svg viewBox="0 0 160 160" aria-hidden="true"><polygon points="${BURST}" fill="${fill}" stroke="${K}" stroke-width="5" stroke-linejoin="round"/></svg><b>${word}</b>`;
  }

  // His open eye (the "rendering" mood) looks at the cursor: `box` holds the drawing and gets --ex and --ey.
  function follow(box, eyeAt = [0.43, 0.52]) {
    addEventListener("pointermove", e => {
      if (e.pointerType === "touch") return;
      const r = box.getBoundingClientRect();
      if (!r.width) return;
      const dx = e.clientX - (r.left + r.width * eyeAt[0]), dy = e.clientY - (r.top + r.height * eyeAt[1]);
      const d = Math.max(1, Math.hypot(dx, dy)), k = Math.min(1, d / 240) * 3.4;
      box.style.setProperty("--ex", (dx / d * k).toFixed(2) + "px");
      box.style.setProperty("--ey", (dy / d * k).toFixed(2) + "px");
    }, {passive: true});
  }

  return {MOODS, svg, head, scene, burst, follow};
})();
