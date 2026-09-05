import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import LandingPage from "./LandingPage";
import "./styles.css";

const App = lazy(() => import("./App"));
const AuthPage = lazy(() => import("./AuthPage"));
const BacktestPrototype = lazy(() => import("./BacktestPrototype"));
const RendererGallery = lazy(() => import("./RendererGallery"));

const pathname = window.location.pathname.replace(/\/+$/, "") || "/";
const Root = pathname === "/"
  ? LandingPage
  : pathname === "/login" || pathname === "/register"
    ? AuthPage
    : pathname.endsWith("/renderers")
      ? RendererGallery
      : pathname.endsWith("/backtests")
        ? BacktestPrototype
        : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Suspense fallback={<div role="status" aria-live="polite">正在打开 Fin Agent…</div>}>
      <Root />
    </Suspense>
  </StrictMode>,
);
