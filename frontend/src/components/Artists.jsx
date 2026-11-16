import { useEffect, useState } from "react";
import { api } from "../api";

const PAGE = 20;

export default function Artists() {
  const [artists, setArtists] = useState([]);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(PAGE);
  // null = showing followed artists; otherwise YT Music results ("loading" or array)
  const [results, setResults] = useState(null);
  const [note, setNote] = useState("");

  const load = () => api("/artists").then(setArtists).catch(() => {});
  useEffect(() => { load(); }, []);

  const q = query.trim();
  const matches = artists.filter((a) => a.name.toLowerCase().includes(q.toLowerCase()));
  const followed = new Set(artists.map((a) => a.channelId));

  const onChange = (e) => {
    setQuery(e.target.value);
    setResults(null);
    setLimit(PAGE);
  };

  const searchYt = async () => {
    setResults("loading");
    try {
      setResults(await api(`/artists/search?q=${encodeURIComponent(q)}`));
    } catch {
      setResults([]);
    }
  };

  const add = async (r) => {
    await api("/artists", { method: "POST", body: { name: r.name, channelId: r.channelId } });
    load();
  };

  const remove = async (a) => {
    await api(`/artists/${a.channelId}`, { method: "DELETE" });
    load();
  };

  const checkNow = async () => {
    await api("/check-now", { method: "POST" });
    setNote("Check started. See the log for details.");
  };

  return (
    <section className="card">
      <div className="row">
        <h2 className="grow">Followed artists <span className="muted">({artists.length})</span></h2>
        <button className="btn" onClick={checkNow}>Check for new releases now</button>
      </div>
      {note && <p className="muted">{note}</p>}

      <input value={query} onChange={onChange} placeholder="Search your artists or find new ones..." />

      <ul className="list">
        {q && results === null && (
          <li className="row action" onClick={searchYt}>
            <span className="grow">Search "{q}" on YT Music to add new artists</span>
            <span className="muted">&rarr;</span>
          </li>
        )}

        {results === "loading" && <li className="muted">Searching...</li>}

        {Array.isArray(results)
          ? results.map((r) => (
              <li key={r.channelId} className="row">
                <span className="grow">{r.name}</span>
                {followed.has(r.channelId) ? (
                  <span className="muted">Followed</span>
                ) : (
                  <button className="btn small" onClick={() => add(r)}>Add</button>
                )}
              </li>
            ))
          : matches.slice(0, limit).map((a) => (
              <li key={a.channelId} className="row">
                <span className="grow">{a.name}</span>
                <button className="btn small danger" onClick={() => remove(a)}>Remove</button>
              </li>
            ))}

        {Array.isArray(results) && !results.length && <li className="muted">No results.</li>}
      </ul>

      {results === null && matches.length > limit && (
        <button className="btn block" onClick={() => setLimit(limit + PAGE)}>
          Show more ({matches.length - limit} remaining)
        </button>
      )}
      {Array.isArray(results) && (
        <button className="btn block" onClick={() => setResults(null)}>Back to my artists</button>
      )}
    </section>
  );
}
