// Load every GLB of a shot before the canvas mounts, holding the render with delayRender, so
// the very first frame already has the models. One promise per URL for the whole page
// (several scenes / objects with the same asset share it).
import { useEffect, useState } from "react";
import { cancelRender, continueRender, delayRender, staticFile } from "remotion";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

const cache = new Map<string, Promise<GLTF>>();

export function loadGlb(path: string): Promise<GLTF> {
  const url = staticFile(path);
  let p = cache.get(url);
  if (!p) {
    p = new GLTFLoader().loadAsync(url);
    cache.set(url, p);
  }
  return p;
}

/** path -> GLTF once every file has loaded; null before. */
export function useGlbs(paths: string[]): Record<string, GLTF> | null {
  const key = [...new Set(paths)].sort().join("|");
  const [state, setState] = useState<Record<string, GLTF> | null>(null);
  const [handle] = useState(() => delayRender(`previs: loading ${key}`, { timeoutInMilliseconds: 120000 }));
  useEffect(() => {
    let live = true;
    let done = false;
    const list = key ? key.split("|") : [];
    Promise.all(list.map((p) => loadGlb(p).then((g) => [p, g] as const)))
      .then((xs) => {
        if (!live) return;
        setState(Object.fromEntries(xs));
        done = true;
        continueRender(handle);
      })
      .catch((e) => cancelRender(e));
    return () => {
      live = false;
      if (!done) continueRender(handle); // unmounted while loading (e.g. scrubbing in the studio)
    };
  }, [key, handle]);
  return state;
}
