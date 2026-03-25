import { useState, useEffect } from "react";
import { Percent, Plus, Trash2, Edit3, ToggleLeft, ToggleRight, RefreshCw, AlertTriangle, Save, X } from "lucide-react";
import { getPromos, createPromo, updatePromo, deletePromo } from "../lib/api";

interface PromoCode {
  id: number;
  code: string;
  discount_pct: number;
  discount_amount: number;
  single_use: boolean;
  is_active: boolean;
  max_uses: number;
  times_used: number;
  expires_at: string | null;
  created_at: string;
}

export default function Discounts() {
  const [promos, setPromos] = useState<PromoCode[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Create form state
  const [newCode, setNewCode] = useState("");
  const [newDiscountType, setNewDiscountType] = useState<"percent" | "amount">("percent");
  const [newDiscountValue, setNewDiscountValue] = useState("");
  const [newSingleUse, setNewSingleUse] = useState(false);
  const [newMaxUses, setNewMaxUses] = useState("");
  const [newExpiresAt, setNewExpiresAt] = useState("");

  // Edit form state
  const [editDiscountType, setEditDiscountType] = useState<"percent" | "amount">("percent");
  const [editDiscountValue, setEditDiscountValue] = useState("");
  const [editSingleUse, setEditSingleUse] = useState(false);
  const [editMaxUses, setEditMaxUses] = useState("");
  const [editExpiresAt, setEditExpiresAt] = useState("");

  const loadPromos = async () => {
    setLoading(true);
    try {
      const res = await getPromos();
      setPromos(res.data);
    } catch {
      setError("Failed to load promo codes");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPromos();
  }, []);

  const handleCreate = async () => {
    if (!newCode.trim()) {
      setError("Promo code is required");
      return;
    }
    const val = parseFloat(newDiscountValue) || 0;
    if (val <= 0) {
      setError("Discount value must be greater than 0");
      return;
    }
    setError("");
    try {
      await createPromo({
        code: newCode.trim().toUpperCase(),
        discount_pct: newDiscountType === "percent" ? val / 100 : 0,
        discount_amount: newDiscountType === "amount" ? Math.round(val * 100) : 0,
        single_use: newSingleUse,
        max_uses: parseInt(newMaxUses) || 0,
        expires_at: newExpiresAt || null,
      });
      setSuccess(`Promo code "${newCode.trim().toUpperCase()}" created!`);
      setShowCreate(false);
      setNewCode("");
      setNewDiscountValue("");
      setNewSingleUse(false);
      setNewMaxUses("");
      setNewExpiresAt("");
      setTimeout(() => setSuccess(""), 3000);
      loadPromos();
    } catch {
      setError("Failed to create promo code. It may already exist.");
    }
  };

  const startEdit = (promo: PromoCode) => {
    setEditingId(promo.id);
    if (promo.discount_pct > 0) {
      setEditDiscountType("percent");
      setEditDiscountValue(String(Math.round(promo.discount_pct * 100)));
    } else {
      setEditDiscountType("amount");
      setEditDiscountValue(String((promo.discount_amount / 100).toFixed(2)));
    }
    setEditSingleUse(promo.single_use);
    setEditMaxUses(promo.max_uses > 0 ? String(promo.max_uses) : "");
    setEditExpiresAt(promo.expires_at || "");
  };

  const handleUpdate = async (promoId: number) => {
    const val = parseFloat(editDiscountValue) || 0;
    if (val <= 0) {
      setError("Discount value must be greater than 0");
      return;
    }
    setError("");
    try {
      await updatePromo(promoId, {
        discount_pct: editDiscountType === "percent" ? val / 100 : 0,
        discount_amount: editDiscountType === "amount" ? Math.round(val * 100) : 0,
        single_use: editSingleUse,
        max_uses: parseInt(editMaxUses) || 0,
        expires_at: editExpiresAt || null,
      });
      setEditingId(null);
      setSuccess("Promo code updated!");
      setTimeout(() => setSuccess(""), 3000);
      loadPromos();
    } catch {
      setError("Failed to update promo code");
    }
  };

  const handleToggleActive = async (promo: PromoCode) => {
    try {
      await updatePromo(promo.id, { is_active: !promo.is_active });
      loadPromos();
    } catch {
      setError("Failed to update promo code");
    }
  };

  const handleDelete = async (promoId: number) => {
    try {
      await deletePromo(promoId);
      setDeleteConfirmId(null);
      setSuccess("Promo code deleted");
      setTimeout(() => setSuccess(""), 3000);
      loadPromos();
    } catch {
      setError("Failed to delete promo code");
    }
  };

  const formatDiscount = (promo: PromoCode) => {
    if (promo.discount_pct > 0) return `${Math.round(promo.discount_pct * 100)}% off`;
    if (promo.discount_amount > 0) return `$${(promo.discount_amount / 100).toFixed(2)} off`;
    return "—";
  };

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    } catch {
      return dateStr;
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Percent className="w-7 h-7 text-green-600" />
            Discount Codes
          </h2>
          <p className="text-sm text-gray-500 mt-1">{promos.length} promo code{promos.length !== 1 ? "s" : ""}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setShowCreate(true); setError(""); }}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm font-medium"
          >
            <Plus className="w-4 h-4" />
            New Promo Code
          </button>
          <button
            onClick={loadPromos}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {/* Messages */}
      {error && (
        <div className="p-3 bg-red-50 rounded-lg border border-red-200 text-sm text-red-700 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {error}
          <button onClick={() => setError("")} className="ml-auto text-red-400 hover:text-red-600"><X className="w-4 h-4" /></button>
        </div>
      )}
      {success && (
        <div className="p-3 bg-green-50 rounded-lg border border-green-200 text-sm text-green-700">
          {success}
        </div>
      )}

      {/* Create Form */}
      {showCreate && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Create New Promo Code</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Promo Code</label>
              <input
                type="text"
                value={newCode}
                onChange={(e) => setNewCode(e.target.value.toUpperCase())}
                placeholder="e.g. SUMMER20"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Discount Type</label>
              <div className="flex gap-2">
                <select
                  value={newDiscountType}
                  onChange={(e) => setNewDiscountType(e.target.value as "percent" | "amount")}
                  className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
                >
                  <option value="percent">Percent Off (%)</option>
                  <option value="amount">Fixed Amount ($)</option>
                </select>
                <input
                  type="number"
                  step={newDiscountType === "percent" ? "1" : "0.01"}
                  value={newDiscountValue}
                  onChange={(e) => setNewDiscountValue(e.target.value)}
                  placeholder={newDiscountType === "percent" ? "15" : "5.00"}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
                />
              </div>
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Max Uses (0 = unlimited)</label>
              <input
                type="number"
                value={newMaxUses}
                onChange={(e) => setNewMaxUses(e.target.value)}
                placeholder="0"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Expires At (optional)</label>
              <input
                type="date"
                value={newExpiresAt}
                onChange={(e) => setNewExpiresAt(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              />
            </div>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={newSingleUse}
                  onChange={(e) => setNewSingleUse(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-300 text-green-600 focus:ring-green-500"
                />
                <span className="text-sm text-gray-700">Single use per customer (email)</span>
              </label>
            </div>
          </div>
          <div className="mt-4 flex gap-2">
            <button
              onClick={handleCreate}
              className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm font-medium"
            >
              <Plus className="w-4 h-4" />
              Create Promo Code
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Promos List */}
      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading promo codes...</div>
      ) : promos.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
          <Percent className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500">No promo codes yet</p>
          <p className="text-sm text-gray-400 mt-1">Create one to get started</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Code</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Discount</th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Uses</th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Single Use</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Expires</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Created</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {promos.map((promo) => (
                <tr key={promo.id} className={`${!promo.is_active ? "bg-gray-50 opacity-60" : ""}`}>
                  {editingId === promo.id ? (
                    <>
                      <td className="px-4 py-3">
                        <span className="font-mono font-bold text-green-700 bg-green-50 px-2 py-1 rounded text-sm">{promo.code}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex gap-1">
                          <select
                            value={editDiscountType}
                            onChange={(e) => setEditDiscountType(e.target.value as "percent" | "amount")}
                            className="px-2 py-1 border border-gray-300 rounded text-xs"
                          >
                            <option value="percent">%</option>
                            <option value="amount">$</option>
                          </select>
                          <input
                            type="number"
                            step={editDiscountType === "percent" ? "1" : "0.01"}
                            value={editDiscountValue}
                            onChange={(e) => setEditDiscountValue(e.target.value)}
                            className="w-20 px-2 py-1 border border-gray-300 rounded text-xs"
                          />
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center">—</td>
                      <td className="px-4 py-3 text-center">
                        <input
                          type="number"
                          value={editMaxUses}
                          onChange={(e) => setEditMaxUses(e.target.value)}
                          placeholder="0"
                          className="w-16 px-2 py-1 border border-gray-300 rounded text-xs text-center"
                        />
                      </td>
                      <td className="px-4 py-3 text-center">
                        <input
                          type="checkbox"
                          checked={editSingleUse}
                          onChange={(e) => setEditSingleUse(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-300 text-green-600"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <input
                          type="date"
                          value={editExpiresAt}
                          onChange={(e) => setEditExpiresAt(e.target.value)}
                          className="px-2 py-1 border border-gray-300 rounded text-xs"
                        />
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">{formatDate(promo.created_at)}</td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => handleUpdate(promo.id)}
                            className="p-1.5 text-green-600 hover:bg-green-50 rounded-lg transition-colors"
                            title="Save"
                          >
                            <Save className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => setEditingId(null)}
                            className="p-1.5 text-gray-400 hover:bg-gray-100 rounded-lg transition-colors"
                            title="Cancel"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="px-4 py-3">
                        <span className="font-mono font-bold text-green-700 bg-green-50 px-2 py-1 rounded text-sm">{promo.code}</span>
                      </td>
                      <td className="px-4 py-3 text-sm font-medium text-gray-900">{formatDiscount(promo)}</td>
                      <td className="px-4 py-3 text-center">
                        <button
                          onClick={() => handleToggleActive(promo)}
                          className="inline-flex items-center gap-1"
                          title={promo.is_active ? "Click to disable" : "Click to enable"}
                        >
                          {promo.is_active ? (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                              <ToggleRight className="w-3.5 h-3.5" /> Active
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                              <ToggleLeft className="w-3.5 h-3.5" /> Inactive
                            </span>
                          )}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-center text-sm text-gray-600">
                        {promo.times_used}{promo.max_uses > 0 ? ` / ${promo.max_uses}` : ""}
                      </td>
                      <td className="px-4 py-3 text-center text-sm text-gray-600">
                        {promo.single_use ? "Yes" : "No"}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">
                        {promo.expires_at ? formatDate(promo.expires_at) : "Never"}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">{formatDate(promo.created_at)}</td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => startEdit(promo)}
                            className="p-1.5 text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
                            title="Edit"
                          >
                            <Edit3 className="w-4 h-4" />
                          </button>
                          {deleteConfirmId === promo.id ? (
                            <div className="flex items-center gap-1">
                              <button
                                onClick={() => handleDelete(promo.id)}
                                className="px-2 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700"
                              >
                                Confirm
                              </button>
                              <button
                                onClick={() => setDeleteConfirmId(null)}
                                className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200"
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={() => setDeleteConfirmId(promo.id)}
                              className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                              title="Delete"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
