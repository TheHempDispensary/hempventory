import { useEffect, useState } from "react";
import { getOnlineOrders, updateOrderStatus, updateOrderNotes, updateOrderCustomer, createShipment, purchaseLabel, getShippingLabel, refundOrder, resendOrderConfirmation, convertToShipping } from "../lib/api";
import { MessageSquare, Save, Edit2, X } from "lucide-react";
import { RefreshCw, Search, Package, ChevronDown, ChevronUp, Truck, CheckCircle, XCircle, Clock, ShoppingCart, Printer, Tag, ExternalLink, Loader2, RotateCcw, AlertTriangle, DollarSign, Mail, MapPin } from "lucide-react";

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
  discount: number;
  promo_code: string;
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
  shipping_service?: string;
  fulfillment_type?: string;
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
  // SQLite CURRENT_TIMESTAMP stores UTC without timezone indicator.
  // Normalize to ISO 8601 with Z suffix so JS treats it as UTC before converting to EST.
  let normalized = dateStr.replace(" ", "T");
  if (!normalized.endsWith("Z") && !normalized.includes("+") && !/\d{2}:\d{2}:\d{2}-/.test(normalized)) {
    normalized += "Z";
  }
  const d = new Date(normalized);
  return d.toLocaleDateString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function getStatusInfo(status: string) {
  return STATUS_OPTIONS.find((s) => s.value === status) || STATUS_OPTIONS[5];
}

const HAZMAT_KEYWORDS = ["vape", "cartridge", "cart ", "disposable", "battery", "510 ", "pod"];

function orderContainsHazmat(items: OrderItem[]): boolean {
  return items.some((item) => {
    const name = item.product_name.toLowerCase();
    return HAZMAT_KEYWORDS.some((kw) => name.includes(kw));
  });
}

function printMultipleOrders(orders: Order[]) {
  if (orders.length === 0) return;
  const printWindow = window.open("", "_blank", "width=800,height=600");
  if (!printWindow) return;

  const pages = orders.map((order) => {
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

    return `
      <div style="page-break-after:always;max-width:700px;margin:0 auto;padding:20px">
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
          ${order.fulfillment_type && order.fulfillment_type.startsWith("pickup")
            ? `<strong>Pickup Location:</strong><br>${order.fulfillment_type === "pickup_west" ? "The Hemp Dispensary \u2014 West<br>6175 Deltona Blvd, Suite 104<br>Spring Hill, FL 34606" : "The Hemp Dispensary \u2014 East<br>14312 Spring Hill Dr<br>Spring Hill, FL 34609"}`
            : `<strong>Ship To:</strong><br>${order.customer_first_name} ${order.customer_last_name}<br>${order.shipping_address}${order.shipping_apartment ? ", " + order.shipping_apartment : ""}<br>${order.shipping_city}, ${order.shipping_state} ${order.shipping_zip}`
          }
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
          ${order.discount ? `<p style="color:#059669">Discount (${order.promo_code || 'Promo'}): -$${(order.discount / 100).toFixed(2)}</p>` : ''}
          <p>Shipping: ${order.shipping_cost === 0 ? "Free" : "$" + (order.shipping_cost / 100).toFixed(2)}</p>
          <p>Tax: $${(order.tax / 100).toFixed(2)}</p>
          <p style="font-size:18px"><strong>Total: $${(order.total / 100).toFixed(2)}</strong></p>
        </div>
        ${order.notes ? "<div style=\"margin-top:16px;padding:12px;background:#fffbeb;border-radius:6px\"><strong>Notes:</strong> " + order.notes + "</div>" : ""}
      </div>`;
  }).join("");

  printWindow.document.write(`
    <html>
    <head><title>Orders - The Hemp Dispensary</title></head>
    <body style="font-family:Arial,sans-serif;margin:0;padding:0">
      ${pages}
      <` + `script>window.print();</` + `script>
    </body>
    </html>
  `);
  printWindow.document.close();
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
        ${order.fulfillment_type && order.fulfillment_type.startsWith("pickup")
          ? `<strong>Pickup Location:</strong><br>${order.fulfillment_type === "pickup_west" ? "The Hemp Dispensary \u2014 West<br>6175 Deltona Blvd, Suite 104<br>Spring Hill, FL 34606" : "The Hemp Dispensary \u2014 East<br>14312 Spring Hill Dr<br>Spring Hill, FL 34609"}`
          : `<strong>Ship To:</strong><br>${order.customer_first_name} ${order.customer_last_name}<br>${order.shipping_address}${order.shipping_apartment ? ", " + order.shipping_apartment : ""}<br>${order.shipping_city}, ${order.shipping_state} ${order.shipping_zip}`
        }
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
        ${order.discount ? `<p style="color:#059669">Discount (${order.promo_code || 'Promo'}): -$${(order.discount / 100).toFixed(2)}</p>` : ''}
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
  const [selectedOrders, setSelectedOrders] = useState<Set<number>>(new Set());

  // Shipping state
  const [shippingOrderId, setShippingOrderId] = useState<number | null>(null);
  const [rates, setRates] = useState<ShippingRate[]>([]);
  const [loadingRates, setLoadingRates] = useState(false);
  const [purchasingLabel, setPurchasingLabel] = useState(false);
  const [shippingError, setShippingError] = useState("");
  const [parcelWeight, setParcelWeight] = useState("0.375");
  const [weightUnit, setWeightUnit] = useState<"lb" | "oz">("lb");
  const [parcelLength, setParcelLength] = useState("10");
  const [parcelWidth, setParcelWidth] = useState("8");
  const [parcelHeight, setParcelHeight] = useState("2");
  const [isHazmat, setIsHazmat] = useState(false);

  // Staff notes state
  const [editingNotes, setEditingNotes] = useState<number | null>(null);
  const [notesText, setNotesText] = useState("");
  const [savingNotes, setSavingNotes] = useState(false);

  // Resend confirmation state
  const [resendingOrderId, setResendingOrderId] = useState<number | null>(null);
  const [resendSuccess, setResendSuccess] = useState("");
  const [resendError, setResendError] = useState("");

  // Refund state
  const [refundingOrderId, setRefundingOrderId] = useState<number | null>(null);
  const [refundConfirm, setRefundConfirm] = useState(false);
  const [processingRefund, setProcessingRefund] = useState(false);
  const [refundError, setRefundError] = useState("");
  const [refundSuccess, setRefundSuccess] = useState("");
  const [refundType, setRefundType] = useState<"full" | "partial">("full");
  const [selectedRefundItems, setSelectedRefundItems] = useState<Record<string, boolean>>({});

  // Edit customer details state
  const [editingCustomer, setEditingCustomer] = useState<number | null>(null);
  const [savingCustomer, setSavingCustomer] = useState(false);
  const [editCustomer, setEditCustomer] = useState({
    customer_first_name: "",
    customer_last_name: "",
    customer_email: "",
    customer_phone: "",
    shipping_address: "",
    shipping_apartment: "",
    shipping_city: "",
    shipping_state: "",
    shipping_zip: "",
  });

  // Convert pickup to shipping state
  const [convertingOrderId, setConvertingOrderId] = useState<number | null>(null);
  const [convertingInProgress, setConvertingInProgress] = useState(false);
  const [convertError, setConvertError] = useState("");
  const [convertAddress, setConvertAddress] = useState({
    shipping_address: "",
    shipping_apartment: "",
    shipping_city: "",
    shipping_state: "",
    shipping_zip: "",
  });

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
        parcel_weight: Math.round((weightUnit === "oz" ? (parseFloat(parcelWeight) || 0.375) / 16 : parseFloat(parcelWeight) || 0.375) * 10000) / 10000,
        parcel_length: parseFloat(parcelLength) || 10,
        parcel_width: parseFloat(parcelWidth) || 8,
        parcel_height: parseFloat(parcelHeight) || 2,
        is_hazmat: isHazmat,
      });
      const fetchedRates = res.data.rates || [];
      setRates(fetchedRates);
      if (fetchedRates.length === 0) {
        setShippingError("No shipping rates available for this address.");
      } else {
        // Auto-select and purchase the rate matching what the customer chose at checkout
        const orderObj = orders.find(o => o.id === orderId);
        if (orderObj?.shipping_service) {
          const match = fetchedRates.find((r: ShippingRate) => r.service_level === orderObj.shipping_service);
          if (match) {
            handlePurchaseLabel(match.id, orderId);
            return;
          }
        }
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

  const getSelectedItemsForRefund = (order: Order): OrderItem[] => {
    return order.items.filter((item, idx) => selectedRefundItems[`${item.product_id}_${idx}`]);
  };

  const calcItemRefundSummary = (order: Order) => {
    const items = getSelectedItemsForRefund(order);
    const itemsSubtotal = items.reduce((sum, item) => sum + item.price * item.quantity, 0);
    const taxProportion = order.subtotal > 0 ? itemsSubtotal / order.subtotal : 0;
    const itemsTax = Math.round(order.tax * taxProportion);
    return { items, itemsSubtotal, itemsTax, total: itemsSubtotal + itemsTax };
  };

  const handleRefund = async (order: Order, amount?: number) => {
    setProcessingRefund(true);
    setRefundError("");
    setRefundSuccess("");
    try {
      let refundAmountCents: number;
      let refundData: { amount?: number; refunded_items?: { product_id: string; product_name: string; sku: string; price: number; quantity: number }[] } = {};

      if (refundType === "partial" && Object.values(selectedRefundItems).some(Boolean)) {
        // Item-level partial refund
        const summary = calcItemRefundSummary(order);
        refundAmountCents = summary.total;
        refundData = {
          refunded_items: summary.items.map(item => ({
            product_id: item.product_id,
            product_name: item.product_name,
            sku: item.sku,
            price: item.price,
            quantity: item.quantity,
          })),
        };
      } else if (amount) {
        refundAmountCents = amount;
        refundData = { amount };
      } else {
        refundAmountCents = order.total;
      }

      await refundOrder(order.id, refundData);
      const allSelected = order.items.every((item, idx) => selectedRefundItems[`${item.product_id}_${idx}`]);
      const isFullRefund = refundType === "full" || allSelected;
      setOrders((prev) =>
        prev.map((o) =>
          o.id === order.id ? { ...o, payment_status: isFullRefund ? "refunded" : "partially_refunded" } : o
        )
      );
      const restockMsg = refundData.refunded_items ? ` (${refundData.refunded_items.length} item(s) restocked to inventory)` : "";
      setRefundSuccess(`Refund of ${formatPrice(refundAmountCents)} processed successfully.${restockMsg}`);
      setRefundConfirm(false);
      setTimeout(() => {
        setRefundingOrderId(null);
        setRefundSuccess("");
        setRefundType("full");
        setSelectedRefundItems({});
      }, 4000);
    } catch (err: unknown) {
      const msg = (err && typeof err === "object" && "response" in err)
        ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || "Refund failed")
        : "Failed to process refund";
      setRefundError(msg);
      setRefundConfirm(false);
    } finally {
      setProcessingRefund(false);
    }
  };

  const handleResendConfirmation = async (order: Order) => {
    setResendingOrderId(order.id);
    setResendSuccess("");
    setResendError("");
    try {
      await resendOrderConfirmation(order.id);
      setResendSuccess(`Confirmation email sent to ${order.customer_email}`);
      setTimeout(() => {
        setResendingOrderId(null);
        setResendSuccess("");
      }, 3000);
    } catch (err: unknown) {
      const msg = (err && typeof err === "object" && "response" in err)
        ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || "Failed to send email")
        : "Failed to send confirmation email";
      setResendError(msg);
      setTimeout(() => {
        setResendingOrderId(null);
        setResendError("");
      }, 5000);
    }
  };

  const handleStartEditCustomer = (order: Order) => {
    setEditingCustomer(order.id);
    setEditCustomer({
      customer_first_name: order.customer_first_name || "",
      customer_last_name: order.customer_last_name || "",
      customer_email: order.customer_email || "",
      customer_phone: order.customer_phone || "",
      shipping_address: order.shipping_address || "",
      shipping_apartment: order.shipping_apartment || "",
      shipping_city: order.shipping_city || "",
      shipping_state: order.shipping_state || "",
      shipping_zip: order.shipping_zip || "",
    });
  };

  const handleSaveCustomer = async (orderId: number) => {
    setSavingCustomer(true);
    try {
      const res = await updateOrderCustomer(orderId, editCustomer);
      setOrders((prev) =>
        prev.map((o) => (o.id === orderId ? { ...o, ...res.data } : o))
      );
      setEditingCustomer(null);
    } catch (err) {
      console.error("Error saving customer details:", err);
    } finally {
      setSavingCustomer(false);
    }
  };

  const handleConvertToShipping = async (orderId: number) => {
    if (!convertAddress.shipping_address || !convertAddress.shipping_city || !convertAddress.shipping_state || !convertAddress.shipping_zip) {
      setConvertError("Address, city, state, and zip are required");
      return;
    }
    setConvertingInProgress(true);
    setConvertError("");
    try {
      const res = await convertToShipping(orderId, convertAddress);
      setOrders((prev) =>
        prev.map((o) =>
          o.id === orderId
            ? {
                ...o,
                fulfillment_type: res.data.fulfillment_type,
                shipping_address: res.data.shipping_address,
                shipping_apartment: res.data.shipping_apartment,
                shipping_city: res.data.shipping_city,
                shipping_state: res.data.shipping_state,
                shipping_zip: res.data.shipping_zip,
              }
            : o
        )
      );
      setConvertingOrderId(null);
      setConvertAddress({ shipping_address: "", shipping_apartment: "", shipping_city: "", shipping_state: "", shipping_zip: "" });
    } catch (err: unknown) {
      const msg = (err && typeof err === "object" && "response" in err)
        ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || "Failed to convert order")
        : "Failed to convert to shipping";
      setConvertError(msg);
    } finally {
      setConvertingInProgress(false);
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

  const toggleSelectOrder = (orderId: number) => {
    setSelectedOrders((prev) => {
      const next = new Set(prev);
      if (next.has(orderId)) next.delete(orderId);
      else next.add(orderId);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedOrders.size === filteredOrders.length) {
      setSelectedOrders(new Set());
    } else {
      setSelectedOrders(new Set(filteredOrders.map((o) => o.id)));
    }
  };

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
        <div className="flex items-center gap-2">
          {selectedOrders.size > 0 && (
            <button
              onClick={() => {
                const ordersToPrint = filteredOrders.filter((o) => selectedOrders.has(o.id));
                printMultipleOrders(ordersToPrint);
              }}
              className="flex items-center gap-2 px-4 py-2 bg-gray-700 text-white rounded-lg hover:bg-gray-800 transition-colors"
            >
              <Printer className="w-4 h-4" />
              Print Selected ({selectedOrders.size})
            </button>
          )}
          <button
            onClick={loadOrders}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
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
          {/* Select All */}
          <div className="flex items-center gap-2 px-2">
            <input
              type="checkbox"
              checked={filteredOrders.length > 0 && selectedOrders.size === filteredOrders.length}
              onChange={toggleSelectAll}
              className="w-4 h-4 rounded border-gray-300 text-green-600 focus:ring-green-500 cursor-pointer"
            />
            <span className="text-sm text-gray-500">Select All ({filteredOrders.length})</span>
          </div>
          {filteredOrders.map((order) => {
            const statusInfo = getStatusInfo(order.payment_status);
            const StatusIcon = statusInfo.icon;
            const isExpanded = expandedOrder === order.id;
            const isShippingOpen = shippingOrderId === order.id;

            return (
              <div key={order.id} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
                {/* Order Header Row */}
                <div className="flex items-center">
                  <div className="pl-4 pr-1 py-4 flex items-center" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedOrders.has(order.id)}
                      onChange={() => toggleSelectOrder(order.id)}
                      className="w-4 h-4 rounded border-gray-300 text-green-600 focus:ring-green-500 cursor-pointer"
                    />
                  </div>
                <button
                  onClick={() => setExpandedOrder(isExpanded ? null : order.id)}
                  className="w-full flex items-center justify-between p-4 pl-2 hover:bg-gray-50 transition-colors text-left"
                >
                  <div className="flex items-center gap-4 flex-1 min-w-0">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-gray-900">{order.order_number}</span>
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusInfo.color}`}>
                          <StatusIcon className="w-3 h-3" />
                          {statusInfo.label}
                        </span>
                        {order.fulfillment_type && order.fulfillment_type.startsWith("pickup") ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
                            <MapPin className="w-3 h-3" />
                            {order.fulfillment_type === "pickup_west" ? "Pickup — West" : "Pickup — East"}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                            <Truck className="w-3 h-3" />
                            {order.shipping_service || "Shipping"}
                          </span>
                        )}
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
                      <p className="text-xs text-gray-400 mt-0.5">{formatDate(order.created_at)}</p>
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
                </div>

                {/* Expanded Detail */}
                {isExpanded && (
                  <div className="border-t border-gray-200 p-4 bg-gray-50">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                      {/* Customer Info */}
                      <div>
                        <div className="flex items-center justify-between mb-2">
                          <h4 className="text-xs font-semibold text-gray-500 uppercase">Customer</h4>
                          {editingCustomer !== order.id && (
                            <button onClick={() => handleStartEditCustomer(order)} className="text-xs text-blue-600 hover:text-blue-800 font-medium flex items-center gap-1">
                              <Edit2 className="w-3 h-3" /> Edit
                            </button>
                          )}
                        </div>
                        {editingCustomer === order.id ? (
                          <div className="space-y-2">
                            <div className="grid grid-cols-2 gap-2">
                              <input type="text" value={editCustomer.customer_first_name} onChange={(e) => setEditCustomer({ ...editCustomer, customer_first_name: e.target.value })} placeholder="First Name" className="px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
                              <input type="text" value={editCustomer.customer_last_name} onChange={(e) => setEditCustomer({ ...editCustomer, customer_last_name: e.target.value })} placeholder="Last Name" className="px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
                            </div>
                            <input type="email" value={editCustomer.customer_email} onChange={(e) => setEditCustomer({ ...editCustomer, customer_email: e.target.value })} placeholder="Email" className="w-full px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
                            <input type="tel" value={editCustomer.customer_phone} onChange={(e) => setEditCustomer({ ...editCustomer, customer_phone: e.target.value })} placeholder="Phone" className="w-full px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
                            <input type="text" value={editCustomer.shipping_address} onChange={(e) => setEditCustomer({ ...editCustomer, shipping_address: e.target.value })} placeholder="Street Address" className="w-full px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
                            <input type="text" value={editCustomer.shipping_apartment} onChange={(e) => setEditCustomer({ ...editCustomer, shipping_apartment: e.target.value })} placeholder="Apt / Suite" className="w-full px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
                            <div className="grid grid-cols-3 gap-2">
                              <input type="text" value={editCustomer.shipping_city} onChange={(e) => setEditCustomer({ ...editCustomer, shipping_city: e.target.value })} placeholder="City" className="px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
                              <input type="text" value={editCustomer.shipping_state} onChange={(e) => setEditCustomer({ ...editCustomer, shipping_state: e.target.value })} placeholder="State" className="px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" maxLength={2} />
                              <input type="text" value={editCustomer.shipping_zip} onChange={(e) => setEditCustomer({ ...editCustomer, shipping_zip: e.target.value })} placeholder="ZIP" className="px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500" />
                            </div>
                            <div className="flex gap-2 pt-1">
                              <button onClick={() => handleSaveCustomer(order.id)} disabled={savingCustomer} className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors text-sm font-medium">
                                <Save className="w-3.5 h-3.5" />{savingCustomer ? "Saving..." : "Save"}
                              </button>
                              <button onClick={() => setEditingCustomer(null)} className="px-3 py-1.5 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium flex items-center gap-1">
                                <X className="w-3.5 h-3.5" />Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <p className="text-sm font-medium text-gray-900">{order.customer_first_name} {order.customer_last_name}</p>
                            <p className="text-sm text-gray-600">{order.customer_email}</p>
                            <p className="text-sm text-gray-600">{order.customer_phone}</p>
                          </>
                        )}
                      </div>

                      {/* Shipping / Pickup */}
                      <div>
                        {order.fulfillment_type && order.fulfillment_type.startsWith("pickup") ? (
                          <>
                            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2 flex items-center gap-1"><MapPin className="w-3.5 h-3.5" /> Pickup Location</h4>
                            <p className="text-sm font-medium text-gray-900">{order.fulfillment_type === "pickup_west" ? "West Location" : "East Location"}</p>
                            <p className="text-sm text-gray-600">{order.fulfillment_type === "pickup_west" ? "6175 Deltona Blvd, Suite 104" : "14312 Spring Hill Dr"}</p>
                            <p className="text-sm text-gray-600">Spring Hill, FL {order.fulfillment_type === "pickup_west" ? "34606" : "34609"}</p>
                          </>
                        ) : (
                          <>
                            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Ship To</h4>
                            <p className="text-sm text-gray-900">{order.shipping_address}</p>
                            {order.shipping_apartment && <p className="text-sm text-gray-600">{order.shipping_apartment}</p>}
                            <p className="text-sm text-gray-600">
                              {order.shipping_city}, {order.shipping_state} {order.shipping_zip}
                            </p>
                            {order.shipping_service && (
                              <p className="text-sm text-blue-700 font-medium mt-1 flex items-center gap-1">
                                <Truck className="w-3.5 h-3.5" /> {order.shipping_service}
                              </p>
                            )}
                          </>
                        )}
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
                        {order.discount > 0 && (
                          <p className="text-green-600">Discount ({order.promo_code || 'Promo'}): -{formatPrice(order.discount)}</p>
                        )}
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

                      <button
                        onClick={() => handleResendConfirmation(order)}
                        disabled={resendingOrderId === order.id}
                        className="flex items-center gap-2 px-4 py-2 bg-blue-100 text-blue-700 rounded-lg hover:bg-blue-200 disabled:opacity-50 transition-colors text-sm font-medium"
                      >
                        {resendingOrderId === order.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mail className="w-4 h-4" />}
                        {resendingOrderId === order.id ? "Sending..." : "Resend Confirmation"}
                      </button>

                      {order.payment_status !== "refunded" && order.payment_status !== "cancelled" && order.charge_id && (
                        <button
                          onClick={() => {
                            setRefundingOrderId(order.id);
                            setRefundConfirm(false);
                            setRefundError("");
                            setRefundSuccess("");
                          }}
                          className="flex items-center gap-2 px-4 py-2 bg-red-100 text-red-700 rounded-lg hover:bg-red-200 transition-colors text-sm font-medium"
                        >
                          <RotateCcw className="w-4 h-4" />
                          Refund Order
                        </button>
                      )}
                      {order.payment_status === "refunded" && (
                        <span className="flex items-center gap-2 px-4 py-2 bg-red-50 text-red-600 rounded-lg text-sm font-medium">
                          <RotateCcw className="w-4 h-4" />
                          Refunded
                        </span>
                      )}

                      {order.fulfillment_type && order.fulfillment_type.startsWith("pickup") && !order.label_url && (
                        <button
                          onClick={() => {
                            if (convertingOrderId === order.id) {
                              setConvertingOrderId(null);
                              setConvertError("");
                            } else {
                              setConvertingOrderId(order.id);
                              setConvertError("");
                              setConvertAddress({ shipping_address: "", shipping_apartment: "", shipping_city: "", shipping_state: "", shipping_zip: "" });
                            }
                          }}
                          className="flex items-center gap-2 px-4 py-2 bg-amber-100 text-amber-700 rounded-lg hover:bg-amber-200 transition-colors text-sm font-medium"
                        >
                          <Truck className="w-4 h-4" />
                          {convertingOrderId === order.id ? "Cancel" : "Convert to Shipping"}
                        </button>
                      )}

                      {!(order.fulfillment_type && order.fulfillment_type.startsWith("pickup")) && (
                        order.label_url ? (
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
                                setIsHazmat(orderContainsHazmat(order.items));
                              }
                            }}
                            className="flex items-center gap-2 px-4 py-2 bg-green-100 text-green-700 rounded-lg hover:bg-green-200 transition-colors text-sm font-medium"
                          >
                            <Tag className="w-4 h-4" />
                            {isShippingOpen ? "Cancel" : "Create Shipping Label"}
                          </button>
                        )
                      )}
                    </div>

                    {/* Convert to Shipping Panel */}
                    {convertingOrderId === order.id && (
                      <div className="mt-4 p-4 bg-amber-50 rounded-lg border border-amber-200">
                        <h4 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
                          <Truck className="w-4 h-4 text-amber-600" />
                          Convert Pickup to Shipping — Enter Shipping Address
                        </h4>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
                          <div className="md:col-span-2">
                            <label className="text-xs text-gray-500 block mb-1">Street Address *</label>
                            <input type="text" value={convertAddress.shipping_address} onChange={(e) => setConvertAddress({ ...convertAddress, shipping_address: e.target.value })}
                              placeholder="123 Main St" className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-amber-500 focus:border-amber-500" />
                          </div>
                          <div className="md:col-span-2">
                            <label className="text-xs text-gray-500 block mb-1">Apt / Suite</label>
                            <input type="text" value={convertAddress.shipping_apartment} onChange={(e) => setConvertAddress({ ...convertAddress, shipping_apartment: e.target.value })}
                              placeholder="Apt 4B" className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-amber-500 focus:border-amber-500" />
                          </div>
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">City *</label>
                            <input type="text" value={convertAddress.shipping_city} onChange={(e) => setConvertAddress({ ...convertAddress, shipping_city: e.target.value })}
                              placeholder="Spring Hill" className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-amber-500 focus:border-amber-500" />
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <label className="text-xs text-gray-500 block mb-1">State *</label>
                              <input type="text" value={convertAddress.shipping_state} onChange={(e) => setConvertAddress({ ...convertAddress, shipping_state: e.target.value })}
                                placeholder="FL" maxLength={2} className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-amber-500 focus:border-amber-500" />
                            </div>
                            <div>
                              <label className="text-xs text-gray-500 block mb-1">ZIP *</label>
                              <input type="text" value={convertAddress.shipping_zip} onChange={(e) => setConvertAddress({ ...convertAddress, shipping_zip: e.target.value })}
                                placeholder="34606" maxLength={10} className="w-full px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-amber-500 focus:border-amber-500" />
                            </div>
                          </div>
                        </div>
                        {convertError && (
                          <div className="text-sm text-red-600 bg-red-50 p-3 rounded-lg mb-3">{convertError}</div>
                        )}
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleConvertToShipping(order.id)}
                            disabled={convertingInProgress}
                            className="flex items-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 transition-colors text-sm font-medium"
                          >
                            {convertingInProgress ? <Loader2 className="w-4 h-4 animate-spin" /> : <Truck className="w-4 h-4" />}
                            {convertingInProgress ? "Converting..." : "Convert & Save Address"}
                          </button>
                          <button
                            onClick={() => { setConvertingOrderId(null); setConvertError(""); }}
                            className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}

                    {/* Resend Confirmation Feedback */}
                    {resendingOrderId === order.id && resendSuccess && (
                      <div className="mt-4 p-3 bg-green-50 rounded-lg border border-green-200 text-sm text-green-700 flex items-center gap-2">
                        <CheckCircle className="w-4 h-4" />
                        {resendSuccess}
                      </div>
                    )}
                    {resendingOrderId === order.id && resendError && (
                      <div className="mt-4 p-3 bg-red-50 rounded-lg border border-red-200 text-sm text-red-700 flex items-center gap-2">
                        <AlertTriangle className="w-4 h-4" />
                        {resendError}
                      </div>
                    )}

                    {/* Refund Confirmation Panel */}
                    {refundingOrderId === order.id && (
                      <div className="mt-4 p-4 bg-red-50 rounded-lg border border-red-200">
                        <h4 className="text-sm font-semibold text-red-900 mb-2 flex items-center gap-2">
                          <AlertTriangle className="w-4 h-4 text-red-600" />
                          Refund Order {order.order_number}
                        </h4>
                        {refundSuccess ? (
                          <div className="text-sm text-green-700 bg-green-50 p-3 rounded-lg flex items-center gap-2">
                            <CheckCircle className="w-4 h-4" />
                            {refundSuccess}
                          </div>
                        ) : refundConfirm ? (
                          <div className="space-y-3">
                            <p className="text-sm text-red-800">
                              <strong>Are you sure?</strong> This will refund <strong>{refundType === "partial" ? formatPrice(calcItemRefundSummary(order).total) : formatPrice(order.total)}</strong> to the customer&apos;s original payment method.{refundType === "partial" && " Selected items will be restocked to inventory."} This action cannot be undone.
                            </p>
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleRefund(order)}
                                disabled={processingRefund}
                                className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors text-sm font-medium"
                              >
                                {processingRefund ? <Loader2 className="w-4 h-4 animate-spin" /> : <DollarSign className="w-4 h-4" />}
                                {processingRefund ? "Processing Refund..." : `Yes, Refund ${refundType === "partial" ? formatPrice(calcItemRefundSummary(order).total) : formatPrice(order.total)}`}
                              </button>
                              <button
                                onClick={() => { setRefundConfirm(false); setRefundingOrderId(null); setRefundType("full"); setSelectedRefundItems({}); }}
                                disabled={processingRefund}
                                className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="space-y-3">
                            {/* Refund Type Toggle */}
                            <div className="flex gap-2">
                              <button
                                onClick={() => { setRefundType("full"); setSelectedRefundItems({}); }}
                                className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
                                  refundType === "full"
                                    ? "bg-red-600 text-white border-red-600"
                                    : "bg-white text-gray-700 border-gray-300 hover:border-red-300"
                                }`}
                              >
                                Full Refund
                              </button>
                              <button
                                onClick={() => { setRefundType("partial"); setSelectedRefundItems({}); }}
                                className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
                                  refundType === "partial"
                                    ? "bg-red-600 text-white border-red-600"
                                    : "bg-white text-gray-700 border-gray-300 hover:border-red-300"
                                }`}
                              >
                                Partial Refund (by item)
                              </button>
                            </div>

                            {/* Item Selection Grid for Partial Refund */}
                            {refundType === "partial" && (
                              <div className="bg-white rounded-lg border border-red-100 overflow-hidden">
                                <div className="px-3 py-2 bg-red-50 text-xs font-semibold text-red-800 uppercase">Select items to refund &amp; restock</div>
                                <table className="w-full text-sm">
                                  <thead>
                                    <tr className="text-left border-b border-gray-200 bg-gray-50">
                                      <th className="px-3 py-2 w-10"></th>
                                      <th className="px-3 py-2 font-medium text-gray-700">Product</th>
                                      <th className="px-3 py-2 font-medium text-gray-700 text-center">Qty</th>
                                      <th className="px-3 py-2 font-medium text-gray-700 text-right">Price</th>
                                      <th className="px-3 py-2 font-medium text-gray-700 text-right">Line Total</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {order.items.map((item, idx) => {
                                      const key = `${item.product_id}_${idx}`;
                                      const isChecked = !!selectedRefundItems[key];
                                      return (
                                        <tr key={key} className={`border-b border-gray-100 cursor-pointer transition-colors ${isChecked ? "bg-red-50" : "hover:bg-gray-50"}`}
                                          onClick={() => setSelectedRefundItems(prev => ({ ...prev, [key]: !prev[key] }))}
                                        >
                                          <td className="px-3 py-2">
                                            <input type="checkbox" checked={isChecked} readOnly
                                              className="w-4 h-4 text-red-600 border-gray-300 rounded focus:ring-red-500" />
                                          </td>
                                          <td className="px-3 py-2 text-gray-900">{item.product_name}</td>
                                          <td className="px-3 py-2 text-center text-gray-600">{item.quantity}</td>
                                          <td className="px-3 py-2 text-right text-gray-600">{formatPrice(item.price)}</td>
                                          <td className="px-3 py-2 text-right text-gray-900 font-medium">{formatPrice(item.price * item.quantity)}</td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>

                                {/* Refund Summary */}
                                {Object.values(selectedRefundItems).some(Boolean) && (() => {
                                  const summary = calcItemRefundSummary(order);
                                  return (
                                    <div className="px-3 py-3 bg-red-50 border-t border-red-200 space-y-1">
                                      <div className="flex justify-between text-sm">
                                        <span className="text-gray-600">Items Subtotal:</span>
                                        <span>{formatPrice(summary.itemsSubtotal)}</span>
                                      </div>
                                      <div className="flex justify-between text-sm">
                                        <span className="text-gray-600">Proportional Tax:</span>
                                        <span>{formatPrice(summary.itemsTax)}</span>
                                      </div>
                                      <div className="flex justify-between text-sm font-bold pt-1 border-t border-red-200">
                                        <span className="text-red-800">Refund Total:</span>
                                        <span className="text-red-600">{formatPrice(summary.total)}</span>
                                      </div>
                                      <p className="text-xs text-gray-500 mt-1">Selected items will be restocked to inventory at the fulfillment location.</p>
                                    </div>
                                  );
                                })()}
                              </div>
                            )}

                            {/* Full Refund Summary */}
                            {refundType === "full" && (
                              <div className="bg-white rounded-lg p-3 border border-red-100">
                                <div className="flex justify-between text-sm">
                                  <span className="text-gray-600">Subtotal:</span>
                                  <span>{formatPrice(order.subtotal)}</span>
                                </div>
                                {order.discount > 0 && (
                                  <div className="flex justify-between text-sm">
                                    <span className="text-green-600">Discount ({order.promo_code || 'Promo'}):</span>
                                    <span className="text-green-600">-{formatPrice(order.discount)}</span>
                                  </div>
                                )}
                                <div className="flex justify-between text-sm">
                                  <span className="text-gray-600">Shipping:</span>
                                  <span>{order.shipping_cost === 0 ? "Free" : formatPrice(order.shipping_cost)}</span>
                                </div>
                                <div className="flex justify-between text-sm">
                                  <span className="text-gray-600">Tax:</span>
                                  <span>{formatPrice(order.tax)}</span>
                                </div>
                                <div className="flex justify-between text-sm font-bold mt-1 pt-1 border-t border-gray-200">
                                  <span>Refund Total:</span>
                                  <span className="text-red-600">{formatPrice(order.total)}</span>
                                </div>
                              </div>
                            )}

                            {refundError && (
                              <div className="text-sm text-red-600 bg-red-100 p-3 rounded-lg">{refundError}</div>
                            )}
                            <div className="flex gap-2">
                              <button
                                onClick={() => {
                                  if (refundType === "partial" && !Object.values(selectedRefundItems).some(Boolean)) {
                                    setRefundError("Please select at least one item to refund.");
                                    return;
                                  }
                                  setRefundError("");
                                  setRefundConfirm(true);
                                }}
                                className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors text-sm font-medium"
                              >
                                <RotateCcw className="w-4 h-4" />
                                {refundType === "partial"
                                  ? (Object.values(selectedRefundItems).some(Boolean)
                                    ? `Refund ${formatPrice(calcItemRefundSummary(order).total)} & Restock`
                                    : "Select items to refund")
                                  : "Process Full Refund"}
                              </button>
                              <button
                                onClick={() => { setRefundingOrderId(null); setRefundType("full"); setSelectedRefundItems({}); }}
                                className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors text-sm font-medium"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Shipping Label Creation Panel */}
                    {isShippingOpen && !order.label_url && (
                      <div className="mt-4 p-4 bg-white rounded-lg border border-green-200">
                        <h4 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
                          <Truck className="w-4 h-4 text-green-600" />
                          Create Shipping Label via Shippo
                        </h4>

                        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">Weight</label>
                            <div className="flex gap-1">
                              <input type="number" step={weightUnit === "oz" ? "1" : "0.1"} value={parcelWeight} onChange={(e) => setParcelWeight(e.target.value)}
                                className="w-full px-3 py-1.5 border border-gray-300 rounded-l text-sm focus:ring-2 focus:ring-green-500" />
                              <select value={weightUnit} onChange={(e) => setWeightUnit(e.target.value as "lb" | "oz")}
                                className="px-2 py-1.5 border border-gray-300 rounded-r text-sm bg-gray-50 focus:ring-2 focus:ring-green-500">
                                <option value="lb">lb</option>
                                <option value="oz">oz</option>
                              </select>
                            </div>
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
                          <div className="flex items-end">
                            <label className="flex items-center gap-2 cursor-pointer px-3 py-1.5">
                              <input type="checkbox" checked={isHazmat} onChange={(e) => setIsHazmat(e.target.checked)}
                                className="w-4 h-4 rounded border-gray-300 text-orange-600 focus:ring-orange-500" />
                              <span className={`text-sm whitespace-nowrap ${isHazmat ? "text-orange-700 font-semibold" : "text-gray-700"}`}>
                                {isHazmat ? "⚠ Hazmat" : "Hazmat"}
                              </span>
                            </label>
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
