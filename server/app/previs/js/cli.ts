// previs command line (docs/PREVIS_CONTRACT.md 4.5): node dist/previs.mjs <command> ...
// Built by build.sh. Part A's asset commands are here; part B's shot commands (compile-shot,
// check-shot, plan-image, fixture-asset) come from shot/commands.ts SHOT_COMMANDS.
import { readFileSync, writeFileSync } from "node:fs";
import { basename } from "node:path";
import type { CheckIssue, ModelMeta } from "./core/types";
import { buildAsset } from "./asset/build";
import { writeGlb } from "./asset/glb";
import { DEFAULT_NORMALIZE_TRIS, normalizeGlb, parseTarget } from "./asset/normalize";
import { turntable } from "./asset/turntable";
import { validateAsset } from "./asset/validate";
import { SHOT_COMMANDS } from "./shot/commands";

const json = (p: string) => JSON.parse(readFileSync(p, "utf8"));
const write = (p: string, v: unknown) => writeFileSync(p, JSON.stringify(v, null, 1));

/** Exit 2 with one JSON CheckIssue per line on stdout. */
function fail(issues: CheckIssue[]): number {
  for (const i of issues) process.stdout.write(JSON.stringify(i) + "\n");
  return 2;
}

function opt(args: string[], name: string): string | undefined {
  const i = args.indexOf(name);
  if (i < 0) return undefined;
  const v = args[i + 1];
  args.splice(i, 2);
  if (v === undefined) throw new Error(`${name} 需要一个值`);
  return v;
}

function maxTris(v: string | undefined, dflt: number): number {
  if (v === undefined) return dflt;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 100) throw new Error(`--max-tris 必须是 ≥ 100 的整数`);
  return n;
}

type Cmd = (args: string[]) => Promise<number>;

export const COMMANDS: Record<string, [string, Cmd]> = {
  "build-asset": [
    "<spec.json> <out.glb> <meta.json>",
    async ([spec, out, meta]) => {
      const r = buildAsset(json(spec));
      if (r.issues.length || !r.model || !r.meta) return fail(r.issues);
      writeFileSync(out, await writeGlb(r.model, r.meta.source.type === "parametric" ? r.meta.source.spec.id : "asset"));
      write(meta, r.meta);
      return 0;
    },
  ],
  "normalize-glb": [
    "<in.glb> <out.glb> <meta.json> --target <w|h|d|longest>:<metres> [--max-tris N] [--source <json>]",
    async (args) => {
      const target = opt(args, "--target");
      const mt = maxTris(opt(args, "--max-tris"), DEFAULT_NORMALIZE_TRIS);
      const srcJson = opt(args, "--source");
      const [inp, out, meta] = args;
      if (!target) throw new Error("缺少 --target");
      const t = parseTarget(target);
      const source = srcJson ? JSON.parse(srcJson) : { type: "upload", filename: basename(inp), target: t };
      const r = await normalizeGlb(readFileSync(inp), t, source, mt);
      if (r.issues.length || !r.model || !r.meta) return fail(r.issues);
      writeFileSync(out, await writeGlb(r.model, "body"));
      write(meta, { ...r.meta, input_tris: r.inputTris });
      return 0;
    },
  ],
  "validate-asset": [
    "<glb> <meta.json> [--max-tris N]",
    async (args) => {
      const mt = maxTris(opt(args, "--max-tris"), 200000);
      const [glb, meta] = args;
      const issues = await validateAsset(readFileSync(glb), json(meta) as ModelMeta, mt);
      return issues.length ? fail(issues) : 0;
    },
  ],
  turntable: [
    "<glb> <meta.json> <out_dir>",
    async ([glb, meta, out]) => {
      const r = turntable(glb, json(meta), out);
      process.stdout.write(JSON.stringify(r) + "\n");
      return 0;
    },
  ],
};

for (const [name, fn] of Object.entries(SHOT_COMMANDS)) {
  COMMANDS[name] = ["(see shot/commands.ts)", async (args) => fn(args)];
}

async function main(argv: string[]): Promise<number> {
  const [cmd, ...args] = argv;
  const c = cmd ? COMMANDS[cmd] : undefined;
  if (!c) {
    process.stderr.write("usage: previs.mjs <command> ...\n" + Object.entries(COMMANDS).map(([k, [u]]) => `  ${k} ${u}\n`).join(""));
    return 1;
  }
  return c[1](args);
}

main(process.argv.slice(2)).then(
  (code) => process.exit(code),
  (e) => {
    process.stderr.write(`${(e as Error).stack ?? e}\n`);
    process.exit(1);
  },
);
