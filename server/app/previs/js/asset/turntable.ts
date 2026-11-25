// turntable <glb> <meta.json> <out_dir>: thumb.jpg (frame 0) + turntable.mp4 (4 s, 360°, 720p)
// with the small Remotion composition in ./turntable/ (one bundle, then render + still).
// shortcut: own minimal composition, not part B's renderer; switch to B's PrevisShot once it lands.
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import type { ModelMeta } from "../core/types";

/** server/app/previs/js (dist/previs.mjs lives one level below it). */
export function jsDir(): string {
  if (process.env.EVP_PREVIS_JS) return process.env.EVP_PREVIS_JS;
  return resolve(dirname(fileURLToPath(import.meta.url)), "..");
}

function remotionBin(): string {
  const base = process.env.EVP_REMOTION_BASE || join(process.env.EVP_TOOLS || join(homedir(), ".cache", "explainer-video"), "remotion-base");
  return join(base, "node_modules", ".bin", "remotion");
}

function run(args: string[], cwd: string, what: string) {
  const r = spawnSync(remotionBin(), args, { cwd, encoding: "utf8", timeout: 900_000 });
  if (r.status !== 0) throw new Error(`remotion ${what} 失败（exit ${r.status}）：${(r.stderr || r.stdout || String(r.error ?? "")).slice(-1500)}`);
}

export function turntable(glbPath: string, meta: ModelMeta, outDir: string, opts: { concurrency?: number } = {}) {
  const src = join(jsDir(), "asset", "turntable");
  if (!existsSync(join(src, "index.ts"))) throw new Error(`找不到转台合成 ${src}`);
  mkdirSync(outDir, { recursive: true });
  const work = mkdtempSync(join(tmpdir(), "evp-turntable-"));
  try {
    const pub = join(work, "public");
    mkdirSync(pub);
    copyFileSync(glbPath, join(pub, "model.glb")); // a copy: Remotion's static server rejects symlinks
    const props = join(work, "props.json");
    writeFileSync(props, JSON.stringify({ glb: "model.glb", bbox: meta.bbox, rig: meta.rig ?? null }));
    const bundle = join(work, "bundle");
    run(["bundle", join(src, "index.ts"), `--public-dir=${pub}`, `--out-dir=${bundle}`, "--log=error"], src, "bundle");
    const common = [`--props=${props}`, "--gl=swangle", "--log=error"];
    const mp4 = resolve(outDir, "turntable.mp4");
    const jpg = resolve(outDir, "thumb.jpg");
    run(["render", bundle, "Turntable", mp4, `--concurrency=${opts.concurrency ?? 2}`, "--codec=h264", "--crf=23", ...common], src, "render");
    run(["still", bundle, "Turntable", jpg, "--frame=0", "--image-format=jpeg", "--jpeg-quality=85", ...common], src, "still");
    return { mp4, thumb: jpg };
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}
