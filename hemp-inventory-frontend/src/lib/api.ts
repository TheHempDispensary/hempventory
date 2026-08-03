import axios from "axios";

export const API_URL = import.meta.env.VITE_API_URL || (
  typeof window !== "undefined" && !["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "https://thd-inventory-api.fly.dev"
    : "http://localhost:8000"
);

const api = axios.create({
  baseURL: API_URL,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Clear all token keys (generic + domain-specific) to prevent auth state mismatch
      localStorage.removeItem("token");
      localStorage.removeItem("userRole");
      localStorage.removeItem("inventory_token");
      localStorage.removeItem("inventory_userRole");
      localStorage.removeItem("timeclock_token");
      localStorage.removeItem("timeclock_userRole");
      window.location.href = "/";
    }
    return Promise.reject(error);
  }
);

// Auth
export const login = (username: string, password: string) =>
  api.post("/api/auth/login", { username, password });

export const getMe = () => api.get("/api/auth/me");

export const changePassword = (currentPassword: string, newPassword: string) =>
  api.post("/api/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });

// Locations
export const getLocations = () => api.get("/api/locations/");

export const addLocation = (data: {
  name: string;
  merchant_id: string;
  api_token: string;
  is_virtual?: boolean;
}) => api.post("/api/locations/", data);

export const deleteLocation = (id: number) =>
  api.delete(`/api/locations/${id}`);

// Inventory
export const syncInventory = () => api.get("/api/inventory/sync");
export const getCachedInventory = () => api.get("/api/inventory/cached");
export const syncLeafLife = () =>
  api.post<{
    status: string;
    created: number;
    updated: number;
    removed: number;
    strains: number;
    errors: string[];
  }>("/api/inventory/leaflife-sync");
export const getSmartPar = (months: number) =>
  api.get("/api/inventory/smart-par", { params: { months } });

export const createItem = (data: {
  name: string;
  price: number;
  sku?: string;
  category?: string;
  initial_stock?: number;
  locations?: number[];
  stock_per_location?: { location_id: number; quantity: number }[];
  par_per_location?: { location_id: number; par_level: number }[];
  price_type?: string;
  cost?: number;
  product_code?: string;
  alternate_name?: string;
  description?: string;
  color_code?: string;
  is_revenue?: boolean;
  is_age_restricted?: boolean;
  age_restriction_type?: string;
  age_restriction_min_age?: number;
  available?: boolean;
  hidden?: boolean;
  auto_manage?: boolean;
  default_tax_rates?: boolean;
}) => api.post("/api/inventory/items", data);

export const getAgeRestrictionTypes = () =>
  api.get("/api/inventory/age-restriction-types");

export const deleteItem = (sku: string, name?: string) =>
  api.delete(`/api/inventory/items/${sku}`, { params: name ? { name } : undefined });

export const bulkDeleteItems = (skus: string[]) =>
  api.post("/api/inventory/items/bulk-delete", { skus });

export const bulkAutoManage = (enable: boolean = true, skus?: string[]) =>
  api.post("/api/inventory/bulk-auto-manage", { enable, skus: skus || null });

export const bulkHideItems = (skus: string[]) =>
  api.post("/api/inventory/items/bulk-hide", { skus });

export const bulkUnhideItems = (skus: string[]) =>
  api.post("/api/inventory/items/bulk-unhide", { skus });

export const pushItemToLocation = (sku: string, locationId: number, initialStock: number = 0) =>
  api.post(`/api/inventory/items/${sku}/push-to-location`, { location_id: locationId, initial_stock: initialStock });

export const fixPosScanning = () => api.post("/api/inventory/fix-pos");

export const transferStock = (sku: string, fromLocationId: number, toLocationId: number, quantity: number, transferGroupId?: string, itemName?: string) =>
  api.post("/api/inventory/transfer-stock", { sku, from_location_id: fromLocationId, to_location_id: toLocationId, quantity, transfer_group_id: transferGroupId, item_name: itemName });

export const getTransferHistory = (limit = 50, offset = 0) =>
  api.get("/api/inventory/transfer-history", { params: { limit, offset } });

export const bulkAssignCategory = (skus: string[], categoryName: string) =>
  api.post("/api/inventory/bulk-assign-category", { skus, category_name: categoryName });

export const setItemCategory = (sku: string, categoryName: string) =>
  api.post("/api/inventory/set-item-category", { sku, category_name: categoryName });

export const bulkStockUpdate = (updates: { sku: string; location_id: number; quantity: number; item_name?: string; clover_item_id?: string }[]) =>
  api.post("/api/inventory/items/bulk-stock-update", { updates });

export const bulkAssignImages = (keyword: string, imageData: string, contentType: string = "image/png", skus?: string[]) =>
  api.post("/api/inventory/bulk-assign-images", { keyword, image_data: imageData, content_type: contentType, skus: skus || null });

export const syncRefunds = () => api.post("/api/inventory/sync-refunds");

export const getRefundHistory = () => api.get("/api/inventory/refund-history");

export const resetLoyaltySync = () => api.post("/api/loyalty/sync-reset");

export const updateItem = (
  sku: string,
  data: {
    name?: string;
    price?: number;
    sku?: string;
    stock_updates?: { location_id: number; quantity: number }[];
  }
) => api.put(`/api/inventory/items/${sku}`, data);

// PAR Levels
export const getParLevels = () => api.get("/api/par/");

export const setParLevel = (
  sku: string,
  locationId: number,
  parLevel: number
) => api.put(`/api/par/${sku}/${locationId}`, { par_level: parLevel });

export const setBulkParLevels = (
  levels: { sku: string; location_id: number; par_level: number }[]
) => api.post("/api/par/bulk", levels);

export const autoSetPar = (months: number = 1) =>
  api.post("/api/inventory/auto-set-par", { months });

export const getParAlerts = () => api.get("/api/par/alerts");

// Alerts
export const getAlertHistory = (limit?: number) =>
  api.get("/api/alerts/history", { params: { limit } });

export const checkAndNotify = () => api.post("/api/alerts/check");

export const getAlertSettings = () => api.get("/api/alerts/settings");

export const updateAlertSettings = (data: {
  notification_email: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_user?: string;
  smtp_password?: string;
}) => api.post("/api/alerts/settings", data);

// Product Images
export const uploadImage = (sku: string, imageData: string, contentType: string = "image/png", productName?: string) =>
  api.post(`/api/inventory/images/${sku}`, { image_data: imageData, content_type: contentType, product_name: productName });

export const getImageUrl = (sku: string, cacheBust?: number) =>
  `${API_URL}/api/inventory/images/${sku}${cacheBust ? `?t=${cacheBust}` : ''}`;

export const deleteImage = (sku: string) =>
  api.delete(`/api/inventory/images/${sku}`);

// Product Image Gallery (multiple images per product)
export const getImageGallery = (sku: string) =>
  api.get(`/api/inventory/images/${sku}/gallery`);

export const uploadGalleryImage = (sku: string, imageData: string, contentType: string = "image/png") =>
  api.post(`/api/inventory/images/${sku}/gallery`, { image_data: imageData, content_type: contentType });

export const getGalleryImageUrl = (sku: string, position: number, cacheBust?: number) =>
  `${API_URL}/api/inventory/images/${sku}/gallery/${position}${cacheBust ? `?t=${cacheBust}` : ''}`;

export const deleteGalleryImage = (sku: string, position: number) =>
  api.delete(`/api/inventory/images/${sku}/gallery/${position}`);

// Loyalty Program
export const getLoyaltyDashboard = () => api.get("/api/loyalty/dashboard");

export const getLoyaltyCustomers = (search?: string, page?: number) =>
  api.get("/api/loyalty/customers", { params: { search, page } });

export const createLoyaltyCustomer = (data: {
  first_name: string;
  last_name?: string;
  phone?: string;
  email?: string;
  birthday?: string;
  notes?: string;
}) => api.post("/api/loyalty/customers", data);

export const getLoyaltyCustomer = (id: number) =>
  api.get(`/api/loyalty/customers/${id}`);

export const updateLoyaltyCustomer = (id: number, data: {
  first_name?: string;
  last_name?: string;
  phone?: string;
  email?: string;
  birthday?: string;
  notes?: string;
}) => api.put(`/api/loyalty/customers/${id}`, data);

export const deleteLoyaltyCustomer = (id: number) =>
  api.delete(`/api/loyalty/customers/${id}`);

export const awardLoyaltyPoints = (customerId: number, data: {
  points: number;
  description?: string;
  order_id?: string;
  location_name?: string;
}) => api.post(`/api/loyalty/customers/${customerId}/award`, data);

export const deductLoyaltyPoints = (customerId: number, data: {
  points: number;
  description?: string;
  location_name?: string;
}) => api.post(`/api/loyalty/customers/${customerId}/deduct`, data);

export const redeemLoyaltyReward = (customerId: number, data: {
  reward_id: number;
  location_name?: string;
}) => api.post(`/api/loyalty/customers/${customerId}/redeem`, data);

export const getLoyaltyRewards = () => api.get("/api/loyalty/rewards");

export const createLoyaltyReward = (data: {
  name: string;
  points_required: number;
  reward_type?: string;
  reward_value: number;
  description?: string;
}) => api.post("/api/loyalty/rewards", data);

export const updateLoyaltyReward = (id: number, data: {
  name?: string;
  points_required?: number;
  reward_type?: string;
  reward_value?: number;
  description?: string;
  is_active?: boolean;
}) => api.put(`/api/loyalty/rewards/${id}`, data);

export const deleteLoyaltyReward = (id: number) =>
  api.delete(`/api/loyalty/rewards/${id}`);

export const getLoyaltySettings = () => api.get("/api/loyalty/settings");

export const syncLoyaltyOrders = () => api.post("/api/loyalty/sync-orders");

export const bulkImportLoyaltyCustomers = () => api.post("/api/loyalty/bulk-import");

export const getLoyaltySyncStatus = () => api.get("/api/loyalty/sync-status");

export const updateLoyaltySettings = (data: {
  points_per_dollar?: string;
  signup_bonus?: string;
  birthday_bonus?: string;
  program_name?: string;
}) => api.put("/api/loyalty/settings", data);

// Item Groups / Variants
export const getItemGroups = () => api.get("/api/inventory/item-groups");

export const getAttributes = () => api.get("/api/inventory/attributes");

export const createItemGroup = (data: {
  name: string;
  price: number;
  sku_prefix?: string;
  category?: string;
  variants: { attribute_name: string; option_names: string[] }[];
  price_type?: string;
  cost?: number;
  description?: string;
  is_revenue?: boolean;
  is_age_restricted?: boolean;
  age_restriction_type?: string;
  age_restriction_min_age?: number;
  available?: boolean;
  hidden?: boolean;
  auto_manage?: boolean;
  default_tax_rates?: boolean;
}) => api.post("/api/inventory/item-groups", data);

export interface ItemGroupRenameResult {
  location: string;
  status: "renamed" | "not_found" | "skipped_leaflife" | "error";
  group_name?: string;
  item_names?: string[];
  error?: string;
}

export const renameItemGroup = (current_name: string, new_name: string) =>
  api.post<{ new_name: string; results: ItemGroupRenameResult[] }>(
    "/api/inventory/item-groups/rename",
    { current_name, new_name },
  );

// Time Clock
export const getEmployees = () => api.get("/api/timeclock/employees");

export const createEmployee = (data: { name: string; pin?: string; username?: string }) =>
  api.post("/api/timeclock/employees", data);

export const updateEmployee = (id: number, data: { name?: string; pin?: string; username?: string; active?: boolean; pay_rate?: number }) =>
  api.put(`/api/timeclock/employees/${id}`, data);

export const deleteEmployee = (id: number) =>
  api.delete(`/api/timeclock/employees/${id}`);

export const clockIn = (employeeId: number) =>
  api.post("/api/timeclock/clock-in", { employee_id: employeeId });

export const clockOut = (employeeId: number) =>
  api.post("/api/timeclock/clock-out", { employee_id: employeeId });

export const getActiveClocks = () => api.get("/api/timeclock/active");

export const getTimeEntries = (params?: { start_date?: string; end_date?: string; employee_id?: number }) =>
  api.get("/api/timeclock/entries", { params });

export const updateTimeEntry = (id: number, data: { clock_in?: string; clock_out?: string }) =>
  api.put(`/api/timeclock/entries/${id}`, data);

export const deleteTimeEntry = (id: number) =>
  api.delete(`/api/timeclock/entries/${id}`);

export const createManualEntry = (data: { employee_id: number; clock_in: string; clock_out: string }) =>
  api.post("/api/timeclock/entries", data);

export const syncEmployeesFromClover = () =>
  api.post("/api/timeclock/sync-employees");

export const syncTipsFromClover = (params?: { start_date?: string; end_date?: string }) =>
  api.post("/api/timeclock/sync-tips", params || {});

export const getTimeclockExportUrl = (params?: { start_date?: string; end_date?: string; employee_id?: number }) => {
  const url = new URL(`${API_URL}/api/timeclock/export`);
  if (params?.start_date) url.searchParams.set("start_date", params.start_date);
  if (params?.end_date) url.searchParams.set("end_date", params.end_date);
  if (params?.employee_id) url.searchParams.set("employee_id", params.employee_id.toString());
  return url.toString();
};

// Sales Report
export const getSalesReport = (params?: { start_date?: string; end_date?: string }) =>
  api.get("/api/sales/report", { params });

// Employee Auth
export const employeeLogin = (username: string, pin: string) =>
  api.post("/api/auth/employee-login", { username, pin });

// Employee Self-Service
export const getMyProfile = () => api.get("/api/timeclock/my-profile");
export const myClockIn = () => api.post("/api/timeclock/my-clock-in");
export const myClockOut = () => api.post("/api/timeclock/my-clock-out");
export const getMyClockStatus = () => api.get("/api/timeclock/my-status");
export const getMyEntries = (params?: { start_date?: string; end_date?: string }) =>
  api.get("/api/timeclock/my-entries", { params });
export const getMyPaystubs = () => api.get("/api/timeclock/my-paystubs");

// Seed employees
export const seedEmployees = () => api.post("/api/timeclock/seed-employees");

// Schedules
export const getSchedules = (params?: { employee_id?: number; start_date?: string; end_date?: string }) =>
  api.get("/api/timeclock/schedules", { params });

export const saveSchedule = (data: {
  employee_id: number;
  date: string;
  start_time: string;
  end_time: string;
  location?: string;
  notes?: string;
}) => api.post("/api/timeclock/schedules", data);

export const updateSchedule = (scheduleId: number, data: {
  employee_id: number;
  date: string;
  start_time: string;
  end_time: string;
  location?: string;
  notes?: string;
}) => api.put(`/api/timeclock/schedules/${scheduleId}`, data);

export const deleteScheduleById = (scheduleId: number) =>
  api.delete(`/api/timeclock/schedules/${scheduleId}`);

export const deleteScheduleByDate = (employeeId: number, date: string) =>
  api.delete(`/api/timeclock/schedules/employee/${employeeId}/date/${date}`);

export const getMySchedule = (params?: { start_date?: string; end_date?: string }) =>
  api.get("/api/timeclock/my-schedule", { params });

// Employee Self-Service: Time-Off & Notes
export const getMyTimeOff = () => api.get("/api/timeclock/my-time-off");
export const submitMyTimeOff = (data: { date: string; reason?: string }) =>
  api.post("/api/timeclock/my-time-off", data);
export const cancelMyTimeOff = (requestId: number) =>
  api.delete(`/api/timeclock/my-time-off/${requestId}`);
export const getMyScheduleNotes = (params?: { start_date?: string; end_date?: string }) =>
  api.get("/api/timeclock/my-schedule-notes", { params });

// Time-Off Requests
export const getTimeOffRequests = (params?: { employee_id?: number; start_date?: string; end_date?: string; status?: string }) =>
  api.get("/api/timeclock/time-off", { params });

export const createTimeOffRequest = (data: { employee_id: number; date: string; reason?: string }) =>
  api.post("/api/timeclock/time-off", data);

export const updateTimeOffRequest = (requestId: number, status: string) =>
  api.put(`/api/timeclock/time-off/${requestId}`, { status });

export const deleteTimeOffRequest = (requestId: number) =>
  api.delete(`/api/timeclock/time-off/${requestId}`);

// Schedule Notes
export const getScheduleNotes = (params?: { start_date?: string; end_date?: string }) =>
  api.get("/api/timeclock/schedule-notes", { params });

export const createScheduleNote = (data: { date: string; note: string; note_type?: string; employee_id?: number }) =>
  api.post("/api/timeclock/schedule-notes", data);

export const updateScheduleNote = (noteId: number, data: { note?: string; note_type?: string; employee_id?: number }) =>
  api.put(`/api/timeclock/schedule-notes/${noteId}`, data);

export const deleteScheduleNote = (noteId: number) =>
  api.delete(`/api/timeclock/schedule-notes/${noteId}`);

// Online Orders (ecommerce)
export const getOnlineOrders = (params?: { limit?: number; offset?: number; status?: string; search?: string }) =>
  api.get("/api/ecommerce/orders", { params });

export const updateOrderStatus = (orderId: number, status: string) =>
  api.patch(`/api/ecommerce/orders/${orderId}/status`, { status });

export const updateOrderNotes = (orderId: number, staffNotes: string) =>
  api.patch(`/api/ecommerce/orders/${orderId}/notes`, { staff_notes: staffNotes });

export const updateOrderCustomer = (orderId: number, data: {
  customer_first_name?: string;
  customer_last_name?: string;
  customer_email?: string;
  customer_phone?: string;
  shipping_address?: string;
  shipping_apartment?: string;
  shipping_city?: string;
  shipping_state?: string;
  shipping_zip?: string;
}) => api.patch(`/api/ecommerce/orders/${orderId}/customer`, data);

export const convertToShipping = (orderId: number, data: {
  shipping_address: string;
  shipping_apartment?: string;
  shipping_city: string;
  shipping_state: string;
  shipping_zip: string;
}) => api.patch(`/api/ecommerce/orders/${orderId}/convert-to-shipping`, data);

export const updateFulfillmentType = (orderId: number, fulfillment_type: string) =>
  api.patch(`/api/ecommerce/orders/${orderId}/fulfillment-type`, { fulfillment_type });

export const recoverOrder = (data: {
  order_number: string;
  charge_id?: string;
  payment_status?: string;
  fulfillment_type: string;
  total: number;
  customer: { first_name: string; last_name: string; email: string; phone?: string };
  shipping_address?: { address?: string; apartment?: string; city?: string; state?: string; zip?: string };
  items: { product_id: string; name: string; sku?: string; price: number; quantity: number }[];
  notes?: string;
}) => api.post("/api/ecommerce/orders/recover", data);

// Schedule Hours
export const getScheduleHours = (params?: { start_date?: string; end_date?: string }) =>
  api.get("/api/timeclock/schedule-hours", { params });

// Bulk Schedule (multi-day)
export const saveBulkSchedule = (data: {
  employee_id: number;
  dates: string[];
  start_time: string;
  end_time: string;
  location?: string;
  notes?: string;
}) => api.post("/api/timeclock/schedules/bulk", data);

// Shift Requests (pickup & trade)
export const getShiftRequests = (params?: { status?: string }) =>
  api.get("/api/timeclock/shift-requests", { params });

export const createShiftPickupRequest = (data: { schedule_id: number; message?: string }) =>
  api.post("/api/timeclock/shift-requests/pickup", data);

export const createShiftTradeRequest = (data: { requester_schedule_id: number; target_schedule_id: number; message?: string }) =>
  api.post("/api/timeclock/shift-requests/trade", data);

export const updateShiftRequest = (requestId: number, status: string) =>
  api.put(`/api/timeclock/shift-requests/${requestId}`, { status });

export const deleteShiftRequest = (requestId: number) =>
  api.delete(`/api/timeclock/shift-requests/${requestId}`);

// Inventory change history
export const getInventoryChanges = (params?: { sku?: string; location?: string; limit?: number; offset?: number }) =>
  api.get("/api/inventory/changes", { params });

// Add variants to existing item
export const addVariantsToItem = (data: {
  item_name: string;
  item_sku?: string;
  price: number;
  sku_prefix?: string;
  variants: { attribute_name: string; option_names: string[] }[];
  keep_original?: boolean;
}) => api.post("/api/inventory/add-variants", data);

// Shipping (Shippo)
export const createShipment = (data: {
  order_id: number;
  parcel_length?: number;
  parcel_width?: number;
  parcel_height?: number;
  parcel_weight?: number;
  is_hazmat?: boolean;
}) => api.post("/api/shipping/create-shipment", data);

export const purchaseLabel = (data: {
  rate_id: string;
  order_id: number;
  label_file_type?: string;
  shipment_id?: number;
}) => api.post("/api/shipping/purchase-label", data);

export const getShippingLabel = (orderId: number) =>
  api.get(`/api/shipping/label/${orderId}`);

export const getOrderShipments = (orderId: number) =>
  api.get(`/api/shipping/shipments/${orderId}`);

// Resend Confirmation
export const resendOrderConfirmation = (orderId: number) =>
  api.post(`/api/ecommerce/orders/${orderId}/resend-confirmation`);

// Refunds
export const refundOrder = (orderId: number, data?: { amount?: number; refunded_items?: { product_id: string; product_name: string; sku: string; price: number; quantity: number }[] }) =>
  api.post(`/api/ecommerce/orders/${orderId}/refund`, data || {});

// Promo / Discount Management
export const getPromos = () => api.get("/api/ecommerce/promos");

export const createPromo = (data: {
  code?: string;
  discount_pct?: number;
  discount_amount?: number;
  single_use?: boolean;
  max_uses?: number;
  expires_at?: string | null;
  starts_at?: string | null;
  applies_to?: string;
  product_ids?: string;
  exclude_from_other_coupons?: boolean;
  sync_to_clover?: boolean;
  in_store_only?: boolean;
  is_direct_discount?: boolean;
  excluded_brands?: string;
}) => api.post("/api/ecommerce/promos", data);

export const updatePromo = (promoId: number, data: {
  discount_pct?: number;
  discount_amount?: number;
  single_use?: boolean;
  is_active?: boolean;
  max_uses?: number;
  expires_at?: string | null;
  starts_at?: string | null;
  applies_to?: string;
  product_ids?: string;
  exclude_from_other_coupons?: boolean;
  sync_to_clover?: boolean;
  in_store_only?: boolean;
  excluded_brands?: string;
}) => api.put(`/api/ecommerce/promos/${promoId}`, data);

export const deletePromo = (promoId: number) =>
  api.delete(`/api/ecommerce/promos/${promoId}`);

// Discount Usage Tracking
export const getDiscountUsage = (code: string) =>
  api.get(`/api/ecommerce/discount-usage/${encodeURIComponent(code)}`);

export const getAllDiscountUsage = () =>
  api.get("/api/ecommerce/discount-usage");

// Volume Discounts
export const getVolumeDiscounts = () => api.get("/api/ecommerce/volume-discounts");

export const getActiveVolumeDiscounts = () => api.get("/api/ecommerce/volume-discounts/active");

export const createVolumeDiscount = (data: {
  product_sku: string;
  product_name: string;
  min_quantity: number;
  discount_type: string;
  discount_value: number;
  customer_label?: string;
  is_active?: boolean;
  sync_to_clover?: boolean;
}) => api.post("/api/ecommerce/volume-discounts", data);

export const updateVolumeDiscount = (id: number, data: {
  product_sku?: string;
  product_name?: string;
  min_quantity?: number;
  discount_type?: string;
  discount_value?: number;
  customer_label?: string;
  is_active?: boolean;
  sync_to_clover?: boolean;
}) => api.put(`/api/ecommerce/volume-discounts/${id}`, data);

export const deleteVolumeDiscount = (id: number) =>
  api.delete(`/api/ecommerce/volume-discounts/${id}`);

// Wholesale Bundles
export const getWholesaleBundles = () => api.get("/api/ecommerce/wholesale-bundles");

export const createWholesaleBundle = (data: {
  name: string;
  description?: string;
  min_quantity: number;
  price_cents: number;
  product_skus: string[];
  category_filter?: string;
  is_active?: boolean;
  image_url?: string;
}) => api.post("/api/ecommerce/wholesale-bundles", data);

export const updateWholesaleBundle = (id: number, data: {
  name?: string;
  description?: string;
  min_quantity?: number;
  price_cents?: number;
  product_skus?: string[];
  category_filter?: string;
  is_active?: boolean;
  image_url?: string;
}) => api.put(`/api/ecommerce/wholesale-bundles/${id}`, data);

export const deleteWholesaleBundle = (id: number) =>
  api.delete(`/api/ecommerce/wholesale-bundles/${id}`);

// Product Attributes (effect & strength for online store)
export const getProductAttributes = () => api.get("/api/inventory/product-attributes");

export const updateProductAttributes = (sku: string, data: {
  effect?: string | null;
  strength?: string | null;
  product_type?: string | null;
  product_name?: string;
}) => api.put(`/api/inventory/product-attributes/${encodeURIComponent(sku)}`, data);

// Product Scraper
export const scrapeProduct = (data: { manufacturer: string; model_number: string }) =>
  api.post("/api/scraper/scrape", data);

export const getManufacturers = () => api.get("/api/scraper/manufacturers");

// Chat / Conversations
export const getChatSessions = (params?: {
  search?: string;
  intent?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}) => api.get("/api/chat/sessions", { params });

export const getChatSession = (sessionId: string) =>
  api.get(`/api/chat/sessions/${sessionId}`);

// COA Lab Results (ACS Laboratory)
export const getCoaStatus = () => api.get("/api/coa/status");

export const syncCoaResults = () => api.post("/api/coa/sync");

export const getCoaSamples = () => api.get("/api/coa/samples");

export const getCoaSample = (accession: string) =>
  api.get(`/api/coa/samples/${encodeURIComponent(accession)}`);

export const getCoaBySku = (sku: string) =>
  api.get(`/api/coa/by-sku/${encodeURIComponent(sku)}`);

export const linkSkuToCoa = (sku: string, sampleAccession: string) =>
  api.post("/api/coa/link", { sku, sample_accession: sampleAccession });

export const unlinkSkuFromCoa = (sku: string, sampleAccession: string) =>
  api.delete("/api/coa/link", { params: { sku, sample_accession: sampleAccession } });

// Manual (non-ACS) COAs
export interface ManualCoaAnalyte {
  panel_name?: string;
  analyte_identifier?: string;
  analyte_abbreviation?: string;
  result?: string;
  result_unit?: string;
  concentration?: number;
  conc_unit?: string;
  analyte_remark?: string;
  panel_remark?: string;
}

export interface ManualCoaPayload {
  product_name?: string;
  description?: string;
  batch_no?: string;
  business_name?: string;
  product_type?: string;
  test_purpose?: string;
  sample_status?: string;
  coa_approved_date?: string;
  coa_url?: string;
  analytes?: ManualCoaAnalyte[];
  skus?: string[];
}

export const createManualCoa = (payload: ManualCoaPayload) =>
  api.post("/api/coa/manual", payload);

export const updateManualCoa = (accession: string, payload: ManualCoaPayload) =>
  api.put(`/api/coa/manual/${encodeURIComponent(accession)}`, payload);

export const deleteManualCoa = (accession: string) =>
  api.delete(`/api/coa/manual/${encodeURIComponent(accession)}`);

export const uploadCoaFile = (file: File) => {
  const data = new FormData();
  data.append("file", file);
  return api.post<{ url: string; filename: string }>("/api/coa/upload", data, {
    headers: { "Content-Type": "multipart/form-data" },
  });
};

// Build an absolute URL for a stored COA reference (upload path or full URL).
export const coaFileUrl = (ref?: string | null): string => {
  if (!ref) return "";
  return /^https?:\/\//.test(ref) ? ref : `${API_URL}${ref}`;
};

// Ecommerce products (public, HQ catalog)
export const getEcommerceProducts = () => api.get("/api/ecommerce/products");

// ── Production planning & tracking ───────────────────────────────────────────
export interface ProductionFlag {
  sku: string;
  product_name: string;
}

export interface ProductionPlanItem {
  sku: string;
  name: string;
  categories: string[];
  in_stock: number;
  stock_by_location: Record<string, number>;
  units_sold: number;
  units_per_month: number;
  needed: number;
  already_planned: number;
  to_produce: number;
  made_in_house: boolean;
}

export interface ProductionBatch {
  id: number;
  sku: string | null;
  product_name: string;
  size: string | null;
  planned_qty: number;
  produced_qty: number;
  status: "planned" | "in_production" | "ready" | "done";
  batch_no: string | null;
  expiration_date: string | null;
  made_by: string | null;
  qa_check: boolean;
  label_ordered: boolean;
  label_qty: number | null;
  notes: string | null;
  source: string;
  plan_date: string | null;
  completed_at: string | null;
  inventoried: boolean;
  inventoried_at: string | null;
  inventoried_qty: number | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
  inventory_result?: { ok: boolean; reason?: string; previous?: number; new?: number; added?: number };
}

export interface BatchPayload {
  product_name?: string;
  sku?: string | null;
  size?: string | null;
  planned_qty?: number;
  produced_qty?: number;
  status?: string;
  batch_no?: string | null;
  expiration_date?: string | null;
  made_by?: string | null;
  qa_check?: boolean;
  label_ordered?: boolean;
  label_qty?: number | null;
  notes?: string | null;
  source?: string;
  plan_date?: string | null;
  add_to_inventory?: boolean;
}

export const getProductionFlags = () =>
  api.get<{ flags: ProductionFlag[] }>("/api/production/flags");

export const setProductionFlag = (sku: string, madeInHouse: boolean, productName?: string) =>
  api.put(`/api/production/flags/${encodeURIComponent(sku)}`, {
    made_in_house: madeInHouse,
    product_name: productName,
  });

export const seedProductionFlags = () =>
  api.post<{ added: number; already_flagged: number; matched: ProductionFlag[] }>(
    "/api/production/seed-flags"
  );

export const getProductionPlan = (months: number) =>
  api.get<{ items: ProductionPlanItem[]; meta: { months: number; flagged: number; days_of_data: number | null } }>(
    "/api/production/plan",
    { params: { months } }
  );

export const getProductionBatches = (status?: string) =>
  api.get<{ batches: ProductionBatch[] }>("/api/production/batches", { params: status ? { status } : undefined });

export const createProductionBatch = (payload: BatchPayload) =>
  api.post<ProductionBatch>("/api/production/batches", payload);

export const updateProductionBatch = (id: number, payload: BatchPayload) =>
  api.put<ProductionBatch>(`/api/production/batches/${id}`, payload);

export const deleteProductionBatch = (id: number) =>
  api.delete(`/api/production/batches/${id}`);

export const addBatchToInventory = (id: number) =>
  api.post<ProductionBatch>(`/api/production/batches/${id}/add-to-inventory`);

export const reorderProductionBatches = (ids: number[]) =>
  api.post<{ ok: boolean; count: number }>("/api/production/batches/reorder", { ids });

export default api;
