// Minimal clay turntable for the asset library (contract 4.4): the model at its default pose
// (core/rig.ts), clay materials from the section 9 palette, one full turn in 4 s.
// Part B owns the real previs renderer; this file only exists so assets get a preview.
import React, { useLayoutEffect, useState, useEffect } from "react";
import { ThreeCanvas } from "@remotion/three";
import { useThree } from "@react-three/fiber";
import { cancelRender, continueRender, delayRender, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { partTransforms } from "../../core/rig";
import type { ModelMeta, V3 } from "../../core/types";

export const FPS = 30;
export const FRAMES = 120;
const PALETTE: Record<string, string> = {
  clay: "#c9c4bb",
  accent1: "#d98c5f",
  accent2: "#6f9fd8",
  accent3: "#8cc084",
  accent4: "#d8c46f",
  glass: "#a9b8c6",
};
const BG = "#e9e6e1";
const VFOV = 30;
const EL = 20; // camera elevation, degrees
const AZ0 = 35; // start azimuth (three-quarter front), degrees from +Z towards +X

export type TurntableProps = { glb: string; bbox: { min: V3; max: V3 }; rig: ModelMeta["rig"] | null };

function clay(root: THREE.Object3D, rig: ModelMeta["rig"] | null) {
  if (rig) {
    for (const [id, t] of Object.entries(partTransforms(rig, {}))) {
      const o = root.getObjectByName(id);
      if (!o) continue;
      o.position.set(t[0], t[1], t[2]);
      o.rotation.set(t[3], t[4], t[5], "XYZ");
    }
  }
  root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const src = m.material as THREE.MeshStandardMaterial;
    const slot = (src.userData?.evp_slot as string) || "clay";
    const opacity = src.opacity ?? 1;
    const glass = slot === "glass" || opacity < 1;
    m.material = new THREE.MeshStandardMaterial({
      color: PALETTE[slot] ?? (/^#[0-9a-f]{6}$/i.test(slot) ? slot : PALETTE.clay),
      roughness: glass ? 0.35 : 0.85,
      metalness: 0,
      transparent: glass,
      opacity: glass ? Math.min(opacity, 0.45) : 1,
      depthWrite: !glass,
      side: glass ? THREE.DoubleSide : THREE.FrontSide,
    });
    m.castShadow = !glass;
    m.receiveShadow = true;
    if (glass) m.renderOrder = 1;
  });
}

const Rig: React.FC<{ c: THREE.Vector3; r: number }> = ({ c, r }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const size = useThree((s) => s.size);
  const dist = (r / Math.sin(((VFOV / 2) * Math.PI) / 180)) * 1.05;
  const az = ((AZ0 + (360 * frame) / durationInFrames) * Math.PI) / 180;
  const el = (EL * Math.PI) / 180;
  useLayoutEffect(() => {
    camera.fov = VFOV;
    camera.aspect = size.width / size.height;
    camera.near = dist / 100;
    camera.far = dist * 20;
    camera.position.set(c.x + dist * Math.cos(el) * Math.sin(az), c.y + dist * Math.sin(el), c.z + dist * Math.cos(el) * Math.cos(az));
    camera.lookAt(c);
    camera.updateProjectionMatrix();
  }, [camera, size, az, el, dist, c]);
  return null;
};

export const Turntable: React.FC<TurntableProps> = ({ glb, bbox, rig }) => {
  const { width, height } = useVideoConfig();
  const [handle] = useState(() => delayRender("load GLB"));
  const [scene, setScene] = useState<THREE.Object3D | null>(null);
  useEffect(() => {
    new GLTFLoader().load(
      staticFile(glb),
      (g) => {
        clay(g.scene, rig);
        setScene(g.scene);
        continueRender(handle);
      },
      undefined,
      (e) => cancelRender(e),
    );
  }, [glb, rig, handle]);
  const min = new THREE.Vector3(...bbox.min);
  const max = new THREE.Vector3(...bbox.max);
  const c = min.clone().add(max).multiplyScalar(0.5);
  const r = Math.max(max.clone().sub(min).length() / 2, 1e-3);
  const L = c.clone().add(new THREE.Vector3(-2.2, 4, 3).multiplyScalar(r));
  return (
    <div style={{ width, height, background: BG }}>
      <ThreeCanvas width={width} height={height} shadows gl={{ antialias: true }}>
        <color attach="background" args={[BG]} />
        <Rig c={c} r={r} />
        <hemisphereLight args={["#ffffff", "#b9b3a8", 1.1]} />
        <ambientLight intensity={0.25} />
        <directionalLight
          ref={(l: THREE.DirectionalLight | null) => {
            if (!l) return;
            l.target.position.copy(c); // the target is not in the scene: update its matrix by hand
            l.target.updateMatrixWorld();
          }}
          position={[L.x, L.y, L.z]}
          intensity={2.2}
          castShadow
          shadow-mapSize={[2048, 2048]}
          shadow-bias={-0.0004}
          shadow-normalBias={r * 0.01}
          shadow-camera-left={-2 * r}
          shadow-camera-right={2 * r}
          shadow-camera-top={2 * r}
          shadow-camera-bottom={-2 * r}
          shadow-camera-near={r * 0.5}
          shadow-camera-far={r * 12}
        />
        <directionalLight position={[c.x + 3 * r, c.y + r, c.z - 3 * r]} intensity={0.6} />
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[c.x, 0, c.z]} receiveShadow>
          <planeGeometry args={[r * 30, r * 30]} />
          <meshStandardMaterial color="#dcd8d0" roughness={1} />
        </mesh>
        {scene ? <primitive object={scene} /> : null}
      </ThreeCanvas>
    </div>
  );
};
