import { useState, useEffect } from "react";
import Login from "./components/Login";
import Layout from "./components/Layout";
import Dashboard from "./components/Dashboard";
import Inventory from "./components/Inventory";
import Alerts from "./components/Alerts";
import Locations from "./components/Locations";
import SettingsPage from "./components/SettingsPage";
import Loyalty from "./components/Loyalty";

function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [currentPage, setCurrentPage] = useState("dashboard");

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (token) {
      setIsLoggedIn(true);
    }
  }, []);

  const handleLogin = () => {
    setIsLoggedIn(true);
    setCurrentPage("dashboard");
  };

  const handleLogout = () => {
    localStorage.removeItem("token");
    setIsLoggedIn(false);
  };

  if (!isLoggedIn) {
    return <Login onLogin={handleLogin} />;
  }

  const renderPage = () => {
    switch (currentPage) {
      case "dashboard":
        return <Dashboard onNavigate={setCurrentPage} />;
      case "inventory":
        return <Inventory />;
      case "alerts":
        return <Alerts />;
      case "locations":
        return <Locations />;
      case "settings":
        return <SettingsPage />;
      case "loyalty":
        return <Loyalty />;
      default:
        return <Dashboard onNavigate={setCurrentPage} />;
    }
  };

  return (
    <Layout currentPage={currentPage} onNavigate={setCurrentPage} onLogout={handleLogout}>
      {renderPage()}
    </Layout>
  );
}

export default App;
