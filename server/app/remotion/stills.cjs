// Render many stills of one Remotion project with ONE bundle and ONE browser
// (docs/REMOTION_CONTRACT.md §3.9). Called by render.stills():
//   node stills.cjs <base node_modules dir> <job.json>
// job.json = {"proj": "<project dir>", "comp": "Main", "bundle": "<out dir>", "gl": "angle",
//             "concurrency": 2, "frames": [{"frame": 123, "out": "/abs/file.png"}, ...]}
// Prints one line "ok <frame> <out>" or "fail <frame> <message>" per frame; exits 1 when
// bundling/browser fails or any frame failed. Same options as `remotion still` with the template's
// remotion.config.ts (PNG, scale 1, chromium gl "angle").
"use strict";
const path = require("path");
const fs = require("fs");
const { createRequire } = require("module");

const [baseNm, jobFile] = process.argv.slice(2);
const req = createRequire(path.join(baseNm, "noop.js"));
const { bundle } = req("@remotion/bundler");
const { openBrowser, selectComposition, renderStill } = req("@remotion/renderer");

async function main() {
  const job = JSON.parse(fs.readFileSync(jobFile, "utf8"));
  const proj = job.proj;
  const chromiumOptions = { gl: job.gl || "angle" };
  const serveUrl = await bundle({
    entryPoint: path.join(proj, "src", "index.ts"),
    rootDir: proj,
    publicDir: path.join(proj, "public"),
    outDir: job.bundle,
    enableCaching: true,
  });
  const browser = await openBrowser("chrome", { chromiumOptions });
  let failed = 0;
  try {
    const composition = await selectComposition({
      serveUrl, id: job.comp || "Main", puppeteerInstance: browser, chromiumOptions,
      logLevel: "error",
    });
    const queue = job.frames.slice();
    const worker = async () => {
      while (queue.length) {
        const { frame, out } = queue.shift();
        try {
          await renderStill({
            composition, serveUrl, output: out, frame, imageFormat: "png", scale: 1,
            puppeteerInstance: browser, chromiumOptions, overwrite: true, logLevel: "error",
          });
          console.log(`ok ${frame} ${out}`);
        } catch (e) {
          failed += 1;
          console.log(`fail ${frame} ${String(e && e.message || e).split("\n")[0]}`);
        }
      }
    };
    await Promise.all(Array.from({ length: Math.max(1, job.concurrency || 1) }, worker));
  } finally {
    await browser.close({ silent: true });
  }
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error(String(e && e.stack || e));
  process.exit(1);
});
