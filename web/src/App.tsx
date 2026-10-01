import { useEffect } from "react";
import { Route, Routes, useNavigate } from "react-router-dom";
import { setUnauthorizedHandler } from "./api";
import Nav from "./components/Nav";
import SearchPage from "./pages/SearchPage";
import SearchDetail from "./pages/SearchDetail";
import History from "./pages/History";
import Sites from "./pages/Sites";
import SettingsPage from "./pages/Settings";

function NotFound() {
  return (
    <div className="page">
      <div className="card empty">
        <h2>404</h2>
        <p className="muted">No such route.</p>
      </div>
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (!window.location.pathname.startsWith("/settings")) {
        navigate("/settings?auth=required");
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate]);

  return (
    <div className="app">
      <div className="bg-grid" aria-hidden />
      <Nav />
      <main>
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/search/:id" element={<SearchDetail />} />
          <Route path="/history" element={<History />} />
          <Route path="/sites" element={<Sites />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
