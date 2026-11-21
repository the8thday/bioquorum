import { useEffect, useState } from "react";
import { errorMessage, request } from "./api";

export function useProjectRead<T>(path: string | null, revision = 0) {
  const [value, setValue] = useState<T>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setValue(undefined);
    setError("");
    if (!path) return;
    request<T>(path, undefined, controller.signal).then(data => {
      if (!controller.signal.aborted) setValue(data);
    }).catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [path, revision]);
  return {value, error};
}
