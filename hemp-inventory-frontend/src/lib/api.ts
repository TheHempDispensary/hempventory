import axios from "axios";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

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
      localStorage.removeItem("token");
      localStorage.removeItem("userRole");
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

export const pushItemToLocation = (sku: string, locationId: number, initialStock: number = 0) =>
  api.post(`/api/inventory/items/${sku}/push-to-location`, { location_id: locationId, initial_stock: initialStock });

export const fixPosScanning = () => api.post("/api/inventory/fix-pos");

export const transferStock = (sku: string, fromLocationId: number, toLocationId: number, quantity: number) =>
  api.post("/api/inventory/transfer-stock", { sku, from_location_id: fromLocationId, to_location_id: toLocationId, quantity });

export const bulkAssignCategory = (skus: string[], categoryName: string) =>
  api.post("/api/inventory/bulk-assign-category", { skus, category_name: categoryName });

export const bulkStockUpdate = (updates: { sku: string; location_id: number; quantity: number }[]) =>
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

// Time Clock
export const getEmployees = () => api.get("/api/timeclock/employees");

export const createEmployee = (data: { name: string; pin?: string }) =>
  api.post("/api/timeclock/employees", data);

export const updateEmployee = (id: number, data: { name?: string; pin?: string; active?: boolean }) =>
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

export const syncEmployeesFromClover = () =>
  api.post("/api/timeclock/sync-employees");

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

export const createScheduleNote = (data: { date: string; note: string }) =>
  api.post("/api/timeclock/schedule-notes", data);

export const deleteScheduleNote = (noteId: number) =>
  api.delete(`/api/timeclock/schedule-notes/${noteId}`);

// Online Orders (ecommerce)
export const getOnlineOrders = (params?: { limit?: number; offset?: number; status?: string }) =>
  api.get("/api/ecommerce/orders", { params });

export const updateOrderStatus = (orderId: number, status: string) =>
  api.patch(`/api/ecommerce/orders/${orderId}/status`, { status });

export const updateOrderNotes = (orderId: number, staffNotes: string) =>
  api.patch(`/api/ecommerce/orders/${orderId}/notes`, { staff_notes: staffNotes });

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
}) => api.post("/api/shipping/purchase-label", data);

export const getShippingLabel = (orderId: number) =>
  api.get(`/api/shipping/label/${orderId}`);

// Resend Confirmation
export const resendOrderConfirmation = (orderId: number) =>
  api.post(`/api/ecommerce/orders/${orderId}/resend-confirmation`);

// Refunds
export const refundOrder = (orderId: number, amount?: number) =>
  api.post(`/api/ecommerce/orders/${orderId}/refund`, amount ? { amount } : {});

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
  is_direct_discount?: boolean;
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
}) => api.put(`/api/ecommerce/promos/${promoId}`, data);

export const deletePromo = (promoId: number) =>
  api.delete(`/api/ecommerce/promos/${promoId}`);

export default api;
