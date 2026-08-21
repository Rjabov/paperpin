import { useCallback, useState } from "react";

export function useZoom() {
  const [zoom, setZoom] = useState(1); // 1 = fit canvas width
  const zoomIn = useCallback(() => setZoom((z) => Math.min(5, +(z * 1.4).toFixed(2))), []);
  const zoomOut = useCallback(() => setZoom((z) => Math.max(0.4, +(z / 1.4).toFixed(2))), []);
  const reset = useCallback(() => setZoom(1), []);
  const onWheel = useCallback((e: React.WheelEvent) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setZoom((z) => Math.min(5, Math.max(0.4, +(z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)).toFixed(2))));
  }, []);
  return { zoom, zoomIn, zoomOut, reset, onWheel };
}

export function ZoomBar({ zoom, zoomIn, zoomOut, reset, sticky = true }: {
  zoom: number; zoomIn: () => void; zoomOut: () => void; reset: () => void;
  sticky?: boolean;
}) {
  const btn = "h-6 w-6 rounded-md border text-[12px] leading-none";
  const style = { background: "var(--panel)", borderColor: "var(--line)", color: "var(--text)" };
  return (
    <div className={`${sticky ? "sticky top-0 z-10 self-end rounded-lg border p-1" : ""} flex items-center gap-1`}
      style={sticky ? { background: "rgba(247,244,236,.94)", borderColor: "var(--line)" } : undefined}
      title="zoom the document (ctrl+scroll works too)">
      <button className={btn} style={style} onClick={zoomOut}>−</button>
      <button className="h-6 rounded-md border px-2 text-[10.5px]" style={style} onClick={reset}>
        {Math.round(zoom * 100)}%
      </button>
      <button className={btn} style={style} onClick={zoomIn}>+</button>
    </div>
  );
}
