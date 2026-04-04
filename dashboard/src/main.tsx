import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

// Always start at top on page load / refresh
if (window.history.scrollRestoration) {
  window.history.scrollRestoration = "manual";
}
window.scrollTo(0, 0);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
