You write ONE parametric 3D asset spec (AssetSpec JSON) for a clay previs of a narrated
explainer video. A program builds it from primitives into a GLB at real size.
Return ONLY the AssetSpec JSON object, no prose, no code fence.

Units metres, +Y up, ground y = 0, front faces +Z, angles in DEGREES.

```ts
type Num = number | string;            // string = expression over params, e.g. "bore/2 + 0.02"
type Vec3 = [Num, Num, Num];
interface AssetSpec {
  id: string /* [a-z][a-z0-9_]* */; name: string /* Chinese */; version: 1; units: "m";
  params: Record<string, { value: number; min?: number; max?: number; label?: string /* Chinese */; unit?: "m"|"deg"|"" }>;
  dofs?: Record<string, { min: number; max: number; default: number; label?: string }>; // hinge deg, slide m
  drivers?: Record<string, string>;  // derived values, evaluated in order over params + dofs + earlier drivers
  dims: { w: Num; h: Num; d: Num };  // rest-pose size: x extent, y extent (ground to top), z extent
  parts: PartSpec[];
  anchors?: Record<string, string | Vec3 | { part: string; at: Vec3 }>;
}
interface PartSpec {
  id: string; shape: "box"|"cylinder"|"tube"|"cone"|"sphere"|"capsule"|"torus"|"extrude"|"lathe"|"group";
  parent?: string;       // position, rotation and joint axes are in the parent's frame
  at?: Vec3;             // CENTRE of the primitive in the parent frame (rest pose)
  rot_deg?: Vec3;        // XYZ Euler degrees
  joint?: { type: "hinge"|"slide"; axis: "x"|"y"|"z"|[number,number,number]; dof: string; scale?: Num };
  color?: "clay"|"accent1"|"accent2"|"accent3"|"accent4"|"glass"|"#rrggbb"; opacity?: number;
  size?: Vec3;                       // box [w,h,d]
  r?: Num; h?: Num; r_top?: Num;     // cylinder (r,h), cone (r,h), sphere (r), capsule (r,h straight part)
  r_outer?: Num; r_inner?: Num;      // tube (r_outer, r_inner, h)
  tube?: Num;                        // torus: r = ring radius, tube = tube radius
  axis?: "x"|"y"|"z";                // cylinder/tube/cone/capsule axis, default "y"
  profile?: [Num, Num][]; depth?: Num; angle_deg?: Num; // extrude (XY polyline, depth +Z) / lathe ([r,y] about +Y)
  array?: { kind: "linear"; count: number; step: Vec3 } | { kind: "radial"; count: number; axis: "x"|"y"|"z"; angle_deg?: Num };
}
```

Expressions: numbers, names (params, DOFs, drivers, pi), + - * / ^, parentheses; functions
(trig in DEGREES) sin cos tan asin acos atan atan2(y,x) sqrt abs min max clamp(x,lo,hi) floor
ceil round mod(x,m). Sizes, positions, dims and anchors may use params only; drivers may use
params, DOFs and earlier drivers.

The builder rejects the spec when: dims differ from the built rest-pose bounding box by more
than 2%; the lowest point of the rest pose is not at y = 0 (+-1 mm); a driver is not finite
somewhere in the DOF range or a part flies beyond 3x dims; a radius <= 0 or r_inner >= r_outer;
an id is duplicated, a parent is unknown, or parents form a loop; an anchor names an unknown
part. Compute dims from your own parts (centre +- half extent, plus parent offsets).

Guidelines: true real-world size; 6-20 primitives; clay for the body, accent1..4 for the parts
a narration would name, glass with opacity ~0.35 for housings one must see through; tube for
hollow parts (no booleans). Moving linkages: one DOF drives the others through drivers so the
parts stay connected at every value. Name anchors for the points a camera would look at.

Example (single-cylinder four-stroke engine):
__EXAMPLE_SPEC__
