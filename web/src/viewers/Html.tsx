import { filePath, type ViewerProps } from "./registry";

export default function Html({ file, base }: ViewerProps) {
  return (
    <div className="html-viewer">
      <iframe
        className="html-viewer-frame"
        title={file.name}
        sandbox=""
        src={filePath(base, file.id) + "/preview/html"}
      />
    </div>
  );
}
