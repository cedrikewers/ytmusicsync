import { useEffect, useRef } from "react";

export default function LogPanel({ logs, onClear }) {
  const ref = useRef(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [logs]);

  return (
    <section className="card">
      <div className="row">
        <h2 className="grow">Live log</h2>
        <button className="btn" onClick={onClear}>Clear</button>
      </div>
      <pre className="log" ref={ref}>
        {logs.length ? logs.join("\n") : "Waiting for log output..."}
      </pre>
    </section>
  );
}
