import { useEffect, useState } from "react";
import { api, PREFIX } from "../api";
import TrimModal from "./TrimModal.jsx";

const q = (o) => new URLSearchParams(o).toString();

function Cover({ dir, file, has, rev, className = "cover" }) {
  if (!has) return <div className={`${className} placeholder`} />;
  const params = { dir, rev, ...(file ? { file } : {}) };
  return <img className={className} src={`${PREFIX}/pending/cover?${q(params)}`} alt="" loading="lazy" />;
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

function SongEditor({ album, song, onSaved, onCancel }) {
  const [f, setF] = useState({
    title: song.title,
    artists: song.artists.join(", "),
    album: song.album,
    album_artist: song.albumArtist,
    track: song.track || "",
    date: song.date,
  });
  const [cover, setCover] = useState(null);
  const [clearCover, setClearCover] = useState(false);
  const [preview, setPreview] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!cover) return setPreview(null);
    const u = URL.createObjectURL(cover);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [cover]);

  const set = (k) => (e) => setF((p) => ({ ...p, [k]: e.target.value }));
  const valid = f.title.trim() && f.album.trim() && f.album_artist.trim();

  const save = async (e) => {
    e.preventDefault();
    if (!valid) return;
    setSaving(true);
    setError("");
    const body = new FormData();
    body.append("dir", album.dir);
    body.append("file", song.file);
    body.append("fields", JSON.stringify({
      ...f,
      clear_cover: clearCover,
      artists: f.artists.split(",").map((a) => a.trim()).filter(Boolean),
    }));
    if (cover) body.append("cover", cover);
    try {
      const resp = await fetch(`${PREFIX}/pending/edit`, { method: "POST", body });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || "Save failed");
      onSaved();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <form className="editor" onSubmit={save}>
      <div className="editor-cover">
        {preview ? <img className="cover" src={preview} alt="" /> : <Cover dir={album.dir} file={song.file} has={song.hasCover && !clearCover} rev={song.rev} />}
        <label className="btn small">
          Upload cover
          <input type="file" accept="image/jpeg,image/png" hidden onChange={(e) => { setCover(e.target.files[0] || null); setClearCover(false); }} />
        </label>
        {(preview || (song.hasCover && !clearCover)) && (
          <button type="button" className="btn small danger" onClick={() => { setCover(null); setClearCover(true); }}>Remove cover</button>
        )}
      </div>
      <div className="fields">
        <label>Title *<input value={f.title} onChange={set("title")} required /></label>
        <label>Artists (comma separated)<input value={f.artists} onChange={set("artists")} /></label>
        <label>Album *<input value={f.album} onChange={set("album")} required /></label>
        <label>Album artist *<input value={f.album_artist} onChange={set("album_artist")} required /></label>
        <div className="row">
          <label className="grow">Track<input type="number" min="0" value={f.track} onChange={set("track")} /></label>
          <label className="grow">Date<input value={f.date} onChange={set("date")} placeholder="YYYY-MM-DD" /></label>
        </div>
        {error && <div className="error">{error}</div>}
        <div className="row end">
          <button type="button" className="btn small" onClick={onCancel} disabled={saving}>Cancel</button>
          <button type="submit" className="btn small primary" disabled={!valid || saving}>{saving ? "Saving..." : "Save"}</button>
        </div>
      </div>
    </form>
  );
}

function Song({ album, song, reload, askDelete, onTrim }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");

  const replace = async (e) => {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api("/pending/replace", { method: "POST", body: { dir: album.dir, file: song.file, url: url.trim() } });
      setUrl("");
      setBusy(false);
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

  if (editing) {
    return (
      <li className="song">
        <SongEditor album={album} song={song} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />
      </li>
    );
  }

  return (
    <li className="song">
      <div className="row">
        <span className="track">{song.track || "-"}</span>
        <div className="grow">
          <div>
            {song.title} {song.missing && <span className="badge err">Missing</span>}
            {song.staged && <span className="badge">Needs album</span>}
          </div>
          <div className="muted small-text">{song.artists.join(", ")}</div>
        </div>
        <button className="btn small" onClick={() => setEditing(true)} disabled={busy}>Edit</button>
        {!song.missing && <button className="btn small" onClick={() => onTrim({ dir: album.dir, file: song.file, title: song.title })} disabled={busy}>Trim</button>}
        {!song.staged && <button className="btn small" onClick={done} disabled={busy}>Done</button>}
        <button className="btn small danger" disabled={busy} onClick={() => askDelete({ dir: album.dir, file: song.file, label: `song "${song.title}"` })}>Delete</button>
      </div>
      <form className="row" onSubmit={replace}>
        <input
          className="grow"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={song.missing ? "YouTube link to download this song" : "Replacement YouTube link"}
          disabled={busy}
        />
        <button className="btn small primary" type="submit" disabled={busy || !url.trim()}>
          {busy ? "Working..." : song.missing ? "Download" : "Replace audio"}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
    </li>
  );
}

export default function Pending() {
  const [albums, setAlbums] = useState(null);
  const [toDelete, setToDelete] = useState(null);
  const [trimming, setTrimming] = useState(null);

  const reload = () => api("/pending").then(setAlbums).catch(() => setAlbums([]));
  useEffect(() => { reload(); }, []);

  const dismiss = async (dir) => {
    if (!dir && !confirm("Mark all pending songs as done? Songs without an album stay.")) return;
    await api("/pending/dismiss", { method: "POST", body: dir ? { dir } : {} });
    reload();
  };

  const confirmDelete = async () => {
    const { dir, file } = toDelete;
    setToDelete(null);
    try {
      await api("/pending/delete", { method: "POST", body: { dir, file } });
    } finally {
      reload();
    }
  };

  if (albums === null) return <p className="muted">Loading...</p>;

  return (
    <section>
      <div className="row page-head">
        <h2 className="grow">Recent Downloads ({albums.reduce((n, a) => n + a.songs.length, 0)} songs)</h2>
        {albums.some((a) => !a.staged) && <button className="btn" onClick={() => dismiss(null)}>Mark all as done</button>}
      </div>
      {!albums.length && <p className="muted">Nothing to review.</p>}
      <div className="grid">
        {albums.map((album) => (
          <article key={album.dir || "staged"} className="card album">
            <div className="album-head">
              {album.staged ? (
                <div className="grow">
                  <h3>No album yet</h3>
                  <div className="muted">Edit a song and set album and album artist to file it into the library.</div>
                </div>
              ) : (
                <>
                  <Cover dir={album.dir} has={album.hasCover} rev={album.rev} />
                  <div className="grow">
                    <h3>{album.album}</h3>
                    <div className="muted">{album.artist}{album.date ? ` · ${album.date.slice(0, 4)}` : ""}</div>
                    <button className="btn small" onClick={() => dismiss(album.dir)}>Mark album as done</button>
                    <button className="btn small danger" onClick={() => setToDelete({ dir: album.dir, label: `album "${album.album}" and its folder` })}>Delete album</button>
                  </div>
                </>
              )}
            </div>
            <ul className="list">
              {album.songs.map((s) => (
                <Song key={`${album.dir}/${s.file}`} album={album} song={s} reload={reload} askDelete={setToDelete} onTrim={setTrimming} />
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
      {trimming && <TrimModal song={trimming} onClose={() => setTrimming(null)} onDone={() => { setTrimming(null); reload(); }} />}
    </section>
  );
}
