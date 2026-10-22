// Rendert die simulierten Beispielbilder in gallery/sample-data/ (nur fuer Entwicklung/Demo).
//   python3 gallery/tools/make_sample_data.py
//   node gallery/tools/render_samples.mjs            (braucht: npm i playwright)
// Optional: CHROMIUM=/pfad/zu/chromium node gallery/tools/render_samples.mjs
import { chromium } from "playwright";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const gallery = join(here, "..");
const render = JSON.parse(readFileSync(join(here, "render.json"), "utf8"));

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage();
await page.goto("file://" + join(here, "render-page.html"));
let seed = 31;
for (const [slug, { file, pos }] of Object.entries(render)) {
  const url = await page.evaluate(([s, p, sd]) => window.renderSample(s, p, sd), [slug, pos, seed += 11]);
  writeFileSync(join(gallery, file), Buffer.from(url.split(",")[1], "base64"));
  console.log("geschrieben:", file);
}
await browser.close();
