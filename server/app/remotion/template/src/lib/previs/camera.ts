// Camera row -> three.js camera. The shot checks (server/app/previs/js/shot/check.ts cameraAt)
// build exactly the same camera, so what the checks see is what renders.
import * as THREE from "three";

export const NEAR = 0.01;
export const FAR = 200;

/** Apply [px,py,pz, lx,ly,lz, vfov_deg, roll_deg] to a perspective camera. */
export function applyCamera(c: THREE.PerspectiveCamera, r: number[], aspect: number): THREE.PerspectiveCamera {
  c.fov = r[6];
  c.aspect = aspect;
  c.near = NEAR;
  c.far = FAR;
  c.position.set(r[0], r[1], r[2]);
  c.up.set(0, 1, 0);
  c.lookAt(r[3], r[4], r[5]);
  if (r[7]) c.rotateZ((r[7] * Math.PI) / 180);
  c.updateProjectionMatrix();
  c.updateMatrixWorld(true);
  return c;
}

/** Pixel position of a world point, or null when it is behind the camera. */
export function toScreen(c: THREE.PerspectiveCamera, p: number[], w: number, h: number): [number, number] | null {
  const v = new THREE.Vector3(p[0], p[1], p[2]);
  if (-v.clone().applyMatrix4(c.matrixWorldInverse).z <= c.near) return null;
  v.project(c);
  return [((v.x + 1) / 2) * w, ((1 - v.y) / 2) * h];
}
