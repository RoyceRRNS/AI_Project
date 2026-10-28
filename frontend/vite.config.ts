/// <reference types="vitest/config" />
import { cpSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const here = dirname(fileURLToPath(import.meta.url));

// pdf.js needs its WASM image decoders (JBIG2/JPEG2000 in scanned PDFs), character maps
// and standard fonts served as static files; copy them into public/ once.
for (const dir of ["cmaps", "standard_fonts", "wasm"]) {
  const src = resolve(here, "node_modules/pdfjs-dist", dir);
  const dst = resolve(here, "public/pdfjs", dir);
  if (existsSync(src) && !existsSync(dst)) cpSync(src, dst, { recursive: true });
}

const apiTarget = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    strictPort: true,
    proxy: { "/api": { target: apiTarget, changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 2500 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
  },
});
