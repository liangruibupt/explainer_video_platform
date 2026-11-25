// Light presets (studio / top / dramatic), ground (grid / plain / none) and the camera driver.
import React, { useLayoutEffect, useMemo } from "react";
import { useThree } from "@react-three/fiber";
import * as THREE from "three";
import { applyCamera } from "./camera";

export const CameraDriver: React.FC<{ row: number[] }> = ({ row }) => {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const size = useThree((s) => s.size);
  useLayoutEffect(() => {
    applyCamera(camera, row, size.width / size.height);
  }, [camera, size, row]);
  return null;
};

/** Key light with a shadow camera that covers the scene box. */
const Key: React.FC<{ dir: THREE.Vector3; center: THREE.Vector3; r: number; color: string; intensity: number; shadows: boolean }> = ({ dir, center, r, color, intensity, shadows }) => {
  const light = useMemo(() => {
    const l = new THREE.DirectionalLight(color, intensity);
    l.position.copy(center).addScaledVector(dir.clone().normalize(), r * 4);
    l.target.position.copy(center);
    l.castShadow = shadows;
    l.shadow.mapSize.set(2048, 2048);
    const cam = l.shadow.camera;
    cam.left = -r * 1.6;
    cam.right = r * 1.6;
    cam.top = r * 1.6;
    cam.bottom = -r * 1.6;
    cam.near = r * 0.5;
    cam.far = r * 8;
    l.shadow.bias = -0.0004;
    l.shadow.normalBias = r * 0.004;
    cam.updateProjectionMatrix();
    return l;
  }, [dir, center, r, color, intensity, shadows]);
  return (
    <>
      <primitive object={light} />
      <primitive object={light.target} />
    </>
  );
};

export const Lights: React.FC<{ preset: string; center: THREE.Vector3; r: number; shadows: boolean }> = ({ preset, center, r, shadows }) => {
  const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
  const dirs = useMemo(() => ({ key: v(3, 5, 2), fill: v(-3, 2, -1), top: v(0.25, 6, 0.4), dk: v(4, 2.2, 1.2), rim: v(-2, 3, -4) }), []);
  if (preset === "top")
    return (
      <>
        <hemisphereLight args={["#ffffff", "#b9b1a6", 0.75]} />
        <Key dir={dirs.top} center={center} r={r} color="#ffffff" intensity={1.9} shadows={shadows} />
      </>
    );
  if (preset === "dramatic")
    return (
      <>
        <hemisphereLight args={["#d5dcff", "#3a3430", 0.18]} />
        <ambientLight intensity={0.06} />
        <Key dir={dirs.dk} center={center} r={r} color="#ffe1c2" intensity={2.6} shadows={shadows} />
        <directionalLight position={center.clone().addScaledVector(dirs.rim.clone().normalize(), r * 4)} color="#bcd2ff" intensity={1.1} />
      </>
    );
  return (
    <>
      <hemisphereLight args={["#ffffff", "#bbb3a8", 0.65]} />
      <ambientLight intensity={0.22} />
      <Key dir={dirs.key} center={center} r={r} color="#ffffff" intensity={1.7} shadows={shadows} />
      <directionalLight position={center.clone().addScaledVector(dirs.fill.clone().normalize(), r * 4)} intensity={0.45} />
    </>
  );
};

export const Ground: React.FC<{ kind: string; size: number; color: string; grid: boolean; shadows: boolean }> = ({ kind, size, color, grid, shadows }) => {
  if (kind === "none") return null;
  const lines = new THREE.Color(color).multiplyScalar(0.8).getStyle();
  const major = new THREE.Color(color).multiplyScalar(0.68).getStyle();
  const div = Math.max(2, Math.round(size / 0.1)); // 10 cm squares
  return (
    <>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow={shadows} position={[0, -0.0005, 0]}>
        <planeGeometry args={[size, size]} />
        <meshStandardMaterial color={color} roughness={1} metalness={0} />
      </mesh>
      {grid && kind === "grid" && <gridHelper args={[size, div, major, lines]} position={[0, 0.0005, 0]} />}
    </>
  );
};
