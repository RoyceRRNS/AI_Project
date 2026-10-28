// Renders the #fig element of a local HTML file to a PNG (used for the report's diagrams):
//   node scripts/render-figure.mjs <file.html> <out.png>
import { chromium } from "@playwright/test";
import { pathToFileURL } from "node:url";

const [src, out] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 3 });
await page.goto(pathToFileURL(src).href);
await page.locator("#fig").screenshot({ path: out });
await browser.close();
console.log("saved", out);
