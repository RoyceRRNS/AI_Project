import { useCallback, useEffect, useState } from "react";
import { storage } from "./store";

export type ThemeChoice = "system" | "light" | "dark";

export const THEME_KEY = "cnr.theme";
const ORDER: ThemeChoice[] = ["system", "light", "dark"];
const DARK_QUERY = "(prefers-color-scheme: dark)";

export function storedTheme(): ThemeChoice {
  const value = storage.get(THEME_KEY);
  return value === "light" || value === "dark" ? value : "system";
}

export function systemPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

export function resolveTheme(choice: ThemeChoice, prefersDark = systemPrefersDark()): "light" | "dark" {
  if (choice !== "system") return choice;
  return prefersDark ? "dark" : "light";
}

export const nextTheme = (choice: ThemeChoice): ThemeChoice => ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length];

/** The saved choice, applied as data-theme on <html>; "system" follows the OS setting live. */
export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(storedTheme);

  useEffect(() => {
    const apply = () => {
      document.documentElement.dataset.theme = resolveTheme(choice);
    };
    apply();
    if (choice !== "system" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(DARK_QUERY);
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [choice]);

  const set = useCallback((next: ThemeChoice) => {
    setChoice(next);
    storage.set(THEME_KEY, next === "system" ? null : next);
  }, []);
  return [choice, set];
}
