import "@fontsource-variable/newsreader";
import "@fontsource-variable/newsreader/wght-italic.css";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./estilos.css";

import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { Dictado } from "./Dictado";
import { Panel } from "./Panel";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {window.location.hash === "#dictado" ? (
      <Dictado />
    ) : window.location.hash !== "#panel" ? (
      <App />
    ) : "__TAURI_INTERNALS__" in window ? (
      <Panel />
    ) : (
      <div className="h-[430px] w-[340px]">
        <Panel />
      </div>
    )}
  </React.StrictMode>,
);
