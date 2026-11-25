// Tests for core/expr.ts and core/rig.ts. Run: server/app/previs/js/test.sh
import assert from "node:assert/strict";
import { evaluate, names, ExprError } from "./expr";
import { dofValues, partTransforms } from "./rig";
import type { ModelMeta } from "./types";

const near = (a: number, b: number, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
let n = 0;
const test = (name: string, fn: () => void) => {
  fn();
  n++;
  console.log("ok", name);
};

test("arithmetic and precedence", () => {
  near(evaluate("1 + 2 * 3", {}), 7);
  near(evaluate("(1 + 2) * 3", {}), 9);
  near(evaluate("2 ^ 3 ^ 2", {}), 512);
  near(evaluate("-2 ^ 2", {}), -4);
  near(evaluate("bore/2 + 0.01", { bore: 0.08 }), 0.05);
  near(evaluate("1e-3 * 2", {}), 0.002);
  near(evaluate(0.25, {}), 0.25);
});

test("degree trig and functions", () => {
  near(evaluate("sin(90)", {}), 1);
  near(evaluate("cos(180)", {}), -1);
  near(evaluate("atan2(1, 1)", {}), 45);
  near(evaluate("clamp(5, 0, 2)", {}), 2);
  near(evaluate("max(1, 4, 2)", {}), 4);
  near(evaluate("pi", {}), Math.PI);
});

test("errors", () => {
  for (const bad of ["1 +", "foo", "nope(1)", "sin(1,2)", "1 / 0", "2 $ 3", "(1"]) {
    assert.throws(() => evaluate(bad, {}), ExprError, bad);
  }
  assert.deepEqual(names("bore/2 + sin(crank) * pi").sort(), ["bore", "crank"]);
});

// Crank-slider: crank hinge about z, piston slides along y with a driver.
const rig: ModelMeta["rig"] = {
  params: { r: 0.05, l: 0.2 },
  dofs: { crank: { min: -720, max: 720, default: 0 } },
  drivers: { piston_y: "r*cos(crank) + sqrt(l^2 - (r*sin(crank))^2) - (r + l)" },
  joints: {
    crank: { type: "hinge", axis: "z", dof: "crank" },
    piston: { type: "slide", axis: "y", dof: "piston_y" },
  },
  rest: { crank: { at: [0, 0, 0], rot: [0, 0, 0] }, piston: { at: [0, 0.25, 0], rot: [0, 0, 0] }, base: { at: [0, 0, 0], rot: [0, 0, 0] } },
};

test("drivers and clamping", () => {
  near(dofValues(rig, { crank: 0 }).piston_y, 0);
  near(dofValues(rig, { crank: 180 }).piston_y, -0.1); // bottom dead centre: -2r
  near(dofValues(rig, { crank: 9999 }).crank, 720);
  assert.throws(() => dofValues(rig, { nope: 1 }));
});

test("part transforms", () => {
  const t = partTransforms(rig, { crank: 90 });
  near(t.crank[5], Math.PI / 2);
  near(t.piston[1], 0.25 + (Math.sqrt(0.04 - 0.0025) - 0.25));
  assert.deepEqual(t.base, [0, 0, 0, 0, 0, 0]);
});

test("mod", () => {
  near(evaluate("mod(-90, 720)", {}), 630);
  near(evaluate("mod(800, 720)", {}), 80);
});

// The four_stroke fixture: drivers finite over the whole DOF range, four-stroke valve timing,
// and the rod's lower end lands on the crank pin at every angle (linkage is consistent).
import plan from "../../examples/four_stroke/plan.json";
test("four_stroke fixture drivers", () => {
  const spec = (plan as any).assets[0].spec;
  const params: Record<string, number> = {};
  for (const [k, p] of Object.entries<any>(spec.params)) params[k] = p.value;
  const r: ModelMeta["rig"] = { params, dofs: spec.dofs, drivers: spec.drivers, joints: {}, rest: {} };
  const { r: cr, rod } = params;
  for (let c = -1440; c <= 1440; c += 5) {
    const v = dofValues(r, { crank: c });
    for (const [k, x] of Object.entries(v)) assert.ok(Number.isFinite(x), `${k} at ${c}`);
    // piston pin y relative to crank centre, rod end = pin + rod * (sin(rod_ang), -cos(rod_ang))
    const pinY = cr + rod + v.piston_y;
    const endX = rod * Math.sin((v.rod_ang * Math.PI) / 180);
    const endY = pinY - rod * Math.cos((v.rod_ang * Math.PI) / 180);
    const a = (c * Math.PI) / 180; // crank pin after a CCW hinge rotation of the rest point (0, r)
    near(endX, -cr * Math.sin(a), 1e-9);
    near(endY, cr * Math.cos(a), 1e-9);
    const cyc = ((c % 720) + 720) % 720;
    if (cyc > 5 && cyc < 175) assert.ok(v.intake_lift > 0 && v.exhaust_lift === 0, `intake at ${c}`);
    if (cyc > 185 && cyc < 535) assert.ok(v.intake_lift === 0 && v.exhaust_lift === 0, `closed at ${c}`);
    if (cyc > 545 && cyc < 715) assert.ok(v.exhaust_lift > 0 && v.intake_lift === 0, `exhaust at ${c}`);
  }
});

console.log(`${n} tests passed`);
