import { useEffect, useState, useMemo } from "react";
import { getSmartPar } from "../lib/api";
import { RefreshCw, Search, ChevronUp, ChevronDown, Download, Calculator } from "lucide-react";

interface ParProduct {
  name: string;
  sku: string;
  categories: string[];
  price: number;
  total_stock: number;
  stock_by_location: Record<string, number>;
  units_sold: number;
  units_per_month: number;
  par_level: number;
  order_qty: number;
}

interface ParMeta {
  months: number;
  days_of_data: number;
  total_products: number;
  total_units_sold: number;
}

type SortField = "name" | "category" | "price" | "total_stock" | "units_sold" | "units_per_month" | "par_level" | "order_qty";
type SortDir = "asc" | "desc";

const MONTH_OPTIONS = [1, 3, 4, 6, 12];

export default function SmartPar() {
  const [products, setProducts] = useState<ParProduct[]>([]);
  const [meta, setMeta] = useState<ParMeta | null>(null);
  const [loading, setLoading] = useState(false);
  const [months, setMonths] = useState(3);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("All");
  const [sortField, setSortField] = useState<SortField>("order_qty");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [error, setError] = useState("");

  const fetchData = async (m: number) => {
    setLoading(true);
    setError("");
    try {
      const res = await getSmartPar(m);
      setProducts(res.data.products);
      setMeta(res.data.meta);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load Smart PAR data";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData(months);
  }, [months]);

  const categories = useMemo(() => {
    const cats = new Set<string>();
    products.forEach((p) => p.categories.forEach((c) => cats.add(c)));
    return ["All", ...Array.from(cats).sort()];
  }, [products]);

  const filtered = useMemo(() => {
    let list = products;
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.sku.toLowerCase().includes(q)
      );
    }
    if (categoryFilter !== "All") {
      list = list.filter((p) => p.categories.includes(categoryFilter));
    }
    return list;
  }, [products, search, categoryFilter]);

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "category":
          cmp = (a.categories[0] || "").localeCompare(b.categories[0] || "");
          break;
        case "price":
          cmp = a.price - b.price;
          break;
        case "total_stock":
          cmp = a.total_stock - b.total_stock;
          break;
        case "units_sold":
          cmp = a.units_sold - b.units_sold;
          break;
        case "units_per_month":
          cmp = a.units_per_month - b.units_per_month;
          break;
        case "par_level":
          cmp = a.par_level - b.par_level;
          break;
        case "order_qty":
          cmp = a.order_qty - b.order_qty;
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [filtered, sortField, sortDir]);

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ChevronDown className="w-3 h-3 text-gray-300 inline ml-0.5" />;
    return sortDir === "asc" ? (
      <ChevronUp className="w-3 h-3 text-green-600 inline ml-0.5" />
    ) : (
      <ChevronDown className="w-3 h-3 text-green-600 inline ml-0.5" />
    );
  };

  const exportCSV = () => {
    const headers = ["Product", "SKU", "Category", "Price", "Current Stock", "Units Sold", "Units/Month", `PAR (${months}mo)`, "Order Qty"];
    const rows = sorted.map((p) => [
      `"${p.name}"`,
      p.sku,
      `"${p.categories.join(", ")}"`,
      `$${p.price.toFixed(2)}`,
      p.total_stock,
      p.units_sold,
      p.units_per_month,
      p.par_level,
      p.order_qty,
    ]);
    const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `smart-par-${months}mo-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Calculator className="w-7 h-7 text-green-600" />
            Smart PAR Calculator
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Recommended reorder quantities based on actual sales velocity
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => fetchData(months)}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-2 text-sm bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            onClick={exportCSV}
            disabled={loading || sorted.length === 0}
            className="flex items-center gap-2 px-3 py-2 text-sm bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            <Download className="w-4 h-4" />
            Export CSV
          </button>
        </div>
      </div>

      {/* Time Period Selector */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
        <div className="flex flex-col sm:flex-row sm:items-center gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Supply Window</label>
            <p className="text-xs text-gray-400">How many months of inventory to keep on hand</p>
          </div>
          <div className="flex gap-2">
            {MONTH_OPTIONS.map((m) => (
              <button
                key={m}
                onClick={() => setMonths(m)}
                disabled={loading}
                className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors ${
                  months === m
                    ? "bg-green-600 text-white border-green-600"
                    : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                } disabled:opacity-50`}
              >
                {m} {m === 1 ? "Month" : "Months"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Meta Info */}
      {meta && !loading && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Products</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{meta.total_products}</p>
          </div>
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Total Units Sold</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{meta.total_units_sold.toLocaleString()}</p>
          </div>
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Days of Data</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{meta.days_of_data}</p>
          </div>

        </div>
      )}

      {/* Search & Filter */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search products..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-transparent"
          />
        </div>
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-transparent bg-white"
        >
          {categories.map((c) => (
            <option key={c} value={c}>
              {c === "All" ? "All Categories" : c}
            </option>
          ))}
        </select>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-12 text-center">
          <RefreshCw className="w-8 h-8 text-green-600 animate-spin mx-auto mb-3" />
          <p className="text-sm text-gray-500">
            Calculating PAR levels from all sales data...
          </p>
          <p className="text-xs text-gray-400 mt-1">
            This may take 30-60 seconds on first load (pulls from Clover POS + online orders)
          </p>
        </div>
      )}

      {/* Table */}
      {!loading && sorted.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
            <p className="text-sm text-gray-500">
              Showing <strong>{sorted.length}</strong> of {products.length} products
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th
                    className="px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 whitespace-nowrap"
                    onClick={() => toggleSort("name")}
                  >
                    Product <SortIcon field="name" />
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 whitespace-nowrap"
                    onClick={() => toggleSort("category")}
                  >
                    Category <SortIcon field="category" />
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 text-right whitespace-nowrap"
                    onClick={() => toggleSort("price")}
                  >
                    Price <SortIcon field="price" />
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 text-right whitespace-nowrap"
                    onClick={() => toggleSort("total_stock")}
                  >
                    In Stock <SortIcon field="total_stock" />
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 text-right whitespace-nowrap"
                    onClick={() => toggleSort("units_sold")}
                  >
                    Units Sold <SortIcon field="units_sold" />
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 text-right whitespace-nowrap"
                    onClick={() => toggleSort("units_per_month")}
                  >
                    Units/Mo <SortIcon field="units_per_month" />
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-green-700 bg-green-50 cursor-pointer hover:text-green-900 text-right whitespace-nowrap"
                    onClick={() => toggleSort("par_level")}
                  >
                    PAR ({months}mo) <SortIcon field="par_level" />
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-amber-700 bg-amber-50 cursor-pointer hover:text-amber-900 text-right whitespace-nowrap"
                    onClick={() => toggleSort("order_qty")}
                  >
                    Order Qty <SortIcon field="order_qty" />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sorted.map((p, i) => (
                  <tr key={`${p.sku}-${i}`} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900" title={p.name}>
                        {p.name}
                      </div>
                      <div className="text-xs text-gray-400">{p.sku}</div>
                    </td>
                    <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                      {p.categories.length > 0 ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700">
                          {p.categories[0]}
                        </span>
                      ) : (
                        <span className="text-gray-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">${p.price.toFixed(2)}</td>
                    <td className="px-4 py-3 text-right">
                      <span
                        className={`font-medium ${
                          p.total_stock <= 0
                            ? "text-red-600"
                            : p.total_stock < p.par_level
                            ? "text-amber-600"
                            : "text-gray-900"
                        }`}
                      >
                        {p.total_stock}
                      </span>
                      {p.stock_by_location && Object.keys(p.stock_by_location).length > 0 && (
                        <div className="text-xs text-gray-400 mt-0.5">
                          {Object.entries(p.stock_by_location)
                            .map(([loc, qty]) => {
                              const short = loc.replace(" Location", "").replace("Hemp Dispensary ", "");
                              return `${short}: ${qty}`;
                            })
                            .join(" · ")}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">{p.units_sold}</td>
                    <td className="px-4 py-3 text-right text-gray-600">{p.units_per_month}</td>
                    <td className="px-4 py-3 text-right bg-green-50/50 font-semibold text-green-700">
                      {p.par_level}
                    </td>
                    <td className="px-4 py-3 text-right bg-amber-50/50">
                      {p.order_qty > 0 ? (
                        <span className="font-semibold text-amber-700">{p.order_qty}</span>
                      ) : (
                        <span className="text-gray-300">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && sorted.length === 0 && products.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
          <p className="text-gray-500">No products match your search or filter.</p>
        </div>
      )}
    </div>
  );
}
