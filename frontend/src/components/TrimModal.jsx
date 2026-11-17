import { useEffect, useRef, useState } from "react";
import { api, PREFIX } from "../api";

const fmt = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, "0")}`;

export default function TrimModal({ song, onClose, onDone }) {
  const [peaks, setPeaks] = useState(null);
  const [duration, setDuration] = useState(0);
  const [range, setRange] = useState([0, 0]);
  const [pos, setPos] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const canvas = useRef(null);
  const audio = useRef(null);
  const src = `${PREFIX}/pending/audio?${new URLSearchParams({ dir: song.dir, file: song.file })}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const wave = await api(`/pending/waveform?${new URLSearchParams({ dir: song.dir, file: song.file })}`);
        if (cancelled) return;
        setPeaks(wave.peaks);
        setDuration(wave.duration);
        setRange([0, wave.duration]);
      } catch (e) {
        if (!cancelled) setError(e.message || "Could not load waveform");
      }
    })();
    return () => { cancelled = true; };
  }, [song.dir, song.file]);

  useEffect(() => {
    const c = canvas.current;
    if (!c || !peaks || !duration) return;
    const ctx = c.getContext("2d");
    const { width, height } = c;
    ctx.clearRect(0, 0, width, height);
    const n = peaks.length;
    const bw = width / n;
    for (let i = 0; i < n; i++) {
      const t = (i / n) * duration;
      const inside = t >= range[0] && t <= range[1];
      ctx.fillStyle = inside ? "#ff3b4e" : "#3a4050";
      const h = Math.max(1, peaks[i] * height);
      ctx.fillRect(i * bw, (height - h) / 2, Math.max(1, bw - 1), h);
    }
    ctx.fillStyle = "#fff";
    ctx.fillRect((pos / duration) * width, 0, 2, height);
  }, [peaks, duration, range, pos]);

  const stopAt = (el) => {
    setPos(el.currentTime);
    if (el.currentTime >= range[1]) {
      el.pause();
      el.currentTime = range[0];
    }
  };

  const play = () => {
    const el = audio.current;
    if (el.currentTime < range[0] || el.currentTime >= range[1]) el.currentTime = range[0];
    el.play();
  };

  const move = (idx) => (e) => {
    const v = Number(e.target.value);
    const next = idx === 0 ? [Math.min(v, range[1] - 0.1), range[1]] : [range[0], Math.max(v, range[0] + 0.1)];
    setRange(next);
    audio.current.currentTime = idx === 0 ? next[0] : Math.max(next[0], next[1] - 2);
  };

  const apply = async () => {
    setSaving(true);
    setError("");
    try {
      await api("/pending/trim", { method: "POST", body: { dir: song.dir, file: song.file, start: range[0], end: range[1] } });
      onDone();
    } catch (e) {
      setError(e.message);
      setSaving(false);
    }
  };

  const unchanged = range[0] <= 0.01 && range[1] >= duration - 0.01;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal wide" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>Trim: {song.title}</h3>
        {!peaks && !error && <p className="muted">Rendering waveform...</p>}
        {peaks && (
          <>
            <canvas ref={canvas} width={1200} height={140} className="wave" />
            <label className="slider">
              Start {fmt(range[0])}
              <input type="range" min="0" max={duration} step="0.05" value={range[0]} onChange={move(0)} />
            </label>
            <label className="slider">
              End {fmt(range[1])}
              <input type="range" min="0" max={duration} step="0.05" value={range[1]} onChange={move(1)} />
            </label>
            <audio ref={audio} src={src} onTimeUpdate={(e) => stopAt(e.target)} controls className="player" />
            <div className="row">
              <button className="btn small" onClick={play}>Play selection</button>
              <span className="muted small-text">New length {fmt(range[1] - range[0])}</span>
            </div>
          </>
        )}
        {error && <div className="error">{error}</div>}
        <div className="row end">
          <button className="btn" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn primary" onClick={apply} disabled={!peaks || saving || unchanged}>{saving ? "Trimming..." : "Apply trim"}</button>
        </div>
      </div>
    </div>
  );
}
