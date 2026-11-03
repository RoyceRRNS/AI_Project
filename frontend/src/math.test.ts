import { describe, expect, it } from "vitest";
import { looksLikeProse, mathQuality, prepareMath, repairFormula } from "./math";

// Docling's reading of a scanned formula: three rows with \\ and a stray & but no environment.
const ROWS = String.raw`\ k x w + ( b a s e _ { c } - & \text {low } 1 \times w ) \\ = \ k x 4 \\ C [ k ] \ = \ 4 \ k + c _ { 3 }`;
const CUT_OFF = String.raw`\begin{array} {`;

describe("repairFormula", () => {
  it("leaves formulas that already render untouched", () => {
    expect(repairFormula(String.raw`\frac{1}{2} \Vert w \Vert^2`, true)).toBe(String.raw`\frac{1}{2} \Vert w \Vert^2`);
  });

  it("wraps rows and alignment points that have no environment in aligned", () => {
    expect(repairFormula(ROWS, true)).toBe(String.raw`\begin{aligned}` + ROWS + String.raw`\end{aligned}`);
    expect(repairFormula("a & b", false)).toBe(String.raw`\begin{aligned}a & b\end{aligned}`);
  });

  it("shows a word the parser turned into a macro as upright text", () => {
    expect(repairFormula(String.raw`t_8 = \computation \, of \, c_3`, true))
      .toBe(String.raw`t_8 = \mathrm{computation} \, of \, c_3`);
    expect(repairFormula(String.raw`\foo + \foobar`, true)).toBe(String.raw`\mathrm{foo} + \mathrm{foobar}`);
  });

  it("repairs a missing environment and an unknown macro in the same formula", () => {
    expect(repairFormula(String.raw`t_7 & = 4 k \\ t_8 & = \computation`, true)).toBe(
      String.raw`\begin{aligned}t_7 & = 4 k \\ t_8 & = \mathrm{computation}\end{aligned}`,
    );
  });

  it("gives up on formulas that were cut off or are not math", () => {
    expect(repairFormula(CUT_OFF, true)).toBeNull();
    expect(repairFormula(String.raw`\frac{1}{`, false)).toBeNull();
    expect(repairFormula(String.raw`\begin{array}{rl} a & b \\`, true)).toBeNull();
  });
});

describe("prepareMath", () => {
  it("returns text without broken math unchanged", () => {
    const text = "Inline $x^2$, a price of \\$5, and\n\n$$\n\\frac{1}{2}\n$$\n\ndone.";
    expect(prepareMath(text)).toEqual({ markdown: text, broken: [], doubtful: 0 });
  });

  it("rewrites a repaired display formula on its own lines and counts it as doubtful", () => {
    const { markdown, broken, doubtful } = prepareMath(`Before\n\n$$\n${ROWS}\n$$\n\nAfter`);
    expect(broken).toEqual([]);
    expect(doubtful).toBe(1);
    expect(markdown).toBe(`Before\n\n$$\n\\begin{aligned}${ROWS}\\end{aligned}\n$$\n\nAfter`);
  });

  it("links each formula it cannot repair, marking display and inline ones", () => {
    const { markdown, broken } = prepareMath(`See $$\n${CUT_OFF}\n$$ and $\\frac{1}{$ here.`);
    expect(broken).toEqual([CUT_OFF, "\\frac{1}{"]);
    expect(markdown).toBe("See [Unreadable formula](#formula-d0) and [Unreadable formula](#formula-i1) here.");
  });

  it("leaves lone and unclosed dollar signs alone", () => {
    expect(prepareMath("costs $5").markdown).toBe("costs $5");
    expect(prepareMath("$$ $$ and $\n\nx$").markdown).toBe("$$ $$ and $\n\nx$");
  });

  it("rates how far a card's formula can be trusted", () => {
    expect(mathQuality(`$$\n${CUT_OFF}\n$$`)).toBe("unreadable");
    expect(mathQuality(`$$\n${CUT_OFF}\n$$ and $$\n${ROWS}\n$$`)).toBe("unreadable");
    expect(mathQuality(`$$\n${ROWS}\n$$`)).toBe("doubtful");
    expect(mathQuality("$$\nThe three address code for this expression can be , t_1 = i * 20\n$$")).toBe("doubtful");
    expect(mathQuality("$$\nP ( A \\ | \\ B ) = \\frac { P ( B \\ | \\ A ) \\, P ( A ) } { P ( B ) }\n$$")).toBe("clean");
    expect(mathQuality("no math")).toBe("clean");
  });
});

describe("looksLikeProse", () => {
  it("spots a sentence read as a formula", () => {
    expect(looksLikeProse("Hence the three address code for A ( i , j )")).toBe(true);
  });

  it("does not count macros, text blocks or spaced-out letters as words", () => {
    expect(looksLikeProse(String.raw`\frac { \partial \mathcal { L } } { \partial h } \quad \tanh ( x )`)).toBe(false);
    expect(looksLikeProse(String.raw`t_5 = \text { computation of } c_2 = \text {base} _ { B }`)).toBe(false);
    expect(looksLikeProse(String.raw`\iota = h i g h _ { j } - l o w _ { j } + 1`)).toBe(false);
    expect(looksLikeProse("x_{max} + base_c")).toBe(false);
  });

  it("does not count subscript and superscript labels as words", () => {
    expect(looksLikeProse(String.raw`h_{t} = \tanh(W_hh h_{t-1} + W_xh x_{t} + b_h), y_{t} = W_hy h_{t}`)).toBe(false);
    expect(looksLikeProse(String.raw`W_{out} h + b_{out} \le x^{max} + e^{iwt}`)).toBe(false);
    expect(looksLikeProse("softmax(W x + b) = argmax p")).toBe(false);
  });

  it("does not count environment names as words", () => {
    const matrices = String.raw`\left( \begin{matrix} 0 \\ 1 \end{matrix} \right) = \left( \begin{matrix} 1 \\ 2 \end{matrix} \right)`;
    expect(looksLikeProse(matrices)).toBe(false);
  });
});
