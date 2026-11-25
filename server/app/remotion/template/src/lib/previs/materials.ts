// Clay materials by palette slot (docs/PREVIS_CONTRACT.md section 9) and the stencil materials
// for solid section caps (same technique as math-visualizations' raup-shell page).
import * as THREE from "three";

export const PALETTE = {
  clay: "#c9c4bb",
  accent1: "#d98c5f",
  accent2: "#6f9fd8",
  accent3: "#8cc084",
  accent4: "#d8c46f",
  glass: "#a9b8c6",
};
export type Palette = typeof PALETTE;
export const GLASS_OPACITY = 0.35;

/** The evp_slot of a glTF material (GLTFLoader puts material extras in userData). */
export function slotOf(m: THREE.Material): string {
  const s = (m.userData as { evp_slot?: string })?.evp_slot;
  return typeof s === "string" ? s : "custom";
}

/** Replace a mesh's glTF material by the clay look; keeps the slot in userData. */
export function clayMaterial(src: THREE.Material, pal: Palette, look: string, objColor?: string): THREE.MeshStandardMaterial {
  const slot = slotOf(src);
  const base = (src as THREE.MeshStandardMaterial).color?.getHexString?.();
  const color = slot === "clay" && objColor ? objColor : (pal as Record<string, string>)[slot] ?? (base ? `#${base}` : pal.clay);
  const glass = slot === "glass" || src.opacity < 1;
  const opacity = glass ? (src.opacity < 1 ? src.opacity : GLASS_OPACITY) : 1;
  const m = new THREE.MeshStandardMaterial({
    color,
    roughness: look === "flat" ? 1 : 0.82,
    metalness: 0,
    flatShading: look === "flat",
    wireframe: look === "wire",
    transparent: glass,
    opacity,
    depthWrite: !glass,
  });
  m.userData = { evp_slot: slot, baseOpacity: opacity, glass, color };
  return m;
}

const stencilBase = {
  depthWrite: false,
  depthTest: false,
  colorWrite: false,
  stencilWrite: true,
  stencilFunc: THREE.AlwaysStencilFunc,
};

/** Back faces +1, front faces -1: non-zero where the plane lies inside the closed solid. */
export function stencilMaterials(plane: THREE.Plane): [THREE.MeshBasicMaterial, THREE.MeshBasicMaterial] {
  const back = new THREE.MeshBasicMaterial({
    ...stencilBase,
    side: THREE.BackSide,
    clippingPlanes: [plane],
    stencilFail: THREE.IncrementWrapStencilOp,
    stencilZFail: THREE.IncrementWrapStencilOp,
    stencilZPass: THREE.IncrementWrapStencilOp,
  });
  const front = new THREE.MeshBasicMaterial({
    ...stencilBase,
    side: THREE.FrontSide,
    clippingPlanes: [plane],
    stencilFail: THREE.DecrementWrapStencilOp,
    stencilZFail: THREE.DecrementWrapStencilOp,
    stencilZPass: THREE.DecrementWrapStencilOp,
  });
  return [back, front];
}

/** The cap drawn where the stencil is non-zero, then the stencil is reset. */
export function capMaterial(color: string): THREE.MeshStandardMaterial {
  const c = new THREE.Color(color).multiplyScalar(0.86);
  return new THREE.MeshStandardMaterial({
    color: c,
    roughness: 0.9,
    metalness: 0,
    side: THREE.DoubleSide,
    stencilWrite: true,
    stencilRef: 0,
    stencilFunc: THREE.NotEqualStencilFunc,
    stencilFail: THREE.ReplaceStencilOp,
    stencilZFail: THREE.ReplaceStencilOp,
    stencilZPass: THREE.ReplaceStencilOp,
  });
}
