// Themes + symbols: no meaning by color alone (every status has a text label),
// high-contrast + monochrome themes, ASCII fallback, reduced-motion (plain
// backend never animates).
export type ThemeName = "default" | "high-contrast" | "monochrome";

export interface Theme {
  name: ThemeName;
  ascii: boolean; // true -> ASCII-safe symbols
  color: boolean; // false -> strip all ANSI
}

export function resolveTheme(name: string | undefined, noColor: boolean): Theme {
  const n: ThemeName =
    name === "high-contrast" || name === "monochrome" ? name : "default";
  return { name: n, ascii: n === "monochrome", color: !noColor && n !== "monochrome" };
}

type SymbolKind =
  | "ok" | "fail" | "warn" | "run" | "paused" | "closed" | "draft"
  | "arrow" | "dot" | "branch" | "corner" | "tee" | "pick";

const UNICODE: Record<SymbolKind, string> = {
  ok: "✓", fail: "✗", warn: "!", run: "●", paused: "❚❚", closed: "■",
  draft: "○", arrow: "→", dot: "·", branch: "├", corner: "└", tee: "┬", pick: "▸",
};

const ASCII: Record<SymbolKind, string> = {
  ok: "[ok]", fail: "[FAIL]", warn: "[!]", run: "[*]", paused: "[||]",
  closed: "[#]", draft: "[ ]", arrow: "->", dot: "-", branch: "|-",
  corner: "`-", tee: "-+-", pick: ">",
};

export function sym(theme: Theme, kind: SymbolKind): string {
  return theme.ascii ? ASCII[kind] : UNICODE[kind];
}

// Minimal ANSI helpers, all gated on theme.color.
const C = {
  bold: "1", dim: "2", red: "31", green: "32", yellow: "33",
  blue: "34", magenta: "35", cyan: "36", white: "37",
};

export function paint(theme: Theme, code: keyof typeof C, s: string): string {
  if (!theme.color) return s;
  return `\u001b[${C[code]}m${s}\u001b[0m`;
}

export function stripAnsi(s: string): string {
  // biome-ignore lint/suspicious/noControlCharactersInRegex: ANSI stripping requires ESC matching
  return s.replace(/\u001b\[[0-9;]*m/g, "");
}

export function fit(s: string, width: number): string {
  const plain = stripAnsi(s);
  if (plain.length <= width) return s + " ".repeat(width - plain.length);
  return `${plain.slice(0, Math.max(0, width - 1))}…`;
}
