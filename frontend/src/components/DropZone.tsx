import { useCallback, useRef, useState } from "react";

export function DropZone({
  disabled,
  onFile,
}: {
  disabled: boolean;
  onFile: (file: File) => void;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const take = useCallback(
    (files: FileList | null) => {
      const f = files?.[0];
      if (f) onFile(f);
    },
    [onFile],
  );

  return (
    <div
      className={`drop${over ? " over" : ""}`}
      onClick={() => !disabled && input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) take(e.dataTransfer.files);
      }}
    >
      <div className="icon">↑</div>
      <p>
        <strong>영상을 여기에 끌어다 놓거나</strong> 클릭해서 선택하세요
      </p>
      <p className="muted">자동으로 49.9MB 이하 .webm 으로 압축됩니다</p>
      <input
        ref={input}
        type="file"
        accept="video/*"
        hidden
        onChange={(e) => take(e.target.files)}
      />
    </div>
  );
}
