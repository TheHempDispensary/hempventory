import { useState } from "react";
import { employeeLogin } from "../lib/api";

interface EmployeeLoginProps {
  onLogin: () => void;
  onSwitchToAdmin: () => void;
}

export default function EmployeeLogin({ onLogin, onSwitchToAdmin }: EmployeeLoginProps) {
  const [username, setUsername] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await employeeLogin(username, pin);
      localStorage.setItem("token", res.data.access_token);
      localStorage.setItem("userRole", "employee");
      // Also store under domain-specific key for login separation
      const mode = window.location.hostname.toLowerCase().startsWith("timeclock") ? "timeclock" : "inventory";
      localStorage.setItem(`${mode}_token`, res.data.access_token);
      localStorage.setItem(`${mode}_userRole`, "employee");
      onLogin();
    } catch {
      setError("Invalid username or PIN");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-green-50 to-emerald-100">
      <div className="w-full max-w-md bg-white rounded-2xl shadow-xl p-8">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-green-100 mb-4">
            <svg className="w-8 h-8 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Hemp Dispensary</h1>
          <p className="text-gray-500 mt-1">Employee Time Clock</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="bg-red-50 text-red-600 p-3 rounded-lg text-sm">
              {error}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
              placeholder="e.g. STozzi"
              required
              autoCapitalize="off"
              autoCorrect="off"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              PIN
            </label>
            <input
              type="password"
              inputMode="numeric"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none tracking-widest text-center text-lg"
              placeholder="••••••"
              required
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 bg-green-600 text-white rounded-lg font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
          >
            {loading ? "Signing in..." : "Clock In / View Time Clock"}
          </button>
        </form>

        <div className="mt-6 text-center">
          <button
            onClick={onSwitchToAdmin}
            className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
          >
            Admin Login
          </button>
        </div>
      </div>
    </div>
  );
}
