import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, request } from "./api";
import type { Snapshot } from "./types";

// The session view is keyed by session ID. Its stream, cursor and transient UI
// are discarded together; reconnects always start from a recorded sequence.
export function useRecord(base: string) {
  const [record, setRecord] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState("");
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const serial = useRef(0);
  const refresh = useCallback(async () => {
    const id = ++serial.current;
    const data = await request<Snapshot>(base);
    if (id === serial.current) { setRecord(data); setError(""); }
    return data;
  }, [base]);

  useEffect(() => {
    if (!base) return;
    let stream: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let generation = 0;
    function disconnect() {
      ++generation;
      ++serial.current;
      clearTimeout(timer);
      stream?.close();
    }
    async function connect() {
      disconnect();
      const version = generation;
      setConnection("Connecting…"); setProgress("");
      try {
        const snapshot = await refresh();
        if (version !== generation) return;
        stream = new EventSource(base + "/events?after=" + snapshot.last_seq);
        stream.onopen = () => setConnection("Connected");
        stream.onerror = () => setConnection("Reconnecting…");
        stream.addEventListener("change", () => {
          clearTimeout(timer);
          timer = setTimeout(() => refresh().catch(e => setError(errorMessage(e))), 100);
        });
        stream.addEventListener("progress", event => {
          const frame = JSON.parse(event.data);
          setProgress(frame ? "Generating · " + frame[1].toLocaleString() + " characters" : "");
        });
      } catch (e) { if (version === generation) setError(errorMessage(e)); }
    }
    function restore(event: PageTransitionEvent) { if (event.persisted) void connect(); }
    void connect();
    window.addEventListener("pagehide", disconnect);
    window.addEventListener("pageshow", restore);
    return () => {
      disconnect();
      window.removeEventListener("pagehide", disconnect);
      window.removeEventListener("pageshow", restore);
    };
  }, [base, refresh]);
  return {record, refresh, progress, connection, error};
}
