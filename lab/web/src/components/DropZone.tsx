import { useCallback, useRef, useState } from "react";

export function DropZone({ onFiles, busy }: {
  onFiles: (files: File[]) => void;
  busy: boolean;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const drop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    onFiles(Array.from(e.dataTransfer.files));
  }, [onFiles]);

  const edge = over ? "var(--accent)" : "var(--line)";
  return (
    <div className="relative m-3 mt-4">
      {/* folder tab */}
      <div className="absolute -top-[9px] left-[12px] h-[10px] w-[52px] rounded-t-[3px] border border-b-0 border-dashed transition-colors"
        style={{ borderColor: edge, background: over ? "rgba(214,64,48,.06)" : "var(--panel2)" }} />
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={drop}
        onClick={() => input.current?.click()}
        className="cursor-pointer rounded-[3px] border border-dashed p-4 text-center text-[12.5px] transition-colors"
        style={{
          borderColor: edge,
          background: over ? "rgba(214,64,48,.06)" : "var(--panel2)",
          color: "var(--muted)",
        }}
      >
        <input
          ref={input} type="file" multiple hidden
          accept=".pdf,.jpg,.jpeg,.png,.webp,.tiff,.heic,.heif"
          onChange={(e) => { onFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }}
        />
        {busy ? "uploading…" : <>drop a <b style={{ color: "var(--text)" }}>PDF / photo</b> here<br />or click to browse</>}
      </div>
    </div>
  );
}
