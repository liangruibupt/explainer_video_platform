import React, { useLayoutEffect } from "react";
import { ThreeCanvas } from "@remotion/three";
import { useThree } from "@react-three/fiber";
import { useCurrentFrame, useVideoConfig, interpolate, Easing } from "remotion";
import * as THREE from "three";

const CLAY = "#c9c4bb";
const ACCENT = ["#d98c5f", "#6f9fd8", "#8cc084", "#d8c46f"];

const Camera: React.FC = () => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const t = interpolate(frame, [0, durationInFrames - 1], [0, 1], {
    easing: Easing.inOut(Easing.cubic),
  });
  const ang = -0.6 + t * 1.4;
  const r = 3.2 - t * 1.2;
  const y = 1.6 - t * 0.5;
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const size = useThree((s) => s.size);
  useLayoutEffect(() => {
    camera.fov = 35;
    camera.aspect = size.width / size.height;
    camera.near = 0.05;
    camera.far = 100;
    camera.position.set(Math.sin(ang) * r, y, Math.cos(ang) * r);
    camera.lookAt(0, 0.6, 0);
    camera.updateProjectionMatrix();
  }, [camera, size, ang, r, y]);
  return null;
};

// One cylinder block with piston, rod, crank and valves (~30 meshes).
const Engine: React.FC<{ x: number; phase: number }> = ({ x, phase }) => {
  const frame = useCurrentFrame();
  const a = (frame / 30) * Math.PI * 2 * 0.5 + phase;
  const crankR = 0.09;
  const pistonY = 0.55 + crankR * Math.cos(a) + 0.02;
  return (
    <group position={[x, 0, 0]}>
      {/* crankcase */}
      <mesh position={[0, 0.2, 0]} castShadow receiveShadow>
        <boxGeometry args={[0.6, 0.4, 0.5]} />
        <meshStandardMaterial color={CLAY} roughness={0.9} />
      </mesh>
      {/* cylinder walls (4 slabs, cut-away front) */}
      {[
        [-0.17, 0.75, 0, 0.04, 0.7, 0.36],
        [0.17, 0.75, 0, 0.04, 0.7, 0.36],
        [0, 0.75, -0.17, 0.3, 0.7, 0.04],
      ].map((b, i) => (
        <mesh key={i} position={[b[0], b[1], b[2]]} castShadow receiveShadow>
          <boxGeometry args={[b[3], b[4], b[5]]} />
          <meshStandardMaterial color={CLAY} roughness={0.9} transparent opacity={0.85} />
        </mesh>
      ))}
      {/* head */}
      <mesh position={[0, 1.13, 0]} castShadow>
        <boxGeometry args={[0.4, 0.08, 0.4]} />
        <meshStandardMaterial color={CLAY} roughness={0.9} />
      </mesh>
      {/* valves */}
      {[-0.07, 0.07].map((vx, i) => (
        <group key={i} position={[vx, 1.08 - (i === 0 ? Math.max(0, Math.sin(a)) : Math.max(0, -Math.sin(a))) * 0.03, 0]}>
          <mesh castShadow>
            <cylinderGeometry args={[0.01, 0.01, 0.2, 12]} />
            <meshStandardMaterial color={ACCENT[i]} />
          </mesh>
          <mesh position={[0, -0.1, 0]} castShadow>
            <cylinderGeometry args={[0.04, 0.04, 0.012, 24]} />
            <meshStandardMaterial color={ACCENT[i]} />
          </mesh>
        </group>
      ))}
      {/* piston */}
      <mesh position={[0, pistonY + 0.3, 0]} castShadow>
        <cylinderGeometry args={[0.145, 0.145, 0.14, 32]} />
        <meshStandardMaterial color={ACCENT[2]} roughness={0.6} />
      </mesh>
      {/* rod */}
      <mesh
        position={[(crankR * Math.sin(a)) / 2, (0.4 + crankR * Math.cos(a) + pistonY + 0.3) / 2, 0.0]}
        rotation={[0, 0, -Math.atan2(crankR * Math.sin(a), pistonY + 0.3 - 0.4)]}
        castShadow
      >
        <boxGeometry args={[0.04, 0.5, 0.03]} />
        <meshStandardMaterial color={ACCENT[3]} />
      </mesh>
      {/* crank disc + pin */}
      <group position={[0, 0.4, 0.05]} rotation={[0, 0, -a]}>
        <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[0.13, 0.13, 0.04, 40]} />
          <meshStandardMaterial color={CLAY} roughness={0.8} />
        </mesh>
        <mesh position={[0, crankR, 0.03]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.02, 0.02, 0.06, 16]} />
          <meshStandardMaterial color={ACCENT[0]} />
        </mesh>
      </group>
      {/* bolts ring on head: 12 small cylinders */}
      {Array.from({ length: 12 }).map((_, i) => (
        <mesh key={i} position={[Math.cos((i / 12) * Math.PI * 2) * 0.17, 1.18, Math.sin((i / 12) * Math.PI * 2) * 0.17]}>
          <cylinderGeometry args={[0.012, 0.012, 0.03, 10]} />
          <meshStandardMaterial color={CLAY} />
        </mesh>
      ))}
    </group>
  );
};

export const Bench: React.FC<{ shadows: boolean; copies: number }> = ({ shadows, copies }) => {
  const { width, height } = useVideoConfig();
  const xs = Array.from({ length: copies }, (_, i) => (i - (copies - 1) / 2) * 0.9);
  return (
    <ThreeCanvas width={width} height={height} shadows={shadows} style={{ background: "#e9e6e1" }}>
      <Camera />
      <ambientLight intensity={0.6} />
      <hemisphereLight args={["#ffffff", "#bbb3a8", 0.6]} />
      <directionalLight
        position={[3, 5, 2]}
        intensity={1.6}
        castShadow={shadows}
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
      />
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[20, 20]} />
        <meshStandardMaterial color="#dedad3" roughness={1} />
      </mesh>
      <gridHelper args={[20, 40, "#b5aea4", "#cbc5bc"]} position={[0, 0.001, 0]} />
      {xs.map((x, i) => (
        <Engine key={i} x={x} phase={i * 1.3} />
      ))}
    </ThreeCanvas>
  );
};
