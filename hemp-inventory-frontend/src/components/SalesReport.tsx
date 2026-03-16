import { useState, useEffect, useCallback } from "react";
import {
  DollarSign,
  ShoppingCart,
  TrendingUp,
  Package,
  RefreshCw,
  MapPin,
  Clock,
} from "lucide-react";
import { getSalesReport } from "../lib/api";

interface SalesData {
  summary: {
    total_revenue: number;
    total_orders: number;
    total_items_sold: number;
    avg_order_value: number;
    start_date: string;
    end_date: string;
  };
  by_location: Record<string, { revenue: number; orders: number; avg_order: number; error?: string }>;
  hourly: { hour: string; label: string; revenue: number; orders: number }[];
  daily: { date: string; label: string; revenue: number; orders: number }[];
  top_items: { name: string; quantity: number; revenue: number }[];
  recent_orders: { id: string; total: number; location: string; time: string; items: number }[];
}

type Preset = "today" | "yesterday" | "week" | "month" | "custom";

export default function SalesReport() {
  const today = new Date().toISOString().slice(0, 10);
  const [startDate, setStartDate] = useState(today);
  const [endDate, setEndDate] = useState(today);
  const [preset, setPreset] = useState<Preset>("today");
  const [data, setData] = useState<SalesData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"overview" | "hourly" | "items" | "orders">("overview");

  const applyPreset = useCallback((p: Preset) => {
    setPreset(p);
    const now = new Date();
    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    switch (p) {
      case "today":
        setStartDate(fmt(now));
        setEndDate(fmt(now));
        break;
      case "yesterday": {
        const y = new Date(now);
        y.setDate(y.getDate() - 1);
        setStartDate(fmt(y));
        setEndDate(fmt(y));
        break;
      }
      case "week": {
        const w = new Date(now);
        w.setDate(w.getDate() - 6);
        setStartDate(fmt(w));
        setEndDate(fmt(now));
        break;
      }
      case "month": {
        const m = new Date(now);
        m.setDate(m.getDate() - 29);
        setStartDate(fmt(m));
        setEndDate(fmt(now));
        break;
      }
    }
  }, []);

  const loadReport = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await getSalesReport({ start_date: startDate, end_date: endDate });
      setData(res.data);
    } catch (err) {
      console.error(err);
      setError("Failed to load sales report");
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate]);

  useEffect(() => {
    loadReport();
  }, [loadReport]);

  const fmtMoney = (cents: number) => {
    const abs = Math.abs(cents);
    const dollars = Math.floor(abs / 100);
    const c = abs % 100;
    const sign = cents < 0 ? "-" : "";
    return `${sign}$${dollars.toLocaleString()}.${c.toString().padStart(2, "0")}`;
  };

  // Bar chart helper
  const maxVal = (arr: number[]) => Math.max(...arr, 1);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <h1 className="text-2xl font-bold text-gray-900">Sales Report</h1>
        <div className="flex items-center gap-2 flex-wrap">
          {(["today", "yesterday", "week", "month"] as Preset[]).map((p) => (
            <button
              key={p}
              onClick={() => applyPreset(p)}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                preset === p
                  ? "bg-green-600 text-white"
                  : "bg-white text-gray-600 border border-gray-200 hover:bg-gray-50"
              }`}
            >
              {p === "today" ? "Today" : p === "yesterday" ? "Yesterday" : p === "week" ? "Last 7 Days" : "Last 30 Days"}
            </button>
          ))}
          <div className="flex items-center gap-1">
            <input
              type="date"
              value={startDate}
              onChange={(e) => { setStartDate(e.target.value); setPreset("custom"); }}
              className="px-2 py-1.5 text-xs border border-gray-200 rounded-lg"
            />
            <span className="text-gray-400 text-xs">to</span>
            <input
              type="date"
              value={endDate}
              onChange={(e) => { setEndDate(e.target.value); setPreset("custom"); }}
              className="px-2 py-1.5 text-xs border border-gray-200 rounded-lg"
            />
          </div>
          <button
            onClick={loadReport}
            disabled={loading}
            className="px-3 py-1.5 bg-green-600 text-white text-xs font-medium rounded-lg hover:bg-green-700 transition-colors flex items-center gap-1 disabled:opacity-50"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">{error}</div>
      )}

      {loading && !data && (
        <div className="flex items-center justify-center py-20">
          <RefreshCw className="w-8 h-8 text-green-600 animate-spin" />
        </div>
      )}

      {data && (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center">
                  <DollarSign className="w-5 h-5 text-green-600" />
                </div>
                <p className="text-sm text-gray-500">Total Revenue</p>
              </div>
              <p className="text-2xl font-bold text-gray-900">{fmtMoney(data.summary.total_revenue)}</p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 rounded-lg bg-blue-100 flex items-center justify-center">
                  <ShoppingCart className="w-5 h-5 text-blue-600" />
                </div>
                <p className="text-sm text-gray-500">Total Orders</p>
              </div>
              <p className="text-2xl font-bold text-gray-900">{data.summary.total_orders.toLocaleString()}</p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 rounded-lg bg-purple-100 flex items-center justify-center">
                  <TrendingUp className="w-5 h-5 text-purple-600" />
                </div>
                <p className="text-sm text-gray-500">Avg Order</p>
              </div>
              <p className="text-2xl font-bold text-gray-900">{fmtMoney(data.summary.avg_order_value)}</p>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 rounded-lg bg-amber-100 flex items-center justify-center">
                  <Package className="w-5 h-5 text-amber-600" />
                </div>
                <p className="text-sm text-gray-500">Items Sold</p>
              </div>
              <p className="text-2xl font-bold text-gray-900">{data.summary.total_items_sold.toLocaleString()}</p>
            </div>
          </div>

          {/* By Location */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <MapPin className="w-5 h-5 text-gray-400" />
              Sales by Location
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {Object.entries(data.by_location).map(([name, loc]) => (
                <div key={name} className="bg-gray-50 rounded-lg p-4">
                  <p className="font-medium text-gray-900 mb-2">{name}</p>
                  {loc.error ? (
                    <p className="text-red-500 text-sm">{loc.error}</p>
                  ) : (
                    <div className="space-y-1">
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-500">Revenue</span>
                        <span className="font-semibold text-gray-900">{fmtMoney(loc.revenue)}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-500">Orders</span>
                        <span className="font-semibold text-gray-900">{loc.orders}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-500">Avg Order</span>
                        <span className="font-semibold text-gray-900">{fmtMoney(loc.avg_order)}</span>
                      </div>
                      {/* Location revenue bar */}
                      {data.summary.total_revenue > 0 && (
                        <div className="mt-2">
                          <div className="w-full h-2 bg-gray-200 rounded-full">
                            <div
                              className="h-2 bg-green-500 rounded-full transition-all"
                              style={{ width: `${(loc.revenue / data.summary.total_revenue) * 100}%` }}
                            />
                          </div>
                          <p className="text-xs text-gray-400 mt-1">
                            {((loc.revenue / data.summary.total_revenue) * 100).toFixed(1)}% of total
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Tabs */}
          <div className="bg-white rounded-xl border border-gray-200">
            <div className="flex border-b border-gray-200">
              {([
                { id: "overview", label: "Daily Trend", icon: TrendingUp },
                { id: "hourly", label: "By Hour", icon: Clock },
                { id: "items", label: "Top Items", icon: Package },
                { id: "orders", label: "Recent Orders", icon: ShoppingCart },
              ] as const).map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
                    tab === t.id
                      ? "border-green-600 text-green-700"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  <t.icon className="w-4 h-4" />
                  {t.label}
                </button>
              ))}
            </div>

            <div className="p-5">
              {/* Daily Trend */}
              {tab === "overview" && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-4">Revenue by Day</h3>
                  {data.daily.length === 0 ? (
                    <p className="text-gray-400 text-sm py-8 text-center">No data for selected range</p>
                  ) : (
                    <div className="space-y-2">
                      {/* Bar chart */}
                      <div className="flex items-end gap-1 h-48">
                        {data.daily.map((d) => {
                          const max = maxVal(data.daily.map((x) => x.revenue));
                          const pct = (d.revenue / max) * 100;
                          return (
                            <div
                              key={d.date}
                              className="flex-1 flex flex-col items-center group relative"
                            >
                              <div
                                className="w-full bg-green-500 rounded-t hover:bg-green-600 transition-colors cursor-pointer min-h-[2px]"
                                style={{ height: `${Math.max(pct, 1)}%` }}
                              />
                              {/* Tooltip */}
                              <div className="absolute bottom-full mb-2 hidden group-hover:block z-10">
                                <div className="bg-gray-900 text-white text-xs rounded-lg px-3 py-2 whitespace-nowrap">
                                  <p className="font-semibold">{d.label}</p>
                                  <p>{fmtMoney(d.revenue)} · {d.orders} orders</p>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                      {/* X-axis labels (show max ~15) */}
                      <div className="flex gap-1">
                        {data.daily.map((d, i) => (
                          <div key={d.date} className="flex-1 text-center">
                            {data.daily.length <= 15 || i % Math.ceil(data.daily.length / 15) === 0 ? (
                              <span className="text-[10px] text-gray-400">{d.label}</span>
                            ) : null}
                          </div>
                        ))}
                      </div>
                      {/* Daily table */}
                      <div className="mt-4 overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-left text-gray-500 border-b border-gray-100">
                              <th className="pb-2 font-medium">Date</th>
                              <th className="pb-2 font-medium text-right">Orders</th>
                              <th className="pb-2 font-medium text-right">Revenue</th>
                              <th className="pb-2 font-medium text-right">Avg Order</th>
                            </tr>
                          </thead>
                          <tbody>
                            {[...data.daily].reverse().map((d) => (
                              <tr key={d.date} className="border-b border-gray-50 hover:bg-gray-50">
                                <td className="py-2 text-gray-900">{d.label}</td>
                                <td className="py-2 text-right text-gray-600">{d.orders}</td>
                                <td className="py-2 text-right font-medium text-gray-900">{fmtMoney(d.revenue)}</td>
                                <td className="py-2 text-right text-gray-600">
                                  {d.orders > 0 ? fmtMoney(Math.round(d.revenue / d.orders)) : "—"}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Hourly Breakdown */}
              {tab === "hourly" && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-4">Sales by Hour of Day</h3>
                  <div className="space-y-2">
                    {/* Hourly bar chart */}
                    <div className="flex items-end gap-0.5 h-48">
                      {data.hourly.map((h) => {
                        const max = maxVal(data.hourly.map((x) => x.revenue));
                        const pct = (h.revenue / max) * 100;
                        return (
                          <div
                            key={h.hour}
                            className="flex-1 flex flex-col items-center group relative"
                          >
                            <div
                              className="w-full bg-blue-500 rounded-t hover:bg-blue-600 transition-colors cursor-pointer min-h-[2px]"
                              style={{ height: `${Math.max(pct, 1)}%` }}
                            />
                            <div className="absolute bottom-full mb-2 hidden group-hover:block z-10">
                              <div className="bg-gray-900 text-white text-xs rounded-lg px-3 py-2 whitespace-nowrap">
                                <p className="font-semibold">{h.label}</p>
                                <p>{fmtMoney(h.revenue)} · {h.orders} orders</p>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {/* X-axis labels */}
                    <div className="flex gap-0.5">
                      {data.hourly.map((h, i) => (
                        <div key={h.hour} className="flex-1 text-center">
                          {i % 3 === 0 ? (
                            <span className="text-[10px] text-gray-400">{h.label}</span>
                          ) : null}
                        </div>
                      ))}
                    </div>
                    {/* Hourly table */}
                    <div className="mt-4 overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-gray-500 border-b border-gray-100">
                            <th className="pb-2 font-medium">Hour</th>
                            <th className="pb-2 font-medium text-right">Orders</th>
                            <th className="pb-2 font-medium text-right">Revenue</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.hourly.filter((h) => h.orders > 0).map((h) => (
                            <tr key={h.hour} className="border-b border-gray-50 hover:bg-gray-50">
                              <td className="py-2 text-gray-900">{h.label}</td>
                              <td className="py-2 text-right text-gray-600">{h.orders}</td>
                              <td className="py-2 text-right font-medium text-gray-900">{fmtMoney(h.revenue)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}

              {/* Top Items */}
              {tab === "items" && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-4">Top Selling Items (by revenue)</h3>
                  {data.top_items.length === 0 ? (
                    <p className="text-gray-400 text-sm py-8 text-center">No items sold in selected range</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-gray-500 border-b border-gray-100">
                            <th className="pb-2 font-medium">#</th>
                            <th className="pb-2 font-medium">Item</th>
                            <th className="pb-2 font-medium text-right">Qty Sold</th>
                            <th className="pb-2 font-medium text-right">Revenue</th>
                            <th className="pb-2 font-medium text-right">% of Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.top_items.map((item, i) => (
                            <tr key={item.name} className="border-b border-gray-50 hover:bg-gray-50">
                              <td className="py-2 text-gray-400">{i + 1}</td>
                              <td className="py-2 text-gray-900 font-medium max-w-xs truncate">{item.name}</td>
                              <td className="py-2 text-right text-gray-600">{item.quantity}</td>
                              <td className="py-2 text-right font-medium text-gray-900">{fmtMoney(item.revenue)}</td>
                              <td className="py-2 text-right text-gray-500">
                                {data.summary.total_revenue > 0
                                  ? ((item.revenue / data.summary.total_revenue) * 100).toFixed(1) + "%"
                                  : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {/* Recent Orders */}
              {tab === "orders" && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-4">Recent Orders</h3>
                  {data.recent_orders.length === 0 ? (
                    <p className="text-gray-400 text-sm py-8 text-center">No orders in selected range</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-gray-500 border-b border-gray-100">
                            <th className="pb-2 font-medium">Time</th>
                            <th className="pb-2 font-medium">Location</th>
                            <th className="pb-2 font-medium text-right">Items</th>
                            <th className="pb-2 font-medium text-right">Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.recent_orders.map((order) => {
                            const dt = new Date(order.time);
                            return (
                              <tr key={order.id} className="border-b border-gray-50 hover:bg-gray-50">
                                <td className="py-2 text-gray-900">
                                  {dt.toLocaleDateString()} {dt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                                </td>
                                <td className="py-2 text-gray-600">{order.location}</td>
                                <td className="py-2 text-right text-gray-600">{order.items}</td>
                                <td className="py-2 text-right font-medium text-gray-900">{fmtMoney(order.total)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
