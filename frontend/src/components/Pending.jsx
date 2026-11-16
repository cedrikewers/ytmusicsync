import { useEffect, useState } from "react";
import { api, PREFIX } from "../api";

function Cover({ album }) {
  return album.hasCover ? (
    <img className="cover" src={`${PREFIX}/pending/cover/${encodeURIComponent(album.dir)}`} alt="" loading="lazy" />
  ) : (
    <div className="cover placeholder" />
  );
}

function ConfirmModal({ message, onConfirm, onCancel }) {
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="card modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <p>{message}</p>
        <div className="row end">
          <button className="btn" onClick={onCancel} autoFocus>Cancel</button>
          <button className="btn primary" onClick={onConfirm}>Delete</button>
        </div>
      </div>
    </div>
  );
}

function Song({ album, song, reload, askDelete }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const replace = async (e) => {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api("/pending/replace", { method: "POST", body: { dir: album.dir, file: song.file, url: url.trim() } });
      reload();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const done = async () => {
    await api("/pending/dismiss", { method: "POST", body: { dir: album.dir, file: song.file } });
    reload();
  };

  return (
    <li className="song">
      <div className="row">
        <span className="track">{song.track || "-"}</span>
        <div className="grow">
          <div>{song.title}</div>
          <div className="muted small-text">{song.artists.join(", ")}</div>
        </div>
        <button className="btn small" onClick={done} disabled={busy}>Done</button>
        <button className="btn small danger" disabled={busy} onClick={() => askDelete({ dir: album.dir, file: song.file, label: `song "${song.title}"` })}>Delete</button>
      </div>
      <form className="row" onSubmit={replace}>
        <input
          className="grow"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Replacement YouTube link"
          disabled={busy}
        />
        <button className="btn small primary" type="submit" disabled={busy || !url.trim()}>
          {busy ? "Replacing..." : "Replace audio"}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
    </li>
  );
}

export default function Pending() {
  const [albums, setAlbums] = useState(null);
  const [toDelete, setToDelete] = useState(null);

  const reload = () => api("/pending").then(setAlbums).catch(() => setAlbums([]));
  useEffect(() => { reload(); }, []);

  const dismiss = async (dir) => {
    if (!dir && !confirm("Mark all pending songs as done?")) return;
    await api("/pending/dismiss", { method: "POST", body: dir ? { dir } : {} });
    reload();
  };

  const confirmDelete = async () => {
    const { dir, file } = toDelete;
    setToDelete(null);
    await api("/pending/delete", { method: "POST", body: { dir, file } });
    reload();
  };

  if (albums === null) return <p className="muted">Loading...</p>;

  return (
    <section>
      <div className="row page-head">
        <h2 className="grow">Recent Downloads ({albums.reduce((n, a) => n + a.songs.length, 0)} songs)</h2>
        {albums.length > 0 && <button className="btn" onClick={() => dismiss(null)}>Mark all as done</button>}
      </div>
      {!albums.length && <p className="muted">Nothing to review.</p>}
      <div className="grid">
        {albums.map((album) => (
          <article key={album.dir} className="card album">
            <div className="album-head">
              <Cover album={album} />
              <div className="grow">
                <h3>{album.album}</h3>
                <div className="muted">{album.artist}{album.date ? ` · ${album.date.slice(0, 4)}` : ""}</div>
                <button className="btn small" onClick={() => dismiss(album.dir)}>Mark album as done</button>
                <button className="btn small danger" onClick={() => setToDelete({ dir: album.dir, label: `album "${album.album}" and its folder` })}>Delete album</button>
              </div>
            </div>
            <ul className="list">
              {album.songs.map((s) => (
                <Song key={s.file} album={album} song={s} reload={reload} askDelete={setToDelete} />
              ))}
            </ul>
          </article>
        ))}
      </div>
      {toDelete && (
        <ConfirmModal
          message={`Permanently delete ${toDelete.label} from disk?`}
          onConfirm={confirmDelete}
          onCancel={() => setToDelete(null)}
        />
      )}
    </section>
  );
}
