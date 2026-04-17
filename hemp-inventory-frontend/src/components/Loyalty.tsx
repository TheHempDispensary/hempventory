import { useState, useEffect, useCallback } from "react";
import {
  getLoyaltyDashboard,
  getLoyaltyCustomers,
  createLoyaltyCustomer,
  getLoyaltyCustomer,
  updateLoyaltyCustomer,
  deleteLoyaltyCustomer,
  awardLoyaltyPoints,
  deductLoyaltyPoints,
  redeemLoyaltyReward,
  getLoyaltyRewards,
  createLoyaltyReward,
  updateLoyaltyReward,
  deleteLoyaltyReward,
  updateLoyaltySettings,
  syncLoyaltyOrders,
  getLoyaltySyncStatus,
  resetLoyaltySync,
  bulkImportLoyaltyCustomers,
} from "../lib/api";
import {
  Search, Plus, Gift, Star, Users, TrendingUp, Award, Settings,
  X, ChevronRight, Minus, Trash2, Edit3, Save, Phone, Mail, Calendar,
  RefreshCw, ShoppingCart, Upload,
} from "lucide-react";

interface LoyaltyCustomer {
  id: number;
  first_name: string;
  last_name: string;
  phone: string;
  email: string;
  birthday: string;
  points_balance: number;
  lifetime_points: number;
  lifetime_redeemed: number;
  clover_customer_id: string;
  notes: string;
  created_at: string;
  updated_at: string;
  transactions?: Transaction[];
  redemptions?: Redemption[];
}

interface Transaction {
  id: number;
  type: string;
  points: number;
  description: string;
  order_id: string;
  location_name: string;
  created_at: string;
}

interface Redemption {
  id: number;
  points_spent: number;
  location_name: string;
  created_at: string;
  reward_name: string;
}

interface Reward {
  id: number;
  name: string;
  points_required: number;
  reward_type: string;
  reward_value: number;
  description: string;
  is_active: boolean;
  created_at: string;
}

interface DashboardStats {
  total_customers: number;
  total_outstanding_points: number;
  total_awarded_points: number;
  total_redeemed_points: number;
}

interface RecentTxn {
  id: number;
  customer_id: number;
  type: string;
  points: number;
  description: string;
  order_id: string;
  location_name: string;
  created_at: string;
  customer_name: string;
}

interface TopCustomer {
  id: number;
  first_name: string;
  last_name: string;
  phone: string;
  points_balance: number;
  lifetime_points: number;
}

type Tab = "overview" | "customers" | "rewards" | "settings";

export default function Loyalty() {
  const [tab, setTab] = useState<Tab>("overview");
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentTxns, setRecentTxns] = useState<RecentTxn[]>([]);
  const [topCustomers, setTopCustomers] = useState<TopCustomer[]>([]);
  const [settings, setSettings] = useState<Record<string, string>>({});

  // Customers
  const [customers, setCustomers] = useState<LoyaltyCustomer[]>([]);
  const [customerSearch, setCustomerSearch] = useState("");
  const [customerTotal, setCustomerTotal] = useState(0);
  const [customerPage, setCustomerPage] = useState(1);
  const [selectedCustomer, setSelectedCustomer] = useState<LoyaltyCustomer | null>(null);
  const [showAddCustomer, setShowAddCustomer] = useState(false);
  const [showPointsModal, setShowPointsModal] = useState<{ type: "award" | "deduct"; customer: LoyaltyCustomer } | null>(null);
  const [showRedeemModal, setShowRedeemModal] = useState<LoyaltyCustomer | null>(null);

  // Rewards
  const [rewards, setRewards] = useState<Reward[]>([]);
  const [showAddReward, setShowAddReward] = useState(false);
  const [editingReward, setEditingReward] = useState<Reward | null>(null);
  const [editRewardForm, setEditRewardForm] = useState({ name: "", points_required: "", reward_value: "", description: "" });

  // Forms
  const [newCustomer, setNewCustomer] = useState({ first_name: "", last_name: "", phone: "", email: "", birthday: "", notes: "" });
  const [pointsForm, setPointsForm] = useState({ points: "", description: "", location_name: "" });
  const [newReward, setNewReward] = useState({ name: "", points_required: "", reward_value: "", description: "" });
  const [settingsForm, setSettingsForm] = useState({ points_per_dollar: "", signup_bonus: "", birthday_bonus: "", program_name: "" });

  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Order sync state
  const [syncing, setSyncing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [syncStatus, setSyncStatus] = useState<{
    total_orders_synced: number;
    total_orders_awarded: number;
    total_points_awarded: number;
    last_sync: string | null;
    recent: { order_id: string; location: string; order_total: number; points_awarded: number; status: string; synced_at: string; customer_name: string }[];
  } | null>(null);

  const showToast = (type: "success" | "error", text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 4000);
  };

  const loadDashboard = useCallback(async () => {
    try {
      const resp = await getLoyaltyDashboard();
      setStats(resp.data.stats);
      setRecentTxns(resp.data.recent_transactions);
      setTopCustomers(resp.data.top_customers);
      setSettings(resp.data.settings);
      setSettingsForm({
        points_per_dollar: resp.data.settings.points_per_dollar || "1",
        signup_bonus: resp.data.settings.signup_bonus || "10",
        birthday_bonus: resp.data.settings.birthday_bonus || "25",
        program_name: resp.data.settings.program_name || "Hemp Rewards",
      });
    } catch (err) {
      console.error("Failed to load dashboard:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCustomers = useCallback(async (search?: string, page?: number) => {
    try {
      const resp = await getLoyaltyCustomers(search, page);
      setCustomers(resp.data.customers);
      setCustomerTotal(resp.data.total);
    } catch (err) {
      console.error("Failed to load customers:", err);
    }
  }, []);

  const loadRewards = useCallback(async () => {
    try {
      const resp = await getLoyaltyRewards();
      setRewards(resp.data.rewards);
    } catch (err) {
      console.error("Failed to load rewards:", err);
    }
  }, []);

  const loadSyncStatus = useCallback(async () => {
    try {
      const resp = await getLoyaltySyncStatus();
      setSyncStatus(resp.data);
    } catch (err) {
      console.error("Failed to load sync status:", err);
    }
  }, []);

  const handleSyncOrders = async () => {
    setSyncing(true);
    try {
      const resp = await syncLoyaltyOrders();
      const data = resp.data;
      if (data.orders_processed > 0) {
        showToast("success", `Synced ${data.orders_processed} orders, awarded ${data.points_awarded} points!`);
      } else {
        showToast("success", `No new orders to sync (${data.orders_skipped} already synced, ${data.orders_no_match} no customer match)`);
      }
      loadDashboard();
      loadSyncStatus();
    } catch (err) {
      console.error("Failed to sync orders:", err);
      showToast("error", "Failed to sync orders from POS");
    } finally {
      setSyncing(false);
    }
  };

  const handleBulkImport = async () => {
    setImporting(true);
    try {
      const resp = await bulkImportLoyaltyCustomers();
      const data = resp.data;
      if (data.imported > 0) {
        showToast("success", `Imported ${data.imported} customers from Clover! (${data.skipped} already enrolled, ${data.total_clover_customers} total in Clover)`);
      } else {
        showToast("success", `No new customers to import (${data.skipped} already enrolled, ${data.total_clover_customers} total in Clover)`);
      }
      loadCustomers(customerSearch || undefined, customerPage);
      loadDashboard();
    } catch (err) {
      console.error("Failed to bulk import:", err);
      showToast("error", "Failed to import customers from Clover");
    } finally {
      setImporting(false);
    }
  };

  const handleResetAndResync = async () => {
    setSyncing(true);
    try {
      await resetLoyaltySync();
      showToast("success", "Sync history cleared. Re-syncing all orders...");
      const resp = await syncLoyaltyOrders();
      const data = resp.data;
      if (data.orders_processed > 0) {
        showToast("success", `Re-synced ${data.orders_processed} orders, awarded ${data.points_awarded} points!`);
      } else {
        showToast("success", `Re-sync complete (${data.orders_no_match} no customer match)`);
      }
      loadDashboard();
      loadSyncStatus();
    } catch (err) {
      console.error("Failed to reset & re-sync:", err);
      showToast("error", "Failed to reset & re-sync");
    } finally {
      setSyncing(false);
    }
  };

  useEffect(() => {
    loadDashboard();
    loadCustomers();
    loadRewards();
    loadSyncStatus();
  }, [loadDashboard, loadCustomers, loadRewards, loadSyncStatus]);

  useEffect(() => {
    const timer = setTimeout(() => {
      loadCustomers(customerSearch || undefined, customerPage);
    }, 300);
    return () => clearTimeout(timer);
  }, [customerSearch, customerPage, loadCustomers]);

  const handleAddCustomer = async () => {
    if (!newCustomer.first_name.trim()) {
      showToast("error", "First name is required");
      return;
    }
    try {
      await createLoyaltyCustomer(newCustomer);
      showToast("success", `Customer "${newCustomer.first_name}" added with sign-up bonus!`);
      setShowAddCustomer(false);
      setNewCustomer({ first_name: "", last_name: "", phone: "", email: "", birthday: "", notes: "" });
      loadCustomers(customerSearch || undefined, customerPage);
      loadDashboard();
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      showToast("error", axErr.response?.data?.detail || "Failed to add customer");
    }
  };

  const handleSelectCustomer = async (id: number) => {
    try {
      const resp = await getLoyaltyCustomer(id);
      setSelectedCustomer(resp.data);
    } catch (err) {
      console.error(err);
    }
  };

  const handleAwardDeduct = async () => {
    if (!showPointsModal || !pointsForm.points) return;
    const pts = parseInt(pointsForm.points);
    if (isNaN(pts) || pts <= 0) { showToast("error", "Enter valid points"); return; }

    try {
      if (showPointsModal.type === "award") {
        await awardLoyaltyPoints(showPointsModal.customer.id, {
          points: pts,
          description: pointsForm.description || "Manual award",
          location_name: pointsForm.location_name || undefined,
        });
        showToast("success", `Awarded ${pts} points`);
      } else {
        await deductLoyaltyPoints(showPointsModal.customer.id, {
          points: pts,
          description: pointsForm.description || "Manual deduction",
          location_name: pointsForm.location_name || undefined,
        });
        showToast("success", `Deducted ${pts} points`);
      }
      setShowPointsModal(null);
      setPointsForm({ points: "", description: "", location_name: "" });
      if (selectedCustomer) handleSelectCustomer(selectedCustomer.id);
      loadCustomers(customerSearch || undefined, customerPage);
      loadDashboard();
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      showToast("error", axErr.response?.data?.detail || "Failed");
    }
  };

  const handleRedeem = async (rewardId: number) => {
    if (!showRedeemModal) return;
    try {
      const resp = await redeemLoyaltyReward(showRedeemModal.id, { reward_id: rewardId });
      showToast("success", `Redeemed: ${resp.data.reward_redeemed} (-${resp.data.points_spent} pts)`);
      setShowRedeemModal(null);
      if (selectedCustomer) handleSelectCustomer(selectedCustomer.id);
      loadCustomers(customerSearch || undefined, customerPage);
      loadDashboard();
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      showToast("error", axErr.response?.data?.detail || "Failed to redeem");
    }
  };

  const handleDeleteCustomer = async (id: number) => {
    if (!confirm("Delete this customer and all their points history?")) return;
    try {
      await deleteLoyaltyCustomer(id);
      showToast("success", "Customer deleted");
      setSelectedCustomer(null);
      loadCustomers(customerSearch || undefined, customerPage);
      loadDashboard();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to delete");
    }
  };

  const handleAddReward = async () => {
    if (!newReward.name || !newReward.points_required || !newReward.reward_value) {
      showToast("error", "Fill in all reward fields");
      return;
    }
    try {
      await createLoyaltyReward({
        name: newReward.name,
        points_required: parseInt(newReward.points_required),
        reward_value: parseFloat(newReward.reward_value),
        description: newReward.description,
      });
      showToast("success", "Reward created");
      setShowAddReward(false);
      setNewReward({ name: "", points_required: "", reward_value: "", description: "" });
      loadRewards();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to create reward");
    }
  };

  const handleEditReward = async () => {
    if (!editingReward || !editRewardForm.name || !editRewardForm.points_required || !editRewardForm.reward_value) {
      showToast("error", "Fill in all reward fields");
      return;
    }
    try {
      await updateLoyaltyReward(editingReward.id, {
        name: editRewardForm.name,
        points_required: parseInt(editRewardForm.points_required),
        reward_value: parseFloat(editRewardForm.reward_value),
        description: editRewardForm.description,
      });
      showToast("success", "Reward updated");
      setEditingReward(null);
      loadRewards();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to update reward");
    }
  };

  const handleToggleReward = async (reward: Reward) => {
    try {
      await updateLoyaltyReward(reward.id, { is_active: !reward.is_active });
      loadRewards();
    } catch (err) {
      console.error(err);
    }
  };

  const handleDeleteReward = async (id: number) => {
    if (!confirm("Delete this reward?")) return;
    try {
      await deleteLoyaltyReward(id);
      loadRewards();
    } catch (err) {
      console.error(err);
    }
  };

  const handleSaveSettings = async () => {
    try {
      await updateLoyaltySettings(settingsForm);
      showToast("success", "Settings saved");
      loadDashboard();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to save settings");
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-green-500" />
      </div>
    );
  }

  const tabs: { id: Tab; label: string; icon: typeof Star }[] = [
    { id: "overview", label: "Overview", icon: TrendingUp },
    { id: "customers", label: "Customers", icon: Users },
    { id: "rewards", label: "Rewards", icon: Gift },
    { id: "settings", label: "Settings", icon: Settings },
  ];

  return (
    <div>
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-white text-sm ${toast.type === "success" ? "bg-green-600" : "bg-red-600"}`}>
          {toast.text}
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{settings.program_name || "Hemp Rewards"}</h1>
          <p className="text-sm text-gray-500">Loyalty program — works across all locations & online</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-gray-100 rounded-lg p-1 w-fit">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                tab === t.id ? "bg-white text-green-700 shadow-sm" : "text-gray-500 hover:text-gray-700"
              }`}
            >
              <Icon className="w-4 h-4" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* ── Overview Tab ── */}
      {tab === "overview" && (
        <div className="space-y-6">
          {/* Stats */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { label: "Total Members", value: stats?.total_customers || 0, icon: Users, color: "text-blue-600 bg-blue-50" },
              { label: "Points Awarded", value: (stats?.total_awarded_points || 0).toLocaleString(), icon: TrendingUp, color: "text-green-600 bg-green-50" },
              { label: "Points Redeemed", value: (stats?.total_redeemed_points || 0).toLocaleString(), icon: Award, color: "text-purple-600 bg-purple-50" },
              { label: "Outstanding", value: (stats?.total_outstanding_points || 0).toLocaleString(), icon: Star, color: "text-orange-600 bg-orange-50" },
            ].map((s) => (
              <div key={s.label} className="bg-white rounded-xl border border-gray-200 p-5">
                <div className="flex items-center gap-3 mb-2">
                  <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${s.color}`}>
                    <s.icon className="w-5 h-5" />
                  </div>
                  <span className="text-sm text-gray-500">{s.label}</span>
                </div>
                <p className="text-2xl font-bold text-gray-900">{s.value}</p>
              </div>
            ))}
          </div>

          {/* POS Order Sync */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-indigo-50 flex items-center justify-center">
                  <ShoppingCart className="w-5 h-5 text-indigo-600" />
                </div>
                <div>
                  <h3 className="font-semibold text-gray-900">POS Order Sync</h3>
                  <p className="text-xs text-gray-500">
                    {syncStatus?.last_sync
                      ? `Last synced: ${new Date(syncStatus.last_sync).toLocaleString("en-US", { timeZone: "America/New_York" })}`
                      : "Never synced — click to sync POS orders and auto-award loyalty points"}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                {syncStatus && syncStatus.total_orders_awarded > 0 && (
                  <div className="text-right text-sm">
                    <span className="text-green-600 font-semibold">{syncStatus.total_points_awarded.toLocaleString()} pts</span>
                    <span className="text-gray-400 ml-1">from {syncStatus.total_orders_awarded} orders</span>
                  </div>
                )}
                <button
                  onClick={handleResetAndResync}
                  disabled={syncing}
                  className="flex items-center gap-2 px-3 py-2 border border-orange-300 bg-orange-50 text-orange-700 rounded-lg hover:bg-orange-100 text-xs disabled:opacity-50 transition-colors"
                  title="Clear sync history and re-process all orders with improved matching"
                >
                  Reset & Re-sync
                </button>
                <button
                  onClick={handleSyncOrders}
                  disabled={syncing}
                  className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm disabled:opacity-50 transition-colors"
                >
                  <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
                  {syncing ? "Syncing..." : "Sync POS Orders"}
                </button>
              </div>
            </div>
            {/* Recent synced orders */}
            {syncStatus && syncStatus.recent && syncStatus.recent.length > 0 && (
              <div className="mt-4 border-t border-gray-100 pt-3">
                <p className="text-xs font-medium text-gray-500 mb-2">Recent Synced Orders</p>
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {syncStatus.recent.slice(0, 5).map((r, i) => (
                    <div key={i} className="flex items-center justify-between text-xs py-1">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${
                          r.status === "awarded" ? "bg-green-500" : r.status === "no_match" ? "bg-gray-300" : "bg-yellow-400"
                        }`} />
                        <span className="text-gray-600">{r.customer_name || "No match"}</span>
                        <span className="text-gray-400">@ {r.location}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-gray-500">${r.order_total.toFixed(2)}</span>
                        {r.points_awarded > 0 && (
                          <span className="text-green-600 font-medium">+{r.points_awarded} pts</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="grid lg:grid-cols-2 gap-6">
            {/* Recent Transactions */}
            <div className="bg-white rounded-xl border border-gray-200">
              <div className="px-5 py-4 border-b border-gray-100">
                <h3 className="font-semibold text-gray-900">Recent Activity</h3>
              </div>
              <div className="divide-y divide-gray-50 max-h-80 overflow-y-auto">
                {recentTxns.length === 0 ? (
                  <p className="text-sm text-gray-400 p-5 text-center">No activity yet</p>
                ) : recentTxns.map((tx) => (
                  <div key={tx.id} className="px-5 py-3 flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-gray-900">{tx.customer_name}</p>
                      <p className="text-xs text-gray-500">{tx.description}</p>
                    </div>
                    <span className={`text-sm font-semibold ${tx.points > 0 ? "text-green-600" : "text-red-500"}`}>
                      {tx.points > 0 ? "+" : ""}{tx.points} pts
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Top Customers */}
            <div className="bg-white rounded-xl border border-gray-200">
              <div className="px-5 py-4 border-b border-gray-100">
                <h3 className="font-semibold text-gray-900">Top Members</h3>
              </div>
              <div className="divide-y divide-gray-50 max-h-80 overflow-y-auto">
                {topCustomers.length === 0 ? (
                  <p className="text-sm text-gray-400 p-5 text-center">No members yet</p>
                ) : topCustomers.map((c, i) => (
                  <div key={c.id} className="px-5 py-3 flex items-center justify-between cursor-pointer hover:bg-gray-50" onClick={() => { setTab("customers"); handleSelectCustomer(c.id); }}>
                    <div className="flex items-center gap-3">
                      <span className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${i < 3 ? "bg-yellow-100 text-yellow-700" : "bg-gray-100 text-gray-500"}`}>
                        {i + 1}
                      </span>
                      <div>
                        <p className="text-sm font-medium text-gray-900">{c.first_name} {c.last_name}</p>
                        {c.phone && <p className="text-xs text-gray-500">{c.phone}</p>}
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-sm font-semibold text-green-600">{c.points_balance} pts</p>
                      <p className="text-xs text-gray-400">{c.lifetime_points} lifetime</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Customers Tab ── */}
      {tab === "customers" && !selectedCustomer && (
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Search by name, phone, or email..."
                value={customerSearch}
                onChange={(e) => { setCustomerSearch(e.target.value); setCustomerPage(1); }}
                className="w-full pl-10 pr-4 py-2.5 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-transparent"
              />
            </div>
            <button
              onClick={handleBulkImport}
              disabled={importing}
              className="flex items-center gap-2 px-4 py-2.5 border border-indigo-300 bg-indigo-50 text-indigo-700 rounded-lg text-sm font-medium hover:bg-indigo-100 disabled:opacity-50 transition-colors"
              title="Import all Clover customers with phone numbers into loyalty program"
            >
              <Upload className={`w-4 h-4 ${importing ? "animate-spin" : ""}`} />
              {importing ? "Importing..." : "Import from Clover"}
            </button>
            <button
              onClick={() => setShowAddCustomer(true)}
              className="flex items-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700"
            >
              <Plus className="w-4 h-4" /> Add Member
            </button>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Name</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600 hidden sm:table-cell">Phone</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600 hidden md:table-cell">Email</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Points</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600 hidden sm:table-cell">Lifetime</th>
                  <th className="text-center px-4 py-3 font-medium text-gray-600">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {customers.map((c) => (
                  <tr key={c.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <button className="text-green-600 hover:underline font-medium" onClick={() => handleSelectCustomer(c.id)}>
                        {c.first_name} {c.last_name}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-gray-600 hidden sm:table-cell">{c.phone || "—"}</td>
                    <td className="px-4 py-3 text-gray-600 hidden md:table-cell">{c.email || "—"}</td>
                    <td className="px-4 py-3 text-right font-semibold text-green-600">{c.points_balance}</td>
                    <td className="px-4 py-3 text-right text-gray-500 hidden sm:table-cell">{c.lifetime_points}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-center gap-1">
                        <button onClick={() => setShowPointsModal({ type: "award", customer: c })} className="p-1.5 rounded hover:bg-green-50 text-green-600" title="Award points"><Plus className="w-4 h-4" /></button>
                        <button onClick={() => setShowRedeemModal(c)} className="p-1.5 rounded hover:bg-purple-50 text-purple-600" title="Redeem reward"><Gift className="w-4 h-4" /></button>
                      </div>
                    </td>
                  </tr>
                ))}
                {customers.length === 0 && (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">No members found</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {customerTotal > 50 && (
            <div className="flex justify-center gap-2">
              <button disabled={customerPage <= 1} onClick={() => setCustomerPage(customerPage - 1)} className="px-3 py-1.5 border rounded text-sm disabled:opacity-50">Prev</button>
              <span className="px-3 py-1.5 text-sm text-gray-600">Page {customerPage} of {Math.ceil(customerTotal / 50)}</span>
              <button disabled={customerPage >= Math.ceil(customerTotal / 50)} onClick={() => setCustomerPage(customerPage + 1)} className="px-3 py-1.5 border rounded text-sm disabled:opacity-50">Next</button>
            </div>
          )}
        </div>
      )}

      {/* ── Customer Detail ── */}
      {tab === "customers" && selectedCustomer && (
        <CustomerDetail
          customer={selectedCustomer}
          rewards={rewards}
          onBack={() => setSelectedCustomer(null)}
          onAward={(c) => setShowPointsModal({ type: "award", customer: c })}
          onDeduct={(c) => setShowPointsModal({ type: "deduct", customer: c })}
          onRedeem={(c) => setShowRedeemModal(c)}
          onDelete={handleDeleteCustomer}
          onUpdate={async (id, data) => {
            try {
              await updateLoyaltyCustomer(id, data);
              showToast("success", "Customer updated");
              handleSelectCustomer(id);
              loadCustomers(customerSearch || undefined, customerPage);
            } catch (err: unknown) {
              const axErr = err as { response?: { data?: { detail?: string } } };
              showToast("error", axErr.response?.data?.detail || "Update failed");
            }
          }}
        />
      )}

      {/* ── Rewards Tab ── */}
      {tab === "rewards" && (
        <div className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-semibold text-gray-900">Reward Tiers</h2>
            <button onClick={() => setShowAddReward(true)} className="flex items-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700">
              <Plus className="w-4 h-4" /> Add Reward
            </button>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {rewards.map((r) => (
              <div key={r.id} className={`bg-white rounded-xl border p-5 ${r.is_active ? "border-gray-200" : "border-gray-100 opacity-60"}`}>
                <div className="flex items-start justify-between mb-3">
                  <div className="w-10 h-10 rounded-lg bg-purple-50 flex items-center justify-center">
                    <Gift className="w-5 h-5 text-purple-600" />
                  </div>
                  <div className="flex gap-1">
                    <button onClick={() => handleToggleReward(r)} className={`px-2 py-1 rounded text-xs font-medium ${r.is_active ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                      {r.is_active ? "Active" : "Inactive"}
                    </button>
                    <button onClick={() => { setEditingReward(r); setEditRewardForm({ name: r.name, points_required: String(r.points_required), reward_value: String(r.reward_value), description: r.description || "" }); }} className="p-1 rounded hover:bg-blue-50 text-blue-400"><Edit3 className="w-3.5 h-3.5" /></button>
                    <button onClick={() => handleDeleteReward(r.id)} className="p-1 rounded hover:bg-red-50 text-red-400"><Trash2 className="w-3.5 h-3.5" /></button>
                  </div>
                </div>
                <h3 className="font-semibold text-gray-900 mb-1">{r.name}</h3>
                <p className="text-sm text-gray-500 mb-3">{r.description || "No description"}</p>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-purple-600">{r.points_required} pts required</span>
                  <span className="text-sm font-bold text-green-600">${r.reward_value.toFixed(2)} off</span>
                </div>
              </div>
            ))}
            {rewards.length === 0 && (
              <div className="col-span-full text-center py-12 text-gray-400">
                <Gift className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p>No rewards configured yet</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Settings Tab ── */}
      {tab === "settings" && (
        <div className="max-w-lg space-y-6">
          <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
            <h2 className="text-lg font-semibold text-gray-900">Program Settings</h2>
            {[
              { key: "program_name" as const, label: "Program Name", type: "text" },
              { key: "points_per_dollar" as const, label: "Points per $1 spent", type: "number" },
              { key: "signup_bonus" as const, label: "Sign-up Bonus (points)", type: "number" },
              { key: "birthday_bonus" as const, label: "Birthday Bonus (points)", type: "number" },
            ].map((field) => (
              <div key={field.key}>
                <label className="block text-sm font-medium text-gray-700 mb-1">{field.label}</label>
                <input
                  type={field.type}
                  value={settingsForm[field.key]}
                  onChange={(e) => setSettingsForm({ ...settingsForm, [field.key]: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-transparent"
                />
              </div>
            ))}
            <button onClick={handleSaveSettings} className="flex items-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700">
              <Save className="w-4 h-4" /> Save Settings
            </button>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="font-semibold text-gray-900 mb-2">How It Works</h3>
            <ul className="text-sm text-gray-600 space-y-2">
              <li>Customers earn <strong>{settings.points_per_dollar || 1} point(s)</strong> per $1 spent</li>
              <li>New members get a <strong>{settings.signup_bonus || 10} point</strong> sign-up bonus</li>
              <li>Birthday bonus: <strong>{settings.birthday_bonus || 25} points</strong></li>
              <li>Points work across <strong>all locations</strong> and your online store</li>
              <li>Customers can look up points on the e-commerce site by phone or email</li>
            </ul>
          </div>
        </div>
      )}

      {/* ── Add Customer Modal ── */}
      {showAddCustomer && (
        <Modal title="Add New Member" onClose={() => setShowAddCustomer(false)}>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">First Name *</label>
                <input value={newCustomer.first_name} onChange={(e) => setNewCustomer({ ...newCustomer, first_name: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Last Name</label>
                <input value={newCustomer.last_name} onChange={(e) => setNewCustomer({ ...newCustomer, last_name: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Phone</label>
              <input value={newCustomer.phone} onChange={(e) => setNewCustomer({ ...newCustomer, phone: e.target.value })} placeholder="352-xxx-xxxx" className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Email</label>
              <input value={newCustomer.email} onChange={(e) => setNewCustomer({ ...newCustomer, email: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Birthday</label>
              <input type="date" value={newCustomer.birthday} onChange={(e) => setNewCustomer({ ...newCustomer, birthday: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Notes</label>
              <textarea value={newCustomer.notes} onChange={(e) => setNewCustomer({ ...newCustomer, notes: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" rows={2} />
            </div>
            <p className="text-xs text-green-600">New member will receive {settings.signup_bonus || "10"} bonus points!</p>
            <button onClick={handleAddCustomer} className="w-full px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700">
              Add Member
            </button>
          </div>
        </Modal>
      )}

      {/* ── Award/Deduct Points Modal ── */}
      {showPointsModal && (
        <Modal title={`${showPointsModal.type === "award" ? "Award" : "Deduct"} Points — ${showPointsModal.customer.first_name}`} onClose={() => { setShowPointsModal(null); setPointsForm({ points: "", description: "", location_name: "" }); }}>
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Points</label>
              <input type="number" value={pointsForm.points} onChange={(e) => setPointsForm({ ...pointsForm, points: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" min="1" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Description</label>
              <input value={pointsForm.description} onChange={(e) => setPointsForm({ ...pointsForm, description: e.target.value })} placeholder={showPointsModal.type === "award" ? "Purchase" : "Adjustment"} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Location</label>
              <input value={pointsForm.location_name} onChange={(e) => setPointsForm({ ...pointsForm, location_name: e.target.value })} placeholder="East / West / Online" className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <p className="text-xs text-gray-500">Current balance: {showPointsModal.customer.points_balance} pts</p>
            <button onClick={handleAwardDeduct} className={`w-full px-4 py-2.5 text-white rounded-lg text-sm font-medium ${showPointsModal.type === "award" ? "bg-green-600 hover:bg-green-700" : "bg-red-600 hover:bg-red-700"}`}>
              {showPointsModal.type === "award" ? "Award" : "Deduct"} Points
            </button>
          </div>
        </Modal>
      )}

      {/* ── Redeem Reward Modal ── */}
      {showRedeemModal && (
        <Modal title={`Redeem Reward — ${showRedeemModal.first_name}`} onClose={() => setShowRedeemModal(null)}>
          <p className="text-sm text-gray-600 mb-3">Balance: <strong className="text-green-600">{showRedeemModal.points_balance} pts</strong></p>
          <div className="space-y-2">
            {rewards.filter(r => r.is_active).map((r) => {
              const canRedeem = showRedeemModal.points_balance >= r.points_required;
              return (
                <button
                  key={r.id}
                  disabled={!canRedeem}
                  onClick={() => handleRedeem(r.id)}
                  className={`w-full flex items-center justify-between p-3 rounded-lg border text-sm ${canRedeem ? "border-green-200 bg-green-50 hover:bg-green-100 cursor-pointer" : "border-gray-100 bg-gray-50 opacity-50 cursor-not-allowed"}`}
                >
                  <div className="text-left">
                    <p className="font-medium text-gray-900">{r.name}</p>
                    <p className="text-xs text-gray-500">{r.points_required} pts</p>
                  </div>
                  <span className="font-bold text-green-600">${r.reward_value.toFixed(2)} off</span>
                </button>
              );
            })}
            {rewards.filter(r => r.is_active).length === 0 && (
              <p className="text-sm text-gray-400 text-center py-4">No active rewards</p>
            )}
          </div>
        </Modal>
      )}

      {/* ── Edit Reward Modal ── */}
      {editingReward && (
        <Modal title="Edit Reward" onClose={() => setEditingReward(null)}>
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Reward Name</label>
              <input value={editRewardForm.name} onChange={(e) => setEditRewardForm({ ...editRewardForm, name: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Points Required</label>
                <input type="number" value={editRewardForm.points_required} onChange={(e) => setEditRewardForm({ ...editRewardForm, points_required: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Discount Value ($)</label>
                <input type="number" step="0.01" value={editRewardForm.reward_value} onChange={(e) => setEditRewardForm({ ...editRewardForm, reward_value: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Description</label>
              <input value={editRewardForm.description} onChange={(e) => setEditRewardForm({ ...editRewardForm, description: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <button onClick={handleEditReward} className="w-full px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700">
              Save Changes
            </button>
          </div>
        </Modal>
      )}

      {/* ── Add Reward Modal ── */}
      {showAddReward && (
        <Modal title="Add New Reward" onClose={() => setShowAddReward(false)}>
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Reward Name</label>
              <input value={newReward.name} onChange={(e) => setNewReward({ ...newReward, name: e.target.value })} placeholder="e.g. $5 off any purchase" className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Points Required</label>
                <input type="number" value={newReward.points_required} onChange={(e) => setNewReward({ ...newReward, points_required: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Discount Value ($)</label>
                <input type="number" step="0.01" value={newReward.reward_value} onChange={(e) => setNewReward({ ...newReward, reward_value: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Description</label>
              <input value={newReward.description} onChange={(e) => setNewReward({ ...newReward, description: e.target.value })} className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
            </div>
            <button onClick={handleAddReward} className="w-full px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700">
              Create Reward
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}


// ── Sub-components ──────────────────────────────────────

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-gray-100"><X className="w-5 h-5 text-gray-400" /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}


function CustomerDetail({
  customer, rewards, onBack, onAward, onDeduct, onRedeem, onDelete, onUpdate,
}: {
  customer: LoyaltyCustomer;
  rewards: Reward[];
  onBack: () => void;
  onAward: (c: LoyaltyCustomer) => void;
  onDeduct: (c: LoyaltyCustomer) => void;
  onRedeem: (c: LoyaltyCustomer) => void;
  onDelete: (id: number) => void;
  onUpdate: (id: number, data: Record<string, string>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    first_name: customer.first_name,
    last_name: customer.last_name,
    phone: customer.phone,
    email: customer.email,
    birthday: customer.birthday,
    notes: customer.notes,
  });

  // Next available reward
  const nextReward = rewards.filter(r => r.is_active).sort((a, b) => a.points_required - b.points_required).find(r => r.points_required > customer.points_balance);
  const progressReward = rewards.filter(r => r.is_active).sort((a, b) => a.points_required - b.points_required)[0];
  const progressPct = progressReward ? Math.min(100, (customer.points_balance / progressReward.points_required) * 100) : 0;

  return (
    <div className="space-y-6">
      <button onClick={onBack} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
        <ChevronRight className="w-4 h-4 rotate-180" /> Back to members
      </button>

      {/* Profile card */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 mb-6">
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-full bg-green-100 flex items-center justify-center text-green-700 font-bold text-xl">
              {customer.first_name.charAt(0)}{(customer.last_name || "").charAt(0)}
            </div>
            <div>
              {editing ? (
                <div className="flex gap-2 mb-1">
                  <input value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} className="px-2 py-1 border rounded text-sm w-28" />
                  <input value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} className="px-2 py-1 border rounded text-sm w-28" />
                </div>
              ) : (
                <h2 className="text-xl font-bold text-gray-900">{customer.first_name} {customer.last_name}</h2>
              )}
              <p className="text-sm text-gray-500">Member since {new Date(customer.created_at).toLocaleDateString("en-US", { timeZone: "America/New_York" })}</p>
            </div>
          </div>
          <div className="flex gap-2">
            {editing ? (
              <>
                <button onClick={() => { onUpdate(customer.id, form); setEditing(false); }} className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white rounded-lg text-sm"><Save className="w-3.5 h-3.5" /> Save</button>
                <button onClick={() => setEditing(false)} className="px-3 py-1.5 border rounded-lg text-sm text-gray-600">Cancel</button>
              </>
            ) : (
              <>
                <button onClick={() => setEditing(true)} className="flex items-center gap-1 px-3 py-1.5 border rounded-lg text-sm text-gray-600 hover:bg-gray-50"><Edit3 className="w-3.5 h-3.5" /> Edit</button>
                <button onClick={() => onDelete(customer.id)} className="flex items-center gap-1 px-3 py-1.5 border border-red-200 rounded-lg text-sm text-red-600 hover:bg-red-50"><Trash2 className="w-3.5 h-3.5" /> Delete</button>
              </>
            )}
          </div>
        </div>

        {/* Contact info */}
        <div className="grid sm:grid-cols-3 gap-4 mb-6">
          {editing ? (
            <>
              <div><label className="text-xs text-gray-500">Phone</label><input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className="w-full px-2 py-1 border rounded text-sm mt-0.5" /></div>
              <div><label className="text-xs text-gray-500">Email</label><input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className="w-full px-2 py-1 border rounded text-sm mt-0.5" /></div>
              <div><label className="text-xs text-gray-500">Birthday</label><input type="date" value={form.birthday} onChange={(e) => setForm({ ...form, birthday: e.target.value })} className="w-full px-2 py-1 border rounded text-sm mt-0.5" /></div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2 text-sm text-gray-600"><Phone className="w-4 h-4 text-gray-400" />{customer.phone || "No phone"}</div>
              <div className="flex items-center gap-2 text-sm text-gray-600"><Mail className="w-4 h-4 text-gray-400" />{customer.email || "No email"}</div>
              <div className="flex items-center gap-2 text-sm text-gray-600"><Calendar className="w-4 h-4 text-gray-400" />{customer.birthday || "No birthday"}</div>
            </>
          )}
        </div>

        {/* Points summary */}
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="bg-green-50 rounded-lg p-4 text-center">
            <p className="text-2xl font-bold text-green-600">{customer.points_balance}</p>
            <p className="text-xs text-green-700">Current Balance</p>
          </div>
          <div className="bg-blue-50 rounded-lg p-4 text-center">
            <p className="text-2xl font-bold text-blue-600">{customer.lifetime_points}</p>
            <p className="text-xs text-blue-700">Lifetime Earned</p>
          </div>
          <div className="bg-purple-50 rounded-lg p-4 text-center">
            <p className="text-2xl font-bold text-purple-600">{customer.lifetime_redeemed}</p>
            <p className="text-xs text-purple-700">Lifetime Redeemed</p>
          </div>
        </div>

        {/* Progress to next reward */}
        {progressReward && (
          <div className="mb-6">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>Progress to: {progressReward.name}</span>
              <span>{customer.points_balance}/{progressReward.points_required} pts</span>
            </div>
            <div className="h-2.5 bg-gray-100 rounded-full overflow-hidden">
              <div className="h-full bg-green-500 rounded-full transition-all" style={{ width: `${progressPct}%` }} />
            </div>
            {nextReward && customer.points_balance < nextReward.points_required && (
              <p className="text-xs text-gray-400 mt-1">{nextReward.points_required - customer.points_balance} more points to next reward</p>
            )}
          </div>
        )}

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2">
          <button onClick={() => onAward(customer)} className="flex items-center gap-1.5 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700"><Plus className="w-4 h-4" /> Award Points</button>
          <button onClick={() => onDeduct(customer)} className="flex items-center gap-1.5 px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200"><Minus className="w-4 h-4" /> Deduct Points</button>
          <button onClick={() => onRedeem(customer)} className="flex items-center gap-1.5 px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700"><Gift className="w-4 h-4" /> Redeem Reward</button>
        </div>
      </div>

      {/* Transaction History */}
      <div className="bg-white rounded-xl border border-gray-200">
        <div className="px-5 py-4 border-b border-gray-100">
          <h3 className="font-semibold text-gray-900">Points History</h3>
        </div>
        <div className="divide-y divide-gray-50 max-h-80 overflow-y-auto">
          {(customer.transactions || []).length === 0 ? (
            <p className="text-sm text-gray-400 p-5 text-center">No transactions yet</p>
          ) : (customer.transactions || []).map((tx) => (
            <div key={tx.id} className="px-5 py-3 flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-900">{tx.description}</p>
                <p className="text-xs text-gray-400">{new Date(tx.created_at).toLocaleString("en-US", { timeZone: "America/New_York" })}{tx.location_name ? ` • ${tx.location_name}` : ""}</p>
              </div>
              <span className={`text-sm font-semibold ${tx.points > 0 ? "text-green-600" : "text-red-500"}`}>
                {tx.points > 0 ? "+" : ""}{tx.points}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
