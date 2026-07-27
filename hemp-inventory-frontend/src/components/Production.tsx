import { useEffect, useMemo, useState } from "react";
import {
  Factory, RefreshCw, Search, Plus, Trash2, X, Check, ClipboardList,
  Beaker, PackageCheck, CheckCircle2,
} from "lucide-react";
import {
  getProductionPlan, getProductionBatches, createProductionBatch,
  updateProductionBatch, deleteProductionBatch, getProductionFlags,
  setProductionFlag, seedProductionFlags, getCachedInventory,
  type ProductionPlanItem, type ProductionBatch, type BatchPayload,
} from "../lib/api";

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

type Tab = "plan" | "board" | "products";

interface InvItem { sku: string; name: string; categories?: string[]; }

export default function Production() {
  const [tab, setTab] = useState<Tab>("plan");
  const [months, setMonths] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [plan, setPlan] = useState<ProductionPlanItem[]>([]);
  const [flaggedCount, setFlaggedCount] = useState(0);
  const [batches, setBatches] = useState<ProductionBatch[]>([]);

  const [flags, setFlags] = useState<Set<string>>(new Set());
  const [inventory, setInventory] = useState<InvItem[]>([]);
  const [prodSearch, setProdSearch] = useState("");
  const [planSearch, setPlanSearch] = useState("");
  const [seeding, setSeeding] = useState(false);
  const [seedMsg, setSeedMsg] = useState("");

  const [editing, setEditing] = useState<ProductionBatch | null>(null);

  const loadPlan = async (m: number) => {
    setLoading(true); setError("");
    try {
      const res = await getProductionPlan(m);
      setPlan(res.data.items);
      setFlaggedCount(res.data.meta.flagged);
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

  const loadFlags = async () => {
    try {
      const res = await getProductionFlags();
      setFlags(new Set(res.data.flags.map((f) => f.sku)));
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
  useEffect(() => { loadBatches(); loadFlags(); }, []);
  useEffect(() => { if (tab === "products" && inventory.length === 0) loadInventory(); }, [tab]);

  const toggleFlag = async (sku: string, name: string) => {
    const on = !flags.has(sku);
    setFlags((prev) => {
      const next = new Set(prev);
      if (on) next.add(sku); else next.delete(sku);
      return next;
    });
    try {
      await setProductionFlag(sku, on, name);
    } catch {
      // revert on failure
      setFlags((prev) => {
        const next = new Set(prev);
        if (on) next.delete(sku); else next.add(sku);
        return next;
      });
    }
  };

  const runSeed = async () => {
    setSeeding(true); setSeedMsg("");
    try {
      const res = await seedProductionFlags();
      setSeedMsg(`Flagged ${res.data.added} product${res.data.added === 1 ? "" : "s"} (${res.data.already_flagged} already flagged).`);
      await loadFlags();
    } catch (e: unknown) {
      setSeedMsg(e instanceof Error ? e.message : "Seeding failed");
    } finally { setSeeding(false); }
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

  const advance = async (b: ProductionBatch) => {
    const next = NEXT_STATUS[b.status];
    if (!next) return;
    const res = await updateProductionBatch(b.id, { status: next });
    setBatches((prev) => prev.map((x) => (x.id === b.id ? res.data : x)));
  };

  const removeBatch = async (id: number) => {
    await deleteProductionBatch(id);
    setBatches((prev) => prev.filter((b) => b.id !== id));
    await loadPlan(months);
  };

  const filteredPlan = useMemo(() => {
    if (!planSearch) return plan;
    const q = planSearch.toLowerCase();
    return plan.filter((p) => p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q));
  }, [plan, planSearch]);

  const filteredInventory = useMemo(() => {
    let list = inventory;
    if (prodSearch) {
      const q = prodSearch.toLowerCase();
      list = list.filter((i) => i.name.toLowerCase().includes(q) || i.sku.toLowerCase().includes(q));
    }
    // flagged first, then alphabetical
    return [...list].sort((a, b) => {
      const fa = flags.has(a.sku) ? 0 : 1;
      const fb = flags.has(b.sku) ? 0 : 1;
      if (fa !== fb) return fa - fb;
      return a.name.localeCompare(b.name);
    });
  }, [inventory, prodSearch, flags]);

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
        {([["plan", "Plan"], ["board", "Board"], ["products", "Made In-House"]] as [Tab, string][]).map(([id, label], idx) => (
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

          {flaggedCount === 0 && !loading ? (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800">
              No products are flagged as made in-house yet. Go to the <strong>Made In-House</strong> tab and hit
              <strong> Auto-detect from sheets</strong> (or flag products manually) to build the plan.
            </div>
          ) : (
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
                  <div className="px-4 py-3 border-b border-gray-100 text-sm text-gray-500">
                    <strong>{filteredPlan.length}</strong> in-house products &mdash; <strong>To Produce</strong> = need (from Smart PAR) minus what's already planned
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-gray-50 text-left">
                        <tr>
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
                          <tr key={p.sku} className="hover:bg-gray-50">
                            <td className="px-4 py-3">
                              <div className="font-medium text-gray-900">{p.name}</div>
                              <div className="text-xs text-gray-400">{p.categories.join(", ")}</div>
                            </td>
                            <td className="px-4 py-3 text-right text-gray-600">{p.in_stock}</td>
                            <td className="px-4 py-3 text-right text-gray-600">{p.units_per_month}</td>
                            <td className="px-4 py-3 text-right text-gray-600">{p.needed}</td>
                            <td className="px-4 py-3 text-right text-gray-500">{p.already_planned || <span className="text-gray-300">&mdash;</span>}</td>
                            <td className="px-4 py-3 text-right bg-amber-50/50">
                              {p.to_produce > 0
                                ? <span className="font-semibold text-amber-700">{p.to_produce}</span>
                                : <span className="text-gray-300">&mdash;</span>}
                            </td>
                            <td className="px-4 py-3 text-right">
                              {p.to_produce > 0 && (
                                <button
                                  onClick={() => addToPlan(p)}
                                  className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium bg-green-600 text-white rounded-md hover:bg-green-700"
                                >
                                  <Plus className="w-3.5 h-3.5" /> Add batch
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                        {filteredPlan.length === 0 && (
                          <tr><td colSpan={7} className="px-4 py-8 text-center text-gray-500">Nothing to produce right now.</td></tr>
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
                source: "manual", plan_date: null, completed_at: null, created_at: "", updated_at: "",
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
                <div key={col.id} className="bg-gray-50 rounded-xl border border-gray-200 p-3">
                  <div className="flex items-center gap-2 mb-3 px-1">
                    <Icon className={`w-4 h-4 ${col.color}`} />
                    <span className="font-semibold text-sm text-gray-700">{col.label}</span>
                    <span className="ml-auto text-xs text-gray-400">{colBatches.length}</span>
                  </div>
                  <div className="space-y-2">
                    {colBatches.map((b) => (
                      <div key={b.id} className="bg-white rounded-lg border border-gray-200 p-3 shadow-sm">
                        <div className="flex items-start justify-between gap-2">
                          <button onClick={() => setEditing(b)} className="text-left font-medium text-sm text-gray-900 hover:text-green-700">
                            {b.product_name}
                          </button>
                          <button onClick={() => removeBatch(b.id)} className="text-gray-300 hover:text-red-500">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                        <div className="text-xs text-gray-500 mt-1 space-y-0.5">
                          {b.size && <div>Size: {b.size}</div>}
                          <div>
                            Qty: {b.status === "done" || b.produced_qty ? `${b.produced_qty || b.planned_qty} made` : `${b.planned_qty} planned`}
                          </div>
                          {b.batch_no && <div>Batch #{b.batch_no}</div>}
                          {b.expiration_date && <div>Exp: {b.expiration_date}</div>}
                          {b.made_by && <div>By: {b.made_by}</div>}
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5 mt-2">
                          {b.qa_check && <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700"><Check className="w-2.5 h-2.5" />QA</span>}
                          {b.label_ordered && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">Label</span>}
                          {b.source === "smart_par" && <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 text-purple-700">Smart PAR</span>}
                        </div>
                        {NEXT_STATUS[b.status] && (
                          <button
                            onClick={() => advance(b)}
                            className="mt-2 w-full text-xs font-medium text-green-700 border border-green-200 rounded-md py-1 hover:bg-green-50"
                          >
                            Move to {STATUS_COLUMNS.find((c) => c.id === NEXT_STATUS[b.status])?.label}
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

      {/* ── MADE IN-HOUSE ────────────────────────────────────────── */}
      {tab === "products" && (
        <div className="space-y-4">
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-gray-900">{flags.size} products flagged as made in-house</p>
              <p className="text-xs text-gray-400">These drive the production plan. Auto-detect matches your two production sheets, then adjust below.</p>
            </div>
            <button
              onClick={runSeed}
              disabled={seeding}
              className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 whitespace-nowrap"
            >
              <RefreshCw className={`w-4 h-4 ${seeding ? "animate-spin" : ""}`} />
              Auto-detect from sheets
            </button>
          </div>
          {seedMsg && <div className="text-sm text-gray-600">{seedMsg}</div>}

          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search products..."
              value={prodSearch}
              onChange={(e) => setProdSearch(e.target.value)}
              className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
            />
          </div>

          <div className="bg-white rounded-xl shadow-sm border border-gray-200 divide-y divide-gray-100 max-h-[600px] overflow-y-auto">
            {filteredInventory.map((i) => {
              const on = flags.has(i.sku);
              return (
                <label key={i.sku} className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-gray-50">
                  <input type="checkbox" checked={on} onChange={() => toggleFlag(i.sku, i.name)} className="w-4 h-4 accent-green-600" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-gray-900 truncate">{i.name}</div>
                    <div className="text-xs text-gray-400">{i.sku}{i.categories?.length ? ` · ${i.categories.join(", ")}` : ""}</div>
                  </div>
                  {on && <span className="text-xs text-green-600 font-medium whitespace-nowrap">In-house</span>}
                </label>
              );
            })}
            {filteredInventory.length === 0 && (
              <p className="px-4 py-8 text-center text-sm text-gray-500">No products found.</p>
            )}
          </div>
        </div>
      )}

      {editing && (
        <BatchModal
          batch={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved, isNew) => {
            setBatches((prev) => isNew ? [saved, ...prev] : prev.map((b) => (b.id === saved.id ? saved : b)));
            setEditing(null);
            loadPlan(months);
          }}
        />
      )}
    </div>
  );
}

function BatchModal({ batch, onClose, onSaved }: {
  batch: ProductionBatch;
  onClose: () => void;
  onSaved: (b: ProductionBatch, isNew: boolean) => void;
}) {
  const isNew = batch.id === 0;
  const [form, setForm] = useState<ProductionBatch>(batch);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const set = <K extends keyof ProductionBatch>(k: K, v: ProductionBatch[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

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
            <label className={label}>Product</label>
            <input className={input} value={form.product_name} onChange={(e) => set("product_name", e.target.value)} />
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
