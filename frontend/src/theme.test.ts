import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, nextTheme, resolveTheme, storedTheme, systemPrefersDark } from "./theme";

describe("theme", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("resolves an explicit choice regardless of the system setting", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("follows the system setting for the system choice", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });

  it("cycles system, light, dark and back", () => {
    expect(nextTheme("system")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
  });

  it("reads a saved choice and ignores anything unknown", () => {
    expect(storedTheme()).toBe("system");
    localStorage.setItem(THEME_KEY, "dark");
    expect(storedTheme()).toBe("dark");
    localStorage.setItem(THEME_KEY, "purple");
    expect(storedTheme()).toBe("system");
  });

  it("treats a browser without matchMedia as light", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(systemPrefersDark()).toBe(false);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("dark") }));
    expect(systemPrefersDark()).toBe(true);
  });
});
