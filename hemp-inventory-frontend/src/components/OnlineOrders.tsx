import { useEffect, useState } from "react";
import { getOnlineOrders, updateOrderStatus, updateOrderNotes, createShipment, purchaseLabel, getShippingLabel } from "../lib/api";
import { MessageSquare, Save } from "lucide-react";
import { RefreshCw, Search, Package, ChevronDown, ChevronUp, Truck, CheckCircle, XCircle, Clock, ShoppingCart, Printer, Tag, ExternalLink, Loader2 } from "lucide-react";

interface OrderItem {
  product_id: string;
  product_name: string;
  sku: string;
  price: number;
  quantity: number;
}

interface Order {
  id: number;
  order_number: string;
  customer_first_name: string;
  customer_last_name: string;
  customer_email: string;
  customer_phone: string;
  shipping_address: string;
  shipping_apartment: string;
  shipping_city: string;
  shipping_state: string;
  shipping_zip: string;
  subtotal: number;
  shipping_cost: number;
  tax: number;
  total: number;
  notes: string;
  charge_id: string;
  payment_status: string;
  created_at: string;
  tracking_number?: string;
  tracking_url?: string;
  label_url?: string;
  staff_notes?: string;
  items: OrderItem[];
}

interface ShippingRate {
  id: string;
  provider: string;
  service_level: string;
  amount: string;
  currency: string;
  estimated_days: number | null;
  duration_terms: string;
}

const STATUS_OPTIONS = [
  { value: "paid", label: "Paid", color: "bg-green-100 text-green-800", icon: CheckCircle },
  { value: "processing", label: "Processing", color: "bg-blue-100 text-blue-800", icon: Package },
  { value: "shipped", label: "Shipped", color: "bg-purple-100 text-purple-800", icon: Truck },
  { value: "delivered", label: "Delivered", color: "bg-gray-100 text-gray-700", icon: CheckCircle },
  { value: "cancelled", label: "Cancelled", color: "bg-red-100 text-red-800", icon: XCircle },
  { value: "pending", label: "Pending", color: "bg-yellow-100 text-yellow-800", icon: Clock },
];

function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatDate(dateStr: string): string {
  if (!dateStr) return "N/A";
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function getStatusInfo(status: string) {
  return STATUS_OPTIONS.find((s) => s.value === status) || STATUS_OPTIONS[5];
}

function printOrder(order: Order) {
  const printWindow = window.open("", "_blank", "width=800,height=600");
  if (!printWindow) return;

  const itemRows = order.items
    .map(
      (item) => `
      <tr>
        <td style="padding:8px;border-bottom:1px solid #eee">${item.product_name}</td>
        <td style="padding:8px;border-bottom:1px solid #eee;text-align:center">${item.quantity}</td>
        <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">$${(item.price / 100).toFixed(2)}</td>
        <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">$${((item.price * item.quantity) / 100).toFixed(2)}</td>
      </tr>`
    )
    .join("");

  printWindow.document.write(`
    <html>
    <head><title>Order ${order.order_number}</title></head>
    <body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px">
      <div style="text-align:center;margin-bottom:20px">
        <h1 style="margin:0;font-size:24px">The Hemp Dispensary</h1>
        <p style="margin:4px 0;color:#666">Order Packing Slip</p>
      </div>
      <hr style="border:1px solid #ddd">
      <div style="display:flex;justify-content:space-between;margin:16px 0">
        <div>
          <strong>Order:</strong> ${order.order_number}<br>
          <strong>Date:</strong> ${formatDate(order.created_at)}<br>
          <strong>Status:</strong> ${order.payment_status.toUpperCase()}
        </div>
        <div style="text-align:right">
          <strong>Customer:</strong><br>
          ${order.customer_first_name} ${order.customer_last_name}<br>
          ${order.customer_email}<br>
          ${order.customer_phone || ""}
        </div>
      </div>
      <div style="background:#f9f9f9;padding:12px;border-radius:6px;margin-bottom:16px">
        <strong>Ship To:</strong><br>
        ${order.customer_first_name} ${order.customer_last_name}<br>
        ${order.shipping_address}${order.shipping_apartment ? ", " + order.shipping_apartment : ""}<br>
        ${order.shipping_city}, ${order.shipping_state} ${order.shipping_zip}
      </div>
      ${order.tracking_number ? "<p><strong>Tracking:</strong> " + order.tracking_number + "</p>" : ""}
      <table style="width:100%;border-collapse:collapse;margin-top:12px">
        <thead>
          <tr style="background:#f3f4f6">
            <th style="padding:8px;text-align:left">Product</th>
            <th style="padding:8px;text-align:center">Qty</th>
            <th style="padding:8px;text-align:right">Price</th>
            <th style="padding:8px;text-align:right">Total</th>
          </tr>
        </thead>
        <tbody>${itemRows}</tbody>
      </table>
      <div style="text-align:right;margin-top:12px">
        <p>Subtotal: $${(order.subtotal / 100).toFixed(2)}</p>
        <p>Shipping: ${order.shipping_cost === 0 ? "Free" : "$" + (order.shipping_cost / 100).toFixed(2)}</p>
        <p>Tax: $${(order.tax / 100).toFixed(2)}</p>
        <p style="font-size:18px"><strong>Total: $${(order.total / 100).toFixed(2)}</strong></p>
      </div>
      ${order.notes ? "<div style=\"margin-top:16px;padding:12px;background:#fffbeb;border-radius:6px\"><strong>Notes:</strong> " + order.notes + "</div>" : ""}
      <` + `script>window.print();</` + `script>
    </body>
    </html>
  `);
  printWindow.document.close();
}

export default function OnlineOrders() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [expandedOrder, setExpandedOrder] = useState<number | null>(null);
  const [updatingStatus, setUpdatingStatus] = useState<number | null>(null);

  // Shipping state
  const [shippingOrderId, setShippingOrderId] = useState<number | null>(null);
  const [rates, setRates] = useState<ShippingRate[]>([]);
  const [loadingRates, setLoadingRates] = useState(false);
  const [purchasingLabel, setPurchasingLabel] = useState(false);
  const [shippingError, setShippingError] = useState("");
  const [parcelWeight, setParcelWeight] = useState("1.0");
  const [parcelLength, setParcelLength] = useState("10");
  const [parcelWidth, setParcelWidth] = useState("8");
  const [parcelHeight, setParcelHeight] = useState("4");

  // Staff notes state
  const [editingNotes, setEditingNotes] = useState<number | null>(null);
  const [notesText, setNotesText] = useState("");
  const [savingNotes, setSavingNotes] = useState(false);

  const loadOrders = async () => {
    setLoading(true);
    try {
      const params: { limit: number; offset: number; status?: string } = { limit: 100, offset: 0 };
      if (statusFilter !== "all") params.status = statusFilter;
      const res = await getOnlineOrders(params);
      setOrders(res.data.orders || []);
      setTotal(res.data.total || 0);
    } catch (err) {
      console.error("Error loading orders:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadOrders();
  }, [statusFilter]);

  const handleStatusChange = async (orderId: number, newStatus: string) => {
    setUpdatingStatus(orderId);
    try {
      await updateOrderStatus(orderId, newStatus);
      setOrders((prev) =>
        prev.map((o) => (o.id === orderId ? { ...o, payment_status: newStatus } : o))
      );
    } catch (err) {
      console.error("Error updating status:", err);
    } finally {
      setUpdatingStatus(null);
    }
  };

  const handleGetRates = async (orderId: number) => {
    setShippingOrderId(orderId);
    setRates([]);
    setShippingError("");
    setLoadingRates(true);
    try {
      const res = await createShipment({
        order_id: orderId,
        parcel_weight: parseFloat(parcelWeight) || 1.0,
        parcel_length: parseFloat(parcelLength) || 10,
        parcel_width: parseFloat(parcelWidth) || 8,
        parcel_height: parseFloat(parcelHeight) || 4,
      });
      setRates(res.data.rates || []);
      if ((res.data.rates || []).length === 0) {
        setShippingError("No shipping rates available for this address.");
      }
    } catch (err: unknown) {
      const msg = (err && typeof err === "object" && "response" in err)
        ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || "Failed to get rates")
        : "Failed to get shipping rates";
      setShippingError(msg);
    } finally {
      setLoadingRates(false);
    }
  };

  const handlePurchaseLabel = async (rateId: string, orderId: number) => {
    setPurchasingLabel(true);
    setShippingError("");
    try {
      const res = await purchaseLabel({ rate_id: rateId, order_id: orderId });
      const { label_url, tracking_number, tracking_url } = res.data;
      setOrders((prev) =>
        prev.map((o) =>
          o.id === orderId
            ? { ...o, label_url, tracking_number, tracking_url, payment_status: "shipped" }
            : o
        )
      );
      setShippingOrderId(null);
      setRates([]);
      if (label_url) {
        window.open(label_url, "_blank");
      }
    } catch (err: unknown) {
      const msg = (err && typeof err === "object" && "response" in err)
        ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || "Failed to purchase label")
        : "Failed to purchase label";
      setShippingError(msg);
    } finally {
      setPurchasingLabel(false);
    }
  };

  const handleViewLabel = async (orderId: number) => {
    try {
      const res = await getShippingLabel(orderId);
      if (res.data.has_label && res.data.label_url) {
        window.open(res.data.label_url, "_blank");
      }
    } catch (err) {
      console.error("Error fetching label:", err);
    }
  };

  const handleSaveNotes = async (orderId: number) => {
    setSavingNotes(true);
    try {
      await updateOrderNotes(orderId, notesText);
      setOrders((prev) =>
        prev.map((o) => (o.id === orderId ? { ...o, staff_notes: notesText } : o))
      );
      setEditingNotes(null);
    } catch (err) {
      console.error("Error saving notes:", err);
    } finally {
      setSavingNotes(false);
    }
  };

  const filteredOrders = orders.filter((o) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      o.order_number.toLowerCase().includes(s) ||
      o.customer_first_name.toLowerCase().includes(s) ||
      o.customer_last_name.toLowerCase().includes(s) ||
      o.customer_email.toLowerCase().includes(s) ||
      o.items.some((item) => item.product_name.toLowerCase().includes(s))
    );
  });

  const stats = {
    total: orders.length,
    paid: orders.filter((o) => o.payment_status === "paid").length,
    processing: orders.filter((o) => o.payment_status === "processing").length,
    shipped: orders.filter((o) => o.payment_status === "shipped").length,
    delivered: orders.filter((o) => o.payment_status === "delivered").length,
  };

  const totalRevenue = orders.reduce((sum, o) => sum + (o.payment_status !== "cancelled" ? o.total : 0), 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <ShoppingCart className="w-7 h-7 text-green-600" />
            Online Orders
          </h2>
          <p className="text-sm text-gray-500 mt-1">{total} total orders</p>
        </div>
        <button
          onClick={loadOrders}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <p className="text-xs text-gray-500 uppercase">Revenue</p>
          <p className="text-xl font-bold text-green-600">{formatPrice(totalRevenue)}</p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <p className="text-xs text-gray-500 uppercase">New / Paid</p>
          <p className="text-xl font-bold text-green-700">{stats.paid}</p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <p className="text-xs text-gray-500 uppercase">Processing</p>
          <p className="text-xl font-bold text-blue-600">{stats.processing}</p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <p className="text-xs text-gray-500 uppercase">Shipped</p>
          <p className="text-xl font-bold text-purple-600">{stats.shipped}</p>
        </div>
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <p className="text-xs text-gray-500 uppercase">Delivered</p>
          <p className="text-xl font-bold text-gray-700">{stats.delivered}</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search orders, customers, products..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
        >
          <option value="all">All Statuses</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </div>

      {/* Orders List */}
      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading orders...</div>
      ) : filteredOrders.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
          <ShoppingCart className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500">No orders found</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredOrders.map((order) => {
            const statusInfo = getStatusInfo(order.payment_status);
            const StatusIcon = statusInfo.icon;
            const isExpanded = expandedOrder === order.id;
            const isShippingOpen = shippingOrderId === order.id;

            return (
              <div key={order.id} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
                {/* Order Header Row */}
                <button
                  onClick={() => setExpandedOrder(isExpanded ? null : order.id)}
                  className="w-full flex items-center justify-between p-4 hover:bg-gray-50 transition-colors text-left"
                >
                  <div className="flex items-center gap-4 flex-1 min-w-0">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-gray-900">{order.order_number}</span>
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusInfo.color}`}>
                          <StatusIcon className="w-3 h-3" />
                          {statusInfo.label}
                        </span>
                        {order.tracking_number && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-800">
                            <Truck className="w-3 h-3" />
                            {order.tracking_number}
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-gray-500 truncate">
                        {order.customer_first_name} {order.customer_last_name} &middot; {order.customer_email}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 flex-shrink-0">
                    <div className="text-right">
                      <p className="font-semibold text-gray-900">{formatPrice(order.total)}</p>
                      <p className="text-xs text-gray-500">{order.items.length} item{order.items.length !== 1 ? "s" : ""}</p>
                    </div>
                    {isExpanded ? <ChevronUp className="w-5 h-5 text-gray-400" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
                  </div>
                </button>

                {/* Expanded Detail */}
                {isExpanded && (
                  <div className="border-t border-gray-200 p-4 bg-gray-50">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                      {/* Customer Info */}
                      <div>
                        <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Customer</h4>
                        <p className="text-sm font-medium text-gray-900">{order.customer_first_name} {order.customer_last_name}</p>
                        <p className="text-sm text-gray-600">{order.customer_email}</p>
                        <p className="text-sm text-gray-600">{order.customer_phone}</p>
                      </div>

                      {/* Shipping */}
                      <div>
                        <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Ship To</h4>
                        <p className="text-sm text-gray-900">{order.shipping_address}</p>
                        {order.shipping_apartment && <p className="text-sm text-gray-600">{order.shipping_apartment}</p>}
                        <p className="text-sm text-gray-600">
                          {order.shipping_city}, {order.shipping_state} {order.shipping_zip}
                        </p>
                      </div>

                      {/* Order Info */}
                      <div>
                        <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Order Info</h4>
                        <p className="text-sm text-gray-600">Placed: {formatDate(order.created_at)}</p>
                        <p className="text-sm text-gray-600">Charge: <span className="font-mono text-xs">{order.charge_id}</span></p>
                        {order.notes && <p className="text-sm text-gray-600 mt-1">Notes: {order.notes}</p>}
                        {order.tracking_number && (
                          <div className="mt-2">
                            <p className="text-sm text-gray-600">
                              Tracking: <span className="font-mono text-xs font-medium">{order.tracking_number}</span>
                            </p>
                            {order.tracking_url && (
                              <a href={order.tracking_url} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-600 hover:underline flex items-center gap-1 mt-1">
                                <ExternalLink className="w-3 h-3" /> Track Package
                              </a>
                            )}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Items Table */}
                    <div className="mt-4">
                      <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Items Ordered</h4>
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left border-b border-gray-200">
                            <th className="pb-2 font-medium text-gray-700">Product</th>
                            <th className="pb-2 font-medium text-gray-700 text-center">Qty</th>
                            <th className="pb-2 font-medium text-gray-700 text-right">Price</th>
                            <th className="pb-2 font-medium text-gray-700 text-right">Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {order.items.map((item, idx) => (
                            <tr key={idx} className="border-b border-gray-100">
                              <td className="py-2 text-gray-900">{item.product_name}</td>
                              <td className="py-2 text-center text-gray-600">{item.quantity}</td>
                              <td className="py-2 text-right text-gray-600">{formatPrice(item.price)}</td>
                              <td className="py-2 text-right text-gray-900">{formatPrice(item.price * item.quantity)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>

                      {/* Totals */}
                      <div className="mt-3 pt-3 border-t border-gray-200 space-y-1 text-sm text-right">
                        <p className="text-gray-600">Subtotal: {formatPrice(order.subtotal)}</p>
                        <p className="text-gray-600">Shipping: {order.shipping_cost === 0 ? "Free" : formatPrice(order.shipping_cost)}</p>
                        <p className="text-gray-600">Tax: {formatPrice(order.tax)}</p>
                        <p className="font-bold text-gray-900 text-base">Total: {formatPrice(order.total)}</p>
                      </div>
                    </div>

                    {/* Staff Notes */}
                    <div className="mt-4 pt-4 border-t border-gray-200">
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="text-xs font-semibold text-gray-500 uppercase flex items-center gap-1">
                          <MessageSquare className="w-3.5 h-3.5" />
                          Staff Notes
                        </h4>
                        {editingNotes !== order.id && (
                          <button
                            onClick={() => {
                              setEditingNotes(order.id);
                              setNotesText(order.staff_notes || "");
                            }}
                            className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                          >
                            {order.staff_notes ? "Edit" : "Add Note"}
                          </button>
                        )}
                      </div>
                      {editingNotes === order.id ? (
                        <div className="space-y-2">
                          <textarea
                            value={notesText}
                            onChange={(e) => setNotesText(e.target.value)}
                            placeholder="Add internal notes for staff (packing instructions, special requests, etc.)..."
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 resize-y"
                            rows={3}
                          />
                          <div className="flex gap-2">
                            <button
                              onClick={() => handleSaveNotes(order.id)}
                              disabled={savingNotes}
                              className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors text-sm font-medium"
                            >
                              <Save className="w-3.5 h-3.5" />
                              {savingNotes ? "Saving..." : "Save Note"}
                            </button>
                            <button
                              onClick={() => setEditingNotes(null)}
                              className="px-3 py-1.5 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : order.staff_notes ? (
                        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-sm text-gray-800 whitespace-pre-wrap">
                          {order.staff_notes}
                        </div>
                      ) : (
                        <p className="text-sm text-gray-400 italic">No staff notes yet</p>
                      )}
                    </div>

                    {/* Action Buttons */}
                    <div className="mt-4 pt-4 border-t border-gray-200 flex flex-wrap items-center gap-3">
                      <button
                        onClick={() => printOrder(order)}
                        className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
                      >
                        <Printer className="w-4 h-4" />
                        Print Order
                      </button>

                      {order.label_url ? (
                        <button
                          onClick={() => handleViewLabel(order.id)}
                          className="flex items-center gap-2 px-4 py-2 bg-indigo-100 text-indigo-700 rounded-lg hover:bg-indigo-200 transition-colors text-sm font-medium"
                        >
                          <Tag className="w-4 h-4" />
                          View / Print Label
                        </button>
                      ) : (
                        <button
                          onClick={() => {
                            if (isShippingOpen) {
                              setShippingOrderId(null);
                              setRates([]);
                            } else {
                              setShippingOrderId(order.id);
                              setRates([]);
                              setShippingError("");
                            }
                          }}
                          className="flex items-center gap-2 px-4 py-2 bg-green-100 text-green-700 rounded-lg hover:bg-green-200 transition-colors text-sm font-medium"
                        >
                          <Tag className="w-4 h-4" />
                          {isShippingOpen ? "Cancel" : "Create Shipping Label"}
                        </button>
                      )}
                    </div>

                    {/* Shipping Label Creation Panel */}
                    {isShippingOpen && !order.label_url && (
                      <div className="mt-4 p-4 bg-white rounded-lg border border-green-200">
                        <h4 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
                          <Truck className="w-4 h-4 text-green-600" />
                          Create Shipping Label via Shippo
                        </h4>

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">Weight (lb)</label>
                            <input type="number" step="0.1" value={parcelWeight} onChange={(e) => setParcelWeight(e.target.value)}
                              className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500" />
                          </div>
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">Length (in)</label>
                            <input type="number" step="0.5" value={parcelLength} onChange={(e) => setParcelLength(e.target.value)}
                              className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500" />
                          </div>
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">Width (in)</label>
                            <input type="number" step="0.5" value={parcelWidth} onChange={(e) => setParcelWidth(e.target.value)}
                              className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500" />
                          </div>
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">Height (in)</label>
                            <input type="number" step="0.5" value={parcelHeight} onChange={(e) => setParcelHeight(e.target.value)}
                              className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500" />
                          </div>
                        </div>

                        <button
                          onClick={() => handleGetRates(order.id)}
                          disabled={loadingRates}
                          className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors text-sm font-medium mb-3"
                        >
                          {loadingRates ? <Loader2 className="w-4 h-4 animate-spin" /> : <Truck className="w-4 h-4" />}
                          {loadingRates ? "Getting Rates..." : "Get Shipping Rates"}
                        </button>

                        {shippingError && (
                          <div className="text-sm text-red-600 bg-red-50 p-3 rounded-lg mb-3">{shippingError}</div>
                        )}

                        {rates.length > 0 && (
                          <div className="space-y-2">
                            <p className="text-xs font-semibold text-gray-500 uppercase">Select a Rate</p>
                            {rates.map((rate) => (
                              <div key={rate.id} className="flex items-center justify-between p-3 border border-gray-200 rounded-lg hover:border-green-300 transition-colors">
                                <div>
                                  <p className="text-sm font-medium text-gray-900">{rate.provider} &mdash; {rate.service_level}</p>
                                  <p className="text-xs text-gray-500">
                                    {rate.estimated_days ? `Est. ${rate.estimated_days} day${rate.estimated_days !== 1 ? "s" : ""}` : rate.duration_terms || ""}
                                  </p>
                                </div>
                                <div className="flex items-center gap-3">
                                  <span className="text-lg font-bold text-gray-900">${rate.amount}</span>
                                  <button
                                    onClick={() => handlePurchaseLabel(rate.id, order.id)}
                                    disabled={purchasingLabel}
                                    className="px-4 py-1.5 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors text-sm font-medium"
                                  >
                                    {purchasingLabel ? "Purchasing..." : "Buy Label"}
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Status Update */}
                    <div className="mt-4 pt-4 border-t border-gray-200 flex items-center gap-3">
                      <span className="text-sm font-medium text-gray-700">Update Status:</span>
                      <div className="flex flex-wrap gap-2">
                        {STATUS_OPTIONS.filter((s) => s.value !== order.payment_status).map((s) => (
                          <button
                            key={s.value}
                            onClick={() => handleStatusChange(order.id, s.value)}
                            disabled={updatingStatus === order.id}
                            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors hover:opacity-80 disabled:opacity-50 ${s.color}`}
                          >
                            {updatingStatus === order.id ? "..." : s.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
