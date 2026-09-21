import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import AuthGate from "./AuthGate";
import BootGate from "./BootGate";
import "./index.css";

// BootGate wraps everything: on a stopped free-tier instance the app's own
// first requests would hang for a minute behind a blank page.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BootGate>
      <AuthGate />
    </BootGate>
  </StrictMode>,
);
