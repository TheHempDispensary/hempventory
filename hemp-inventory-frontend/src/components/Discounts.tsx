import { useEffect, useState } from "react";
import { getDiscounts, createDiscount, updateDiscount, deleteDiscount } from "../lib/api";
import { Tag, Plus, Trash2, ToggleLeft, ToggleRight, ExternalLink, Percent, DollarSign, RefreshCw, Globe, Store, Layers } from "lucide-react";

interface Discount {
  id: number;
  code: string;
  discount_type: string;
  discount_value: number;
  description: string;
  min_order_amount: number;
  max_uses: number;
  times_used: number;
  is_active: number;
  applies_to: string;
  starts_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export default function Discounts() {
  const [discounts, setDiscounts] = useState<Discount[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Form state
  const [code, setCode] = useState("");
  const [discountType, setDiscountType] = useState("percentage");
  const [discountValue, setDiscountValue] = useState("");
  const [description, setDescription] = useState("");
  const [maxUses, setMaxUses] = useState("");
  const [appliesTo, setAppliesTo] = useState("both");
  const [expiresAt, setExpiresAt] = useState("");

  const SITE_URL = "https://www.thehempdispensary.com";

  const loadDiscounts = async () => {
    setLoading(true);
    try {
      const res = await getDiscounts();
      setDiscounts(res.data.discounts || []);
    } catch (err) {
      console.error("Error loading discounts:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDiscounts();
  }, []);

  const resetForm = () => {
    setCode("");
    setDiscountType("percentage");
    setDiscountValue("");
    setDescription("");
    setMaxUses("");
    setAppliesTo("both");
    setExpiresAt("");
    setError("");
  };

  const handleCreate = async () => {
    if (!code.trim()) { setError("Code is required"); return; }
    if (!discountValue || parseFloat(discountValue) <= 0) { setError("Value must be greater than 0"); return; }

    setSaving(true);
    setError("");
    try {
      await createDiscount({
        code: code.trim().toUpperCase(),
        discount_type: discountType,
        discount_value: parseFloat(discountValue),
        description,
        max_uses: maxUses ? parseInt(maxUses) : 0,
        applies_to: appliesTo,
        expires_at: expiresAt || undefined,
      });
      resetForm();
      setShowForm(false);
      await loadDiscounts();
    } catch (err: unknown) {
      const msg = (err && typeof err === "object" && "response" in err)
        ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || "Failed to create discount")
        : "Failed to create discount";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (discount: Discount) => {
    try {
      await updateDiscount(discount.id, { is_active: !discount.is_active });
      setDiscounts((prev) =>
        prev.map((d) => (d.id === discount.id ? { ...d, is_active: d.is_active ? 0 : 1 } : d))
      );
    } catch (err) {
      console.error("Error toggling discount:", err);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Are you sure you want to delete this discount code?")) return;
    try {
      await deleteDiscount(id);
      setDiscounts((prev) => prev.filter((d) => d.id !== id));
    } catch (err) {
      console.error("Error deleting discount:", err);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "—";
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  };

  const activeCount = discounts.filter((d) => d.is_active).length;
  const totalRedemptions = discounts.reduce((sum, d) => sum + d.times_used, 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Tag className="w-7 h-7 text-green-600" />
            Discount Codes
          </h2>
          <p className="text-sm text-gray-500 mt-1">Manage promo codes for the online store</p>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={SITE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
          >
            <ExternalLink className="w-4 h-4" />
            View Store
          </a>
          <button
            onClick={loadDiscounts}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={() => { setShowForm(!showForm); resetForm(); }}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm font-medium"
          >
            <Plus className="w-4 h-4" />
            New Code
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <p className="text-xs text-gray-500 uppercase">Total Codes</p>
          <p className="text-xl font-bold text-gray-900">{discounts.length}</p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <p className="text-xs text-gray-500 uppercase">Active</p>
          <p className="text-xl font-bold text-green-600">{activeCount}</p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <p className="text-xs text-gray-500 uppercase">Total Redemptions</p>
          <p className="text-xl font-bold text-blue-600">{totalRedemptions}</p>
        </div>
      </div>

      {/* Create Form */}
      {showForm && (
        <div className="bg-white rounded-lg border border-green-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Create Discount Code</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Code</label>
              <input
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                placeholder="e.g. SUMMER20"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 uppercase"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Type</label>
              <select
                value={discountType}
                onChange={(e) => setDiscountType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              >
                <option value="percentage">Percentage Off (%)</option>
                <option value="fixed">Fixed Amount ($)</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">
                {discountType === "percentage" ? "Discount (%)" : "Discount ($)"}
              </label>
              <input
                type="number"
                value={discountValue}
                onChange={(e) => setDiscountValue(e.target.value)}
                placeholder={discountType === "percentage" ? "15" : "5.00"}
                step={discountType === "percentage" ? "1" : "0.01"}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Description</label>
              <input
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="e.g. 15% off for first-time customers"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Applies To</label>
              <select
                value={appliesTo}
                onChange={(e) => setAppliesTo(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              >
                <option value="both">Online &amp; In-Store</option>
                <option value="online">Online Only</option>
                <option value="in_store">In-Store Only</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Max Uses (0 = unlimited)</label>
              <input
                type="number"
                value={maxUses}
                onChange={(e) => setMaxUses(e.target.value)}
                placeholder="0"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Expires At (optional)</label>
              <input
                type="date"
                value={expiresAt}
                onChange={(e) => setExpiresAt(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              />
            </div>
          </div>
          {error && <p className="text-sm text-red-600 mt-3">{error}</p>}
          <div className="flex gap-3 mt-4">
            <button
              onClick={handleCreate}
              disabled={saving}
              className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors text-sm font-medium"
            >
              {saving ? "Creating..." : "Create Code"}
            </button>
            <button
              onClick={() => { setShowForm(false); resetForm(); }}
              className="px-6 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Discount List */}
      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading discounts...</div>
      ) : discounts.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
          <Tag className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500">No discount codes yet</p>
          <p className="text-sm text-gray-400 mt-1">Create your first code to get started</p>
        </div>
      ) : (
        <div className="space-y-3">
          {discounts.map((discount) => (
            <div
              key={discount.id}
              className={`bg-white rounded-lg border ${discount.is_active ? "border-gray-200" : "border-gray-100 opacity-60"} p-4`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${discount.is_active ? "bg-green-100" : "bg-gray-100"}`}>
                    {discount.discount_type === "percentage"
                      ? <Percent className={`w-5 h-5 ${discount.is_active ? "text-green-600" : "text-gray-400"}`} />
                      : <DollarSign className={`w-5 h-5 ${discount.is_active ? "text-green-600" : "text-gray-400"}`} />
                    }
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-gray-900 text-lg">{discount.code}</span>
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${discount.is_active ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-500"}`}>
                        {discount.is_active ? "Active" : "Inactive"}
                      </span>
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700 flex items-center gap-1">
                        {discount.applies_to === "online" ? <><Globe className="w-3 h-3" /> Online</> : discount.applies_to === "in_store" ? <><Store className="w-3 h-3" /> In-Store</> : <><Layers className="w-3 h-3" /> Both</>}
                      </span>
                    </div>
                    <p className="text-sm text-gray-500">
                      {discount.discount_type === "percentage"
                        ? `${discount.discount_value}% off`
                        : `$${discount.discount_value.toFixed(2)} off`}
                      {discount.description && ` — ${discount.description}`}
                    </p>
                    <div className="flex items-center gap-4 mt-1 text-xs text-gray-400">
                      <span>Used: {discount.times_used}{discount.max_uses > 0 ? `/${discount.max_uses}` : ""}</span>
                      {discount.expires_at && <span>Expires: {formatDate(discount.expires_at)}</span>}
                      <span>Created: {formatDate(discount.created_at)}</span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleToggle(discount)}
                    className="p-2 rounded-lg hover:bg-gray-100 transition-colors"
                    title={discount.is_active ? "Deactivate" : "Activate"}
                  >
                    {discount.is_active
                      ? <ToggleRight className="w-6 h-6 text-green-600" />
                      : <ToggleLeft className="w-6 h-6 text-gray-400" />
                    }
                  </button>
                  <button
                    onClick={() => handleDelete(discount.id)}
                    className="p-2 rounded-lg hover:bg-red-50 text-gray-400 hover:text-red-600 transition-colors"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Info Box */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
        <p className="font-medium mb-1">How discount codes work:</p>
        <ul className="list-disc list-inside space-y-1 text-blue-700">
          <li>Active codes can be used at checkout on <a href={SITE_URL} target="_blank" rel="noopener noreferrer" className="underline font-medium">{SITE_URL.replace("https://", "")}</a></li>
          <li>Choose where each code applies: <strong>Online</strong>, <strong>In-Store</strong>, or <strong>Both</strong></li>
          <li>Toggle codes on/off instantly without deleting them</li>
          <li>Set max uses to limit how many times a code can be redeemed (0 = unlimited)</li>
          <li>Set an expiration date to auto-expire seasonal promotions</li>
        </ul>
      </div>
    </div>
  );
}
