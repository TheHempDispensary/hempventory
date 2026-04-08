import { useState, useEffect, useCallback } from "react";
import { Percent, Plus, Trash2, Edit3, ToggleLeft, ToggleRight, RefreshCw, AlertTriangle, Save, X, Calendar, ShoppingBag, Ban, Cloud, Tag, Package, Search } from "lucide-react";
import { getPromos, createPromo, updatePromo, deletePromo, getVolumeDiscounts, createVolumeDiscount, updateVolumeDiscount, deleteVolumeDiscount } from "../lib/api";
import api from "../lib/api";

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
  starts_at: string | null;
  applies_to: string;
  product_ids: string;
  exclude_from_other_coupons: boolean;
  clover_discount_id: string;
  is_direct_discount: boolean;
  created_at: string;
}

interface CloverItem {
  id: string;
  name: string;
  sku?: string;
}

interface VolumeDiscount {
  id: number;
  product_sku: string;
  product_name: string;
  min_quantity: number;
  discount_type: string;
  discount_value: number;
  customer_label: string;
  is_active: boolean;
  sync_to_clover: boolean;
  clover_discount_id: string;
  created_at: string;
  updated_at: string;
}

export default function Discounts() {
  const [promos, setPromos] = useState<PromoCode[]>([]);
  const [volumeDiscounts, setVolumeDiscounts] = useState<VolumeDiscount[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [activeTab, setActiveTab] = useState<"promo" | "direct" | "volume">("promo");

  const [products, setProducts] = useState<CloverItem[]>([]);
  const [productSearch, setProductSearch] = useState("");

  // Volume discount form
  const [vdProductSearch, setVdProductSearch] = useState("");
  const [vdSelectedProduct, setVdSelectedProduct] = useState<CloverItem | null>(null);
  const [vdMinQty, setVdMinQty] = useState("2");
  const [vdDiscountType, setVdDiscountType] = useState<"fixed_total" | "amount_off" | "percent_off">("fixed_total");
  const [vdDiscountValue, setVdDiscountValue] = useState("");
  const [vdLabel, setVdLabel] = useState("");
  const [vdSyncToClover, setVdSyncToClover] = useState(false);
  const [vdEditingId, setVdEditingId] = useState<number | null>(null);
  const [vdDeleteConfirmId, setVdDeleteConfirmId] = useState<number | null>(null);

  // Create form
  const [newIsDirectDiscount, setNewIsDirectDiscount] = useState(false);
  const [newCode, setNewCode] = useState("");
  const [newDiscountType, setNewDiscountType] = useState<"percent" | "amount">("percent");
  const [newDiscountValue, setNewDiscountValue] = useState("");
  const [newSingleUse, setNewSingleUse] = useState(false);
  const [newMaxUses, setNewMaxUses] = useState("");
  const [newExpiresAt, setNewExpiresAt] = useState("");
  const [newStartsAt, setNewStartsAt] = useState("");
  const [newAppliesTo, setNewAppliesTo] = useState<"all" | "specific">("all");
  const [newProductIds, setNewProductIds] = useState<string[]>([]);
  const [newExcludeOtherCoupons, setNewExcludeOtherCoupons] = useState(false);
  const [newSyncToClover, setNewSyncToClover] = useState(false);

  // Edit form
  const [editDiscountType, setEditDiscountType] = useState<"percent" | "amount">("percent");
  const [editDiscountValue, setEditDiscountValue] = useState("");
  const [editSingleUse, setEditSingleUse] = useState(false);
  const [editMaxUses, setEditMaxUses] = useState("");
  const [editExpiresAt, setEditExpiresAt] = useState("");
  const [editStartsAt, setEditStartsAt] = useState("");
  const [editAppliesTo, setEditAppliesTo] = useState<"all" | "specific">("all");
  const [editProductIds, setEditProductIds] = useState<string[]>([]);
  const [editExcludeOtherCoupons, setEditExcludeOtherCoupons] = useState(false);
  const [editSyncToClover, setEditSyncToClover] = useState(false);

  const loadPromos = async () => {
    setLoading(true);
    try {
      const [promoRes, vdRes] = await Promise.all([getPromos(), getVolumeDiscounts()]);
      setPromos(promoRes.data);
      setVolumeDiscounts(vdRes.data);
    } catch {
      setError("Failed to load discounts");
    } finally {
      setLoading(false);
    }
  };

  const loadProducts = useCallback(async () => {
    try {
      const res = await api.get("/api/ecommerce/products");
      const data = res.data;
      const itemList = data.products || data || [];
      const items = (itemList as { id: string; name: string; sku?: string }[]).map((p) => ({ id: p.id, name: p.name, sku: p.sku || p.id }));
      setProducts(items);
    } catch {
      // non-critical
    }
  }, []);

  useEffect(() => { loadPromos(); loadProducts(); }, [loadProducts]);

  const resetCreateForm = () => {
    setNewIsDirectDiscount(false); setNewCode(""); setNewDiscountValue(""); setNewSingleUse(false); setNewMaxUses("");
    setNewExpiresAt(""); setNewStartsAt(""); setNewAppliesTo("all"); setNewProductIds([]);
    setNewExcludeOtherCoupons(false); setNewSyncToClover(false);
  };

  const resetVdForm = () => {
    setVdProductSearch(""); setVdSelectedProduct(null); setVdMinQty("2");
    setVdDiscountType("fixed_total"); setVdDiscountValue(""); setVdLabel("");
    setVdSyncToClover(false); setVdEditingId(null);
  };

  const handleCreateVolumeDiscount = async () => {
    if (!vdSelectedProduct) { setError("Please select a product"); return; }
    const qty = parseInt(vdMinQty) || 0;
    if (qty < 2) { setError("Minimum quantity must be at least 2"); return; }
    const val = parseFloat(vdDiscountValue) || 0;
    if (val <= 0) { setError("Discount value must be greater than 0"); return; }
    setError("");
    try {
      await createVolumeDiscount({
        product_sku: vdSelectedProduct.sku || vdSelectedProduct.id,
        product_name: vdSelectedProduct.name,
        min_quantity: qty,
        discount_type: vdDiscountType,
        discount_value: val,
        customer_label: vdLabel,
        sync_to_clover: vdSyncToClover,
      });
      setSuccess("Volume discount created!" + (vdSyncToClover ? " Synced to Clover POS." : ""));
      setShowCreate(false); resetVdForm();
      setTimeout(() => setSuccess(""), 3000);
      loadPromos();
    } catch { setError("Failed to create volume discount"); }
  };

  const startVdEdit = (vd: VolumeDiscount) => {
    setVdEditingId(vd.id);
    setVdSelectedProduct({ id: vd.product_sku, name: vd.product_name, sku: vd.product_sku });
    setVdMinQty(String(vd.min_quantity));
    setVdDiscountType(vd.discount_type as "fixed_total" | "amount_off" | "percent_off");
    setVdDiscountValue(String(vd.discount_value));
    setVdLabel(vd.customer_label);
    setVdSyncToClover(!!vd.sync_to_clover);
  };

  const handleUpdateVolumeDiscount = async (id: number) => {
    const qty = parseInt(vdMinQty) || 0;
    if (qty < 2) { setError("Minimum quantity must be at least 2"); return; }
    const val = parseFloat(vdDiscountValue) || 0;
    if (val <= 0) { setError("Discount value must be greater than 0"); return; }
    setError("");
    try {
      await updateVolumeDiscount(id, {
        product_sku: vdSelectedProduct?.sku || vdSelectedProduct?.id,
        product_name: vdSelectedProduct?.name,
        min_quantity: qty,
        discount_type: vdDiscountType,
        discount_value: val,
        customer_label: vdLabel,
        sync_to_clover: vdSyncToClover,
      });
      setVdEditingId(null); resetVdForm();
      setSuccess("Volume discount updated!"); setTimeout(() => setSuccess(""), 3000);
      loadPromos();
    } catch { setError("Failed to update volume discount"); }
  };

  const handleToggleVdActive = async (vd: VolumeDiscount) => {
    try { await updateVolumeDiscount(vd.id, { is_active: !vd.is_active }); loadPromos(); }
    catch { setError("Failed to update volume discount"); }
  };

  const handleDeleteVolumeDiscount = async (id: number) => {
    try { await deleteVolumeDiscount(id); setVdDeleteConfirmId(null); setSuccess("Volume discount deleted"); setTimeout(() => setSuccess(""), 3000); loadPromos(); }
    catch { setError("Failed to delete volume discount"); }
  };

  const formatVdRule = (vd: VolumeDiscount) => {
    if (vd.discount_type === "fixed_total") return `Buy ${vd.min_quantity}+ → $${vd.discount_value.toFixed(2)} total`;
    if (vd.discount_type === "amount_off") return `Buy ${vd.min_quantity}+ → $${vd.discount_value.toFixed(2)} off each`;
    if (vd.discount_type === "percent_off") return `Buy ${vd.min_quantity}+ → ${vd.discount_value}% off`;
    return "";
  };

  const vdFilteredProducts = products.filter((p) => p.name.toLowerCase().includes(vdProductSearch.toLowerCase()));

  const handleCreate = async () => {
    if (!newIsDirectDiscount && !newCode.trim()) { setError("Promo code is required"); return; }
    const val = parseFloat(newDiscountValue) || 0;
    if (val <= 0) { setError("Discount value must be greater than 0"); return; }
    if (newAppliesTo === "specific" && newProductIds.length === 0) { setError("Please select at least one product"); return; }
    setError("");
    try {
      await createPromo({
        code: newIsDirectDiscount ? "" : newCode.trim().toUpperCase(),
        is_direct_discount: newIsDirectDiscount,
        discount_pct: newDiscountType === "percent" ? val / 100 : 0,
        discount_amount: newDiscountType === "amount" ? Math.round(val * 100) : 0,
        single_use: newSingleUse,
        max_uses: parseInt(newMaxUses) || 0,
        expires_at: newExpiresAt || null,
        starts_at: newStartsAt || null,
        applies_to: newAppliesTo,
        product_ids: newProductIds.join(","),
        exclude_from_other_coupons: newExcludeOtherCoupons,
        sync_to_clover: newSyncToClover,
      });
      const label = newIsDirectDiscount ? "Direct discount" : "Promo code \"" + newCode.trim().toUpperCase() + "\"";
      setSuccess(label + " created!" + (newSyncToClover ? " Synced to Clover POS." : ""));
      setShowCreate(false); resetCreateForm();
      setTimeout(() => setSuccess(""), 3000);
      loadPromos();
    } catch { setError("Failed to create. Code may already exist."); }
  };

  const startEdit = (promo: PromoCode) => {
    setEditingId(promo.id);
    if (promo.discount_pct > 0) { setEditDiscountType("percent"); setEditDiscountValue(String(Math.round(promo.discount_pct * 100))); }
    else { setEditDiscountType("amount"); setEditDiscountValue(String((promo.discount_amount / 100).toFixed(2))); }
    setEditSingleUse(promo.single_use);
    setEditMaxUses(promo.max_uses > 0 ? String(promo.max_uses) : "");
    setEditExpiresAt(promo.expires_at || "");
    setEditStartsAt(promo.starts_at || "");
    setEditAppliesTo((promo.applies_to === "specific" ? "specific" : "all") as "all" | "specific");
    setEditProductIds(promo.product_ids ? promo.product_ids.split(",").filter(Boolean) : []);
    setEditExcludeOtherCoupons(promo.exclude_from_other_coupons);
    setEditSyncToClover(!!promo.clover_discount_id);
  };

  const handleUpdate = async (promoId: number) => {
    const val = parseFloat(editDiscountValue) || 0;
    if (val <= 0) { setError("Discount value must be greater than 0"); return; }
    setError("");
    try {
      await updatePromo(promoId, {
        discount_pct: editDiscountType === "percent" ? val / 100 : 0,
        discount_amount: editDiscountType === "amount" ? Math.round(val * 100) : 0,
        single_use: editSingleUse,
        max_uses: parseInt(editMaxUses) || 0,
        expires_at: editExpiresAt || null,
        starts_at: editStartsAt || null,
        applies_to: editAppliesTo,
        product_ids: editProductIds.join(","),
        exclude_from_other_coupons: editExcludeOtherCoupons,
        sync_to_clover: editSyncToClover,
      });
      setEditingId(null); setSuccess("Discount updated!");
      setTimeout(() => setSuccess(""), 3000); loadPromos();
    } catch { setError("Failed to update discount"); }
  };

  const handleToggleActive = async (promo: PromoCode) => {
    try { await updatePromo(promo.id, { is_active: !promo.is_active }); loadPromos(); }
    catch { setError("Failed to update discount"); }
  };

  const handleDelete = async (promoId: number) => {
    try { await deletePromo(promoId); setDeleteConfirmId(null); setSuccess("Discount deleted"); setTimeout(() => setSuccess(""), 3000); loadPromos(); }
    catch { setError("Failed to delete discount"); }
  };

  const toggleProductId = (id: string, selected: string[], setSelected: (ids: string[]) => void) => {
    setSelected(selected.includes(id) ? selected.filter((pid) => pid !== id) : [...selected, id]);
  };

  const formatDiscount = (promo: PromoCode) => {
    if (promo.discount_pct > 0) return Math.round(promo.discount_pct * 100) + "% off";
    if (promo.discount_amount > 0) return "$" + (promo.discount_amount / 100).toFixed(2) + " off";
    return "\u2014";
  };

  const formatDate = (dateStr: string) => {
    try { return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }); }
    catch { return dateStr; }
  };

  const filteredProducts = products.filter((p) => p.name.toLowerCase().includes(productSearch.toLowerCase()));

  const renderProductSelector = (appliesTo: "all" | "specific", setAppliesTo: (v: "all" | "specific") => void,
    selectedIds: string[], setSelectedIds: (ids: string[]) => void, idSuffix: string) => (
    <div className="space-y-3">
      <label className="text-sm font-medium text-gray-700 block">Applies To</label>
      <div className="flex gap-3">
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="radio" name={"applies-to-" + idSuffix} checked={appliesTo === "all"}
            onChange={() => { setAppliesTo("all"); setSelectedIds([]); }} className="w-4 h-4 text-green-600 focus:ring-green-500" />
          <span className="text-sm text-gray-700">All Products</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="radio" name={"applies-to-" + idSuffix} checked={appliesTo === "specific"}
            onChange={() => setAppliesTo("specific")} className="w-4 h-4 text-green-600 focus:ring-green-500" />
          <span className="text-sm text-gray-700">Specific Products</span>
        </label>
      </div>
      {appliesTo === "specific" && (
        <div className="border border-gray-200 rounded-lg p-3 max-h-48 overflow-y-auto">
          <input type="text" placeholder="Search products..." value={productSearch}
            onChange={(e) => setProductSearch(e.target.value)}
            className="w-full px-3 py-1.5 border border-gray-200 rounded text-sm mb-2 focus:ring-2 focus:ring-green-500" />
          {selectedIds.length > 0 && <p className="text-xs text-green-600 mb-2">{selectedIds.length} product{selectedIds.length !== 1 ? "s" : ""} selected</p>}
          {filteredProducts.length === 0 ? <p className="text-xs text-gray-400">No products found</p> : (
            filteredProducts.slice(0, 50).map((p) => (
              <label key={p.id} className="flex items-center gap-2 py-1 cursor-pointer hover:bg-gray-50 px-1 rounded">
                <input type="checkbox" checked={selectedIds.includes(p.id)}
                  onChange={() => toggleProductId(p.id, selectedIds, setSelectedIds)}
                  className="w-3.5 h-3.5 rounded border-gray-300 text-green-600 focus:ring-green-500" />
                <span className="text-xs text-gray-700 truncate">{p.name}</span>
              </label>
            ))
          )}
        </div>
      )}
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Percent className="w-7 h-7 text-green-600" /> Discounts
          </h2>
          <p className="text-sm text-gray-500 mt-1">{promos.length + volumeDiscounts.length} discount{promos.length + volumeDiscounts.length !== 1 ? "s" : ""}</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => { setShowCreate(true); setError(""); }}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm font-medium">
            <Plus className="w-4 h-4" /> New Discount
          </button>
          <button onClick={loadPromos} disabled={loading}
            className="flex items-center gap-2 px-3 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm">
            <RefreshCw className={"w-4 h-4" + (loading ? " animate-spin" : "")} />
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 rounded-lg border border-red-200 text-sm text-red-700 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" /> {error}
          <button onClick={() => setError("")} className="ml-auto text-red-400 hover:text-red-600"><X className="w-4 h-4" /></button>
        </div>
      )}
      {success && <div className="p-3 bg-green-50 rounded-lg border border-green-200 text-sm text-green-700">{success}</div>}

      {showCreate && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Create New Discount</h3>
          <div className="mb-4 flex gap-3">
            <button onClick={() => { setNewIsDirectDiscount(false); setActiveTab("promo"); }}
              className={"px-4 py-2 rounded-lg text-sm font-medium border transition-colors " + (activeTab === "promo" ? "bg-green-600 text-white border-green-600" : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50")}>
              <Tag className="w-4 h-4 inline mr-1" /> Promo Code
            </button>
            <button onClick={() => { setNewIsDirectDiscount(true); setActiveTab("direct"); }}
              className={"px-4 py-2 rounded-lg text-sm font-medium border transition-colors " + (activeTab === "direct" ? "bg-purple-600 text-white border-purple-600" : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50")}>
              <ShoppingBag className="w-4 h-4 inline mr-1" /> Direct Discount
            </button>
            <button onClick={() => setActiveTab("volume")}
              className={"px-4 py-2 rounded-lg text-sm font-medium border transition-colors " + (activeTab === "volume" ? "bg-amber-600 text-white border-amber-600" : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50")}>
              <Package className="w-4 h-4 inline mr-1" /> Volume Discount
            </button>
          </div>
          {activeTab === "volume" && (
            <div>
              <p className="text-xs text-gray-500 mb-4 -mt-2">Volume discounts automatically apply when a customer buys a minimum quantity of a specific product.</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="md:col-span-2">
                  <label className="text-sm font-medium text-gray-700 block mb-1">Product</label>
                  {vdSelectedProduct ? (
                    <div className="flex items-center gap-2 p-2 bg-green-50 border border-green-200 rounded-lg">
                      <span className="text-sm font-medium text-green-800">{vdSelectedProduct.name}</span>
                      <span className="text-xs text-green-600">({vdSelectedProduct.sku})</span>
                      <button onClick={() => setVdSelectedProduct(null)} className="ml-auto text-red-400 hover:text-red-600"><X className="w-4 h-4" /></button>
                    </div>
                  ) : (
                    <div className="relative">
                      <div className="flex items-center gap-2 border border-gray-300 rounded-lg px-3 py-2">
                        <Search className="w-4 h-4 text-gray-400" />
                        <input type="text" value={vdProductSearch} onChange={(e) => setVdProductSearch(e.target.value)}
                          placeholder="Search for a product..." className="flex-1 text-sm outline-none" />
                      </div>
                      {vdProductSearch.length >= 2 && (
                        <div className="absolute z-10 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                          {vdFilteredProducts.slice(0, 20).map((p) => (
                            <button key={p.id + p.name} onClick={() => { setVdSelectedProduct(p); setVdProductSearch(""); }}
                              className="w-full text-left px-3 py-2 text-sm hover:bg-green-50 border-b border-gray-100 last:border-b-0">
                              <span className="font-medium">{p.name}</span>
                              {p.sku && <span className="text-xs text-gray-500 ml-2">({p.sku})</span>}
                            </button>
                          ))}
                          {vdFilteredProducts.length === 0 && <div className="px-3 py-2 text-sm text-gray-400">No products found</div>}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700 block mb-1">Minimum Quantity</label>
                  <input type="number" min="2" value={vdMinQty} onChange={(e) => setVdMinQty(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500 focus:border-amber-500" />
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700 block mb-1">Discount Type</label>
                  <select value={vdDiscountType} onChange={(e) => setVdDiscountType(e.target.value as "fixed_total" | "amount_off" | "percent_off")}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500">
                    <option value="fixed_total">Fixed Total Price (e.g. 2 for $35)</option>
                    <option value="amount_off">Amount Off Per Unit (e.g. $2.50 off each)</option>
                    <option value="percent_off">Percent Off (e.g. 12.5% off)</option>
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700 block mb-1">
                    {vdDiscountType === "fixed_total" ? "Total Price ($)" : vdDiscountType === "amount_off" ? "Amount Off Per Unit ($)" : "Percent Off (%)"}
                  </label>
                  <input type="number" step={vdDiscountType === "percent_off" ? "0.1" : "0.01"} value={vdDiscountValue}
                    onChange={(e) => setVdDiscountValue(e.target.value)}
                    placeholder={vdDiscountType === "fixed_total" ? "35.00" : vdDiscountType === "amount_off" ? "2.50" : "12.5"}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500" />
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700 block mb-1">Customer Label (shown on site)</label>
                  <input type="text" value={vdLabel} onChange={(e) => setVdLabel(e.target.value)}
                    placeholder='e.g. "Buy 2 for $35 🔥"'
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500" />
                </div>
                <div className="flex items-end pb-1">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={vdSyncToClover} onChange={(e) => setVdSyncToClover(e.target.checked)}
                      className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                    <Cloud className="w-4 h-4 text-blue-500" />
                    <span className="text-sm text-gray-700">Sync to Clover POS</span>
                  </label>
                </div>
              </div>
              <div className="mt-4 flex gap-2">
                <button onClick={handleCreateVolumeDiscount}
                  className="flex items-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 transition-colors text-sm font-medium">
                  <Plus className="w-4 h-4" /> Create Volume Discount
                </button>
                <button onClick={() => { setShowCreate(false); resetVdForm(); }}
                  className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium">Cancel</button>
              </div>
            </div>
          )}
          {activeTab !== "volume" && (<>
          {newIsDirectDiscount && (
            <p className="text-xs text-gray-500 mb-4 -mt-2">Direct discounts are applied automatically to selected products. No promo code needed.</p>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {!newIsDirectDiscount && (<div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Promo Code</label>
              <input type="text" value={newCode} onChange={(e) => setNewCode(e.target.value.toUpperCase())}
                placeholder="e.g. SUMMER20" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
            </div>)}
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Discount Type</label>
              <div className="flex gap-2">
                <select value={newDiscountType} onChange={(e) => setNewDiscountType(e.target.value as "percent" | "amount")}
                  className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500">
                  <option value="percent">Percent Off (%)</option>
                  <option value="amount">Fixed Amount ($)</option>
                </select>
                <input type="number" step={newDiscountType === "percent" ? "1" : "0.01"} value={newDiscountValue}
                  onChange={(e) => setNewDiscountValue(e.target.value)} placeholder={newDiscountType === "percent" ? "15" : "5.00"}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
              </div>
            </div>
            {!newIsDirectDiscount && (<>
            <div>
              <label className="text-sm font-medium text-gray-700 block mb-1">Max Uses (0 = unlimited)</label>
              <input type="number" value={newMaxUses} onChange={(e) => setNewMaxUses(e.target.value)} placeholder="0"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
            </div>
            <div className="flex items-center gap-3 pt-6">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={newSingleUse} onChange={(e) => setNewSingleUse(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-300 text-green-600 focus:ring-green-500" />
                <span className="text-sm text-gray-700">Single use per customer</span>
              </label>
            </div>
            </>)}
            <div>
              <label className="text-sm font-medium text-gray-700 flex items-center gap-1 mb-1">
                <Calendar className="w-3.5 h-3.5" /> Start Date (optional)
              </label>
              <input type="date" value={newStartsAt} onChange={(e) => setNewStartsAt(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 flex items-center gap-1 mb-1">
                <Calendar className="w-3.5 h-3.5" /> End Date (optional)
              </label>
              <input type="date" value={newExpiresAt} onChange={(e) => setNewExpiresAt(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
            </div>
          </div>
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
            {renderProductSelector(newAppliesTo, setNewAppliesTo, newProductIds, setNewProductIds, "new")}
            <div className="space-y-3">
              <label className="flex items-center gap-2 cursor-pointer pt-6">
                <input type="checkbox" checked={newExcludeOtherCoupons} onChange={(e) => setNewExcludeOtherCoupons(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-300 text-orange-600 focus:ring-orange-500" />
                <Ban className="w-4 h-4 text-orange-500" />
                <span className="text-sm text-gray-700">Exclude from other coupons</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={newSyncToClover} onChange={(e) => setNewSyncToClover(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                <Cloud className="w-4 h-4 text-blue-500" />
                <span className="text-sm text-gray-700">Sync to Clover POS</span>
              </label>
            </div>
          </div>
          <div className="mt-4 flex gap-2">
            <button onClick={handleCreate}
              className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm font-medium">
              <Plus className="w-4 h-4" /> {newIsDirectDiscount ? "Create Direct Discount" : "Create Promo Code"}
            </button>
            <button onClick={() => { setShowCreate(false); resetCreateForm(); }}
              className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium">Cancel</button>
          </div>
          </>)}
        </div>
      )}

      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading discounts...</div>
      ) : promos.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
          <Percent className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500">No discounts yet</p>
          <p className="text-sm text-gray-400 mt-1">Create one to get started</p>
        </div>
      ) : (
        <div className="space-y-3">
          {promos.map((promo) => (
            <div key={promo.id} className={"bg-white rounded-lg border border-gray-200" + (!promo.is_active ? " opacity-60" : "")}>
              {editingId === promo.id ? (
                <div className="p-5">
                  <div className="flex items-center justify-between mb-4">
                    <span className={"font-mono font-bold px-3 py-1 rounded text-sm " + (promo.is_direct_discount ? "text-purple-700 bg-purple-50" : "text-green-700 bg-green-50")}>
                      {promo.is_direct_discount ? "DIRECT DISCOUNT" : promo.code}
                    </span>
                    <div className="flex items-center gap-1">
                      <button onClick={() => handleUpdate(promo.id)} className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700">
                        <Save className="w-3.5 h-3.5" /> Save
                      </button>
                      <button onClick={() => setEditingId(null)} className="px-3 py-1.5 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200">Cancel</button>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    <div>
                      <label className="text-sm font-medium text-gray-700 block mb-1">Discount</label>
                      <div className="flex gap-1">
                        <select value={editDiscountType} onChange={(e) => setEditDiscountType(e.target.value as "percent" | "amount")}
                          className="px-2 py-1.5 border border-gray-300 rounded text-sm">
                          <option value="percent">%</option><option value="amount">$</option>
                        </select>
                        <input type="number" step={editDiscountType === "percent" ? "1" : "0.01"} value={editDiscountValue}
                          onChange={(e) => setEditDiscountValue(e.target.value)} className="w-24 px-2 py-1.5 border border-gray-300 rounded text-sm" />
                      </div>
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-700 block mb-1">Max Uses (0 = unlimited)</label>
                      <input type="number" value={editMaxUses} onChange={(e) => setEditMaxUses(e.target.value)} placeholder="0"
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm" />
                    </div>
                    <div className="flex items-end pb-1">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={editSingleUse} onChange={(e) => setEditSingleUse(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-300 text-green-600" />
                        <span className="text-sm text-gray-700">Single use per customer</span>
                      </label>
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-700 flex items-center gap-1 mb-1"><Calendar className="w-3.5 h-3.5" /> Start Date</label>
                      <input type="date" value={editStartsAt} onChange={(e) => setEditStartsAt(e.target.value)}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm" />
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-700 flex items-center gap-1 mb-1"><Calendar className="w-3.5 h-3.5" /> End Date</label>
                      <input type="date" value={editExpiresAt} onChange={(e) => setEditExpiresAt(e.target.value)}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm" />
                    </div>
                    <div className="space-y-2 flex flex-col justify-end pb-1">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={editExcludeOtherCoupons} onChange={(e) => setEditExcludeOtherCoupons(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-300 text-orange-600" />
                        <Ban className="w-3.5 h-3.5 text-orange-500" /><span className="text-sm text-gray-700">Exclude other coupons</span>
                      </label>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={editSyncToClover} onChange={(e) => setEditSyncToClover(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-300 text-blue-600" />
                        <Cloud className="w-3.5 h-3.5 text-blue-500" /><span className="text-sm text-gray-700">Sync to Clover</span>
                      </label>
                    </div>
                  </div>
                  <div className="mt-3">
                    {renderProductSelector(editAppliesTo, setEditAppliesTo, editProductIds, setEditProductIds, "edit-" + promo.id)}
                  </div>
                </div>
              ) : (
                <div className="p-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 flex-wrap">
                      {promo.is_direct_discount ? (
                        <span className="font-bold text-purple-700 bg-purple-50 px-3 py-1 rounded text-sm flex items-center gap-1">
                          <ShoppingBag className="w-3.5 h-3.5" /> Direct Discount
                        </span>
                      ) : (
                        <span className="font-mono font-bold text-green-700 bg-green-50 px-3 py-1 rounded text-sm">{promo.code}</span>
                      )}
                      <span className="text-sm font-medium text-gray-900">{formatDiscount(promo)}</span>
                      <button onClick={() => handleToggleActive(promo)} className="inline-flex items-center gap-1"
                        title={promo.is_active ? "Click to disable" : "Click to enable"}>
                        {promo.is_active ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                            <ToggleRight className="w-3.5 h-3.5" /> Active</span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                            <ToggleLeft className="w-3.5 h-3.5" /> Inactive</span>
                        )}
                      </button>
                    </div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => startEdit(promo)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded-lg transition-colors" title="Edit">
                        <Edit3 className="w-4 h-4" /></button>
                      {deleteConfirmId === promo.id ? (
                        <div className="flex items-center gap-1">
                          <button onClick={() => handleDelete(promo.id)} className="px-2 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700">Confirm</button>
                          <button onClick={() => setDeleteConfirmId(null)} className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200">Cancel</button>
                        </div>
                      ) : (
                        <button onClick={() => setDeleteConfirmId(promo.id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg transition-colors" title="Delete">
                          <Trash2 className="w-4 h-4" /></button>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-xs text-gray-500">
                    {!promo.is_direct_discount && <span>Uses: {promo.times_used}{promo.max_uses > 0 ? " / " + promo.max_uses : ""}</span>}
                    {promo.single_use && !promo.is_direct_discount && <span className="text-orange-600">Single use per email</span>}
                    {promo.starts_at && <span><Calendar className="w-3 h-3 inline" /> Starts: {formatDate(promo.starts_at)}</span>}
                    {promo.expires_at && <span><Calendar className="w-3 h-3 inline" /> Expires: {formatDate(promo.expires_at)}</span>}
                    {promo.applies_to === "specific" && (
                      <span className="text-blue-600">
                        <ShoppingBag className="w-3 h-3 inline" /> {promo.product_ids ? promo.product_ids.split(",").length : 0} specific product{promo.product_ids && promo.product_ids.split(",").length !== 1 ? "s" : ""}
                      </span>
                    )}
                    {promo.applies_to === "all" && <span className="text-gray-400">All products</span>}
                    {promo.exclude_from_other_coupons && <span className="text-orange-600"><Ban className="w-3 h-3 inline" /> Excludes other coupons</span>}
                    {promo.clover_discount_id && <span className="text-blue-600"><Cloud className="w-3 h-3 inline" /> Clover synced</span>}
                    <span className="text-gray-400">Created: {formatDate(promo.created_at)}</span>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Volume Discounts List */}
      {!loading && volumeDiscounts.length > 0 && (
        <div className="space-y-3 mt-6">
          <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <Package className="w-5 h-5 text-amber-600" /> Volume Discounts
            <span className="text-sm font-normal text-gray-500">({volumeDiscounts.length})</span>
          </h3>
          {volumeDiscounts.map((vd) => (
            <div key={vd.id} className={"bg-white rounded-lg border border-gray-200" + (!vd.is_active ? " opacity-60" : "")}>
              {vdEditingId === vd.id ? (
                <div className="p-5">
                  <div className="flex items-center justify-between mb-4">
                    <span className="font-bold text-amber-700 bg-amber-50 px-3 py-1 rounded text-sm flex items-center gap-1">
                      <Package className="w-3.5 h-3.5" /> Volume Discount
                    </span>
                    <div className="flex items-center gap-1">
                      <button onClick={() => handleUpdateVolumeDiscount(vd.id)} className="flex items-center gap-1 px-3 py-1.5 bg-amber-600 text-white rounded-lg text-sm hover:bg-amber-700">
                        <Save className="w-3.5 h-3.5" /> Save
                      </button>
                      <button onClick={() => { setVdEditingId(null); resetVdForm(); }} className="px-3 py-1.5 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200">Cancel</button>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="md:col-span-2">
                      <label className="text-sm font-medium text-gray-700 block mb-1">Product</label>
                      {vdSelectedProduct ? (
                        <div className="flex items-center gap-2 p-2 bg-green-50 border border-green-200 rounded-lg">
                          <span className="text-sm font-medium text-green-800">{vdSelectedProduct.name}</span>
                          <button onClick={() => setVdSelectedProduct(null)} className="ml-auto text-red-400 hover:text-red-600"><X className="w-4 h-4" /></button>
                        </div>
                      ) : (
                        <div className="relative">
                          <div className="flex items-center gap-2 border border-gray-300 rounded-lg px-3 py-2">
                            <Search className="w-4 h-4 text-gray-400" />
                            <input type="text" value={vdProductSearch} onChange={(e) => setVdProductSearch(e.target.value)}
                              placeholder="Search for a product..." className="flex-1 text-sm outline-none" />
                          </div>
                          {vdProductSearch.length >= 2 && (
                            <div className="absolute z-10 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                              {vdFilteredProducts.slice(0, 20).map((p) => (
                                <button key={p.id + p.name} onClick={() => { setVdSelectedProduct(p); setVdProductSearch(""); }}
                                  className="w-full text-left px-3 py-2 text-sm hover:bg-green-50 border-b border-gray-100 last:border-b-0">
                                  <span className="font-medium">{p.name}</span>
                                </button>
                              ))}
                              {vdFilteredProducts.length === 0 && <div className="px-3 py-2 text-sm text-gray-400">No products found</div>}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-700 block mb-1">Min Quantity</label>
                      <input type="number" min="2" value={vdMinQty} onChange={(e) => setVdMinQty(e.target.value)}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm" />
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-700 block mb-1">Discount Type</label>
                      <select value={vdDiscountType} onChange={(e) => setVdDiscountType(e.target.value as "fixed_total" | "amount_off" | "percent_off")}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm">
                        <option value="fixed_total">Fixed Total Price</option>
                        <option value="amount_off">Amount Off Per Unit</option>
                        <option value="percent_off">Percent Off</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-700 block mb-1">
                        {vdDiscountType === "fixed_total" ? "Total Price ($)" : vdDiscountType === "amount_off" ? "Off Per Unit ($)" : "Percent Off (%)"}
                      </label>
                      <input type="number" step="0.01" value={vdDiscountValue} onChange={(e) => setVdDiscountValue(e.target.value)}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm" />
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-700 block mb-1">Customer Label</label>
                      <input type="text" value={vdLabel} onChange={(e) => setVdLabel(e.target.value)}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm" />
                    </div>
                    <div className="flex items-end pb-1">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={vdSyncToClover} onChange={(e) => setVdSyncToClover(e.target.checked)}
                          className="w-4 h-4 rounded border-gray-300 text-blue-600" />
                        <Cloud className="w-3.5 h-3.5 text-blue-500" /><span className="text-sm text-gray-700">Sync to Clover</span>
                      </label>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="p-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="font-bold text-amber-700 bg-amber-50 px-3 py-1 rounded text-sm flex items-center gap-1">
                        <Package className="w-3.5 h-3.5" /> Volume Discount
                      </span>
                      <span className="text-sm font-medium text-gray-900">{formatVdRule(vd)}</span>
                      <button onClick={() => handleToggleVdActive(vd)} className="inline-flex items-center gap-1"
                        title={vd.is_active ? "Click to disable" : "Click to enable"}>
                        {vd.is_active ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                            <ToggleRight className="w-3.5 h-3.5" /> Active</span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                            <ToggleLeft className="w-3.5 h-3.5" /> Inactive</span>
                        )}
                      </button>
                    </div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => startVdEdit(vd)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded-lg transition-colors" title="Edit">
                        <Edit3 className="w-4 h-4" /></button>
                      {vdDeleteConfirmId === vd.id ? (
                        <div className="flex items-center gap-1">
                          <button onClick={() => handleDeleteVolumeDiscount(vd.id)} className="px-2 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700">Confirm</button>
                          <button onClick={() => setVdDeleteConfirmId(null)} className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200">Cancel</button>
                        </div>
                      ) : (
                        <button onClick={() => setVdDeleteConfirmId(vd.id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg transition-colors" title="Delete">
                          <Trash2 className="w-4 h-4" /></button>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-xs text-gray-500">
                    <span className="text-amber-600"><ShoppingBag className="w-3 h-3 inline" /> {vd.product_name}</span>
                    {vd.customer_label && <span className="text-gray-700 font-medium">{vd.customer_label}</span>}
                    {vd.clover_discount_id && <span className="text-blue-600"><Cloud className="w-3 h-3 inline" /> Clover synced</span>}
                    <span className="text-gray-400">Created: {formatDate(vd.created_at)}</span>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
