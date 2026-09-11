import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import LandingPage from "./LandingPage";
import { stripAppBase } from "./appPath";
import "./styles.css";

const App = lazy(() => import("./App"));
const AuthPage = lazy(() => import("./AuthPage"));
const BacktestPrototype = lazy(() => import("./BacktestPrototype"));
const RendererGallery = lazy(() => import("./RendererGallery"));
const SkillStudio = lazy(() => import("./SkillStudio"));

const pathname = stripAppBase(window.location.pathname);
const Root = pathname === "/"
  ? LandingPage
  : pathname === "/login" || pathname === "/register"
    ? AuthPage
    : pathname === "/skills/studio" || pathname.startsWith("/skills/studio/")
      ? SkillStudio
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
