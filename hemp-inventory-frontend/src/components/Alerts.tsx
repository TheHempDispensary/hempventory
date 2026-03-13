import { useEffect, useState } from "react";
import { getParAlerts, checkAndNotify, getAlertHistory } from "../lib/api";
import { AlertTriangle, Bell, RefreshCw, Clock, Send } from "lucide-react";

interface Alert {
  sku: string;
  product_name: string;
  location: string;
  location_id: number;
  current_stock: number;
  par_level: number;
  deficit: number;
  recommendation: string;
}

interface AlertHistoryItem {
  id: number;
  sku: string;
  product_name: string;
  location_id: number;
  current_stock: number;
  par_level: number;
  alert_type: string;
  notified_at: string;
  email_sent: boolean;
  location_name: string;
}

export default function Alerts() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [history, setHistory] = useState<AlertHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const [alertRes, historyRes] = await Promise.all([
        getParAlerts().catch(() => ({ data: { alerts: [] } })),
        getAlertHistory(50).catch(() => ({ data: [] })),
      ]);
      setAlerts(alertRes.data.alerts || []);
      setHistory(historyRes.data || []);
    } catch (err) {
      console.error("Error loading alerts:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleCheckAndNotify = async () => {
    setChecking(true);
    setCheckResult(null);
    try {
      const res = await checkAndNotify();
      const { alerts_found, email_sent, notification_email } = res.data;
      if (alerts_found === 0) {
        setCheckResult("All items are above PAR levels. No alerts to send.");
      } else if (email_sent) {
        setCheckResult(`Found ${alerts_found} alert(s). Email sent to ${notification_email}.`);
      } else if (!notification_email) {
        setCheckResult(`Found ${alerts_found} alert(s). Configure email in Settings to receive notifications.`);
      } else {
        setCheckResult(`Found ${alerts_found} alert(s). Email could not be sent — check SMTP settings.`);
      }
      await loadData();
    } catch (err) {
      setCheckResult("Error checking alerts. Please try again.");
      console.error(err);
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 text-green-600 animate-spin" />
        <span className="ml-3 text-gray-600">Loading alerts...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">PAR Alerts</h2>
          <p className="text-gray-500 text-sm">Monitor stock levels and send notifications</p>
        </div>
        <button
          onClick={handleCheckAndNotify}
          disabled={checking}
          className="flex items-center gap-2 px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 text-sm transition-colors"
        >
          <Send className={`w-4 h-4 ${checking ? "animate-pulse" : ""}`} />
          {checking ? "Checking..." : "Check & Send Alerts"}
        </button>
      </div>

      {checkResult && (
        <div className={`p-4 rounded-lg text-sm ${checkResult.includes("Error") || checkResult.includes("could not") ? "bg-red-50 text-red-700" : checkResult.includes("No alerts") || checkResult.includes("above PAR") ? "bg-green-50 text-green-700" : "bg-blue-50 text-blue-700"}`}>
          {checkResult}
        </div>
      )}

      {/* Current Alerts */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-orange-500" />
          <h3 className="font-semibold text-gray-900">Current Alerts ({alerts.length})</h3>
        </div>
        {alerts.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <Bell className="w-10 h-10 mx-auto mb-3 text-gray-300" />
            <p>No items below PAR levels</p>
            <p className="text-xs mt-1">Set PAR levels in the Inventory page to start monitoring</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-100">
            {alerts.map((alert, i) => (
              <div key={i} className="px-5 py-4 hover:bg-gray-50">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="font-medium text-gray-900">{alert.product_name}</p>
                    <p className="text-sm text-gray-500 mt-0.5">
                      {alert.location} · SKU: {alert.sku}
                    </p>
                  </div>
                  <div className="text-right">
                    <div className="flex items-center gap-2">
                      <span className="text-2xl font-bold text-red-600">{alert.current_stock}</span>
                      <span className="text-gray-400">/</span>
                      <span className="text-lg text-gray-500">{alert.par_level}</span>
                    </div>
                    <p className="text-xs text-gray-400">current / par</p>
                  </div>
                </div>
                <div className="mt-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg">
                  <p className="text-sm text-amber-800">{alert.recommendation}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Alert History */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
          <Clock className="w-5 h-5 text-gray-400" />
          <h3 className="font-semibold text-gray-900">Alert History</h3>
        </div>
        {history.length === 0 ? (
          <div className="text-center py-8 text-gray-500 text-sm">
            No alert history yet
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  <th className="px-4 py-3">Product</th>
                  <th className="px-4 py-3">Location</th>
                  <th className="px-4 py-3">Stock</th>
                  <th className="px-4 py-3">PAR</th>
                  <th className="px-4 py-3">Email</th>
                  <th className="px-4 py-3">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {history.map((h) => (
                  <tr key={h.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm font-medium text-gray-900">
                      {h.product_name}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">{h.location_name}</td>
                    <td className="px-4 py-3 text-sm font-semibold text-red-600">{h.current_stock}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{h.par_level}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${h.email_sent ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                        {h.email_sent ? "Sent" : "Not sent"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500">
                      {new Date(h.notified_at + "Z").toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
