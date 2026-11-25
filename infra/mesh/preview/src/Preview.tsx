import React, { useEffect, useLayoutEffect, useState } from "react";
import { ThreeCanvas } from "@remotion/three";
import { useThree } from "@react-three/fiber";
import { continueRender, delayRender, staticFile, useVideoConfig } from "remotion";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

// Still of one GLB: fitted to view, camera at azimuth `az` (degrees, from +Z toward +X) and
// elevation 20 deg. clay=true replaces every material with the previs clay colour (the way the
// previs renderer shows image-to-3D assets); clay=false keeps the GLB's own (Paint) texture.
export type PreviewProps = { glb: string; az: number; clay: boolean };

const CLAY = "#c9c4bb";

const Model: React.FC<PreviewProps> = ({ glb, az, clay }) => {
  const [obj, setObj] = useState<THREE.Object3D | null>(null);
  const [handle] = useState(() => delayRender("load glb"));
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const size = useThree((s) => s.size);
  const advance = useThree((s) => s.advance);
  useEffect(() => {
    new GLTFLoader().load(
      staticFile(glb),
      (g) => {
        const root = g.scene;
        root.traverse((o) => {
          const m = o as THREE.Mesh;
          if (!m.isMesh) return;
          m.castShadow = m.receiveShadow = true;
          if (clay) m.material = new THREE.MeshStandardMaterial({ color: CLAY, roughness: 0.85 });
          else (m.material as THREE.MeshStandardMaterial).roughness = 0.8;
          m.geometry.computeVertexNormals();
        });
        // ground the model at y = 0, centred on x / z
        const b = new THREE.Box3().setFromObject(root);
        const c = b.getCenter(new THREE.Vector3());
        root.position.set(-c.x, -b.min.y, -c.z);
        setObj(root);
      },
      undefined,
      (e) => {
        console.error(e);
        continueRender(handle);
      },
    );
  }, [glb, clay, handle]);
  useLayoutEffect(() => {
    if (!obj) return;
    const b = new THREE.Box3().setFromObject(obj);
    const s = b.getSize(new THREE.Vector3());
    const r = Math.max(s.x, s.y, s.z);
    const a = (az * Math.PI) / 180, el = (20 * Math.PI) / 180, d = r * 2.4;
    camera.fov = 30;
    camera.aspect = size.width / size.height;
    camera.near = r / 100;
    camera.far = r * 100;
    camera.position.set(Math.sin(a) * Math.cos(el) * d, s.y / 2 + Math.sin(el) * d, Math.cos(a) * Math.cos(el) * d);
    camera.lookAt(0, s.y / 2, 0);
    camera.updateProjectionMatrix();
    // ThreeCanvas only draws on frame changes; the model arrived after the first draw
    advance(performance.now());
    continueRender(handle);
  }, [obj, camera, size, az, handle, advance]);
  return obj ? <primitive object={obj} /> : null;
};

export const Preview: React.FC<PreviewProps> = (props) => {
  const { width, height } = useVideoConfig();
  return (
    <div style={{ width, height, background: "#eceae6" }}>
      <ThreeCanvas width={width} height={height} shadows gl={{ preserveDrawingBuffer: true }}>
        <hemisphereLight args={["#ffffff", "#b8b2a8", 0.9]} />
        <directionalLight position={[3, 5, 4]} intensity={2.2} castShadow shadow-mapSize={[2048, 2048]} />
        <directionalLight position={[-4, 2, -2]} intensity={0.6} />
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[50, 50]} />
          <shadowMaterial opacity={0.25} />
        </mesh>
        <Model {...props} />
      </ThreeCanvas>
    </div>
  );
};
