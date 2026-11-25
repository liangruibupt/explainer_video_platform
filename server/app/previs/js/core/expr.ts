// Safe arithmetic expressions for asset specs and drivers (docs/PREVIS_CONTRACT.md section 4.2).
// FIXED by P0. No eval / Function: a small recursive-descent parser.
//
// Grammar: numbers (1, 0.5, 1e-3), names (params, DOFs, drivers, `pi`), + - * / ^ (power,
// right-assoc), unary -, parentheses, calls. Trig takes and returns DEGREES:
//   sin cos tan asin acos atan atan2(y,x) sqrt abs min max clamp(x,lo,hi) floor ceil round
//   mod(x,m) (result in [0,m), also for negative x)
// Unknown names, unknown functions, wrong arity, non-finite results -> ExprError.

export class ExprError extends Error {
  constructor(message: string, public expr: string) {
    super(`${message} (in "${expr}")`);
  }
}

const D = Math.PI / 180;
const FUNCS: Record<string, [number, number, (...a: number[]) => number]> = {
  // name: [min args, max args, fn]
  sin: [1, 1, (x) => Math.sin(x * D)],
  cos: [1, 1, (x) => Math.cos(x * D)],
  tan: [1, 1, (x) => Math.tan(x * D)],
  asin: [1, 1, (x) => Math.asin(x) / D],
  acos: [1, 1, (x) => Math.acos(x) / D],
  atan: [1, 1, (x) => Math.atan(x) / D],
  atan2: [2, 2, (y, x) => Math.atan2(y, x) / D],
  sqrt: [1, 1, Math.sqrt],
  abs: [1, 1, Math.abs],
  min: [1, 16, Math.min],
  max: [1, 16, Math.max],
  clamp: [3, 3, (x, lo, hi) => Math.min(hi, Math.max(lo, x))],
  floor: [1, 1, Math.floor],
  ceil: [1, 1, Math.ceil],
  round: [1, 1, Math.round],
  mod: [2, 2, (x, m) => ((x % m) + m) % m], // always in [0, m), e.g. mod(crank, 720)
};
const CONSTS: Record<string, number> = { pi: Math.PI };

type Tok = { k: "num"; v: number } | { k: "id"; v: string } | { k: "op"; v: string };

function lex(src: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === " " || c === "\t") {
      i++;
    } else if (/[0-9.]/.test(c)) {
      const m = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(src.slice(i));
      if (!m) throw new ExprError(`bad number at ${i}`, src);
      out.push({ k: "num", v: parseFloat(m[0]) });
      i += m[0].length;
    } else if (/[A-Za-z_]/.test(c)) {
      const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i))!;
      out.push({ k: "id", v: m[0] });
      i += m[0].length;
    } else if ("+-*/^(),".includes(c)) {
      out.push({ k: "op", v: c });
      i++;
    } else {
      throw new ExprError(`unexpected "${c}"`, src);
    }
  }
  return out;
}

/** Names an expression refers to (excluding functions and constants). */
export function names(src: string): string[] {
  const t = lex(src);
  const s = new Set<string>();
  t.forEach((tok, i) => {
    if (tok.k === "id" && !(tok.v in CONSTS) && !(t[i + 1]?.k === "op" && t[i + 1].v === "(")) s.add(tok.v);
  });
  return [...s];
}

export function evaluate(src: string | number, env: Record<string, number>): number {
  if (typeof src === "number") {
    if (!Number.isFinite(src)) throw new ExprError("not a finite number", String(src));
    return src;
  }
  const text: string = src;
  const toks = lex(text);
  let p = 0;
  const peek = () => toks[p];
  const isOp = (v: string) => peek()?.k === "op" && peek().v === v;
  const expect = (v: string) => {
    if (!isOp(v)) throw new ExprError(`expected "${v}"`, text);
    p++;
  };
  function primary(): number {
    const t = peek();
    if (!t) throw new ExprError("unexpected end", text);
    if (t.k === "num") {
      p++;
      return t.v;
    }
    if (t.k === "op" && t.v === "(") {
      p++;
      const v = add();
      expect(")");
      return v;
    }
    if (t.k === "op" && (t.v === "-" || t.v === "+")) {
      p++;
      const v = power();
      return t.v === "-" ? -v : v;
    }
    if (t.k === "id") {
      p++;
      if (isOp("(")) {
        p++;
        const f = FUNCS[t.v];
        if (!f) throw new ExprError(`unknown function ${t.v}`, text);
        const args: number[] = [];
        if (!isOp(")")) {
          args.push(add());
          while (isOp(",")) {
            p++;
            args.push(add());
          }
        }
        expect(")");
        if (args.length < f[0] || args.length > f[1]) throw new ExprError(`${t.v} takes ${f[0]}-${f[1]} args`, text);
        return f[2](...args);
      }
      if (t.v in env) return env[t.v];
      if (t.v in CONSTS) return CONSTS[t.v];
      throw new ExprError(`unknown name ${t.v}`, text);
    }
    throw new ExprError(`unexpected "${t.v}"`, text);
  }
  function power(): number {
    const b = primary();
    if (isOp("^")) {
      p++;
      return Math.pow(b, unary());
    }
    return b;
  }
  function unary(): number {
    if (isOp("-")) {
      p++;
      return -unary();
    }
    if (isOp("+")) {
      p++;
      return unary();
    }
    return power();
  }
  function mul(): number {
    let v = unary();
    while (isOp("*") || isOp("/")) {
      const o = toks[p++].v;
      const r = unary();
      v = o === "*" ? v * r : v / r;
    }
    return v;
  }
  function add(): number {
    let v = mul();
    while (isOp("+") || isOp("-")) {
      const o = toks[p++].v;
      const r = mul();
      v = o === "+" ? v + r : v - r;
    }
    return v;
  }
  const v = add();
  if (p !== toks.length) throw new ExprError(`unexpected "${(toks[p] as Tok).v}"`, text);
  if (!Number.isFinite(v)) throw new ExprError("result is not finite", text);
  return v;
}

export const evalVec = (v: (string | number)[], env: Record<string, number>): [number, number, number] =>
  [evaluate(v[0], env), evaluate(v[1], env), evaluate(v[2], env)];
