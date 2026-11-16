import { useEffect, useRef, useState } from "react";
import { PREFIX } from "./api";

const MAX_LOG_LINES = 1000;

// Shared WebSocket: collects log lines and per-URL download progress.
export function useSocket() {
  const [logs, setLogs] = useState([]);
  const [progress, setProgress] = useState({});
  const [titles, setTitles] = useState({});
  const wsRef = useRef(null);

  useEffect(() => {
    let closed = false;
    let timer;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}${PREFIX}/ws`);
      wsRef.current = ws;
      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        if (msg.event === "log") {
          setLogs((prev) => [...prev, msg.line].slice(-MAX_LOG_LINES));
        } else if (msg.event === "progress") {
          setProgress((prev) => ({ ...prev, [msg.url]: msg.status }));
          if (msg.title) setTitles((prev) => ({ ...prev, [msg.url]: msg.title }));
        }
      };
      ws.onclose = () => {
        if (!closed) timer = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
    };
    connect();

    return () => {
      closed = true;
      clearTimeout(timer);
      wsRef.current?.close();
    };
  }, []);

  return { logs, clearLogs: () => setLogs([]), progress, titles, setProgress };
}
