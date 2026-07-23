import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import BacktestPrototype from "./BacktestPrototype";
import RendererGallery from "./RendererGallery";
import "./styles.css";

const pathname = window.location.pathname;
const Root = pathname.endsWith("/renderers")
  ? RendererGallery
  : pathname.endsWith("/backtests")
    ? BacktestPrototype
    : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
