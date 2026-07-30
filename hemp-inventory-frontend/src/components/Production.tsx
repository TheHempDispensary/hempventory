import { useEffect, useMemo, useRef, useState } from "react";
import {
  Factory, RefreshCw, Search, Plus, Trash2, X, Check, ClipboardList,
  Beaker, PackageCheck, CheckCircle2, Boxes, Pencil, ChevronUp, ChevronDown,
} from "lucide-react";
import {
  getProductionPlan, getProductionBatches, createProductionBatch,
  updateProductionBatch, deleteProductionBatch, getCachedInventory, addBatchToInventory,
  reorderProductionBatches,
  type ProductionPlanItem, type ProductionBatch, type BatchPayload,
} from "../lib/api";
import { matchesSearch } from "../lib/utils";

const MONTH_OPTIONS = [1, 3, 4, 6, 12];

const STATUS_COLUMNS: { id: ProductionBatch["status"]; label: string; icon: typeof ClipboardList; color: string }[] = [
  { id: "planned", label: "Planned", icon: ClipboardList, color: "text-gray-500" },
  { id: "in_production", label: "In Production", icon: Beaker, color: "text-blue-600" },
  { id: "ready", label: "Ready", icon: PackageCheck, color: "text-amber-600" },
  { id: "done", label: "Done", icon: CheckCircle2, color: "text-green-600" },
];

const NEXT_STATUS: Record<ProductionBatch["status"], ProductionBatch["status"] | null> = {
  planned: "in_production",
  in_production: "ready",
  ready: "done",
  done: null,
};

type Tab = "plan" | "board";

// Some Clover products share a SKU (e.g. a "BATCH ..." duplicate), so selection
// and React keys must use name too — otherwise duplicate-SKU rows collide and
// one silently disappears from the list.
const planKey = (p: ProductionPlanItem) => `${p.sku}::${p.name}`;

interface InvItem { sku: string; name: string; categories?: string[]; }

export default function Production() {
  const [tab, setTab] = useState<Tab>("plan");
  const [months, setMonths] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [plan, setPlan] = useState<ProductionPlanItem[]>([]);
  const [batches, setBatches] = useState<ProductionBatch[]>([]);

  const [inventory, setInventory] = useState<InvItem[]>([]);
  const [planSearch, setPlanSearch] = useState("");

  const [editing, setEditing] = useState<ProductionBatch | null>(null);
  const [toast, setToast] = useState("");

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkAdding, setBulkAdding] = useState(false);

  const dragIdRef = useRef<number | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);
  const [dragOverId, setDragOverId] = useState<number | null>(null);

  // Reorder the dragged card to sit before `targetId` (or at the end when
  // targetId is null) within its own status column, then persist the order.
  const reorderWithinColumn = async (status: ProductionBatch["status"], targetId: number | null) => {
    const draggedId = dragIdRef.current;
    dragIdRef.current = null;
    setDragId(null);
    setDragOverId(null);
    if (draggedId == null || draggedId === targetId) return;
    const colItems = batches.filter((b) => b.status === status);
    const others = batches.filter((b) => b.status !== status);
    const fromIdx = colItems.findIndex((b) => b.id === draggedId);
    if (fromIdx < 0) return; // dragged card is in a different column — ignore
    const reordered = [...colItems];
    const [moved] = reordered.splice(fromIdx, 1);
    const toIdx = targetId == null ? reordered.length : reordered.findIndex((b) => b.id === targetId);
    reordered.splice(toIdx < 0 ? reordered.length : toIdx, 0, moved);
    const withOrder = reordered.map((b, i) => ({ ...b, sort_order: i }));
    setBatches([...others, ...withOrder]);
    try {
      await reorderProductionBatches(withOrder.map((b) => b.id));
    } catch {
      loadBatches();
    }
  };

  // Move a card up/down within its status column via explicit buttons — the
  // reliable, cross-browser alternative to native drag (which iOS/Safari and
  // some setups don't fire). dir = -1 (up) or +1 (down).
  const moveCard = async (status: ProductionBatch["status"], id: number, dir: -1 | 1) => {
    const colItems = batches.filter((b) => b.status === status);
    const others = batches.filter((b) => b.status !== status);
    const idx = colItems.findIndex((b) => b.id === id);
    const swap = idx + dir;
    if (idx < 0 || swap < 0 || swap >= colItems.length) return;
    const reordered = [...colItems];
    [reordered[idx], reordered[swap]] = [reordered[swap], reordered[idx]];
    const withOrder = reordered.map((b, i) => ({ ...b, sort_order: i }));
    setBatches([...others, ...withOrder]);
    try {
      await reorderProductionBatches(withOrder.map((b) => b.id));
    } catch {
      loadBatches();
    }
  };

  const loadPlan = async (m: number) => {
    setLoading(true); setError("");
    setSelected(new Set());
    try {
      const res = await getProductionPlan(m);
      setPlan(res.data.items);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load production plan");
    } finally { setLoading(false); }
  };

  const loadBatches = async () => {
    try {
      const res = await getProductionBatches();
      setBatches(res.data.batches);
    } catch { /* non-fatal */ }
  };

  const loadInventory = async () => {
    try {
      const res = await getCachedInventory();
      const items: InvItem[] = (res.data.items || []).map((i: InvItem) => ({
        sku: i.sku, name: i.name, categories: i.categories || [],
      }));
      setInventory(items);
    } catch { /* non-fatal */ }
  };

  useEffect(() => { loadPlan(months); }, [months]);
  useEffect(() => { loadBatches(); loadInventory(); }, []);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(""), 6000);
  };

  const addToPlan = async (item: ProductionPlanItem) => {
    const payload: BatchPayload = {
      product_name: item.name,
      sku: item.sku,
      planned_qty: item.to_produce,
      status: "planned",
      source: "smart_par",
    };
    const res = await createProductionBatch(payload);
    setBatches((prev) => [res.data, ...prev]);
    await loadPlan(months); // refresh already_planned
  };

  const openBatchForPlanItem = (item: ProductionPlanItem) => {
    setEditing({
      id: 0, sku: item.sku, product_name: item.name, size: null,
      planned_qty: item.to_produce || 0, produced_qty: 0,
      status: "planned", batch_no: null, expiration_date: null, made_by: null,
      qa_check: false, label_ordered: false, label_qty: null, notes: null,
      source: "smart_par", plan_date: null, completed_at: null,
      inventoried: false, inventoried_at: null, inventoried_qty: null,
      sort_order: 0, created_at: "", updated_at: "",
    });
  };

  const toggleSelect = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const addSelectedToPlan = async () => {
    const items = filteredPlan.filter((p) => p.to_produce > 0 && selected.has(planKey(p)));
    if (items.length === 0) return;
    setBulkAdding(true);
    try {
      const created: ProductionBatch[] = [];
      for (const item of items) {
        const res = await createProductionBatch({
          product_name: item.name,
          sku: item.sku,
          planned_qty: item.to_produce,
          status: "planned",
          source: "smart_par",
        });
        created.push(res.data);
      }
      setBatches((prev) => [...created, ...prev]);
      flash(`Added ${created.length} batch${created.length === 1 ? "" : "es"} to the board.`);
      await loadPlan(months);
    } catch (e: unknown) {
      flash(e instanceof Error ? e.message : "Couldn't create batches.");
    } finally { setBulkAdding(false); }
  };

  const advance = async (b: ProductionBatch) => {
    const next = NEXT_STATUS[b.status];
    if (!next) return;
    const res = await updateProductionBatch(b.id, { status: next });
    setBatches((prev) => prev.map((x) => (x.id === b.id ? res.data : x)));
    const inv = res.data.inventory_result;
    if (inv) {
      flash(inv.ok
        ? `Added ${inv.added} of "${b.product_name}" to HQ stock (${inv.previous} → ${inv.new}).`
        : `Couldn't add "${b.product_name}" to HQ stock: ${inv.reason}`);
    }
    loadPlan(months);
  };

  const pushToInventory = async (b: ProductionBatch) => {
    try {
      const res = await addBatchToInventory(b.id);
      setBatches((prev) => prev.map((x) => (x.id === b.id ? res.data : x)));
      const inv = res.data.inventory_result;
      if (inv?.ok) flash(`Added ${inv.added} of "${b.product_name}" to HQ stock (${inv.previous} → ${inv.new}).`);
      loadPlan(months);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      flash(msg || "Could not add to HQ stock.");
    }
  };

  const removeBatch = async (id: number) => {
    await deleteProductionBatch(id);
    setBatches((prev) => prev.filter((b) => b.id !== id));
    await loadPlan(months);
  };

  const filteredPlan = useMemo(() => {
    if (!planSearch) return plan;
    return plan.filter((p) => matchesSearch(planSearch, p.name, p.sku));
  }, [plan, planSearch]);

  const selectablePlan = useMemo(
    () => filteredPlan.filter((p) => p.to_produce > 0),
    [filteredPlan],
  );
  const allSelected = selectablePlan.length > 0 && selectablePlan.every((p) => selected.has(planKey(p)));
  const selectedCount = selectablePlan.filter((p) => selected.has(planKey(p))).length;

  const toggleSelectAll = () => {
    setSelected((prev) => {
      if (selectablePlan.every((p) => prev.has(planKey(p)))) return new Set();
      return new Set(selectablePlan.map(planKey));
    });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Factory className="w-7 h-7 text-green-600" />
            Production
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Auto-planned from Smart PAR &mdash; make what you're short on, and track each batch to done
          </p>
        </div>
        <button
          onClick={() => { loadPlan(months); loadBatches(); }}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-2 text-sm bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden bg-white">
        {([["plan", "Plan"], ["board", "Board"]] as [Tab, string][]).map(([id, label], idx) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${idx > 0 ? "border-l border-gray-300" : ""} ${
              tab === id ? "bg-green-600 text-white" : "text-gray-700 hover:bg-gray-50"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">{error}</div>
      )}
      {toast && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-sm text-emerald-800 flex items-start justify-between gap-3">
          <span>{toast}</span>
          <button onClick={() => setToast("")} className="text-emerald-500 hover:text-emerald-700"><X className="w-4 h-4" /></button>
        </div>
      )}

      {/* ── PLAN ─────────────────────────────────────────────────── */}
      {tab === "plan" && (
        <div className="space-y-4">
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 flex flex-col sm:flex-row sm:items-center gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Supply Window</label>
              <p className="text-xs text-gray-400">How many months to keep on hand</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {MONTH_OPTIONS.map((m) => (
                <button
                  key={m}
                  onClick={() => setMonths(m)}
                  disabled={loading}
                  className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors ${
                    months === m ? "bg-green-600 text-white border-green-600" : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                  } disabled:opacity-50`}
                >
                  {m} {m === 1 ? "Month" : "Months"}
                </button>
              ))}
            </div>
          </div>

          {(
            <>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search products to produce..."
                  value={planSearch}
                  onChange={(e) => setPlanSearch(e.target.value)}
                  className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
                />
              </div>

              {loading ? (
                <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-12 text-center">
                  <RefreshCw className="w-8 h-8 text-green-600 animate-spin mx-auto mb-3" />
                  <p className="text-sm text-gray-500">Calculating from sales velocity...</p>
                </div>
              ) : (
                <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                  {selectedCount > 0 ? (
                    <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3 bg-green-50">
                      <span className="text-sm text-green-800 font-medium">{selectedCount} selected</span>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setSelected(new Set())}
                          className="px-2.5 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100 rounded-md"
                        >
                          Clear
                        </button>
                        <button
                          onClick={addSelectedToPlan}
                          disabled={bulkAdding}
                          className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50"
                        >
                          <Plus className="w-3.5 h-3.5" /> {bulkAdding ? "Adding..." : `Create ${selectedCount} batch${selectedCount === 1 ? "" : "es"}`}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="px-4 py-3 border-b border-gray-100 text-sm text-gray-500">
                      <strong>{filteredPlan.length}</strong> products (all except LeafLife) &mdash; <strong>To Produce</strong> = need from Smart PAR (<strong>Planned</strong> shows batches already in the pipeline). Tick rows to create several batches at once.
                    </div>
                  )}
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-gray-50 text-left">
                        <tr>
                          <th className="px-4 py-3 w-10">
                            <input
                              type="checkbox"
                              checked={allSelected}
                              disabled={selectablePlan.length === 0}
                              onChange={toggleSelectAll}
                              className="w-4 h-4 accent-green-600 disabled:opacity-40"
                              title="Select all"
                            />
                          </th>
                          <th className="px-4 py-3 font-medium text-gray-600">Product</th>
                          <th className="px-4 py-3 font-medium text-gray-600 text-right whitespace-nowrap">In Stock</th>
                          <th className="px-4 py-3 font-medium text-gray-600 text-right whitespace-nowrap">Sold/mo</th>
                          <th className="px-4 py-3 font-medium text-gray-600 text-right whitespace-nowrap">Need</th>
                          <th className="px-4 py-3 font-medium text-gray-600 text-right whitespace-nowrap">Planned</th>
                          <th className="px-4 py-3 font-medium text-amber-700 bg-amber-50 text-right whitespace-nowrap">To Produce</th>
                          <th className="px-4 py-3"></th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {filteredPlan.map((p) => (
                          <tr key={planKey(p)} className={`hover:bg-gray-50 ${selected.has(planKey(p)) ? "bg-green-50/60" : ""}`}>
                            <td className="px-4 py-3">
                              {p.to_produce > 0 && (
                                <input
                                  type="checkbox"
                                  checked={selected.has(planKey(p))}
                                  onChange={() => toggleSelect(planKey(p))}
                                  className="w-4 h-4 accent-green-600"
                                />
                              )}
                            </td>
                            <td className="px-4 py-3">
                              <div className="font-medium text-gray-900">{p.name}</div>
                              <div className="text-xs text-gray-400">{p.categories.join(", ")}</div>
                            </td>
                            <td className="px-4 py-3 text-right text-gray-600">
                              {p.in_stock}
                              {p.stock_by_location && Object.keys(p.stock_by_location).length > 0 && (
                                <div className="text-xs text-gray-400 mt-0.5">
                                  {Object.entries(p.stock_by_location)
                                    .map(([loc, qty]) => `${loc.replace(" Location", "").replace("Hemp Dispensary ", "")}: ${qty}`)
                                    .join(" · ")}
                                </div>
                              )}
                            </td>
                            <td className="px-4 py-3 text-right text-gray-600">{p.units_per_month}</td>
                            <td className="px-4 py-3 text-right text-gray-600">{p.needed}</td>
                            <td className="px-4 py-3 text-right text-gray-500">{p.already_planned || <span className="text-gray-300">&mdash;</span>}</td>
                            <td className="px-4 py-3 text-right bg-amber-50/50">
                              {p.to_produce > 0
                                ? <span className="font-semibold text-amber-700">{p.to_produce}</span>
                                : <span className="text-gray-300">&mdash;</span>}
                            </td>
                            <td className="px-4 py-3 text-right">
                              {p.to_produce > 0 ? (
                                <button
                                  onClick={() => addToPlan(p)}
                                  className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium bg-green-600 text-white rounded-md hover:bg-green-700"
                                >
                                  <Plus className="w-3.5 h-3.5" /> Add batch
                                </button>
                              ) : (
                                <button
                                  onClick={() => openBatchForPlanItem(p)}
                                  title="Set a quantity and add this product to the board"
                                  className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-green-700 border border-green-200 rounded-md hover:bg-green-50"
                                >
                                  <Plus className="w-3.5 h-3.5" /> Add batch
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                        {filteredPlan.length === 0 && (
                          <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-500">Nothing to produce right now.</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── BOARD ────────────────────────────────────────────────── */}
      {tab === "board" && (
        <div className="space-y-4">
          <div className="flex justify-end">
            <button
              onClick={() => setEditing({
                id: 0, sku: null, product_name: "", size: null, planned_qty: 0, produced_qty: 0,
                status: "planned", batch_no: null, expiration_date: null, made_by: null,
                qa_check: false, label_ordered: false, label_qty: null, notes: null,
                source: "manual", plan_date: null, completed_at: null,
                inventoried: false, inventoried_at: null, inventoried_qty: null,
                sort_order: 0, created_at: "", updated_at: "",
              })}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium bg-green-600 text-white rounded-lg hover:bg-green-700"
            >
              <Plus className="w-4 h-4" /> New batch
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {STATUS_COLUMNS.map((col) => {
              const Icon = col.icon;
              const colBatches = batches.filter((b) => b.status === col.id);
              return (
                <div
                  key={col.id}
                  className="bg-gray-50 rounded-xl border border-gray-200 p-3"
                  onDragOver={(e) => { if (dragId !== null) e.preventDefault(); }}
                  onDrop={(e) => { e.preventDefault(); reorderWithinColumn(col.id, null); }}
                >
                  <div className="flex items-center gap-2 mb-3 px-1">
                    <Icon className={`w-4 h-4 ${col.color}`} />
                    <span className="font-semibold text-sm text-gray-700">{col.label}</span>
                    <span className="ml-auto text-xs text-gray-400">{colBatches.length}</span>
                  </div>
                  <div className="space-y-2 min-h-[8px]">
                    {colBatches.map((b, idx) => (
                      <div
                        key={b.id}
                        draggable
                        onDragStart={(e) => {
                          dragIdRef.current = b.id;
                          setDragId(b.id);
                          e.dataTransfer.effectAllowed = "move";
                          e.dataTransfer.setData("text/plain", String(b.id));
                        }}
                        onDragEnd={() => { dragIdRef.current = null; setDragId(null); setDragOverId(null); }}
                        onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (dragId !== null && dragId !== b.id) setDragOverId(b.id); }}
                        onDragLeave={() => setDragOverId((cur) => (cur === b.id ? null : cur))}
                        onDrop={(e) => { e.preventDefault(); e.stopPropagation(); reorderWithinColumn(col.id, b.id); }}
                        className={`bg-white rounded-lg border p-3 shadow-sm transition ${
                          dragOverId === b.id ? "border-green-400 ring-2 ring-green-200" : "border-gray-200"
                        } ${dragId === b.id ? "opacity-50" : ""}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-start gap-1.5 min-w-0">
                            <span className="shrink-0 flex flex-col -my-0.5">
                              <button
                                onClick={() => moveCard(col.id, b.id, -1)}
                                disabled={idx === 0}
                                title="Move up"
                                className="text-gray-300 hover:text-green-600 disabled:opacity-30 disabled:hover:text-gray-300 leading-none"
                              >
                                <ChevronUp className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => moveCard(col.id, b.id, 1)}
                                disabled={idx === colBatches.length - 1}
                                title="Move down"
                                className="text-gray-300 hover:text-green-600 disabled:opacity-30 disabled:hover:text-gray-300 leading-none"
                              >
                                <ChevronDown className="w-3.5 h-3.5" />
                              </button>
                            </span>
                            <button onClick={() => setEditing(b)} className="text-left font-medium text-sm text-gray-900 hover:text-green-700">
                              {b.product_name}
                            </button>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button onClick={() => setEditing(b)} title="Edit / rename / add note" className="text-gray-300 hover:text-green-600">
                              <Pencil className="w-3.5 h-3.5" />
                            </button>
                            <button onClick={() => removeBatch(b.id)} title="Delete batch" className="text-gray-300 hover:text-red-500">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                        <div className="text-xs text-gray-500 mt-1 space-y-0.5">
                          {b.size && <div>Size: {b.size}</div>}
                          <div>
                            Qty: {b.status === "done" || b.produced_qty ? `${b.produced_qty || b.planned_qty} made` : `${b.planned_qty} planned`}
                          </div>
                          {b.batch_no && <div>Batch #{b.batch_no}</div>}
                          {b.expiration_date && <div>Exp: {b.expiration_date}</div>}
                          {b.made_by && <div>By: {b.made_by}</div>}
                          {b.notes && <div className="mt-1 text-gray-600 bg-gray-50 rounded px-1.5 py-1 whitespace-pre-wrap break-words">{b.notes}</div>}
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5 mt-2">
                          {b.qa_check && <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700"><Check className="w-2.5 h-2.5" />QA</span>}
                          {b.label_ordered && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">Label</span>}
                          {b.source === "smart_par" && <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 text-purple-700">Smart PAR</span>}
                          {b.inventoried && <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700"><Boxes className="w-2.5 h-2.5" />In HQ stock</span>}
                        </div>
                        {NEXT_STATUS[b.status] && (
                          <button
                            onClick={() => advance(b)}
                            className="mt-2 w-full text-xs font-medium text-green-700 border border-green-200 rounded-md py-1 hover:bg-green-50"
                          >
                            Move to {STATUS_COLUMNS.find((c) => c.id === NEXT_STATUS[b.status])?.label}
                          </button>
                        )}
                        {b.status === "done" && !b.inventoried && (
                          <button
                            onClick={() => pushToInventory(b)}
                            className="mt-2 w-full inline-flex items-center justify-center gap-1 text-xs font-medium text-emerald-700 border border-emerald-200 rounded-md py-1 hover:bg-emerald-50"
                          >
                            <Boxes className="w-3 h-3" /> Add to HQ stock
                          </button>
                        )}
                      </div>
                    ))}
                    {colBatches.length === 0 && (
                      <p className="text-xs text-gray-400 text-center py-4">Empty</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {editing && (
        <BatchModal
          batch={editing}
          products={inventory}
          onClose={() => setEditing(null)}
          onSaved={(saved, isNew) => {
            setBatches((prev) => isNew ? [saved, ...prev] : prev.map((b) => (b.id === saved.id ? saved : b)));
            setEditing(null);
            const inv = saved.inventory_result;
            if (inv) {
              flash(inv.ok
                ? `Added ${inv.added} of "${saved.product_name}" to HQ stock (${inv.previous} → ${inv.new}).`
                : `Couldn't add "${saved.product_name}" to HQ stock: ${inv.reason}`);
            }
            loadPlan(months);
          }}
        />
      )}
    </div>
  );
}

function BatchModal({ batch, products, onClose, onSaved }: {
  batch: ProductionBatch;
  products: InvItem[];
  onClose: () => void;
  onSaved: (b: ProductionBatch, isNew: boolean) => void;
}) {
  const isNew = batch.id === 0;
  const [form, setForm] = useState<ProductionBatch>(batch);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [addToInventory, setAddToInventory] = useState(true);
  const [prodQuery, setProdQuery] = useState("");
  const [showList, setShowList] = useState(false);

  const set = <K extends keyof ProductionBatch>(k: K, v: ProductionBatch[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const matches = useMemo(() => {
    const q = prodQuery.trim();
    if (!q) return [];
    return products
      .filter((p) => matchesSearch(q, p.name, p.sku))
      .slice(0, 8);
  }, [prodQuery, products]);

  const willInventory = addToInventory && form.status === "done" && !form.inventoried && !!form.sku;

  const save = async () => {
    if (!form.product_name.trim()) { setErr("Product name is required"); return; }
    setSaving(true); setErr("");
    const payload: BatchPayload = {
      product_name: form.product_name,
      sku: form.sku,
      size: form.size,
      planned_qty: Number(form.planned_qty) || 0,
      produced_qty: Number(form.produced_qty) || 0,
      status: form.status,
      batch_no: form.batch_no,
      expiration_date: form.expiration_date,
      made_by: form.made_by,
      qa_check: form.qa_check,
      label_ordered: form.label_ordered,
      label_qty: form.label_qty,
      notes: form.notes,
      add_to_inventory: addToInventory,
    };
    try {
      const res = isNew ? await createProductionBatch(payload) : await updateProductionBatch(form.id, payload);
      onSaved(res.data, isNew);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Save failed");
      setSaving(false);
    }
  };

  const input = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-green-500";
  const label = "block text-xs font-medium text-gray-600 mb-1";

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 sticky top-0 bg-white">
          <h2 className="font-semibold text-gray-900">{isNew ? "New Batch" : "Edit Batch"}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-5 space-y-4">
          {err && <div className="bg-red-50 border border-red-200 rounded-lg p-2.5 text-sm text-red-700">{err}</div>}
          <div>
            <label className={label}>Product name / title</label>
            <input className={input} value={form.product_name} onChange={(e) => { set("product_name", e.target.value); set("sku", null); }} />
            <p className="text-xs text-gray-400 mt-1">Rename freely (e.g. making a different strain in place of one you're out of). Editing the name unlinks the catalog SKU &mdash; re-link below if you want "Done" to add it to HQ stock.</p>
            {form.sku && <p className="text-xs text-emerald-600 mt-1">Linked to {form.sku} &mdash; "Done" can add to HQ stock</p>}
            <div className="relative mt-2">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                className="w-full pl-8 pr-3 py-1.5 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-1 focus:ring-green-500"
                placeholder="Link to a catalog product (for repackaged / ad-hoc items)..."
                value={prodQuery}
                onChange={(e) => { setProdQuery(e.target.value); setShowList(true); }}
                onFocus={() => setShowList(true)}
              />
              {showList && matches.length > 0 && (
                <div className="absolute z-10 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                  {matches.map((p) => (
                    <button
                      key={p.sku}
                      onClick={() => { set("product_name", p.name); set("sku", p.sku); setProdQuery(""); setShowList(false); }}
                      className="block w-full text-left px-3 py-2 text-xs hover:bg-gray-50"
                    >
                      <div className="text-gray-900">{p.name}</div>
                      <div className="text-gray-400">{p.sku}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={label}>Size</label>
              <input className={input} value={form.size || ""} onChange={(e) => set("size", e.target.value)} placeholder="e.g. 2 oz, 100 ct" />
            </div>
            <div>
              <label className={label}>Status</label>
              <select className={input} value={form.status} onChange={(e) => set("status", e.target.value as ProductionBatch["status"])}>
                {STATUS_COLUMNS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>Planned Qty</label>
              <input type="number" className={input} value={form.planned_qty} onChange={(e) => set("planned_qty", Number(e.target.value))} />
            </div>
            <div>
              <label className={label}>Produced Qty</label>
              <input type="number" className={input} value={form.produced_qty} onChange={(e) => set("produced_qty", Number(e.target.value))} />
            </div>
            <div>
              <label className={label}>Batch #</label>
              <input className={input} value={form.batch_no || ""} onChange={(e) => set("batch_no", e.target.value)} />
            </div>
            <div>
              <label className={label}>Expiration</label>
              <input className={input} value={form.expiration_date || ""} onChange={(e) => set("expiration_date", e.target.value)} placeholder="MM/DD/YY" />
            </div>
            <div>
              <label className={label}>Made By</label>
              <input className={input} value={form.made_by || ""} onChange={(e) => set("made_by", e.target.value)} />
            </div>
            <div>
              <label className={label}>Labels Ordered (qty)</label>
              <input type="number" className={input} value={form.label_qty ?? ""} onChange={(e) => set("label_qty", e.target.value === "" ? null : Number(e.target.value))} />
            </div>
          </div>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={form.qa_check} onChange={(e) => set("qa_check", e.target.checked)} className="w-4 h-4 accent-green-600" />
              QA checked
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={form.label_ordered} onChange={(e) => set("label_ordered", e.target.checked)} className="w-4 h-4 accent-green-600" />
              Labels ordered
            </label>
          </div>
          <div>
            <label className={label}>Notes</label>
            <textarea className={input} rows={2} value={form.notes || ""} onChange={(e) => set("notes", e.target.value)} />
          </div>
          {!form.inventoried && (
            <div className="bg-emerald-50 border border-emerald-100 rounded-lg p-3">
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input type="checkbox" checked={addToInventory} onChange={(e) => setAddToInventory(e.target.checked)} className="w-4 h-4 accent-emerald-600" />
                Add produced qty to HQ stock when marked <strong>Done</strong>
              </label>
              {willInventory && (
                <p className="text-xs text-emerald-700 mt-1">
                  Will add {Number(form.produced_qty) || Number(form.planned_qty) || 0} to HQ inventory on save.
                </p>
              )}
              {addToInventory && form.status === "done" && !form.sku && (
                <p className="text-xs text-amber-600 mt-1">Link a catalog product above so it can be added to HQ stock.</p>
              )}
            </div>
          )}
          {form.inventoried && (
            <div className="bg-emerald-50 border border-emerald-100 rounded-lg p-3 text-sm text-emerald-700">
              Already added {form.inventoried_qty} to HQ stock{form.inventoried_at ? ` on ${form.inventoried_at}` : ""}.
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-gray-100 sticky bottom-0 bg-white">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">Cancel</button>
          <button onClick={save} disabled={saving} className="px-4 py-2 text-sm font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50">
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
