import { useState } from "react";
import { api } from "../api";

export default function Downloads({ progress, titles, counts, setProgress }) {
  const [text, setText] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    const urls = text.split("\n").map((u) => u.trim()).filter(Boolean);
    if (!urls.length) return;
    setProgress((p) => ({ ...p, ...Object.fromEntries(urls.map((u) => [u, p[u] ?? "Waiting..."])) }));
    await api("/queue", { method: "POST", body: { urls } });
    setText("");
  };

  return (
    <section className="card">
      <h2>Download</h2>
      <form onSubmit={submit}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"https://music.youtube.com/watch?v=...\nhttps://music.youtube.com/browse/MPREb_..."}
          rows={5}
        />
        <button className="btn primary" type="submit">Download</button>
      </form>
      <h3>Progress</h3>
      <ul className="list">
        {Object.entries(progress).map(([url, status]) => (
          <li key={url} className="dl">
            <div className="row">
              <span className="grow ellipsis" title={url}>{titles[url] || url}</span>
              <span className={`badge ${status === "Completed" ? "ok" : status === "Error" ? "err" : ""}`}>{status}</span>
            </div>
            {counts[url] && (
              <div className="row">
                <div className="bar grow">
                  <div style={{ width: `${(counts[url].done / counts[url].total) * 100}%` }} />
                </div>
                <span className="muted small-text">{counts[url].done}/{counts[url].total}</span>
              </div>
            )}
          </li>
        ))}
        {!Object.keys(progress).length && <li className="muted">Nothing yet.</li>}
      </ul>
    </section>
  );
}
