/**
 * Math read from PDFs does not always parse. Docling's formula model can leave out the
 * environment around a multi-line formula, cut a formula off, or turn a word into a macro, and
 * KaTeX then shows the whole formula as red source code. Repair what can be repaired and mark
 * the rest, so the page can show something readable instead.
 */

import katex from "katex";

export interface PreparedMath {
  markdown: string;
  /** The LaTeX of each formula that could not be repaired, linked as #formula-d<i> or #formula-i<i>. */
  broken: string[];
  /** How many formulas render but were probably misread: they needed repair, or are a sentence. */
  doubtful: number;
}

/** "doubtful": it renders, but probably not as the notes have it. */
export type MathQuality = "clean" | "doubtful" | "unreadable";

export const BROKEN_FORMULA_HREF = /^#formula-([di])(\d+)$/;

const UNDEFINED_MACRO = /Undefined control sequence: \\([A-Za-z]+)/;
const ALIGNMENT = /&|\\\\/;
const MAX_REPAIRS = 8;
const MAX_CACHE = 1000;
const cache = new Map<string, string | null>();

const TEXT_MACRO = /\\(?:text|textbf|textit|mathrm|operatorname|mbox|begin|end)\s*\{[^{}]*\}/g;
const MACRO = /\\[A-Za-z]+/g;
const SCRIPT = /[_^]\s*(?:\{[^{}]*\}|[A-Za-z0-9]+)/g; // labels such as W_hh or x_{max}
const WORD = /[A-Za-z]{3,}/g;
const MIN_PROSE_WORDS = 3;

/** A sentence the parser took for a formula: math mode runs its words together in italics. */
export function looksLikeProse(tex: string): boolean {
  const plain = tex.replace(TEXT_MACRO, " ").replace(MACRO, " ").replace(SCRIPT, " ");
  return (plain.match(WORD) ?? []).length >= MIN_PROSE_WORDS;
}

function parseError(tex: string, displayMode: boolean): string | null {
  try {
    katex.renderToString(tex, { displayMode, throwOnError: true, strict: "ignore" });
    return null;
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}

/** `tex` in a form KaTeX renders, or null when it cannot be repaired. */
export function repairFormula(tex: string, displayMode: boolean): string | null {
  const key = `${displayMode ? "d" : "i"}${tex}`;
  const cached = cache.get(key);
  if (cached !== undefined) return cached;
  let candidate = tex;
  let error = parseError(candidate, displayMode);
  let aligned = false;
  for (let i = 0; error && i < MAX_REPAIRS; i++) {
    const macro = UNDEFINED_MACRO.exec(error);
    if (macro) {
      // A word read as a macro, e.g. \computation: show it as upright text.
      candidate = candidate.replace(new RegExp(String.raw`\\${macro[1]}(?![A-Za-z])`, "g"), String.raw`\mathrm{${macro[1]}}`);
    } else if (!aligned && ALIGNMENT.test(candidate) && !candidate.includes(String.raw`\begin{`)) {
      // Rows (\\) and alignment points (&) written without the environment around them.
      candidate = String.raw`\begin{aligned}` + candidate + String.raw`\end{aligned}`;
      aligned = true;
    } else {
      break;
    }
    error = parseError(candidate, displayMode);
  }
  const result = error ? null : candidate;
  if (cache.size >= MAX_CACHE) cache.clear();
  cache.set(key, result);
  return result;
}

function closingDelimiter(text: string, from: number, delim: string): number {
  for (let j = text.indexOf(delim, from); j >= 0; j = text.indexOf(delim, j + 1)) {
    if (text[j - 1] !== "\\") return j;
  }
  return -1;
}

/**
 * Repair the `$…$` and `$$…$$` math in markdown. A formula that cannot be repaired is replaced
 * by a link to `#formula-d<i>` (display) or `#formula-i<i>` (inline), for the renderer to show.
 */
export function prepareMath(text: string): PreparedMath {
  const broken: string[] = [];
  let doubtful = 0;
  let out = "";
  let i = 0;
  while (i < text.length) {
    const next = text.indexOf("$", i);
    if (next < 0) {
      out += text.slice(i);
      break;
    }
    out += text.slice(i, next);
    if (text[next - 1] === "\\") {
      out += "$";
      i = next + 1;
      continue;
    }
    const display = text.startsWith("$$", next);
    const delim = display ? "$$" : "$";
    const end = closingDelimiter(text, next + delim.length, delim);
    const body = end < 0 ? "" : text.slice(next + delim.length, end);
    if (end < 0 || !body.trim() || (!display && body.includes("\n\n"))) {
      out += delim;
      i = next + delim.length;
      continue;
    }
    const tex = body.trim();
    const fixed = repairFormula(tex, display);
    if (fixed === tex) out += text.slice(next, end + delim.length);
    else if (fixed !== null) out += display ? `$$\n${fixed}\n$$` : `$${fixed}$`;
    else out += `[Unreadable formula](#formula-${display ? "d" : "i"}${broken.push(tex) - 1})`;
    if (fixed !== null && (fixed !== tex || looksLikeProse(tex))) doubtful++;
    i = end + delim.length;
  }
  return { markdown: out, broken, doubtful };
}

/** How far the math in `text` can be trusted to show what the notes say. */
export function mathQuality(text: string): MathQuality {
  const { broken, doubtful } = prepareMath(text);
  return broken.length ? "unreadable" : doubtful ? "doubtful" : "clean";
}
