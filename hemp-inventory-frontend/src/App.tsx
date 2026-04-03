import { useState, useEffect } from "react";
import Login from "./components/Login";
import EmployeeLogin from "./components/EmployeeLogin";
import EmployeeTimeClock from "./components/EmployeeTimeClock";
import EmployeeAccount from "./components/EmployeeAccount";
import Layout from "./components/Layout";
import Dashboard from "./components/Dashboard";
import Inventory from "./components/Inventory";
import Alerts from "./components/Alerts";
import Locations from "./components/Locations";
import SettingsPage from "./components/SettingsPage";
import Loyalty from "./components/Loyalty";
import TimeClock from "./components/TimeClock";
import SalesReport from "./components/SalesReport";
import OnlineOrders from "./components/OnlineOrders";
import Discounts from "./components/Discounts";
import ProductScraper from "./components/ProductScraper";

// Detect which domain we're on to separate login state
function getAppMode(): "timeclock" | "inventory" {
  const host = window.location.hostname.toLowerCase();
  if (host.startsWith("timeclock")) return "timeclock";
  return "inventory";
}

function getStorageKey(key: string): string {
  const mode = getAppMode();
  return `${mode}_${key}`;
}

function App() {
  const appMode = getAppMode();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [userRole, setUserRole] = useState<"admin" | "employee" | null>(null);
  const [currentPage, setCurrentPage] = useState("inventory");
  const [showAdminLogin, setShowAdminLogin] = useState(appMode === "inventory");
  const [employeePage, setEmployeePage] = useState<"timeclock" | "account">("timeclock");

  useEffect(() => {
    const token = localStorage.getItem(getStorageKey("token"));
    const role = localStorage.getItem(getStorageKey("userRole"));
    if (token) {
      // Also set the generic "token" key so the API interceptor can read it
      localStorage.setItem("token", token);
      setIsLoggedIn(true);
      setUserRole(role === "employee" ? "employee" : "admin");
    }
  }, []);

  const handleAdminLogin = () => {
    setIsLoggedIn(true);
    setUserRole("admin");
    // Store under domain-specific key + generic key for API interceptor
    const token = localStorage.getItem("token") || "";
    localStorage.setItem(getStorageKey("token"), token);
    localStorage.setItem(getStorageKey("userRole"), "admin");
    localStorage.setItem("userRole", "admin");
    setCurrentPage("inventory");
  };

  const handleEmployeeLogin = () => {
    setIsLoggedIn(true);
    setUserRole("employee");
    // Store under domain-specific key + generic key for API interceptor
    const token = localStorage.getItem("token") || "";
    localStorage.setItem(getStorageKey("token"), token);
    localStorage.setItem(getStorageKey("userRole"), "employee");
    setEmployeePage("timeclock");
  };

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("userRole");
    localStorage.removeItem(getStorageKey("token"));
    localStorage.removeItem(getStorageKey("userRole"));
    setIsLoggedIn(false);
    setUserRole(null);
    setShowAdminLogin(appMode === "inventory");
  };

  // Not logged in — show login screens
  if (!isLoggedIn) {
    if (showAdminLogin) {
      return (
        <div>
          <Login onLogin={handleAdminLogin} />
          <div className="fixed bottom-4 left-1/2 -translate-x-1/2">
            <button
              onClick={() => setShowAdminLogin(false)}
              className="text-sm text-gray-400 hover:text-gray-600 bg-white px-4 py-2 rounded-lg shadow"
            >
              Back to Employee Login
            </button>
          </div>
        </div>
      );
    }
    return (
      <EmployeeLogin
        onLogin={handleEmployeeLogin}
        onSwitchToAdmin={() => setShowAdminLogin(true)}
      />
    );
  }

  // Employee view — simplified layout with just time clock and account
  if (userRole === "employee") {
    return (
      <div className="min-h-screen bg-gray-50">
        {/* Employee Header */}
        <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
          <div className="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center">
                <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <span className="font-semibold text-gray-900">Hemp Dispensary</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setEmployeePage("timeclock")}
                className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  employeePage === "timeclock"
                    ? "bg-green-100 text-green-700"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                Time Clock
              </button>
              <button
                onClick={() => setEmployeePage("account")}
                className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  employeePage === "account"
                    ? "bg-green-100 text-green-700"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                My Account
              </button>
              <button
                onClick={handleLogout}
                className="ml-2 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 rounded-md transition-colors"
              >
                Sign Out
              </button>
            </div>
          </div>
        </header>

        {/* Employee Content */}
        <main className="max-w-4xl mx-auto px-4 py-6">
          {employeePage === "timeclock" ? <EmployeeTimeClock /> : <EmployeeAccount />}
        </main>
      </div>
    );
  }

  // Admin view — full app with sidebar
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
      case "timeclock":
        return <TimeClock />;
      case "sales":
        return <SalesReport />;
      case "orders":
        return <OnlineOrders />;
      case "discounts":
        return <Discounts />;
      case "scraper":
        return <ProductScraper />;
      default:
        return <Inventory />;
    }
  };

  return (
    <Layout currentPage={currentPage} onNavigate={setCurrentPage} onLogout={handleLogout}>
      {renderPage()}
    </Layout>
  );
}

export default App;
