import { useState } from "react";
import { useSocket } from "./useSocket";
import Downloads from "./components/Downloads.jsx";
import Artists from "./components/Artists.jsx";
import Pending from "./components/Pending.jsx";
import LogPanel from "./components/LogPanel.jsx";

const TABS = [
  ["downloads", "Download"],
  ["pending", "Recent Downloads"],
  ["artists", "Artists"],
  ["logs", "Log"],
];

export default function App() {
  const [tab, setTab] = useState("downloads");
  const socket = useSocket();

  return (
    <div className="app">
      <header className="topbar">
        <h1>YT Music Bridge</h1>
        <nav>
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {tab === "downloads" && <Downloads progress={socket.progress} titles={socket.titles} counts={socket.counts} setProgress={socket.setProgress} />}
        {tab === "pending" && <Pending />}
        {tab === "artists" && <Artists />}
        {tab === "logs" && <LogPanel logs={socket.logs} onClear={socket.clearLogs} />}
      </main>
    </div>
  );
}
