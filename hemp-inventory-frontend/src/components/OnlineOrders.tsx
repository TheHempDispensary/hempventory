import { useEffect, useState } from "react";
import { getOnlineOrders, updateOrderStatus } from "../lib/api";
import { RefreshCw, Search, Package, ChevronDown, ChevronUp, Truck, CheckCircle, XCircle, Clock, ShoppingCart } from "lucide-react";

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
  items: OrderItem[];
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

export default function OnlineOrders() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [expandedOrder, setExpandedOrder] = useState<number | null>(null);
  const [updatingStatus, setUpdatingStatus] = useState<number | null>(null);

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

            return (
              <div key={order.id} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
                {/* Order Header Row */}
                <button
                  onClick={() => setExpandedOrder(isExpanded ? null : order.id)}
                  className="w-full flex items-center justify-between p-4 hover:bg-gray-50 transition-colors text-left"
                >
                  <div className="flex items-center gap-4 flex-1 min-w-0">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-gray-900">{order.order_number}</span>
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusInfo.color}`}>
                          <StatusIcon className="w-3 h-3" />
                          {statusInfo.label}
                        </span>
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
