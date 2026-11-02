import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const FIXTURES = resolve(dirname(fileURLToPath(import.meta.url)), ".fixtures");

/** Builds the synthetic lecture PDFs (and invalid files) used for uploads. */
export default function globalSetup() {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const python = resolve(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
  if (!existsSync(resolve(FIXTURES, "Lecture_04_SVMs.pdf"))) {
    execFileSync(python, ["-m", "course_notes_rag.samples", FIXTURES], { cwd: root, stdio: "inherit" });
  }
  if (!existsSync(resolve(FIXTURES, "papers", "End_Semester_2024.pdf"))) {
    execFileSync(python, ["-m", "course_notes_rag.papers.samples", resolve(FIXTURES, "papers")], { cwd: root, stdio: "inherit" });
  }
}
