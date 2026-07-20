import { useState, useEffect, useCallback } from "react";
import {
  getCoaStatus,
  syncCoaResults,
  getCoaSamples,
  getCoaSample,
  linkSkuToCoa,
  unlinkSkuFromCoa,
  getEcommerceProducts,
  createManualCoa,
  updateManualCoa,
  deleteManualCoa,
  uploadCoaFile,
  coaFileUrl,
  type ManualCoaAnalyte,
} from "../lib/api";
import {
  RefreshCw,
  Search,
  FlaskConical,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronUp,
  Link2,
  Unlink,
  ArrowLeft,
  Wifi,
  WifiOff,
  Beaker,
  FileText,
  Package,
  Plus,
  Pencil,
  Trash2,
  ExternalLink,
} from "lucide-react";

interface CoaProduct {
  description: string;
  batch_no: string;
  business_name: string;
  product_type: string;
  order_number: string;
  sample_status: string;
  coa_approved_date: string;
  sample_count: number;
  first_accession: string;
  linked_skus: string | null;
  source?: string;
  coa_approved_filepath?: string | null;
}

interface CoaSample {
  id: number;
  sample_accession: string;
  order_number: string;
  batch_no: string;
  business_name: string;
  product_name: string;
  product_type: string;
  consumption_type: string;
  description: string;
  test_purpose: string;
  sample_status: string;
  order_date: string;
  test_start_date: string;
  coa_approved_date: string;
  postal_code: string;
  extracted_from: string;
  synced_at: string;
  linked_skus: string | null;
  source?: string;
  coa_approved_filepath?: string | null;
}

interface AnalyteResult {
  id: number;
  sample_accession: string;
  panel_name: string;
  panel_identifier: string;
  analyte_abbreviation: string;
  analyte_identifier: string;
  concentration: number;
  conc_unit: string;
  result: string;
  result_unit: string;
  analyte_remark: string;
  panel_remark: string;
}

interface InventoryItem {
  sku: string;
  name: string;
}

function formatDate(dateStr: string): string {
  if (!dateStr) return "—";
  const d = new Date(dateStr + (dateStr.includes("Z") || dateStr.includes("+") ? "" : "Z"));
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "America/New_York",
  });
}

function statusColor(status: string): string {
  const s = (status || "").toLowerCase();
  if (s.includes("completed") || s.includes("passed")) return "bg-green-100 text-green-700";
  if (s.includes("progress") || s.includes("pending")) return "bg-yellow-100 text-yellow-700";
  if (s.includes("failed") || s.includes("incomplete")) return "bg-red-100 text-red-700";
  return "bg-gray-100 text-gray-700";
}

function remarkBadge(remark: string): string {
  const r = (remark || "").toLowerCase();
  if (r === "passed" || r === "pass") return "bg-green-100 text-green-700";
  if (r === "failed" || r === "fail") return "bg-red-100 text-red-700";
  if (r.includes("incomplete")) return "bg-yellow-100 text-yellow-700";
  return "bg-gray-100 text-gray-600";
}

export default function LabResults() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [acsUser, setAcsUser] = useState("");
  const [products, setProducts] = useState<CoaProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [search, setSearch] = useState("");
  const [toast, setToast] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Detail view
  const [selectedAccession, setSelectedAccession] = useState<string | null>(null);
  const [detailSample, setDetailSample] = useState<CoaSample | null>(null);
  const [detailAnalytes, setDetailAnalytes] = useState<AnalyteResult[]>([]);
  const [detailLinkedSkus, setDetailLinkedSkus] = useState<string[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Link modal
  const [showLinkModal, setShowLinkModal] = useState(false);
  const [inventoryItems, setInventoryItems] = useState<InventoryItem[]>([]);
  const [linkSearch, setLinkSearch] = useState("");
  const [linking, setLinking] = useState(false);

  // Expanded panels in detail view
  const [expandedPanels, setExpandedPanels] = useState<Set<string>>(new Set());

  // Manual (non-ACS) COA modal
  const emptyForm = {
    product_name: "",
    description: "",
    batch_no: "",
    business_name: "",
    product_type: "",
    sample_status: "Passed",
    coa_approved_date: "",
    coa_url: "",
  };
  const [uploadingCoa, setUploadingCoa] = useState(false);
  const [showCoaModal, setShowCoaModal] = useState(false);
  const [editingAccession, setEditingAccession] = useState<string | null>(null);
  const [form, setForm] = useState({ ...emptyForm });
  const [formAnalytes, setFormAnalytes] = useState<ManualCoaAnalyte[]>([]);
  const [savingCoa, setSavingCoa] = useState(false);

  const showToast = useCallback((type: "success" | "error", text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 3000);
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const resp = await getCoaStatus();
      setConnected(resp.data.connected);
      setAcsUser(resp.data.user || "");
    } catch {
      setConnected(false);
    }
  }, []);

  const loadProducts = useCallback(async () => {
    try {
      const resp = await getCoaSamples();
      setProducts(resp.data);
    } catch {
      setProducts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    loadProducts();
  }, [loadStatus, loadProducts]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const resp = await syncCoaResults();
      const { synced_samples, synced_analytes } = resp.data;
      showToast("success", `Synced ${synced_samples} samples, ${synced_analytes} analyte results`);
      await loadProducts();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Sync failed";
      showToast("error", msg);
    } finally {
      setSyncing(false);
    }
  };

  const loadDetail = async (accession: string) => {
    setSelectedAccession(accession);
    setLoadingDetail(true);
    try {
      const resp = await getCoaSample(accession);
      setDetailSample(resp.data.sample);
      setDetailAnalytes(resp.data.analytes);
      setDetailLinkedSkus(resp.data.linked_skus);
      // Auto-expand all panels
      const panels = new Set<string>(resp.data.analytes.map((a: AnalyteResult) => a.panel_name || "Other"));
      setExpandedPanels(panels);
    } catch {
      showToast("error", "Failed to load sample details");
    } finally {
      setLoadingDetail(false);
    }
  };

  const openLinkModal = async () => {
    setShowLinkModal(true);
    setLinkSearch("");
    try {
      const resp = await getEcommerceProducts();
      const products = resp.data.products || [];
      const items: InventoryItem[] = products.map(
        (p: { sku: string; name: string }) => ({
          sku: p.sku,
          name: p.name,
        })
      );
      setInventoryItems(items);
    } catch {
      setInventoryItems([]);
    }
  };

  const handleLink = async (sku: string) => {
    if (!selectedAccession) return;
    setLinking(true);
    try {
      await linkSkuToCoa(sku, selectedAccession);
      setDetailLinkedSkus((prev) => [...prev, sku]);
      showToast("success", `Linked ${sku} to ${selectedAccession}`);
      setShowLinkModal(false);
      await loadProducts();
    } catch {
      showToast("error", "Failed to link SKU");
    } finally {
      setLinking(false);
    }
  };

  const handleUnlink = async (sku: string) => {
    if (!selectedAccession) return;
    try {
      await unlinkSkuFromCoa(sku, selectedAccession);
      setDetailLinkedSkus((prev) => prev.filter((s) => s !== sku));
      showToast("success", `Unlinked ${sku}`);
      await loadProducts();
    } catch {
      showToast("error", "Failed to unlink SKU");
    }
  };

  const togglePanel = (panelName: string) => {
    setExpandedPanels((prev) => {
      const next = new Set(prev);
      if (next.has(panelName)) next.delete(panelName);
      else next.add(panelName);
      return next;
    });
  };

  const openAddCoaModal = () => {
    setEditingAccession(null);
    setForm({ ...emptyForm });
    setFormAnalytes([]);
    setShowCoaModal(true);
  };

  const openEditCoaModal = () => {
    if (!detailSample) return;
    setEditingAccession(detailSample.sample_accession);
    setForm({
      product_name: detailSample.product_name || "",
      description: detailSample.description || "",
      batch_no: detailSample.batch_no || "",
      business_name: detailSample.business_name || "",
      product_type: detailSample.product_type || "",
      sample_status: detailSample.sample_status || "Passed",
      coa_approved_date: (detailSample.coa_approved_date || "").slice(0, 10),
      coa_url: detailSample.coa_approved_filepath || "",
    });
    setFormAnalytes(
      detailAnalytes.map((a) => ({
        panel_name: a.panel_name,
        analyte_identifier: a.analyte_identifier || a.analyte_abbreviation,
        result: a.result,
        result_unit: a.result_unit,
        analyte_remark: a.analyte_remark,
      }))
    );
    setShowCoaModal(true);
  };

  const addFormAnalyte = () =>
    setFormAnalytes((prev) => [
      ...prev,
      { panel_name: "Cannabinoids", analyte_identifier: "", result: "", result_unit: "%", analyte_remark: "" },
    ]);

  const updateFormAnalyte = (i: number, patch: Partial<ManualCoaAnalyte>) =>
    setFormAnalytes((prev) => prev.map((a, idx) => (idx === i ? { ...a, ...patch } : a)));

  const removeFormAnalyte = (i: number) =>
    setFormAnalytes((prev) => prev.filter((_, idx) => idx !== i));

  const handleSaveCoa = async () => {
    if (!form.product_name.trim() && !form.description.trim()) {
      showToast("error", "Enter a product name or description");
      return;
    }
    setSavingCoa(true);
    try {
      const payload = { ...form, analytes: formAnalytes };
      if (editingAccession) {
        await updateManualCoa(editingAccession, payload);
        showToast("success", "COA updated");
        await loadDetail(editingAccession);
      } else {
        await createManualCoa(payload);
        showToast("success", "COA added");
      }
      setShowCoaModal(false);
      await loadProducts();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save COA";
      showToast("error", msg);
    } finally {
      setSavingCoa(false);
    }
  };

  const handleUploadCoaFile = async (
    e: React.ChangeEvent<HTMLInputElement>
  ) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploadingCoa(true);
    try {
      const res = await uploadCoaFile(file);
      setForm((f) => ({ ...f, coa_url: res.data.url }));
      showToast("success", "File uploaded");
    } catch {
      showToast("error", "Upload failed (PDF or image, max 25 MB)");
    } finally {
      setUploadingCoa(false);
    }
  };

  const handleDeleteCoa = async () => {
    if (!detailSample) return;
    if (!window.confirm("Delete this COA? This cannot be undone.")) return;
    try {
      await deleteManualCoa(detailSample.sample_accession);
      showToast("success", "COA deleted");
      setSelectedAccession(null);
      await loadProducts();
    } catch {
      showToast("error", "Failed to delete COA");
    }
  };

  const filteredProducts = products.filter((p) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      (p.description || "").toLowerCase().includes(q) ||
      (p.batch_no || "").toLowerCase().includes(q) ||
      (p.order_number || "").toLowerCase().includes(q) ||
      (p.business_name || "").toLowerCase().includes(q) ||
      (p.first_accession || "").toLowerCase().includes(q) ||
      (p.linked_skus || "").toLowerCase().includes(q)
    );
  });

  const filteredLinkItems = inventoryItems.filter((item) => {
    if (!linkSearch) return true;
    const q = linkSearch.toLowerCase();
    return item.sku.toLowerCase().includes(q) || item.name.toLowerCase().includes(q);
  });

  // Group analytes by panel for detail view
  const analytesByPanel: Record<string, AnalyteResult[]> = {};
  for (const a of detailAnalytes) {
    const key = a.panel_name || "Other";
    if (!analytesByPanel[key]) analytesByPanel[key] = [];
    analytesByPanel[key].push(a);
  }

  // ── Manual COA add/edit modal (shared by list + detail views) ────
  const coaModal = showCoaModal && (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        <div className="p-4 border-b border-gray-200 flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">
            {editingAccession ? "Edit COA" : "Add COA (non-ACS lab)"}
          </h3>
          <button onClick={() => setShowCoaModal(false)} className="p-1 hover:bg-gray-100 rounded">
            <XCircle className="w-5 h-5 text-gray-400" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Product Name *</label>
              <input
                type="text"
                value={form.product_name}
                onChange={(e) => setForm({ ...form, product_name: e.target.value })}
                placeholder="e.g. Blue Dream 3.5g"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Lab / Source</label>
              <input
                type="text"
                value={form.business_name}
                onChange={(e) => setForm({ ...form, business_name: e.target.value })}
                placeholder="e.g. Green Scientific Labs"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Batch #</label>
              <input
                type="text"
                value={form.batch_no}
                onChange={(e) => setForm({ ...form, batch_no: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Product Type</label>
              <input
                type="text"
                value={form.product_type}
                onChange={(e) => setForm({ ...form, product_type: e.target.value })}
                placeholder="e.g. Flower, Vape, Edible"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">COA Approved Date</label>
              <input
                type="date"
                value={form.coa_approved_date}
                onChange={(e) => setForm({ ...form, coa_approved_date: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Status</label>
              <select
                value={form.sample_status}
                onChange={(e) => setForm({ ...form, sample_status: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
              >
                <option value="Passed">Passed</option>
                <option value="Failed">Failed</option>
                <option value="Pending">Pending</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Description</label>
            <input
              type="text"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">COA Document</label>
            <div className="flex items-center gap-2">
              <label className="shrink-0 px-3 py-2 border border-gray-200 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 cursor-pointer flex items-center gap-1.5">
                {uploadingCoa ? (
                  <RefreshCw className="w-4 h-4 animate-spin" />
                ) : (
                  <Plus className="w-4 h-4" />
                )}
                {uploadingCoa ? "Uploading..." : "Upload PDF"}
                <input
                  type="file"
                  accept=".pdf,image/*"
                  onChange={handleUploadCoaFile}
                  disabled={uploadingCoa}
                  className="hidden"
                />
              </label>
              <input
                type="url"
                value={form.coa_url}
                onChange={(e) => setForm({ ...form, coa_url: e.target.value })}
                placeholder="...or paste a link to the PDF"
                className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
              />
            </div>
            {form.coa_url && (
              <a
                href={coaFileUrl(form.coa_url)}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-xs text-green-600 hover:text-green-700"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                View attached document
              </a>
            )}
          </div>

          {/* Optional analyte rows */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-medium text-gray-500">Results (optional)</label>
              <button
                onClick={addFormAnalyte}
                className="text-xs text-green-600 font-medium flex items-center gap-1 hover:text-green-700"
              >
                <Plus className="w-3.5 h-3.5" /> Add result
              </button>
            </div>
            {formAnalytes.length === 0 ? (
              <p className="text-xs text-gray-400">No results added. You can add cannabinoid/potency rows or just attach the COA link above.</p>
            ) : (
              <div className="space-y-2">
                {formAnalytes.map((a, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <input
                      type="text"
                      value={a.analyte_identifier || ""}
                      onChange={(e) => updateFormAnalyte(i, { analyte_identifier: e.target.value })}
                      placeholder="Analyte (e.g. Total THC)"
                      className="flex-1 px-2 py-1.5 border border-gray-200 rounded text-sm outline-none focus:ring-1 focus:ring-green-500"
                    />
                    <input
                      type="text"
                      value={a.result || ""}
                      onChange={(e) => updateFormAnalyte(i, { result: e.target.value })}
                      placeholder="Result"
                      className="w-24 px-2 py-1.5 border border-gray-200 rounded text-sm outline-none focus:ring-1 focus:ring-green-500"
                    />
                    <input
                      type="text"
                      value={a.result_unit || ""}
                      onChange={(e) => updateFormAnalyte(i, { result_unit: e.target.value })}
                      placeholder="Unit"
                      className="w-16 px-2 py-1.5 border border-gray-200 rounded text-sm outline-none focus:ring-1 focus:ring-green-500"
                    />
                    <button onClick={() => removeFormAnalyte(i)} className="p-1 hover:bg-red-50 rounded">
                      <Trash2 className="w-4 h-4 text-red-500" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="p-4 border-t border-gray-200 flex justify-end gap-2">
          <button
            onClick={() => setShowCoaModal(false)}
            className="px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={handleSaveCoa}
            disabled={savingCoa}
            className="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 flex items-center gap-2"
          >
            {savingCoa && <RefreshCw className="w-4 h-4 animate-spin" />}
            {editingAccession ? "Save Changes" : "Add COA"}
          </button>
        </div>
      </div>
    </div>
  );

  // ── Detail view ─────────────────────────────────────────────────

  if (selectedAccession) {
    return (
      <div className="space-y-6">
        {/* Toast */}
        {toast && (
          <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg shadow-lg text-sm font-medium ${toast.type === "success" ? "bg-green-600 text-white" : "bg-red-600 text-white"}`}>
            {toast.text}
          </div>
        )}

        {/* Header */}
        <div className="flex items-center gap-4">
          <button
            onClick={() => setSelectedAccession(null)}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-gray-600" />
          </button>
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
              COA: {selectedAccession}
              {detailSample?.source === "manual" && (
                <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                  Manual
                </span>
              )}
            </h1>
            {detailSample && (
              <p className="text-sm text-gray-500">
                {detailSample.description || detailSample.product_name || "Lab Sample"}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {detailSample?.coa_approved_filepath &&
              /^(https?:\/\/|\/api\/coa\/file\/)/.test(detailSample.coa_approved_filepath) && (
                <a
                  href={coaFileUrl(detailSample.coa_approved_filepath)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 border border-gray-200 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-50 flex items-center gap-1.5"
                >
                  <ExternalLink className="w-4 h-4" />
                  View COA
                </a>
              )}
            {detailSample?.source === "manual" && (
              <>
                <button
                  onClick={openEditCoaModal}
                  className="px-3 py-1.5 border border-gray-200 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-50 flex items-center gap-1.5"
                >
                  <Pencil className="w-4 h-4" />
                  Edit
                </button>
                <button
                  onClick={handleDeleteCoa}
                  className="px-3 py-1.5 border border-red-200 text-red-600 text-sm font-medium rounded-lg hover:bg-red-50 flex items-center gap-1.5"
                >
                  <Trash2 className="w-4 h-4" />
                  Delete
                </button>
              </>
            )}
          </div>
        </div>
        {coaModal}

        {loadingDetail ? (
          <div className="flex items-center justify-center py-20">
            <RefreshCw className="w-6 h-6 text-gray-400 animate-spin" />
          </div>
        ) : detailSample ? (
          <>
            {/* Sample Info Card */}
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <FileText className="w-5 h-5 text-green-600" />
                Sample Information
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 text-sm">
                <div>
                  <span className="text-gray-500">Order #:</span>{" "}
                  <span className="font-medium">{detailSample.order_number || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Batch:</span>{" "}
                  <span className="font-medium">{detailSample.batch_no || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Business:</span>{" "}
                  <span className="font-medium">{detailSample.business_name || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Product:</span>{" "}
                  <span className="font-medium">{detailSample.product_name || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Type:</span>{" "}
                  <span className="font-medium">{detailSample.product_type || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Consumption:</span>{" "}
                  <span className="font-medium">{detailSample.consumption_type || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Extracted From:</span>{" "}
                  <span className="font-medium">{detailSample.extracted_from || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Test Purpose:</span>{" "}
                  <span className="font-medium">{detailSample.test_purpose || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Status:</span>{" "}
                  <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${statusColor(detailSample.sample_status)}`}>
                    {detailSample.sample_status || "—"}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Order Date:</span>{" "}
                  <span className="font-medium">{formatDate(detailSample.order_date)}</span>
                </div>
                <div>
                  <span className="text-gray-500">Test Started:</span>{" "}
                  <span className="font-medium">{formatDate(detailSample.test_start_date)}</span>
                </div>
                <div>
                  <span className="text-gray-500">COA Approved:</span>{" "}
                  <span className="font-medium">{formatDate(detailSample.coa_approved_date)}</span>
                </div>
                {detailSample.description && (
                  <div className="md:col-span-2 lg:col-span-3">
                    <span className="text-gray-500">Description:</span>{" "}
                    <span className="font-medium">{detailSample.description}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Linked Products */}
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                  <Package className="w-5 h-5 text-green-600" />
                  Linked Products
                </h2>
                <button
                  onClick={openLinkModal}
                  className="px-3 py-1.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors flex items-center gap-1.5"
                >
                  <Link2 className="w-4 h-4" />
                  Link SKU
                </button>
              </div>
              {detailLinkedSkus.length === 0 ? (
                <p className="text-sm text-gray-500">
                  No products linked yet. Link an inventory SKU to associate this COA with a product.
                </p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {detailLinkedSkus.map((sku) => (
                    <span
                      key={sku}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-50 text-green-700 text-sm font-medium rounded-lg border border-green-200"
                    >
                      {sku}
                      <button
                        onClick={() => handleUnlink(sku)}
                        className="p-0.5 hover:bg-green-100 rounded"
                        title="Unlink"
                      >
                        <Unlink className="w-3.5 h-3.5" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Analyte Results by Panel */}
            {Object.keys(analytesByPanel).length === 0 ? (
              <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-gray-500">
                <Beaker className="w-8 h-8 mx-auto mb-2 text-gray-300" />
                No analyte results available for this sample.
              </div>
            ) : (
              Object.entries(analytesByPanel).map(([panelName, analytes]) => {
                const isExpanded = expandedPanels.has(panelName);
                const overallRemark = analytes[0]?.panel_remark || "";
                return (
                  <div key={panelName} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                    <button
                      onClick={() => togglePanel(panelName)}
                      className="w-full flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <FlaskConical className="w-5 h-5 text-green-600" />
                        <span className="font-semibold text-gray-900">{panelName}</span>
                        <span className="text-sm text-gray-400">({analytes.length} analytes)</span>
                        {overallRemark && (
                          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${remarkBadge(overallRemark)}`}>
                            {overallRemark}
                          </span>
                        )}
                      </div>
                      {isExpanded ? (
                        <ChevronUp className="w-5 h-5 text-gray-400" />
                      ) : (
                        <ChevronDown className="w-5 h-5 text-gray-400" />
                      )}
                    </button>
                    {isExpanded && (
                      <div className="border-t border-gray-200">
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="bg-gray-50 text-gray-500 text-left">
                                <th className="px-6 py-3 font-medium">Analyte</th>
                                <th className="px-6 py-3 font-medium">Result</th>
                                <th className="px-6 py-3 font-medium">Concentration</th>
                                <th className="px-6 py-3 font-medium">Status</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-100">
                              {analytes.map((a, i) => (
                                <tr key={i} className="hover:bg-gray-50">
                                  <td className="px-6 py-3">
                                    <div className="font-medium text-gray-900">
                                      {a.analyte_abbreviation || a.analyte_identifier || "—"}
                                    </div>
                                    {a.analyte_identifier && a.analyte_abbreviation && a.analyte_identifier !== a.analyte_abbreviation && (
                                      <div className="text-xs text-gray-400">{a.analyte_identifier}</div>
                                    )}
                                  </td>
                                  <td className="px-6 py-3 font-mono text-gray-700">
                                    {a.result || "—"} {a.result_unit || ""}
                                  </td>
                                  <td className="px-6 py-3 font-mono text-gray-700">
                                    {a.concentration != null && a.concentration !== 0
                                      ? `${a.concentration} ${a.conc_unit || ""}`
                                      : "—"}
                                  </td>
                                  <td className="px-6 py-3">
                                    {a.analyte_remark ? (
                                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${remarkBadge(a.analyte_remark)}`}>
                                        {a.analyte_remark.toLowerCase().includes("pass") ? (
                                          <CheckCircle2 className="w-3 h-3" />
                                        ) : a.analyte_remark.toLowerCase().includes("fail") ? (
                                          <XCircle className="w-3 h-3" />
                                        ) : null}
                                        {a.analyte_remark}
                                      </span>
                                    ) : (
                                      <span className="text-gray-400">—</span>
                                    )}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </>
        ) : (
          <div className="text-center py-10 text-gray-500">Sample not found.</div>
        )}

        {/* Link Modal */}
        {showLinkModal && (
          <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[80vh] flex flex-col">
              <div className="p-4 border-b border-gray-200 flex items-center justify-between">
                <h3 className="font-semibold text-gray-900">Link Product to COA</h3>
                <button onClick={() => setShowLinkModal(false)} className="p-1 hover:bg-gray-100 rounded">
                  <XCircle className="w-5 h-5 text-gray-400" />
                </button>
              </div>
              <div className="p-4 border-b border-gray-200">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                  <input
                    type="text"
                    value={linkSearch}
                    onChange={(e) => setLinkSearch(e.target.value)}
                    placeholder="Search by SKU or product name..."
                    className="w-full pl-10 pr-4 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
                  />
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-2">
                {filteredLinkItems.length === 0 ? (
                  <p className="text-center text-gray-500 text-sm py-4">No products found</p>
                ) : (
                  filteredLinkItems.slice(0, 50).map((item) => (
                    <button
                      key={item.sku}
                      onClick={() => handleLink(item.sku)}
                      disabled={linking || detailLinkedSkus.includes(item.sku)}
                      className={`w-full text-left px-4 py-3 rounded-lg text-sm hover:bg-green-50 transition-colors flex items-center justify-between ${
                        detailLinkedSkus.includes(item.sku) ? "opacity-50 cursor-not-allowed" : ""
                      }`}
                    >
                      <div>
                        <div className="font-medium text-gray-900">{item.name}</div>
                        <div className="text-xs text-gray-500">SKU: {item.sku}</div>
                      </div>
                      {detailLinkedSkus.includes(item.sku) ? (
                        <span className="text-xs text-green-600 font-medium">Linked</span>
                      ) : (
                        <Link2 className="w-4 h-4 text-gray-400" />
                      )}
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── List view ───────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg shadow-lg text-sm font-medium ${toast.type === "success" ? "bg-green-600 text-white" : "bg-red-600 text-white"}`}>
          {toast.text}
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Lab Results (COA)</h1>
          <p className="text-sm text-gray-500 mt-1">
            ACS Laboratory results plus manually-added non-ACS COAs
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Connection status */}
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium ${
            connected === true
              ? "bg-green-50 text-green-700 border border-green-200"
              : connected === false
              ? "bg-red-50 text-red-700 border border-red-200"
              : "bg-gray-50 text-gray-500 border border-gray-200"
          }`}>
            {connected === true ? (
              <>
                <Wifi className="w-3.5 h-3.5" />
                ACS Connected{acsUser ? ` (${acsUser})` : ""}
              </>
            ) : connected === false ? (
              <>
                <WifiOff className="w-3.5 h-3.5" />
                Not Connected
              </>
            ) : (
              "Checking..."
            )}
          </div>
          <button
            onClick={openAddCoaModal}
            className="px-4 py-2 border border-green-600 text-green-700 text-sm font-medium rounded-lg hover:bg-green-50 transition-colors flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Add COA
          </button>
          <button
            onClick={handleSync}
            disabled={syncing || connected === false}
            className="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
            {syncing ? "Syncing..." : "Sync from ACS Lab"}
          </button>
        </div>
      </div>
      {coaModal}

      {/* Search */}
      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by accession, description, batch, SKU..."
          className="w-full pl-10 pr-4 py-2.5 bg-white border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none"
        />
      </div>

      {/* Products list */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <RefreshCw className="w-6 h-6 text-gray-400 animate-spin" />
        </div>
      ) : filteredProducts.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <FlaskConical className="w-12 h-12 text-gray-300 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            {products.length === 0 ? "No Lab Results Yet" : "No Matching Results"}
          </h3>
          <p className="text-sm text-gray-500 max-w-md mx-auto">
            {products.length === 0
              ? "Click \"Sync from ACS Lab\" to pull your Certificate of Analysis results from ACS Laboratory."
              : "Try a different search term."}
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                  <th className="px-6 py-3 font-medium">Description</th>
                  <th className="px-6 py-3 font-medium">Batch</th>
                  <th className="px-6 py-3 font-medium">Samples</th>
                  <th className="px-6 py-3 font-medium">Status</th>
                  <th className="px-6 py-3 font-medium">COA Date</th>
                  <th className="px-6 py-3 font-medium">Linked SKUs</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredProducts.map((p) => (
                  <tr
                    key={`${p.description}-${p.batch_no}`}
                    onClick={() => loadDetail(p.first_accession)}
                    className="hover:bg-green-50/50 cursor-pointer transition-colors"
                  >
                    <td className="px-6 py-4 text-gray-700 max-w-xs">
                      <div className="flex items-center gap-2">
                        <span className="truncate">{p.description || p.business_name || "—"}</span>
                        {p.source === "manual" && (
                          <span className="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-100 text-purple-700">
                            Manual
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 font-mono text-gray-600 text-xs">
                      {p.batch_no || "—"}
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-block px-2 py-0.5 bg-blue-50 text-blue-700 text-xs font-medium rounded">
                        {p.sample_count}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${statusColor(p.sample_status)}`}>
                        {p.sample_status || "—"}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-gray-600">
                      {formatDate(p.coa_approved_date)}
                    </td>
                    <td className="px-6 py-4">
                      {p.linked_skus ? (
                        <div className="flex flex-wrap gap-1">
                          {p.linked_skus.split(",").map((sku) => (
                            <span
                              key={sku}
                              className="inline-block px-2 py-0.5 bg-green-50 text-green-700 text-xs font-medium rounded border border-green-200"
                            >
                              {sku}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-gray-400 text-xs">Not linked</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
