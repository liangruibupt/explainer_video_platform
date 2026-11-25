// Part B commands for the previs CLI (docs/PREVIS_CONTRACT.md section 8). js/cli.ts imports
// SHOT_COMMANDS and dispatches to them (node js/dist/previs.mjs <command>, built by js/build.sh).
//
//   compile-shot --plan plan.json --shot s02 --assets assets.json --words words.json
//                --durations durations.json --size 1920x1080 --out s02.json
//                [--order scenes.json] [--report s02.compile.json]
//   check-shot <resolved.json> --assets assets.json [--prev <resolved of the previous shot>]
//                -> report JSON (CheckIssue[]) on stdout; exit 2 when it has an error
//   plan-image <resolved.json> --assets assets.json --out s02.plan.json   (+ s02.plan.svg)
//   fixture-asset <spec.json> <out.glb> <meta.json>    TEST ONLY (shot/testkit.ts)
//
// assets.json maps a set object's `asset` string to its library dir (meta.json + file.glb):
//   {"@four_stroke": "/data/assets/asset_x"}  or  {"@four_stroke": {"dir": "...", "id": "asset_x"}}
// The ResolvedShot's object asset id is `id` (default: the key without "@"), its GLB path
// "previs/<id>.glb" (project.py copies the file there).
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { CheckIssue, ModelMeta, PrevisPlan, WordEntry } from "../core/types";
import { compileShot, type AssetInfo } from "./compile";
import { checkAxis, checkShot, plan, planSvg, type RShot } from "./check";
import { buildTestAsset } from "./testkit";

const readJson = (p: string) => JSON.parse(readFileSync(p, "utf8"));

function opts(argv: string[]): { pos: string[]; o: Record<string, string> } {
  const pos: string[] = [];
  const o: Record<string, string> = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith("--")) o[argv[i].slice(2)] = argv[++i];
    else pos.push(argv[i]);
  }
  return { pos, o };
}

export function loadAssets(path: string): Record<string, AssetInfo> {
  const raw = readJson(path) as Record<string, string | { dir: string; id?: string }>;
  const out: Record<string, AssetInfo> = {};
  for (const [k, v] of Object.entries(raw)) {
    const dir = typeof v === "string" ? v : v.dir;
    const id = (typeof v === "string" ? undefined : v.id) ?? k.replace(/^@/, "");
    out[k] = { meta: readJson(join(dir, "meta.json")) as ModelMeta, glb: `previs/${id}.glb`, id };
  }
  return out;
}

/** Object id -> meta for a resolved shot (matched through the asset id). */
export function metasFor(shot: RShot, assets: Record<string, AssetInfo>): Record<string, ModelMeta> {
  const byId = new Map(Object.values(assets).map((a) => [a.id!, a.meta]));
  const out: Record<string, ModelMeta> = {};
  for (const o of shot.objects) {
    const m = byId.get(o.asset);
    if (m) out[o.id] = m;
  }
  return out;
}

export const SHOT_COMMANDS: Record<string, (argv: string[]) => number> = {
  "compile-shot": (argv) => {
    const { o } = opts(argv);
    const [w, h] = (o.size ?? "1920x1080").split("x").map(Number);
    const order = o.order ? (readJson(o.order) as { id: string }[]).map((s) => s.id) : undefined;
    const { shot, issues } = compileShot({
      plan: readJson(o.plan) as PrevisPlan,
      shot: o.shot,
      assets: loadAssets(o.assets),
      words: readJson(o.words) as WordEntry[],
      durations: readJson(o.durations) as number[],
      order,
      width: w,
      height: h,
    });
    writeFileSync(o.out, JSON.stringify(shot));
    if (o.report) writeFileSync(o.report, JSON.stringify(issues, null, 1));
    else for (const i of issues) console.log(JSON.stringify(i));
    return issues.some((i) => i.severity === "error") ? 2 : 0;
  },
  "check-shot": (argv) => {
    const { pos, o } = opts(argv);
    const shot = readJson(pos[0]) as RShot;
    const assets = loadAssets(o.assets);
    const metas = metasFor(shot, assets);
    const issues: CheckIssue[] = checkShot(shot, metas);
    if (o.prev) {
      const prev = readJson(o.prev) as RShot;
      issues.push(...checkAxis(prev, shot, { ...metasFor(prev, assets), ...metas }));
    }
    console.log(JSON.stringify(issues, null, 1));
    return issues.some((i) => i.severity === "error") ? 2 : 0;
  },
  "plan-image": (argv) => {
    const { pos, o } = opts(argv);
    const shot = readJson(pos[0]) as RShot;
    const d = plan(shot, metasFor(shot, loadAssets(o.assets)));
    writeFileSync(o.out, JSON.stringify(d));
    writeFileSync(o.out.replace(/\.json$/, "") + ".svg", planSvg(d));
    return 0;
  },
  "fixture-asset": (argv) => {
    const { pos } = opts(argv);
    const j = readJson(pos[0]); // an AssetSpec, a plan asset {spec}, or a plan (first parametric asset)
    const spec = j.assets ? j.assets.find((a: { kind: string }) => a.kind === "parametric").spec : (j.spec ?? j);
    const b = buildTestAsset(spec);
    writeFileSync(pos[1], b.glb);
    writeFileSync(pos[2], JSON.stringify(b.meta, null, 1));
    return 0;
  },
};
