import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import RendererGallery from "./RendererGallery";
import "./styles.css";

const Root = window.location.pathname.endsWith("/renderers") ? RendererGallery : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
