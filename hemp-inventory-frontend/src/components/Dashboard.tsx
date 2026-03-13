import { useEffect, useState, useMemo } from "react";
import { syncInventory, getParAlerts } from "../lib/api";
import { Package, AlertTriangle, MapPin, TrendingDown, RefreshCw, ChevronDown, ChevronUp, CheckSquare, Square, Minus, Download, Search } from "lucide-react";

interface DashboardProps {
  onNavigate: (page: string) => void;
}

interface LocationStock {
  location_id: number;
  stock: number;
  par_level: number | null;
  status: string;
  clover_item_id: string;
}

interface InventoryItem {
  sku: string;
  name: string;
  price: number;
  categories: string[];
  locations: Record<string, LocationStock>;
}

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

export default function Dashboard({ onNavigate }: DashboardProps) {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [locations, setLocations] = useState<{ id: number; name: string }[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [sortField, setSortField] = useState<"name" | "sku" | "price" | "category" | "stock" | "par">("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [sortLocation, setSortLocation] = useState<string>("");
  const [selectedItems, setSelectedItems] = useState<Set<string>>(new Set());

  const loadData = async () => {
    setLoading(true);
    try {
      const [invRes, alertRes] = await Promise.all([
        syncInventory(),
        getParAlerts().catch(() => ({ data: { alerts: [] } })),
      ]);
      setItems(invRes.data.items);
      setLocations(invRes.data.locations);
      setAlerts(alertRes.data.alerts || []);
    } catch (err) {
      console.error("Error loading dashboard:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    await loadData();
    setSyncing(false);
  };

  useEffect(() => {
    loadData();
  }, []);

  const totalItems = items.length;
  const outOfStockCount = items.filter((i) =>
    Object.values(i.locations).some((l) => l.stock <= 0)
  ).length;

  const lowStockItems = useMemo(() => {
    return items.filter((item) =>
      Object.values(item.locations).some(
        (l) => (l.par_level !== null && l.stock <= l.par_level) || l.stock <= 5
      )
    );
  }, [items]);

  const categories = useMemo(() => {
    const cats = new Set<string>();
    lowStockItems.forEach((item) => item.categories.forEach((c) => cats.add(c)));
    return Array.from(cats).sort();
  }, [lowStockItems]);

  const filteredLowStock = useMemo(() => {
    let filtered = lowStockItems;
    if (search) {
      const s = search.toLowerCase();
      filtered = filtered.filter(
        (i) => i.name.toLowerCase().includes(s) || i.sku.toLowerCase().includes(s)
      );
    }
    if (categoryFilter !== "all") {
      filtered = filtered.filter((i) => i.categories.includes(categoryFilter));
    }
    filtered.sort((a, b) => {
      let cmp = 0;
      if (sortField === "name") cmp = a.name.localeCompare(b.name);
      else if (sortField === "sku") cmp = a.sku.localeCompare(b.sku);
      else if (sortField === "price") cmp = a.price - b.price;
      else if (sortField === "category") {
        const aCat = a.categories[0] || "";
        const bCat = b.categories[0] || "";
        cmp = aCat.localeCompare(bCat);
      } else if (sortField === "stock") {
        const aHasLoc = sortLocation && a.locations[sortLocation];
        const bHasLoc = sortLocation && b.locations[sortLocation];
        if (!aHasLoc && !bHasLoc) { cmp = 0; }
        else if (!aHasLoc) { return 1; }
        else if (!bHasLoc) { return -1; }
        else { cmp = a.locations[sortLocation].stock - b.locations[sortLocation].stock; }
      } else if (sortField === "par") {
        const aHasLoc = sortLocation && a.locations[sortLocation];
        const bHasLoc = sortLocation && b.locations[sortLocation];
        if (!aHasLoc && !bHasLoc) { cmp = 0; }
        else if (!aHasLoc) { return 1; }
        else if (!bHasLoc) { return -1; }
        else { cmp = (a.locations[sortLocation].par_level ?? 0) - (b.locations[sortLocation].par_level ?? 0); }
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return filtered;
  }, [lowStockItems, search, categoryFilter, sortField, sortDir, sortLocation]);

  const toggleSort = (field: "name" | "sku" | "price" | "category" | "stock" | "par", locName?: string) => {
    if (sortField === field && sortLocation === (locName || "")) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDir("asc");
      setSortLocation(locName || "");
    }
  };

  const SortIcon = ({ field }: { field: string }) => {
    if (sortField !== field) return null;
    return sortDir === "asc" ? (
      <ChevronUp className="w-3 h-3 inline ml-1" />
    ) : (
      <ChevronDown className="w-3 h-3 inline ml-1" />
    );
  };

  const toggleSelectAll = () => {
    if (selectedItems.size === filteredLowStock.length) {
      setSelectedItems(new Set());
    } else {
      setSelectedItems(new Set(filteredLowStock.map((i) => i.sku)));
    }
  };

  const toggleSelectItem = (sku: string) => {
    const next = new Set(selectedItems);
    if (next.has(sku)) next.delete(sku);
    else next.add(sku);
    setSelectedItems(next);
  };

  const handleDownloadExcel = () => {
    const selectedData = filteredLowStock.filter((i) => selectedItems.has(i.sku));
    const dataToExport = selectedData.length > 0 ? selectedData : filteredLowStock;
    const headers = ["Product Name", "SKU", "Price", "Category"];
    locations.forEach((loc) => { headers.push(`${loc.name} Stock`); headers.push(`${loc.name} PAR`); });
    const rows = dataToExport.map((item) => {
      const row: string[] = [item.name, item.sku, `$${(item.price / 100).toFixed(2)}`, item.categories.join("; ")];
      locations.forEach((loc) => {
        const locData = item.locations[loc.name];
        row.push(locData ? locData.stock.toString() : "");
        row.push(locData && locData.par_level !== null ? locData.par_level.toString() : "");
      });
      return row;
    });
    const csvContent = [headers.join(","), ...rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(","))].join("\n");
    const BOM = "\uFEFF";
    const blob = new Blob([BOM + csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `low_stock_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 text-green-600 animate-spin" />
        <span className="ml-3 text-gray-600">Loading inventory from Clover...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Low Stock</h2>
          <p className="text-gray-500 mt-1">Items below their PAR levels across all locations</p>
        </div>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
          Sync Now
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white rounded-xl p-5 border border-gray-200 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-blue-100 flex items-center justify-center">
              <Package className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Total Products</p>
              <p className="text-2xl font-bold text-gray-900">{totalItems}</p>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl p-5 border border-gray-200 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center">
              <MapPin className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Locations</p>
              <p className="text-2xl font-bold text-gray-900">{locations.length}</p>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl p-5 border border-gray-200 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-orange-100 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Low Stock Items</p>
              <p className="text-2xl font-bold text-gray-900">{lowStockItems.length}</p>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl p-5 border border-gray-200 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-red-100 flex items-center justify-center">
              <TrendingDown className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Out of Stock</p>
              <p className="text-2xl font-bold text-gray-900">{outOfStockCount}</p>
            </div>
          </div>
        </div>
      </div>

      {/* PAR Alerts */}
      {alerts.length > 0 && (
        <div className="bg-white rounded-xl border border-red-200 shadow-sm">
          <div className="px-5 py-4 border-b border-red-100 flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-red-500" />
            <h3 className="font-semibold text-red-700">PAR Alerts ({alerts.length})</h3>
            <button
              onClick={() => onNavigate("alerts")}
              className="ml-auto text-sm text-red-600 hover:text-red-700 font-medium"
            >
              View All →
            </button>
          </div>
          <div className="divide-y divide-red-50">
            {alerts.slice(0, 5).map((alert, i) => (
              <div key={i} className="px-5 py-3 flex items-center justify-between">
                <div>
                  <p className="font-medium text-gray-900 text-sm">{alert.product_name}</p>
                  <p className="text-xs text-gray-500">{alert.location} · SKU: {alert.sku}</p>
                </div>
                <div className="text-right">
                  <p className="text-sm font-semibold text-red-600">
                    {alert.current_stock} / {alert.par_level}
                  </p>
                  <p className="text-xs text-gray-500">{alert.recommendation}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Low stock items table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="px-5 py-4 border-b border-gray-100">
          <h3 className="font-semibold text-gray-900">Items Below PAR</h3>
          <p className="text-xs text-gray-400 mt-0.5">Items where current stock is at or below the PAR (reorder) level</p>
        </div>

        {/* Filters */}
        <div className="px-5 py-3 border-b border-gray-100 flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search by name or SKU..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none text-sm"
            />
          </div>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
          >
            <option value="all">All Categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <button
            onClick={handleDownloadExcel}
            className="flex items-center gap-1.5 px-3 py-2 bg-green-700 text-white rounded-lg text-sm hover:bg-green-800"
          >
            <Download className="w-3.5 h-3.5" />
            Download CSV
          </button>
          <span className="text-sm text-gray-500 self-center">{filteredLowStock.length} items</span>
        </div>

        {/* Bulk Action Bar */}
        {selectedItems.size > 0 && (
          <div className="px-5 py-3 bg-green-50 border-b border-green-200 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <CheckSquare className="w-5 h-5 text-green-600" />
              <span className="text-sm font-medium text-green-700">
                {selectedItems.size} item{selectedItems.size !== 1 ? "s" : ""} selected
              </span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setSelectedItems(new Set())}
                className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
              >
                Clear Selection
              </button>
              <button
                onClick={handleDownloadExcel}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-green-700 text-white rounded-lg text-sm hover:bg-green-800"
              >
                <Download className="w-3.5 h-3.5" />
                Download Selected
              </button>
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                <th className="px-3 py-3 w-10">
                  <button
                    onClick={toggleSelectAll}
                    className="text-gray-400 hover:text-green-600 transition-colors"
                    title={selectedItems.size === filteredLowStock.length ? "Deselect all" : "Select all"}
                  >
                    {selectedItems.size === filteredLowStock.length && filteredLowStock.length > 0 ? (
                      <CheckSquare className="w-4 h-4 text-green-600" />
                    ) : selectedItems.size > 0 ? (
                      <Minus className="w-4 h-4 text-green-600" />
                    ) : (
                      <Square className="w-4 h-4" />
                    )}
                  </button>
                </th>
                <th className="px-5 py-3 cursor-pointer hover:text-gray-700" onClick={() => toggleSort("name")}>
                  Product <SortIcon field="name" />
                </th>
                <th className="px-5 py-3 cursor-pointer hover:text-gray-700" onClick={() => toggleSort("sku")}>
                  SKU <SortIcon field="sku" />
                </th>
                <th className="px-5 py-3 cursor-pointer hover:text-gray-700" onClick={() => toggleSort("price")}>
                  Price <SortIcon field="price" />
                </th>
                <th className="px-5 py-3 cursor-pointer hover:text-gray-700" onClick={() => toggleSort("category")}>
                  Category <SortIcon field="category" />
                </th>
                {locations.map((loc) => (
                  <th key={loc.id} className="px-5 py-3 cursor-pointer hover:text-gray-700" onClick={() => toggleSort("stock", loc.name)}>
                    {loc.name} Stock {sortField === "stock" && sortLocation === loc.name ? <SortIcon field="stock" /> : sortField === "stock" ? <span className="text-gray-300 inline"><SortIcon field="stock" /></span> : null}
                  </th>
                ))}
                {locations.map((loc) => (
                  <th key={`par-${loc.id}`} className="px-5 py-3 cursor-pointer hover:text-gray-700" onClick={() => toggleSort("par", loc.name)}>
                    {loc.name} PAR {sortField === "par" && sortLocation === loc.name ? <SortIcon field="par" /> : sortField === "par" ? <span className="text-gray-300 inline"><SortIcon field="par" /></span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filteredLowStock.map((item) => (
                <tr key={item.sku} className={`hover:bg-green-50 transition-colors ${selectedItems.has(item.sku) ? "bg-green-50/50" : ""}`}>
                  <td className="px-3 py-3 w-10">
                    <button onClick={() => toggleSelectItem(item.sku)} className="text-gray-400 hover:text-green-600 transition-colors">
                      {selectedItems.has(item.sku) ? (
                        <CheckSquare className="w-4 h-4 text-green-600" />
                      ) : (
                        <Square className="w-4 h-4" />
                      )}
                    </button>
                  </td>
                  <td className="px-5 py-3">
                    <p className="text-sm font-medium text-gray-900">{item.name}</p>
                  </td>
                  <td className="px-5 py-3 text-xs text-gray-500 font-mono">
                    {item.sku.length > 15 ? item.sku.slice(0, 15) + "..." : item.sku}
                  </td>
                  <td className="px-5 py-3 text-sm text-gray-700">
                    ${(item.price / 100).toFixed(2)}
                  </td>
                  <td className="px-5 py-3">
                    {item.categories.map((c) => (
                      <span key={c} className="inline-block px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs mr-1">{c}</span>
                    ))}
                  </td>
                  {locations.map((loc) => {
                    const locData = item.locations[loc.name];
                    const stock = locData?.stock ?? 0;
                    const isLow = locData && (locData.stock <= 5 || (locData.par_level !== null && locData.stock <= locData.par_level));
                    return (
                      <td key={loc.id} className={`px-5 py-3 text-sm font-semibold text-center ${!locData ? "text-gray-300" : isLow ? "text-red-600" : "text-gray-900"}`}>
                        {locData ? stock : "\u2014"}
                      </td>
                    );
                  })}
                  {locations.map((loc) => {
                    const locData = item.locations[loc.name];
                    return (
                      <td key={`par-${loc.id}`} className="px-5 py-3 text-sm text-center">
                        {locData && locData.par_level !== null ? (
                          <span className="font-semibold text-gray-700">{locData.par_level}</span>
                        ) : (
                          <span className="text-gray-300">{"\u2014"}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filteredLowStock.length === 0 && (
          <div className="text-center py-12 text-gray-500">No low stock items found.</div>
        )}
        <div className="px-5 py-3 border-t border-gray-100">
          <button onClick={() => onNavigate("inventory")} className="text-sm text-green-600 hover:text-green-700 font-medium">
            View Full Inventory →
          </button>
        </div>
      </div>
    </div>
  );
}
