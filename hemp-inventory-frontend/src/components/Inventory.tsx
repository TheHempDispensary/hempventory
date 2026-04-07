import { useEffect, useState, useMemo } from "react";
import { syncInventory, getCachedInventory, setParLevel, createItem, updateItem, deleteItem, bulkDeleteItems, bulkAutoManage, fixPosScanning, pushItemToLocation, transferStock, bulkAssignCategory, bulkAssignImages, syncRefunds, getAgeRestrictionTypes, uploadImage, getImageUrl, deleteImage as deleteProductImage, createItemGroup, bulkStockUpdate, addVariantsToItem, getInventoryChanges, getProductAttributes, updateProductAttributes, getImageGallery, uploadGalleryImage, getGalleryImageUrl, deleteGalleryImage } from "../lib/api";
import { RefreshCw, Search, Plus, ChevronDown, ChevronUp, X, Save, Package, Trash2, CheckSquare, Square, Minus, Image, Download, Upload, Settings, ArrowRightLeft, Images, Layers, Tag, ChevronsLeft, ChevronsRight, ChevronLeft, ChevronRight } from "lucide-react";

interface LocationStock {
  location_id: number;
  stock: number;
  par_level: number | null;
  status: string;
  clover_item_id: string;
}

interface InventoryItem {
  id: string;
  sku: string;
  name: string;
  price: number;
  categories: string[];
  locations: Record<string, LocationStock>;
  // Extended Clover fields
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
  has_image?: boolean;
  item_group_name?: string;
}

interface LocationInfo {
  id: number;
  name: string;
  merchant_id: string;
}

export default function Inventory() {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [locations, setLocations] = useState<LocationInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [locationFilter, setLocationFilter] = useState("all");
  const [sortField, setSortField] = useState<"name" | "sku" | "stock" | "price" | "category" | "par">("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [sortLocation, setSortLocation] = useState<string>("");
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(50);
  const [editingPar, setEditingPar] = useState<{ sku: string; locName: string } | null>(null);
  const [parValue, setParValue] = useState("");
  // Batch stock editing: key = "sku::locName", value = string (edited value)
  const [pendingStockChanges, setPendingStockChanges] = useState<Map<string, { sku: string; locationId: number; locName: string; value: string; originalValue: number; itemName: string; cloverItemId: string }>>(new Map());
  const [savingStock, setSavingStock] = useState(false);
  const [showAddItem, setShowAddItem] = useState(false);
  const [newItem, setNewItem] = useState<{
    name: string;
    price: string;
    sku: string;
    category: string;
    stocks: Record<string, string>;
    pars: Record<string, string>;
    price_type: string;
    cost: string;
    product_code: string;
    alternate_name: string;
    description: string;
    color_code: string;
    is_revenue: boolean;
    is_age_restricted: boolean;
    age_restriction_type: string;
    age_restriction_min_age: string;
    available: boolean;
    hidden: boolean;
    auto_manage: boolean;
    default_tax_rates: boolean;
  }>({
    name: "", price: "", sku: "", category: "", stocks: {}, pars: {},
    price_type: "FIXED", cost: "", product_code: "", alternate_name: "",
    description: "", color_code: "", is_revenue: true, is_age_restricted: false,
    age_restriction_type: "Vitamin & Supplements", age_restriction_min_age: "21",
    available: true, hidden: false, auto_manage: true, default_tax_rates: true,
  });
  const [ageRestrictionTypes, setAgeRestrictionTypes] = useState<{id?: string; name: string; minimumAge: number}[]>([]);
  const [addItemTab, setAddItemTab] = useState("details");
  const [addingItem, setAddingItem] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<InventoryItem | null>(null);
  const [addItemMessage, setAddItemMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Multi-select state
  const [selectedItems, setSelectedItems] = useState<Set<string>>(new Set());
  const [showBulkConfirm, setShowBulkConfirm] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [autoManaging, setAutoManaging] = useState(false);
  const [pushingToLocation, setPushingToLocation] = useState<number | null>(null);
  const [syncingRefunds, setSyncingRefunds] = useState(false);

  // Cache-busting counter: incremented after image uploads to force browser to fetch fresh images
  const [imageCacheBust, setImageCacheBust] = useState(() => Date.now());

  // Bulk category state
  const [showBulkCategory, setShowBulkCategory] = useState(false);
  const [bulkCategoryName, setBulkCategoryName] = useState("");
  const [assigningCategory, setAssigningCategory] = useState(false);

  // Edit modal state
  const [editItem, setEditItem] = useState<InventoryItem | null>(null);
  const [editTab, setEditTab] = useState("details");
  const [editForm, setEditForm] = useState<{
    name: string;
    price: string;
    stocks: Record<string, string>;
    pars: Record<string, string>;
    price_type: string;
    cost: string;
    product_code: string;
    alternate_name: string;
    description: string;
    color_code: string;
    is_revenue: boolean;
    is_age_restricted: boolean;
    age_restriction_type: string;
    age_restriction_min_age: string;
    available: boolean;
    hidden: boolean;
    auto_manage: boolean;
    default_tax_rates: boolean;
    effect: string;
    strength: string;
    product_type: string;
  }>({
    name: "", price: "", stocks: {}, pars: {},
    price_type: "FIXED", cost: "", product_code: "", alternate_name: "",
    description: "", color_code: "", is_revenue: true, is_age_restricted: false,
    age_restriction_type: "Vitamin & Supplements", age_restriction_min_age: "21",
    available: true, hidden: false, auto_manage: false, default_tax_rates: true,
    effect: "", strength: "", product_type: "",
  });

  // Product attributes loaded from backend
  const [productAttrsMap, setProductAttrsMap] = useState<Record<string, { effect?: string; strength?: string; product_type?: string }>>({});
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Image state
  const [newItemImageFile, setNewItemImageFile] = useState<File | null>(null);
  const [newItemImagePreview, setNewItemImagePreview] = useState<string | null>(null);
  const [editImageFile, setEditImageFile] = useState<File | null>(null);
  const [editImagePreview, setEditImagePreview] = useState<string | null>(null);
  const [imageUploading, setImageUploading] = useState(false);

  // Gallery state (additional images beyond the primary one)
  const [galleryImages, setGalleryImages] = useState<{ id: number; position: number; content_type: string; created_at: string }[]>([]);
  const [galleryUploading, setGalleryUploading] = useState(false);

  // Variant state
  const [hasVariants, setHasVariants] = useState(false);
  const [variantAttributes, setVariantAttributes] = useState<{ attribute_name: string; option_names: string[] }[]>([
    { attribute_name: "", option_names: [""] }
  ]);

  // Add-variants-to-existing-item state
  const [editVariantAttrs, setEditVariantAttrs] = useState<{ attribute_name: string; option_names: string[] }[]>([
    { attribute_name: "", option_names: [""] }
  ]);
  const [addingVariants, setAddingVariants] = useState(false);
  const [keepOriginal, setKeepOriginal] = useState(false);

  // Inventory change history state
  const [changeHistory, setChangeHistory] = useState<Array<{ id: number; sku: string; product_name: string; location_name: string; old_stock: number; new_stock: number; change_amount: number; change_source: string; created_at: string }>>([]);
  const [changeHistoryLoading, setChangeHistoryLoading] = useState(false);

  const handleAddItemWithVariants = async () => {
    setAddItemMessage(null);
    if (!newItem.name) {
      setAddItemMessage({ type: "error", text: "Product name is required." });
      setAddItemTab("details");
      return;
    }
    if (!newItem.price || isNaN(parseFloat(newItem.price)) || parseFloat(newItem.price) <= 0) {
      setAddItemMessage({ type: "error", text: "A valid price is required." });
      setAddItemTab("details");
      return;
    }
    // Validate variants
    const validVariants = variantAttributes.filter(v => v.attribute_name.trim() && v.option_names.some(o => o.trim()));
    if (validVariants.length === 0) {
      setAddItemMessage({ type: "error", text: "At least one attribute with options is required for variants." });
      setAddItemTab("variants");
      return;
    }
    setAddingItem(true);
    try {
      const response = await createItemGroup({
        name: newItem.name,
        price: Math.round(parseFloat(newItem.price) * 100),
        sku_prefix: newItem.sku || undefined,
        category: newItem.category || undefined,
        variants: validVariants.map(v => ({
          attribute_name: v.attribute_name.trim(),
          option_names: v.option_names.filter(o => o.trim()).map(o => o.trim()),
        })),
        price_type: newItem.price_type || undefined,
        cost: newItem.cost ? Math.round(parseFloat(newItem.cost) * 100) : undefined,
        description: newItem.description || undefined,
        is_revenue: newItem.is_revenue,
        is_age_restricted: newItem.is_age_restricted,
        age_restriction_type: newItem.is_age_restricted && newItem.age_restriction_type ? newItem.age_restriction_type : undefined,
        age_restriction_min_age: newItem.is_age_restricted && newItem.age_restriction_min_age ? parseInt(newItem.age_restriction_min_age) : undefined,
        available: newItem.available,
        hidden: newItem.hidden,
        auto_manage: newItem.auto_manage,
        default_tax_rates: newItem.default_tax_rates,
      });
      const results = response.data?.results || [];
      const errors = results.filter((r: { status: string }) => r.status === "error");
      if (errors.length > 0 && errors.length === results.length) {
        setAddItemMessage({ type: "error", text: `Failed to create item group: ${errors[0]?.error || "Unknown error"}` });
        return;
      }
      const totalItems = results.reduce((sum: number, r: { items_created?: number }) => sum + (r.items_created || 0), 0);
      setShowAddItem(false);
      setNewItem({
        name: "", price: "", sku: "", category: "", stocks: {}, pars: {},
        price_type: "FIXED", cost: "", product_code: "", alternate_name: "",
        description: "", color_code: "", is_revenue: true, is_age_restricted: false,
        age_restriction_type: "Vitamin & Supplements", age_restriction_min_age: "21",
          available: true, hidden: false, auto_manage: true, default_tax_rates: true,
        });
        setHasVariants(false);
      setVariantAttributes([{ attribute_name: "", option_names: [""] }]);
      setAddItemTab("details");
      setAddItemMessage(null);
      setToast({ type: "success", text: `Item group "${newItem.name}" created with ${totalItems} variant(s) per location. Syncing...` });
      setTimeout(() => setToast(null), 6000);
      await new Promise(resolve => setTimeout(resolve, 2000));
      await loadData();
    } catch (err: unknown) {
      console.error("Error creating item group:", err);
      const axiosError = err as { response?: { data?: { detail?: string } } };
      setAddItemMessage({ type: "error", text: axiosError.response?.data?.detail || "Failed to create item group." });
    } finally {
      setAddingItem(false);
    }
  };

  const loadData = async (forceSync = false) => {
    setLoadError(null);
    try {
      const res = forceSync ? await syncInventory() : await getCachedInventory();
      setItems(res.data.items || []);
      setLocations(res.data.locations || []);
    } catch (err) {
      console.error("Error loading inventory:", err);
      // Only show error if we have no data yet (don't overwrite existing data on refresh failures)
      if (items.length === 0) {
        setLoadError("Failed to load inventory. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    await loadData(true);
    setSyncing(false);
  };

  useEffect(() => {
    loadData();
    // Load product attributes (effect/strength) from backend
    getProductAttributes().then(res => {
      const map: Record<string, { effect?: string; strength?: string; product_type?: string }> = {};
      for (const attr of res.data.attributes || []) {
        map[attr.sku] = { effect: attr.effect || undefined, strength: attr.strength || undefined, product_type: attr.product_type || undefined };
      }
      setProductAttrsMap(map);
    }).catch(() => {});
  }, []);

  const categories = useMemo(() => {
    const cats = new Set<string>();
    items.forEach((item) => item.categories.forEach((c) => cats.add(c)));
    return Array.from(cats).sort();
  }, [items]);

  const filteredItems = useMemo(() => {
    let filtered = [...items];

    if (search) {
      const s = search.toLowerCase();
      filtered = filtered.filter(
        (i) =>
          i.name.toLowerCase().includes(s) ||
          i.sku.toLowerCase().includes(s)
      );
    }

    if (categoryFilter !== "all") {
      filtered = filtered.filter((i) => i.categories.includes(categoryFilter));
    }

    if (locationFilter !== "all") {
      filtered = filtered.filter((i) => i.locations[locationFilter]);
    }

    filtered.sort((a, b) => {
      let cmp = 0;
      if (sortField === "name") cmp = a.name.localeCompare(b.name);
      else if (sortField === "sku") cmp = a.sku.localeCompare(b.sku);
      else if (sortField === "price") cmp = a.price - b.price;
      else if (sortField === "category") {
        const aCat = a.categories[0] || "";
        const bCat = b.categories[0] || "";
        cmp = aCat.localeCompare(bCat);
      } else if (sortField === "stock") {
        const aHasLoc = sortLocation && a.locations[sortLocation];
        const bHasLoc = sortLocation && b.locations[sortLocation];
        if (!aHasLoc && !bHasLoc) { cmp = 0; }
        else if (!aHasLoc) { return 1; }
        else if (!bHasLoc) { return -1; }
        else { cmp = a.locations[sortLocation].stock - b.locations[sortLocation].stock; }
      } else if (sortField === "par") {
        const aHasLoc = sortLocation && a.locations[sortLocation];
        const bHasLoc = sortLocation && b.locations[sortLocation];
        if (!aHasLoc && !bHasLoc) { cmp = 0; }
        else if (!aHasLoc) { return 1; }
        else if (!bHasLoc) { return -1; }
        else { cmp = (a.locations[sortLocation].par_level ?? 0) - (b.locations[sortLocation].par_level ?? 0); }
      }
      return sortDir === "asc" ? cmp : -cmp;
    });

    return filtered;
  }, [items, search, categoryFilter, locationFilter, sortField, sortDir, sortLocation]);

  // Reset to page 1 when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [search, categoryFilter, locationFilter, sortField, sortDir, sortLocation]);

  const totalPages = Math.max(1, Math.ceil(filteredItems.length / itemsPerPage));
  const paginatedItems = useMemo(() => {
    const start = (currentPage - 1) * itemsPerPage;
    return filteredItems.slice(start, start + itemsPerPage);
  }, [filteredItems, currentPage, itemsPerPage]);

  const handleSetPar = async (sku: string, locationId: number) => {
    const val = parseFloat(parValue);
    if (isNaN(val) || val < 0) return;
    try {
      await setParLevel(sku, locationId, val);
      setEditingPar(null);
      setParValue("");
      await loadData();
    } catch (err) {
      console.error("Error setting PAR:", err);
    }
  };

  const handleFileToBase64 = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        // Remove the data:image/xxx;base64, prefix
        const base64 = result.split(",")[1];
        resolve(base64);
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  };

  const handleNewItemFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setNewItemImageFile(file);
      const url = URL.createObjectURL(file);
      setNewItemImagePreview(url);
    }
  };

  const handleEditFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setEditImageFile(file);
      const url = URL.createObjectURL(file);
      setEditImagePreview(url);
    }
  };

  const handleUploadEditImage = async () => {
    if (!editItem || !editImageFile) return;
    setImageUploading(true);
    try {
      const base64 = await handleFileToBase64(editImageFile);
      await uploadImage(editItem.sku, base64, editImageFile.type, editItem.name);
      setEditImageFile(null);
      setEditImagePreview(null);
      setSaveMessage({ type: "success", text: "Image uploaded successfully!" });
      setImageCacheBust(Date.now());
      await loadData();
    } catch (err) {
      console.error("Error uploading image:", err);
      setSaveMessage({ type: "error", text: "Failed to upload image." });
    } finally {
      setImageUploading(false);
    }
  };

  const handleDeleteEditImage = async () => {
    if (!editItem) return;
    setImageUploading(true);
    try {
      await deleteProductImage(editItem.sku);
      setEditImagePreview(null);
      setSaveMessage({ type: "success", text: "Image deleted." });
      setImageCacheBust(Date.now());
      await loadData();
    } catch (err) {
      console.error("Error deleting image:", err);
    } finally {
      setImageUploading(false);
    }
  };

  const loadGalleryImages = async (sku: string) => {
    try {
      const res = await getImageGallery(sku);
      setGalleryImages(res.data);
    } catch {
      setGalleryImages([]);
    }
  };

  const handleGalleryFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!editItem) return;
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setGalleryUploading(true);
    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const base64 = await handleFileToBase64(file);
        await uploadGalleryImage(editItem.sku, base64, file.type);
      }
      setSaveMessage({ type: "success", text: `${files.length} image${files.length > 1 ? "s" : ""} added to gallery!` });
      await loadGalleryImages(editItem.sku);
      setImageCacheBust(Date.now());
    } catch (err) {
      console.error("Error uploading gallery image:", err);
      setSaveMessage({ type: "error", text: "Failed to upload gallery image." });
    } finally {
      setGalleryUploading(false);
      e.target.value = "";
    }
  };

  const handleDeleteGalleryImage = async (position: number) => {
    if (!editItem) return;
    setGalleryUploading(true);
    try {
      await deleteGalleryImage(editItem.sku, position);
      setSaveMessage({ type: "success", text: "Gallery image deleted." });
      await loadGalleryImages(editItem.sku);
      setImageCacheBust(Date.now());
    } catch (err) {
      console.error("Error deleting gallery image:", err);
    } finally {
      setGalleryUploading(false);
    }
  };

  const handleAddItem = async () => {
    setAddItemMessage(null);
    if (!newItem.name) {
      setAddItemMessage({ type: "error", text: "Product name is required." });
      setAddItemTab("details");
      return;
    }
    if (!newItem.price || isNaN(parseFloat(newItem.price)) || parseFloat(newItem.price) <= 0) {
      setAddItemMessage({ type: "error", text: "A valid price is required." });
      setAddItemTab("details");
      return;
    }
    setAddingItem(true);
    try {
      const stockPerLocation: { location_id: number; quantity: number }[] = [];
      const parPerLocation: { location_id: number; par_level: number }[] = [];
      for (const loc of locations) {
        const stockVal = parseFloat(newItem.stocks[loc.name] || "0");
        if (!isNaN(stockVal) && stockVal > 0) {
          stockPerLocation.push({ location_id: loc.id, quantity: stockVal });
        }
        const parVal = parseFloat(newItem.pars[loc.name] || "0");
        if (!isNaN(parVal) && parVal > 0) {
          parPerLocation.push({ location_id: loc.id, par_level: parVal });
        }
      }
      const response = await createItem({
        name: newItem.name,
        price: Math.round(parseFloat(newItem.price) * 100),
        sku: newItem.sku || undefined,
        category: newItem.category || undefined,
        stock_per_location: stockPerLocation.length > 0 ? stockPerLocation : undefined,
        par_per_location: parPerLocation.length > 0 ? parPerLocation : undefined,
        price_type: newItem.price_type || undefined,
        cost: newItem.cost ? Math.round(parseFloat(newItem.cost) * 100) : undefined,
        product_code: newItem.product_code || undefined,
        alternate_name: newItem.alternate_name || undefined,
        description: newItem.description || undefined,
        color_code: newItem.color_code || undefined,
        is_revenue: newItem.is_revenue,
        is_age_restricted: newItem.is_age_restricted,
        age_restriction_type: newItem.is_age_restricted && newItem.age_restriction_type ? newItem.age_restriction_type : undefined,
        age_restriction_min_age: newItem.is_age_restricted && newItem.age_restriction_min_age ? parseInt(newItem.age_restriction_min_age) : undefined,
        available: newItem.available,
        hidden: newItem.hidden,
        auto_manage: newItem.auto_manage,
        default_tax_rates: newItem.default_tax_rates,
      });
      // Check if any locations had errors
      const results = response.data?.results || [];
      const errors = results.filter((r: { status: string }) => r.status === "error");
      if (errors.length > 0 && errors.length === results.length) {
        setAddItemMessage({ type: "error", text: `Failed to create item: ${errors[0]?.error || "Unknown error"}` });
        return;
      }
      // Upload image if one was selected
      const itemSku = newItem.sku || response.data?.sku;
      if (itemSku && newItemImageFile) {
        try {
          const base64 = await handleFileToBase64(newItemImageFile);
          await uploadImage(itemSku, base64, newItemImageFile.type, newItem.name);
        } catch (imgErr) {
          console.error("Error uploading image:", imgErr);
        }
      }
      setShowAddItem(false);
      setNewItem({
        name: "", price: "", sku: "", category: "", stocks: {}, pars: {},
        price_type: "FIXED", cost: "", product_code: "", alternate_name: "",
        description: "", color_code: "", is_revenue: true, is_age_restricted: false,
        age_restriction_type: "Vitamin & Supplements", age_restriction_min_age: "21",
          available: true, hidden: false, auto_manage: true, default_tax_rates: true,
        });
        setNewItemImageFile(null);
      setNewItemImagePreview(null);
      setAddItemTab("details");
      setAddItemMessage(null);
      // Show success toast
      const createdCount = results.filter((r: { status: string }) => r.status === "created").length;
      setToast({ type: "success", text: `Item "${newItem.name}" created at ${createdCount} location(s). Syncing inventory...` });
      setTimeout(() => setToast(null), 6000);
      // Wait 2 seconds for Clover to index the new item before syncing
      await new Promise(resolve => setTimeout(resolve, 2000));
      await loadData();
    } catch (err: unknown) {
      console.error("Error adding item:", err);
      const errorMsg = err instanceof Error ? err.message : "Unknown error occurred";
      const axiosError = err as { response?: { data?: { detail?: string } } };
      const detail = axiosError?.response?.data?.detail || errorMsg;
      setAddItemMessage({ type: "error", text: `Failed to create item: ${detail}` });
    } finally {
      setAddingItem(false);
    }
  };

  const handleDeleteItem = async (item: InventoryItem) => {
    setDeleting(item.sku);
    try {
      await deleteItem(item.sku);
      setConfirmDelete(null);
      setEditItem(null);
      // Optimistically remove from state immediately
      setItems(prev => prev.filter(i => i.sku !== item.sku));
    } catch (err) {
      console.error("Error deleting item:", err);
      await loadData();
    } finally {
      setDeleting(null);
    }
  };

  const openEditModal = (item: InventoryItem) => {
    const stocks: Record<string, string> = {};
    const pars: Record<string, string> = {};
    for (const [locName, locData] of Object.entries(item.locations)) {
      stocks[locName] = locData.stock.toString();
      pars[locName] = locData.par_level !== null ? locData.par_level.toString() : "";
    }
    setEditForm({
      name: item.name,
      price: (item.price / 100).toFixed(2),
      stocks,
      pars,
      price_type: item.price_type || "FIXED",
      cost: item.cost ? (item.cost / 100).toFixed(2) : "",
      product_code: item.product_code || "",
      alternate_name: item.alternate_name || "",
      description: item.description || "",
      color_code: item.color_code || "",
      is_revenue: item.is_revenue !== undefined ? item.is_revenue : true,
      is_age_restricted: item.is_age_restricted || false,
      age_restriction_type: item.age_restriction_type || "Vitamin & Supplements",
      age_restriction_min_age: item.age_restriction_min_age ? item.age_restriction_min_age.toString() : "21",
      available: item.available !== undefined ? item.available : true,
      hidden: item.hidden || false,
      auto_manage: item.auto_manage || false,
      default_tax_rates: item.default_tax_rates !== undefined ? item.default_tax_rates : true,
      effect: productAttrsMap[item.sku]?.effect || "",
      strength: productAttrsMap[item.sku]?.strength || "",
      product_type: productAttrsMap[item.sku]?.product_type || "",
    });
    setEditTab("details");
    setSaveMessage(null);
    setEditImageFile(null);
    setEditImagePreview(item.has_image ? getImageUrl(item.sku, imageCacheBust) : null);
    setEditVariantAttrs([{ attribute_name: "", option_names: [""] }]);
    setKeepOriginal(false);
    setGalleryImages([]);
    loadGalleryImages(item.sku);
    setEditItem(item);
  };

  const handleAddVariantsToExisting = async () => {
    if (!editItem) return;
    const validAttrs = editVariantAttrs.filter(a => a.attribute_name.trim() && a.option_names.some(o => o.trim()));
    if (validAttrs.length === 0) {
      setSaveMessage({ type: "error", text: "At least one attribute with options is required." });
      return;
    }
    setAddingVariants(true);
    setSaveMessage(null);
    try {
      await addVariantsToItem({
        item_name: editItem.name,
        item_sku: editItem.sku,
        price: editItem.price,
        variants: validAttrs.map(a => ({
          attribute_name: a.attribute_name.trim(),
          option_names: a.option_names.filter(o => o.trim()).map(o => o.trim()),
        })),
        keep_original: keepOriginal,
      });
      setSaveMessage({ type: "success", text: "Variants created! Run a sync to see new items." });
      setEditVariantAttrs([{ attribute_name: "", option_names: [""] }]);
    } catch (err) {
      const axiosError = err as { response?: { data?: { detail?: string } } };
      const detail = axiosError?.response?.data?.detail || "Failed to add variants";
      setSaveMessage({ type: "error", text: detail });
    } finally {
      setAddingVariants(false);
    }
  };

  const handleSaveEdit = async () => {
    if (!editItem) return;
    setSaving(true);
    setSaveMessage(null);
    try {
      const priceInCents = Math.round(parseFloat(editForm.price) * 100);
      const stockUpdates: { location_id: number; quantity: number }[] = [];

      for (const [locName, locData] of Object.entries(editItem.locations)) {
        const newStock = parseFloat(editForm.stocks[locName] || "0");
        if (!isNaN(newStock) && newStock !== locData.stock) {
          stockUpdates.push({ location_id: locData.location_id, quantity: newStock });
        }
      }

      const updateData: Record<string, unknown> = {};

      if (editForm.name !== editItem.name) updateData.name = editForm.name;
      if (priceInCents !== editItem.price) updateData.price = priceInCents;
      if (stockUpdates.length > 0) updateData.stock_updates = stockUpdates;
      // Extended fields
      if (editForm.price_type) updateData.price_type = editForm.price_type;
      if (editForm.cost) updateData.cost = Math.round(parseFloat(editForm.cost) * 100);
      if (editForm.product_code) updateData.product_code = editForm.product_code;
      if (editForm.alternate_name) updateData.alternate_name = editForm.alternate_name;
      if (editForm.description) updateData.description = editForm.description;
      if (editForm.color_code) updateData.color_code = editForm.color_code;
      updateData.is_revenue = editForm.is_revenue;
      updateData.available = editForm.available;
      updateData.hidden = editForm.hidden;
      updateData.auto_manage = editForm.auto_manage;
      updateData.default_tax_rates = editForm.default_tax_rates;
      updateData.is_age_restricted = editForm.is_age_restricted;
      if (editForm.is_age_restricted && editForm.age_restriction_type) {
        updateData.age_restriction_type = editForm.age_restriction_type;
        updateData.age_restriction_min_age = parseInt(editForm.age_restriction_min_age) || 21;
      }

      // Save PAR level changes
      const parPromises: Promise<unknown>[] = [];
      for (const [locName, locData] of Object.entries(editItem.locations)) {
        const newPar = editForm.pars[locName];
        const oldPar = locData.par_level;
        if (newPar !== undefined && newPar !== "") {
          const newParNum = parseFloat(newPar);
          if (!isNaN(newParNum) && newParNum !== oldPar) {
            parPromises.push(setParLevel(editItem.sku, locData.location_id, newParNum));
          }
        } else if (newPar === "" && oldPar !== null) {
          parPromises.push(setParLevel(editItem.sku, locData.location_id, 0));
        }
      }

      // Check if effect/strength changed
      const oldAttrs = productAttrsMap[editItem.sku] || {};
      const effectChanged = editForm.effect !== (oldAttrs.effect || "");
      const strengthChanged = editForm.strength !== (oldAttrs.strength || "");
      const typeChanged = editForm.product_type !== (oldAttrs.product_type || "");
      const hasAttrChanges = effectChanged || strengthChanged || typeChanged;

      if (Object.keys(updateData).length === 0 && parPromises.length === 0 && !hasAttrChanges) {
        setSaveMessage({ type: "success", text: "No changes to save." });
        setSaving(false);
        return;
      }

      if (parPromises.length > 0) {
        await Promise.all(parPromises);
      }

      // Save effect/strength attributes to local DB
      if (hasAttrChanges) {
        await updateProductAttributes(editItem.sku, {
          effect: editForm.effect || null,
          strength: editForm.strength || null,
          product_type: editForm.product_type || null,
          product_name: editItem.name,
        });
        // Update local cache
        setProductAttrsMap(prev => ({
          ...prev,
          [editItem.sku]: {
            effect: editForm.effect || undefined,
            strength: editForm.strength || undefined,
            product_type: editForm.product_type || undefined,
          },
        }));
      }

      if (Object.keys(updateData).length === 0) {
        setSaveMessage({ type: "success", text: hasAttrChanges ? "Product attributes saved!" : "PAR levels updated!" });
        await loadData();
        setSaving(false);
        return;
      }

      const res = await updateItem(editItem.sku, updateData);
      const results = res.data.results || [];
      const errors = results.filter((r: { status: string }) => r.status === "error");

      if (errors.length > 0) {
        const firstError = (errors[0] as { error?: string }).error || "";
        const locationNames = errors.map((e: { location: string }) => e.location).join(", ");
        setSaveMessage({
          type: "error",
          text: firstError ? `${firstError} (${locationNames})` : `Updated with errors at: ${locationNames}`,
        });
      } else {
        setSaveMessage({ type: "success", text: hasAttrChanges ? "Changes & attributes saved!" : "Changes saved to Clover!" });
        await loadData();
      }
    } catch (err) {
      console.error("Error saving item:", err);
      setSaveMessage({ type: "error", text: "Failed to save changes. Please try again." });
    } finally {
      setSaving(false);
    }
  };

  const handleDownloadExcel = () => {
    const selectedData = filteredItems.filter((i) => selectedItems.has(i.id));
    const dataToExport = selectedData.length > 0 ? selectedData : filteredItems;
    const headers = ["Product Name", "SKU", "Price", "Category"];
    locations.forEach((loc) => { headers.push(`${loc.name} Stock`); headers.push(`${loc.name} PAR`); });
    const rows = dataToExport.map((item) => {
      const row: string[] = [item.name, item.sku, `$${(item.price / 100).toFixed(2)}`, item.categories.join("; ")];
      locations.forEach((loc) => {
        const locData = item.locations[loc.name];
        row.push(locData ? locData.stock.toString() : "");
        row.push(locData && locData.par_level !== null ? locData.par_level.toString() : "");
      });
      return row;
    });
    const csvContent = [headers.join(","), ...rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(","))].join("\n");
    const BOM = "\uFEFF";
    const blob = new Blob([BOM + csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `inventory_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const toggleSort = (field: "name" | "sku" | "stock" | "price" | "category" | "par", locName?: string) => {
    if (sortField === field && sortLocation === (locName || "")) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDir("asc");
      setSortLocation(locName || "");
    }
  };

  const SortIcon = ({ field }: { field: string }) => {
    if (sortField !== field) return null;
    return sortDir === "asc" ? (
      <ChevronUp className="w-3 h-3 inline ml-1" />
    ) : (
      <ChevronDown className="w-3 h-3 inline ml-1" />
    );
  };

  const stockColor = (stock: number, par: number | null) => {
    if (stock <= 0) return "text-red-600 bg-red-50";
    if (par !== null && stock <= par) return "text-orange-600 bg-orange-50";
    if (par !== null && stock <= par * 1.5) return "text-yellow-600 bg-yellow-50";
    return "text-green-700 bg-green-50";
  };

  // Multi-select handlers
  const toggleSelectAll = () => {
    if (selectedItems.size === filteredItems.length) {
      setSelectedItems(new Set());
    } else {
      setSelectedItems(new Set(filteredItems.map((i) => i.id)));
    }
  };

  const toggleSelectItem = (id: string) => {
    const next = new Set(selectedItems);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedItems(next);
  };

  const handleBulkDelete = async () => {
    setBulkDeleting(true);
    try {
      const skusToDelete = items.filter(i => selectedItems.has(i.id)).map(i => i.sku);
      const uniqueSkus = [...new Set(skusToDelete)];
      await bulkDeleteItems(uniqueSkus);
      // Optimistically remove from state immediately
      const deletedSkuSet = new Set(uniqueSkus);
      setItems(prev => prev.filter(i => !deletedSkuSet.has(i.sku)));
      setSelectedItems(new Set());
      setShowBulkConfirm(false);
    } catch (err) {
      console.error("Error bulk deleting:", err);
      await loadData();
    } finally {
      setBulkDeleting(false);
    }
  };

  const handleBulkAssignCategory = async () => {
    if (!bulkCategoryName.trim()) return;
    setAssigningCategory(true);
    try {
      const skusToAssign = items.filter(i => selectedItems.has(i.id)).map(i => i.sku);
      const resp = await bulkAssignCategory([...new Set(skusToAssign)], bulkCategoryName.trim());
      const data = resp.data;
      setToast({ type: "success", text: `Category "${data.category}" assigned to ${data.total_assigned} item(s) across ${data.results?.length || 0} location(s)` });
      setTimeout(() => setToast(null), 6000);
      setShowBulkCategory(false);
      setBulkCategoryName("");
      setSelectedItems(new Set());
      await loadData();
    } catch (err) {
      console.error("Error assigning category:", err);
      const axiosError = err as { response?: { data?: { detail?: string } } };
      setToast({ type: "error", text: axiosError?.response?.data?.detail || "Failed to assign category" });
      setTimeout(() => setToast(null), 5000);
    } finally {
      setAssigningCategory(false);
    }
  };

  const handleBulkAutoManage = async () => {
    if (!confirm("WARNING: Enabling Auto-Manage causes Clover to auto-hide items from POS when stock reaches 0. This can block scanning!\n\nOnly enable this if you want Clover to auto-deduct stock on sales. You may need to run 'Fix POS Scanning' afterward if items become unscannable.\n\nContinue?")) return;
    setAutoManaging(true);
    try {
      const resp = await bulkAutoManage(true);
      const data = resp.data;
      setToast({ type: "success", text: `Auto-Manage enabled: ${data.total_updated} items updated across ${data.results?.length || 0} location(s)` });
      setTimeout(() => setToast(null), 6000);
      await loadData();
    } catch (err) {
      console.error("Error enabling auto-manage:", err);
      setToast({ type: "error", text: "Failed to enable auto-manage stock." });
      setTimeout(() => setToast(null), 5000);
    } finally {
      setAutoManaging(false);
    }
  };

  const [fixingPos, setFixingPos] = useState(false);
  const handleFixPos = async () => {
    if (!confirm("Fix POS Scanning: This will disable Auto-Manage and make ALL items visible/scannable at POS across all locations.\n\nThis fixes the issue where items with 0 stock can't be scanned.\n\nContinue?")) return;
    setFixingPos(true);
    try {
      const resp = await fixPosScanning();
      const data = resp.data;
      setToast({ type: "success", text: data.message || `Fixed ${data.total_fixed} items. All items now scannable at POS.` });
      setTimeout(() => setToast(null), 8000);
      await loadData();
    } catch (err) {
      console.error("Error fixing POS:", err);
      setToast({ type: "error", text: "Failed to fix POS scanning. Try again." });
      setTimeout(() => setToast(null), 5000);
    } finally {
      setFixingPos(false);
    }
  };

  const handlePushToLocation = async (sku: string, locationId: number, locationName: string) => {
    setPushingToLocation(locationId);
    try {
      await pushItemToLocation(sku, locationId, 0);
      setSaveMessage({ type: "success", text: `Item created at ${locationName}! Syncing...` });
      await loadData();
      // Re-open the edit modal with refreshed data
      const updatedItem = items.find(i => i.sku === sku);
      if (updatedItem) {
        openEditModal(updatedItem);
      }
    } catch (err) {
      console.error("Error pushing item to location:", err);
      const axiosError = err as { response?: { data?: { detail?: string } } };
      const detail = axiosError?.response?.data?.detail || "Failed to create item at location";
      setSaveMessage({ type: "error", text: detail });
    } finally {
      setPushingToLocation(null);
    }
  };

  // Transfer stock state
  const [showTransfer, setShowTransfer] = useState(false);
  const [transferSearch, setTransferSearch] = useState("");
  const [transferItems, setTransferItems] = useState<Map<string, { item: InventoryItem; quantity: string }>>(new Map());
  const [transferFromId, setTransferFromId] = useState(0);
  const [transferToId, setTransferToId] = useState(0);
  const [transferring, setTransferring] = useState(false);
  const [transferResults, setTransferResults] = useState<{ name: string; status: string }[]>([]);

  const transferSearchResults = useMemo(() => {
    if (!transferSearch || transferSearch.length < 2) return [];
    const q = transferSearch.toLowerCase();
    return items.filter(i =>
      i.name.toLowerCase().includes(q) || i.sku.toLowerCase().includes(q)
    ).slice(0, 50);
  }, [transferSearch, items]);

  const toggleTransferItem = (item: InventoryItem) => {
    const next = new Map(transferItems);
    if (next.has(item.id)) {
      next.delete(item.id);
    } else {
      next.set(item.id, { item, quantity: "1" });
    }
    setTransferItems(next);
  };

  const setTransferQuantity = (id: string, qty: string) => {
    const next = new Map(transferItems);
    const entry = next.get(id);
    if (entry) {
      next.set(id, { ...entry, quantity: qty });
      setTransferItems(next);
    }
  };

  const handleTransferStock = async () => {
    if (transferItems.size === 0 || !transferFromId || !transferToId) return;
    if (transferFromId === transferToId) {
      setToast({ type: "error", text: "Source and destination must be different locations" });
      setTimeout(() => setToast(null), 4000);
      return;
    }
    setTransferring(true);
    setTransferResults([]);
    const results: { name: string; status: string }[] = [];
    for (const [, { item, quantity }] of transferItems) {
      const qty = parseFloat(quantity);
      if (!qty || qty <= 0) {
        results.push({ name: item.name, status: "Skipped (invalid quantity)" });
        continue;
      }
      try {
        const resp = await transferStock(item.sku, transferFromId, transferToId, qty);
        const d = resp.data;
        results.push({ name: d.item_name, status: `Transferred ${d.quantity}` });
      } catch (err) {
        const axiosError = err as { response?: { data?: { detail?: string } } };
        results.push({ name: item.name, status: axiosError?.response?.data?.detail || "Failed" });
      }
    }
    setTransferResults(results);
    const successCount = results.filter(r => r.status.startsWith("Transferred")).length;
    setToast({ type: successCount > 0 ? "success" : "error", text: `${successCount} of ${results.length} items transferred` });
    setTimeout(() => setToast(null), 6000);
    setTransferring(false);
    if (successCount > 0) await loadData();
  };

  const resetTransferModal = () => {
    setShowTransfer(false);
    setTransferSearch("");
    setTransferItems(new Map());
    setTransferFromId(0);
    setTransferToId(0);
    setTransferResults([]);
  };

  // Bulk image assignment state
  const [showBulkImage, setShowBulkImage] = useState(false);
  const [bulkImageKeyword, setBulkImageKeyword] = useState("");
  const [bulkImageFile, setBulkImageFile] = useState<File | null>(null);
  const [bulkImagePreview, setBulkImagePreview] = useState<string | null>(null);
  const [assigningImages, setAssigningImages] = useState(false);
  const [bulkImageResult, setBulkImageResult] = useState<{ assigned: number; products: { sku: string; name: string }[] } | null>(null);
  const [bulkImageMatches, setBulkImageMatches] = useState<{ sku: string; name: string }[]>([]);
  const [bulkImageSelected, setBulkImageSelected] = useState<Set<string>>(new Set());

  const handleBulkImageFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setBulkImageFile(file);
    const reader = new FileReader();
    reader.onload = () => setBulkImagePreview(reader.result as string);
    reader.readAsDataURL(file);
  };

  // Preview matching products client-side when keyword changes
  const handleBulkImagePreview = () => {
    if (!bulkImageKeyword || bulkImageKeyword.length < 2) {
      setBulkImageMatches([]);
      setBulkImageSelected(new Set());
      return;
    }
    const kw = bulkImageKeyword.toLowerCase();
    const seen = new Set<string>();
    const matches: { sku: string; name: string }[] = [];
    for (const item of items) {
      if (item.name.toLowerCase().includes(kw) && !seen.has(item.sku)) {
        seen.add(item.sku);
        matches.push({ sku: item.sku, name: item.name });
      }
    }
    matches.sort((a, b) => a.name.localeCompare(b.name));
    setBulkImageMatches(matches);
    setBulkImageSelected(new Set(matches.map((m) => m.sku)));
  };

  const toggleBulkImageItem = (sku: string) => {
    setBulkImageSelected((prev) => {
      const next = new Set(prev);
      if (next.has(sku)) next.delete(sku);
      else next.add(sku);
      return next;
    });
  };

  const toggleAllBulkImageItems = () => {
    if (bulkImageSelected.size === bulkImageMatches.length) {
      setBulkImageSelected(new Set());
    } else {
      setBulkImageSelected(new Set(bulkImageMatches.map((m) => m.sku)));
    }
  };

  const handleBulkAssignImages = async () => {
    if (!bulkImageKeyword || !bulkImageFile || bulkImageSelected.size === 0) return;
    setAssigningImages(true);
    setBulkImageResult(null);
    try {
      const reader = new FileReader();
      const base64Promise = new Promise<string>((resolve) => {
        reader.onload = () => {
          const result = reader.result as string;
          resolve(result.split(",")[1]); // strip data:image/...;base64,
        };
        reader.readAsDataURL(bulkImageFile);
      });
      const base64 = await base64Promise;
      const selectedSkus = Array.from(bulkImageSelected);
      const resp = await bulkAssignImages(bulkImageKeyword, base64, bulkImageFile.type || "image/png", selectedSkus);
      const d = resp.data;
      setBulkImageResult({ assigned: d.assigned, products: d.products || [] });
      if (d.assigned > 0) {
        setToast({ type: "success", text: `Image assigned to ${d.assigned} of ${bulkImageMatches.length} products` });
        setTimeout(() => setToast(null), 6000);
        setImageCacheBust(Date.now());
        // Force sync (not cached) so has_image flags are fresh
        loadData(true).catch(() => {});
      } else {
        setToast({ type: "error", text: `No products found matching "${bulkImageKeyword}"` });
        setTimeout(() => setToast(null), 4000);
      }
    } catch (err) {
      const axiosError = err as { response?: { data?: { detail?: string } } };
      setToast({ type: "error", text: axiosError?.response?.data?.detail || "Failed to assign images" });
      setTimeout(() => setToast(null), 5000);
    } finally {
      setAssigningImages(false);
    }
  };

  const handleBatchStockSave = async () => {
    // Filter to only changes where value actually changed
    const changedEntries = Array.from(pendingStockChanges.values()).filter(
      (c) => parseFloat(c.value) !== c.originalValue && !isNaN(parseFloat(c.value))
    );
    if (changedEntries.length === 0) {
      setPendingStockChanges(new Map());
      setToast({ type: "success", text: "No changes to save." });
      setTimeout(() => setToast(null), 3000);
      return;
    }
    setSavingStock(true);
    try {
      const updates = changedEntries.map((c) => ({
        sku: c.sku,
        location_id: c.locationId,
        quantity: parseFloat(c.value),
        item_name: c.itemName,
        clover_item_id: c.cloverItemId,
      }));
      const resp = await bulkStockUpdate(updates);
      const data = resp.data;
      setPendingStockChanges(new Map());
      setToast({ type: "success", text: `${data.total_updated} stock update(s) saved!` });
      setTimeout(() => setToast(null), 4000);
      await loadData();
    } catch (err) {
      console.error("Batch stock save error:", err);
      setToast({ type: "error", text: "Failed to save stock changes" });
      setTimeout(() => setToast(null), 5000);
    } finally {
      setSavingStock(false);
    }
  };

  const handleSyncRefunds = async () => {
    setSyncingRefunds(true);
    try {
      const resp = await syncRefunds();
      const data = resp.data;
      if (data.refunds_processed > 0) {
        setToast({ type: "success", text: `${data.refunds_processed} refund(s) synced — stock updated!` });
        await loadData();
      } else {
        setToast({ type: "success", text: "No new refunds to sync." });
      }
    } catch (err) {
      console.error("Error syncing refunds:", err);
      setToast({ type: "error", text: "Failed to sync refunds" });
    } finally {
      setSyncingRefunds(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 text-green-600 animate-spin" />
        <span className="ml-3 text-gray-600">Loading inventory...</span>
      </div>
    );
  }

  if (loadError && items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <div className="text-red-500 text-lg font-medium">{loadError}</div>
        <button
          onClick={() => { setLoading(true); setLoadError(null); loadData(true); }}
          className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Toast Notification */}
      {toast && (
        <div className={`fixed top-4 right-4 z-[60] px-4 py-3 rounded-lg shadow-lg text-sm font-medium transition-all ${toast.type === "success" ? "bg-green-600 text-white" : "bg-red-600 text-white"}`}>
          {toast.text}
        </div>
      )}
      {/* Floating Save Bar for batch stock edits */}
      {pendingStockChanges.size > 0 && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[60] bg-amber-600 text-white px-6 py-3 rounded-xl shadow-2xl flex items-center gap-4 animate-bounce-once">
          <span className="text-sm font-medium">
            {pendingStockChanges.size} stock edit{pendingStockChanges.size > 1 ? "s" : ""} pending
          </span>
          <button
            onClick={handleBatchStockSave}
            disabled={savingStock}
            className="flex items-center gap-2 px-4 py-1.5 bg-white text-amber-700 rounded-lg text-sm font-bold hover:bg-amber-50 disabled:opacity-50 transition-colors"
          >
            <Save className="w-4 h-4" />
            {savingStock ? "Saving..." : "Save All"}
          </button>
          <button
            onClick={() => setPendingStockChanges(new Map())}
            disabled={savingStock}
            className="text-amber-200 hover:text-white text-sm disabled:opacity-50"
          >
            Discard
          </button>
        </div>
      )}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Inventory</h2>
          <p className="text-gray-500 text-sm">{filteredItems.length} of {items.length} products</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowAddItem(true)}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 text-sm transition-colors"
          >
            <Plus className="w-4 h-4" /> Add Item
          </button>
          <button
            onClick={handleFixPos}
            disabled={fixingPos}
            className="flex items-center gap-2 px-4 py-2 border border-emerald-300 bg-emerald-50 text-emerald-700 rounded-lg hover:bg-emerald-100 text-sm disabled:opacity-50 transition-colors"
            title="Fix POS scanning issues: disable auto-manage and make all items scannable"
          >
            <Package className={`w-4 h-4 ${fixingPos ? "animate-bounce" : ""}`} />
            {fixingPos ? "Fixing..." : "Fix POS Scanning"}
          </button>
          <button
            onClick={handleBulkAutoManage}
            disabled={autoManaging}
            className="flex items-center gap-2 px-4 py-2 border border-blue-300 bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100 text-sm disabled:opacity-50 transition-colors"
            title="WARNING: Enable auto-manage stock. May cause items to become unscannable at POS when stock reaches 0."
          >
            <Settings className={`w-4 h-4 ${autoManaging ? "animate-spin" : ""}`} />
            {autoManaging ? "Enabling..." : "Auto-Manage All"}
          </button>
          <button
            onClick={() => setShowTransfer(true)}
            className="flex items-center gap-2 px-4 py-2 border border-purple-300 bg-purple-50 text-purple-700 rounded-lg hover:bg-purple-100 text-sm transition-colors"
            title="Transfer stock between locations"
          >
            <ArrowRightLeft className="w-4 h-4" />
            Transfer
          </button>
          <button
            onClick={() => { setShowBulkImage(true); setBulkImageResult(null); }}
            className="flex items-center gap-2 px-4 py-2 border border-pink-300 bg-pink-50 text-pink-700 rounded-lg hover:bg-pink-100 text-sm transition-colors"
            title="Assign one image to all products matching a keyword"
          >
            <Images className="w-4 h-4" />
            Bulk Images
          </button>
          <button
            onClick={handleSyncRefunds}
            disabled={syncingRefunds}
            className="flex items-center gap-2 px-4 py-2 border border-orange-300 bg-orange-50 text-orange-700 rounded-lg hover:bg-orange-100 text-sm disabled:opacity-50 transition-colors"
            title="Sync refunds from POS and add returned items back to stock"
          >
            <RefreshCw className={`w-4 h-4 ${syncingRefunds ? "animate-spin" : ""}`} />
            {syncingRefunds ? "Syncing..." : "Sync Refunds"}
          </button>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
            Sync
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by name or SKU..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none text-sm"
          />
        </div>
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
        >
          <option value="all">All Categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select
          value={locationFilter}
          onChange={(e) => setLocationFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
        >
          <option value="all">All Locations</option>
          {locations.map((loc) => (
            <option key={loc.id} value={loc.name}>{loc.name}</option>
          ))}
        </select>
      </div>

      {/* Add Item Modal */}
      {showAddItem && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-green-100 rounded-lg">
                  <Plus className="w-5 h-5 text-green-600" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold">Add New Item</h3>
                  <p className="text-xs text-gray-400">Item will be created at all locations</p>
                </div>
              </div>
              <button onClick={() => setShowAddItem(false)} className="p-1 hover:bg-gray-100 rounded-lg">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>

            {/* Error/Success Message */}
            {addItemMessage && (
              <div className={`mb-4 p-3 rounded-lg text-sm ${addItemMessage.type === "error" ? "bg-red-50 text-red-700 border border-red-200" : "bg-green-50 text-green-700 border border-green-200"}`}>
                {addItemMessage.text}
              </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 mb-4 overflow-x-auto border-b border-gray-200 pb-px">
              {[
                { id: "details", label: "Details" },
                { id: "variants", label: "Variants" },
                { id: "online", label: "Online Ordering" },
                { id: "taxes", label: "Taxes & Fees" },
                { id: "categories", label: "Categories" },
                { id: "cost", label: "Cost" },
                { id: "tracking", label: "Item Tracking" },
                { id: "image", label: "Image" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setAddItemTab(tab.id)}
                  className={`px-3 py-2 text-xs font-medium whitespace-nowrap rounded-t-lg transition-colors ${
                    addItemTab === tab.id
                      ? "bg-green-50 text-green-700 border-b-2 border-green-600"
                      : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Tab Content */}
            <div className="space-y-4">
              {/* Details Tab */}
              {addItemTab === "details" && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Product Name *</label>
                    <input
                      type="text"
                      value={newItem.name}
                      onChange={(e) => setNewItem({ ...newItem, name: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                      placeholder="Product name"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Price ($) *</label>
                      <input
                        type="number"
                        step="0.01"
                        value={newItem.price}
                        onChange={(e) => setNewItem({ ...newItem, price: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                        placeholder="0.00"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Price Type</label>
                      <select
                        value={newItem.price_type}
                        onChange={(e) => setNewItem({ ...newItem, price_type: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                      >
                        <option value="FIXED">Fixed</option>
                        <option value="VARIABLE">Variable</option>
                        <option value="PER_UNIT">Per Unit</option>
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Item Color</label>
                    <select
                      value={newItem.color_code}
                      onChange={(e) => setNewItem({ ...newItem, color_code: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                    >
                      <option value="">None</option>
                      <option value="#e74c3c">Red</option>
                      <option value="#e67e22">Orange</option>
                      <option value="#f1c40f">Yellow</option>
                      <option value="#2ecc71">Green</option>
                      <option value="#3498db">Blue</option>
                      <option value="#9b59b6">Purple</option>
                      <option value="#1abc9c">Teal</option>
                      <option value="#95a5a6">Gray</option>
                    </select>
                  </div>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newItem.is_age_restricted}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setNewItem({ ...newItem, is_age_restricted: checked });
                        if (checked && ageRestrictionTypes.length === 0) {
                          getAgeRestrictionTypes().then(res => setAgeRestrictionTypes(res.data.types)).catch(() => {});
                        }
                      }}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">This is an age-restricted item</span>
                      <p className="text-xs text-gray-400">This item requires additional confirmation or approval during the item fulfillment process. For example, alcoholic beverages.</p>
                    </div>
                  </label>
                  {newItem.is_age_restricted && (
                    <div className="grid grid-cols-2 gap-3 pl-4 border-l-2 border-green-200">
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Type of restriction *</label>
                        <select
                          value={newItem.age_restriction_type}
                          onChange={(e) => setNewItem({ ...newItem, age_restriction_type: e.target.value })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                        >
                          <option value="">Select</option>
                          <option value="Alcohol">Alcohol</option>
                          <option value="Tobacco">Tobacco</option>
                          <option value="OTC drugs">OTC drugs</option>
                          <option value="Vitamin & Supplements">Vitamin & Supplements</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Min. Age *</label>
                        <input
                          type="number"
                          value={newItem.age_restriction_min_age}
                          onChange={(e) => setNewItem({ ...newItem, age_restriction_min_age: e.target.value })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                          placeholder="21"
                          min="1"
                        />
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* Variants Tab */}
              {addItemTab === "variants" && (
                <>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={hasVariants}
                      onChange={(e) => {
                        setHasVariants(e.target.checked);
                        if (e.target.checked && variantAttributes.length === 0) {
                          setVariantAttributes([{ attribute_name: "", option_names: [""] }]);
                        }
                      }}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Add items with variants</span>
                      <p className="text-xs text-gray-400">Variants allow you to create nearly identical items that vary only by one or more attributes.</p>
                    </div>
                  </label>

                  {hasVariants && (
                    <div className="space-y-4 mt-2">
                      <div className="flex items-center gap-2 mb-1">
                        <Layers className="w-4 h-4 text-green-600" />
                        <h4 className="text-sm font-semibold text-gray-700">Add variants</h4>
                      </div>

                      {variantAttributes.map((attr, attrIdx) => (
                        <div key={attrIdx} className="border border-gray-200 rounded-lg p-4 bg-gray-50">
                          <div className="flex items-center justify-between mb-3">
                            <label className="block text-sm font-medium text-gray-700">Select attribute</label>
                            {variantAttributes.length > 1 && (
                              <button
                                onClick={() => setVariantAttributes(variantAttributes.filter((_, i) => i !== attrIdx))}
                                className="text-red-500 hover:text-red-700 p-1"
                              >
                                <X className="w-4 h-4" />
                              </button>
                            )}
                          </div>
                          <select
                            value={attr.attribute_name}
                            onChange={(e) => {
                              const updated = [...variantAttributes];
                              updated[attrIdx] = { ...updated[attrIdx], attribute_name: e.target.value };
                              setVariantAttributes(updated);
                            }}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none mb-3"
                          >
                            <option value="">Select attribute</option>
                            <option value="Size">Size</option>
                            <option value="Color">Color</option>
                            <option value="Flavor">Flavor</option>
                            <option value="Strength">Strength</option>
                            <option value="Weight">Weight</option>
                            <option value="Custom">Custom attribute</option>
                          </select>

                          {attr.attribute_name === "Custom" && (
                            <input
                              type="text"
                              placeholder="Custom attribute name"
                              value={attr.attribute_name === "Custom" ? "" : attr.attribute_name}
                              onChange={(e) => {
                                const updated = [...variantAttributes];
                                updated[attrIdx] = { ...updated[attrIdx], attribute_name: e.target.value || "Custom" };
                                setVariantAttributes(updated);
                              }}
                              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none mb-3"
                            />
                          )}

                          <label className="block text-sm font-medium text-gray-700 mb-2">Options</label>
                          <p className="text-xs text-gray-400 mb-2">Options help differentiate similar but unique items. For example, small, medium, and large.</p>
                          {attr.option_names.map((opt, optIdx) => (
                            <div key={optIdx} className="flex items-center gap-2 mb-2">
                              <input
                                type="text"
                                value={opt}
                                onChange={(e) => {
                                  const updated = [...variantAttributes];
                                  const newOptions = [...updated[attrIdx].option_names];
                                  newOptions[optIdx] = e.target.value;
                                  updated[attrIdx] = { ...updated[attrIdx], option_names: newOptions };
                                  setVariantAttributes(updated);
                                }}
                                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                                placeholder="Option name"
                              />
                              {attr.option_names.length > 1 && (
                                <button
                                  onClick={() => {
                                    const updated = [...variantAttributes];
                                    const newOptions = updated[attrIdx].option_names.filter((_, i) => i !== optIdx);
                                    updated[attrIdx] = { ...updated[attrIdx], option_names: newOptions };
                                    setVariantAttributes(updated);
                                  }}
                                  className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded"
                                >
                                  <Minus className="w-4 h-4" />
                                </button>
                              )}
                            </div>
                          ))}
                          <button
                            onClick={() => {
                              const updated = [...variantAttributes];
                              updated[attrIdx] = { ...updated[attrIdx], option_names: [...updated[attrIdx].option_names, ""] };
                              setVariantAttributes(updated);
                            }}
                            className="flex items-center gap-1.5 text-green-600 hover:text-green-700 text-sm font-medium mt-1"
                          >
                            <Plus className="w-3.5 h-3.5" />
                            Add another option
                          </button>
                        </div>
                      ))}

                      <button
                        onClick={() => setVariantAttributes([...variantAttributes, { attribute_name: "", option_names: [""] }])}
                        className="flex items-center gap-1.5 text-green-600 hover:text-green-700 text-sm font-medium px-1"
                      >
                        <Plus className="w-4 h-4" />
                        Add another attribute
                      </button>

                      {/* Preview of combinations */}
                      {(() => {
                        const validAttrs = variantAttributes.filter(v => v.attribute_name && v.attribute_name !== "Custom" && v.option_names.some(o => o.trim()));
                        if (validAttrs.length === 0) return null;
                        const optionArrays = validAttrs.map(v => v.option_names.filter(o => o.trim()));
                        const combos: string[][] = optionArrays.reduce<string[][]>(
                          (acc, opts) => acc.flatMap(combo => opts.map(opt => [...combo, opt])),
                          [[]]
                        );
                        return (
                          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mt-2">
                            <p className="text-xs font-semibold text-green-700 mb-2">
                              Preview: {combos.length} variant{combos.length !== 1 ? "s" : ""} will be created
                            </p>
                            <div className="space-y-1 max-h-32 overflow-y-auto">
                              {combos.slice(0, 20).map((combo, idx) => (
                                <p key={idx} className="text-xs text-green-600">
                                  {newItem.name || "Item"} — {combo.join(" / ")}
                                </p>
                              ))}
                              {combos.length > 20 && (
                                <p className="text-xs text-green-500 italic">...and {combos.length - 20} more</p>
                              )}
                            </div>
                          </div>
                        );
                      })()}
                    </div>
                  )}
                </>
              )}

              {/* Online Ordering Tab */}
              {addItemTab === "online" && (
                <>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={!newItem.hidden}
                      onChange={(e) => setNewItem({ ...newItem, hidden: !e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Show Online</span>
                      <p className="text-xs text-gray-400">Make this item visible on your online store</p>
                    </div>
                  </label>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Online Name</label>
                    <input
                      type="text"
                      value={newItem.alternate_name}
                      onChange={(e) => setNewItem({ ...newItem, alternate_name: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                      placeholder="Alternate name for online display"
                    />
                    <p className="text-xs text-gray-400 mt-1">Leave blank to use the product name</p>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                    <textarea
                      value={newItem.description}
                      onChange={(e) => setNewItem({ ...newItem, description: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                      placeholder="Product description for online orders"
                      rows={3}
                    />
                  </div>
                </>
              )}

              {/* Taxes & Fees Tab */}
              {addItemTab === "taxes" && (
                <>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newItem.default_tax_rates}
                      onChange={(e) => setNewItem({ ...newItem, default_tax_rates: e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Apply Default Tax Rates</span>
                      <p className="text-xs text-gray-400">Use your location&apos;s default tax rates for this item</p>
                    </div>
                  </label>
                  <p className="text-xs text-gray-500 px-1">Tax rates are configured per-location in your Clover dashboard.</p>
                </>
              )}

              {/* Categories Tab */}
              {addItemTab === "categories" && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Category</label>
                    <div className="flex gap-2">
                      <select
                        value={newItem.category}
                        onChange={(e) => setNewItem({ ...newItem, category: e.target.value })}
                        className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                      >
                        <option value="">No category</option>
                        {categories.map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                      <input
                        type="text"
                        value={categories.includes(newItem.category) ? "" : newItem.category}
                        onChange={(e) => setNewItem({ ...newItem, category: e.target.value })}
                        className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                        placeholder="Or type new category"
                      />
                    </div>
                    <p className="text-xs text-gray-400 mt-1">Select an existing category or create a new one.</p>
                  </div>
                </>
              )}

              {/* Cost Tab */}
              {addItemTab === "cost" && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Item Cost ($)</label>
                    <input
                      type="number"
                      step="0.01"
                      value={newItem.cost}
                      onChange={(e) => setNewItem({ ...newItem, cost: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                      placeholder="0.00"
                    />
                    <p className="text-xs text-gray-400 mt-1">Your cost for this item (used for profit reporting)</p>
                  </div>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={!newItem.is_revenue}
                      onChange={(e) => setNewItem({ ...newItem, is_revenue: !e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Non-Revenue Item</span>
                      <p className="text-xs text-gray-400">This item does not count toward revenue</p>
                    </div>
                  </label>
                </>
              )}

              {/* Item Tracking Tab */}
              {addItemTab === "tracking" && (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Product Code</label>
                      <input
                        type="text"
                        value={newItem.product_code}
                        onChange={(e) => setNewItem({ ...newItem, product_code: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                        placeholder="UPC / Barcode"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">SKU</label>
                      <input
                        type="text"
                        value={newItem.sku}
                        onChange={(e) => setNewItem({ ...newItem, sku: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                        placeholder="Optional"
                      />
                    </div>
                  </div>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newItem.available}
                      onChange={(e) => setNewItem({ ...newItem, available: e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Available for Sale</span>
                      <p className="text-xs text-gray-400">Item can be purchased by customers</p>
                    </div>
                  </label>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newItem.auto_manage}
                      onChange={(e) => setNewItem({ ...newItem, auto_manage: e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Auto-Manage Stock</span>
                      <p className="text-xs text-gray-400">Automatically decrement stock on each sale</p>
                    </div>
                  </label>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">Stock per Location</label>
                    <div className="space-y-2">
                      {locations.map((loc) => (
                        <div key={loc.id} className="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-lg">
                          <span className="text-sm font-medium text-gray-700">{loc.name}</span>
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              value={newItem.stocks[loc.name] || ""}
                              onChange={(e) =>
                                setNewItem({
                                  ...newItem,
                                  stocks: { ...newItem.stocks, [loc.name]: e.target.value },
                                })
                              }
                              className="w-24 px-3 py-1.5 border border-gray-300 rounded-lg text-sm text-center focus:ring-2 focus:ring-green-500 outline-none"
                              placeholder="0"
                            />
                            <span className="text-xs text-gray-400">units</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">PAR Levels</label>
                    <div className="space-y-2">
                      {locations.map((loc) => (
                        <div key={`par-${loc.id}`} className="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-lg">
                          <span className="text-sm font-medium text-gray-700">{loc.name}</span>
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              value={newItem.pars[loc.name] || ""}
                              onChange={(e) =>
                                setNewItem({
                                  ...newItem,
                                  pars: { ...newItem.pars, [loc.name]: e.target.value },
                                })
                              }
                              className="w-24 px-3 py-1.5 border border-gray-300 rounded-lg text-sm text-center focus:ring-2 focus:ring-green-500 outline-none"
                              placeholder="Not set"
                            />
                            <span className="text-xs text-gray-400">units</span>
                          </div>
                        </div>
                      ))}
                    </div>
                    <p className="text-xs text-gray-400 mt-1">Set minimum stock levels to trigger reorder alerts.</p>
                  </div>
                </>
              )}

              {/* Image Tab */}
              {addItemTab === "image" && (
                <>
                  {/* Upload Section */}
                  <div className="mb-4">
                    <div className="flex items-center gap-2 mb-2">
                      <Upload className="w-5 h-5 text-green-600" />
                      <span className="text-sm font-medium text-gray-700">Upload Image</span>
                    </div>
                    <p className="text-xs text-gray-500 mb-3">
                      Upload a product photo (JPG, PNG, WebP). This image will be stored in our app for e-commerce use.
                    </p>
                    <div className="flex items-center gap-3">
                      <label className="flex items-center gap-2 px-4 py-2 bg-green-50 text-green-700 rounded-lg cursor-pointer hover:bg-green-100 border border-green-200 text-sm font-medium">
                        <Upload className="w-4 h-4" />
                        Choose File
                        <input
                          type="file"
                          accept="image/*"
                          onChange={handleNewItemFileSelect}
                          className="hidden"
                        />
                      </label>
                      {newItemImageFile && (
                        <span className="text-xs text-gray-500">{newItemImageFile.name}</span>
                      )}
                    </div>
                    {newItemImagePreview && (
                      <div className="mt-3 relative inline-block">
                        <img src={newItemImagePreview} alt="Preview" className="w-32 h-32 object-cover rounded-lg border border-gray-200" />
                        <button
                          onClick={() => { setNewItemImageFile(null); setNewItemImagePreview(null); }}
                          className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-0.5"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </div>
                    )}
                  </div>

                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mt-3">
                    <p className="text-xs text-blue-700">
                      <strong>For e-commerce:</strong> Images are stored in our app and available for your online store. They won&apos;t appear in Clover POS.
                    </p>
                  </div>
                </>
              )}
            </div>

            <div className="flex gap-3 mt-6 pt-4 border-t border-gray-100">
              <button
                onClick={() => setShowAddItem(false)}
                className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 font-medium"
              >
                Cancel
              </button>
              <button
                onClick={hasVariants ? handleAddItemWithVariants : handleAddItem}
                disabled={addingItem || !newItem.name || !newItem.price}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 disabled:opacity-50 font-medium"
              >
                {addingItem ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    {hasVariants ? "Creating Variants..." : "Creating..."}
                  </>
                ) : (
                  <>
                    {hasVariants ? <Layers className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                    {hasVariants ? "Create Variants at All Locations" : "Add to All Locations"}
                  </>
                )}
              </button>
            </div>
            <p className="text-xs text-gray-400 text-center mt-2">
              Item will be pushed to Clover POS at all connected locations.
            </p>
          </div>
        </div>
      )}

      {/* Edit Item Modal */}
      {editItem && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-green-100 rounded-lg">
                  <Package className="w-5 h-5 text-green-600" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-gray-900">Edit Product</h3>
                  <p className="text-xs text-gray-400 font-mono">SKU: {editItem.sku}</p>
                </div>
              </div>
              <button onClick={() => setEditItem(null)} className="p-1 hover:bg-gray-100 rounded-lg">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>

            {saveMessage && (
              <div
                className={`mb-4 px-4 py-3 rounded-lg text-sm ${
                  saveMessage.type === "success"
                    ? "bg-green-50 text-green-700 border border-green-200"
                    : "bg-red-50 text-red-700 border border-red-200"
                }`}
              >
                {saveMessage.text}
              </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 mb-4 border-b border-gray-200 overflow-x-auto">
              {[
                { id: "details", label: "Details" },
                { id: "online", label: "Online Ordering" },
                { id: "taxes", label: "Taxes & Fees" },
                { id: "stock", label: "Stock & PAR" },
                { id: "cost", label: "Cost" },
                { id: "tracking", label: "Item Tracking" },
                { id: "history", label: "Change History" },
                { id: "image", label: "Image" },
                { id: "variants", label: "Add Variants" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setEditTab(tab.id)}
                  className={`px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
                    editTab === tab.id
                      ? "border-green-500 text-green-700"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="space-y-4 min-h-[300px]">
              {/* Details Tab */}
              {editTab === "details" && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Product Name *</label>
                    <input
                      type="text"
                      value={editForm.name}
                      onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                      disabled={!!editItem?.item_group_name}
                      className={`w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none ${editItem?.item_group_name ? "bg-gray-100 cursor-not-allowed" : ""}`}
                    />
                    {editItem?.item_group_name && (
                      <p className="text-xs text-amber-600 mt-1">Name is controlled by the variant group &quot;{editItem.item_group_name}&quot; in Clover and cannot be changed here.</p>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Price ($) *</label>
                      <input
                        type="number"
                        step="0.01"
                        value={editForm.price}
                        onChange={(e) => setEditForm({ ...editForm, price: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Price Type</label>
                      <select
                        value={editForm.price_type}
                        onChange={(e) => setEditForm({ ...editForm, price_type: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                      >
                        <option value="FIXED">Fixed</option>
                        <option value="VARIABLE">Variable</option>
                        <option value="PER_UNIT">Per Unit</option>
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Item Color</label>
                    <select
                      value={editForm.color_code}
                      onChange={(e) => setEditForm({ ...editForm, color_code: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                    >
                      <option value="">None</option>
                      <option value="#e74c3c">Red</option>
                      <option value="#e67e22">Orange</option>
                      <option value="#f1c40f">Yellow</option>
                      <option value="#2ecc71">Green</option>
                      <option value="#3498db">Blue</option>
                      <option value="#9b59b6">Purple</option>
                      <option value="#1abc9c">Teal</option>
                      <option value="#95a5a6">Gray</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Category</label>
                    <div className="flex gap-1 flex-wrap">
                      {editItem.categories.length > 0 ? (
                        editItem.categories.map((c) => (
                          <span key={c} className="inline-block px-3 py-1 bg-gray-100 text-gray-600 rounded-lg text-sm">{c}</span>
                        ))
                      ) : (
                        <span className="text-sm text-gray-400">No category</span>
                      )}
                    </div>
                    <p className="text-xs text-gray-400 mt-1">Categories can be managed from the Clover dashboard.</p>
                  </div>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={editForm.is_age_restricted}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setEditForm({ ...editForm, is_age_restricted: checked });
                        if (checked && ageRestrictionTypes.length === 0) {
                          getAgeRestrictionTypes().then(res => setAgeRestrictionTypes(res.data.types)).catch(() => {});
                        }
                      }}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">This is an age-restricted item</span>
                      <p className="text-xs text-gray-400">Requires additional confirmation or approval during fulfillment.</p>
                    </div>
                  </label>
                  {editForm.is_age_restricted && (
                    <div className="grid grid-cols-2 gap-3 pl-4 border-l-2 border-green-200">
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Type of restriction *</label>
                        <select
                          value={editForm.age_restriction_type}
                          onChange={(e) => setEditForm({ ...editForm, age_restriction_type: e.target.value })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                        >
                          <option value="">Select</option>
                          <option value="Alcohol">Alcohol</option>
                          <option value="Tobacco">Tobacco</option>
                          <option value="OTC drugs">OTC drugs</option>
                          <option value="Vitamin & Supplements">Vitamin & Supplements</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Min. Age *</label>
                        <input
                          type="number"
                          value={editForm.age_restriction_min_age}
                          onChange={(e) => setEditForm({ ...editForm, age_restriction_min_age: e.target.value })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                          placeholder="21"
                          min="1"
                        />
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* Online Ordering Tab */}
              {editTab === "online" && (
                <>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={!editForm.hidden}
                      onChange={(e) => setEditForm({ ...editForm, hidden: !e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Show Online</span>
                      <p className="text-xs text-gray-400">Display this item for online ordering</p>
                    </div>
                  </label>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Online Name</label>
                    <input
                      type="text"
                      value={editForm.alternate_name}
                      onChange={(e) => setEditForm({ ...editForm, alternate_name: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                      placeholder="Display name for online ordering"
                    />
                    <p className="text-xs text-gray-400 mt-1">Leave blank to use the product name.</p>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                    <textarea
                      value={editForm.description}
                      onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none resize-none"
                      placeholder="Item description for online ordering"
                      rows={3}
                    />
                  </div>
                  <div className="border-t border-gray-200 pt-4 mt-2">
                    <h4 className="text-sm font-semibold text-gray-700 mb-3">Product Attributes (for website filtering)</h4>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">How Do You Want to Feel?</label>
                        <select
                          value={editForm.effect}
                          onChange={(e) => setEditForm({ ...editForm, effect: e.target.value })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                        >
                          <option value="">Auto-detect</option>
                          <option value="Relax">Relax</option>
                          <option value="Sleep">Sleep</option>
                          <option value="Energy">Energy</option>
                          <option value="Focus">Focus</option>
                        </select>
                        <p className="text-xs text-gray-400 mt-1">Website &quot;How Do You Want to Feel?&quot; filter.</p>
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Strength</label>
                        <select
                          value={editForm.strength}
                          onChange={(e) => setEditForm({ ...editForm, strength: e.target.value })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                        >
                          <option value="">Auto-detect (by price)</option>
                          <option value="High">High</option>
                          <option value="Medium">Medium</option>
                          <option value="Low">Low</option>
                        </select>
                        <p className="text-xs text-gray-400 mt-1">Strength badge on product card.</p>
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Type</label>
                        <select
                          value={editForm.product_type}
                          onChange={(e) => setEditForm({ ...editForm, product_type: e.target.value })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-green-500 outline-none"
                        >
                          <option value="">Not set</option>
                          <option value="Hybrid">Hybrid</option>
                          <option value="Indica">Indica</option>
                          <option value="Sativa">Sativa</option>
                        </select>
                        <p className="text-xs text-gray-400 mt-1">Hybrid, Indica, or Sativa (HempVentory only).</p>
                      </div>
                    </div>
                  </div>
                </>
              )}

              {/* Taxes & Fees Tab */}
              {editTab === "taxes" && (
                <>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={editForm.default_tax_rates}
                      onChange={(e) => setEditForm({ ...editForm, default_tax_rates: e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Apply Default Tax Rates</span>
                      <p className="text-xs text-gray-400">Use the default tax rates configured in your Clover dashboard</p>
                    </div>
                  </label>
                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                    <p className="text-xs text-blue-700">
                      Custom tax rates can be configured in your Clover dashboard under <strong>Setup &gt; Tax Rates</strong>.
                    </p>
                  </div>
                </>
              )}

              {/* Stock & PAR Tab */}
              {editTab === "stock" && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">Stock by Location</label>
                    <div className="space-y-2">
                      {locations.map((loc) => {
                        const locData = editItem.locations[loc.name];
                        if (!locData) return (
                          <div key={loc.id} className="flex items-center justify-between px-3 py-2 bg-yellow-50 border border-yellow-200 rounded-lg">
                            <div>
                              <span className="text-sm text-gray-600">{loc.name}</span>
                              <p className="text-xs text-yellow-600">Not at this location</p>
                            </div>
                            <button
                              onClick={() => handlePushToLocation(editItem.sku, loc.id, loc.name)}
                              disabled={pushingToLocation === loc.id}
                              className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white text-xs font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
                            >
                              {pushingToLocation === loc.id ? (
                                <><RefreshCw className="w-3 h-3 animate-spin" /> Creating...</>
                              ) : (
                                <><Plus className="w-3 h-3" /> Create at {loc.name}</>
                              )}
                            </button>
                          </div>
                        );
                        return (
                          <div key={loc.id} className="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-lg">
                            <span className="text-sm font-medium text-gray-700">{loc.name}</span>
                            <div className="flex items-center gap-2">
                              <input
                                type="number"
                                value={editForm.stocks[loc.name] || "0"}
                                onChange={(e) =>
                                  setEditForm({
                                    ...editForm,
                                    stocks: { ...editForm.stocks, [loc.name]: e.target.value },
                                  })
                                }
                                className="w-24 px-3 py-1.5 border border-gray-300 rounded-lg text-sm text-center focus:ring-2 focus:ring-green-500 outline-none"
                              />
                              <span className="text-xs text-gray-400">units</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">PAR Levels</label>
                    <div className="space-y-2">
                      {locations.map((loc) => {
                        const locData = editItem.locations[loc.name];
                        if (!locData) return null;
                        return (
                          <div key={loc.id} className="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-lg">
                            <span className="text-sm font-medium text-gray-700">{loc.name}</span>
                            <div className="flex items-center gap-2">
                              <input
                                type="number"
                                value={editForm.pars[loc.name] ?? ""}
                                onChange={(e) =>
                                  setEditForm({
                                    ...editForm,
                                    pars: { ...editForm.pars, [loc.name]: e.target.value },
                                  })
                                }
                                className="w-24 px-3 py-1.5 border border-gray-300 rounded-lg text-sm text-center focus:ring-2 focus:ring-green-500 outline-none"
                                placeholder="Not set"
                              />
                              <span className="text-xs text-gray-400">units</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <p className="text-xs text-gray-400 mt-1">Set minimum stock levels to trigger reorder alerts.</p>
                  </div>
                </>
              )}

              {/* Cost Tab */}
              {editTab === "cost" && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Item Cost ($)</label>
                    <input
                      type="number"
                      step="0.01"
                      value={editForm.cost}
                      onChange={(e) => setEditForm({ ...editForm, cost: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                      placeholder="0.00"
                    />
                    <p className="text-xs text-gray-400 mt-1">Your cost to purchase or produce this item.</p>
                  </div>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={editForm.is_revenue}
                      onChange={(e) => setEditForm({ ...editForm, is_revenue: e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Revenue Item</span>
                      <p className="text-xs text-gray-400">Track this item as revenue in reports</p>
                    </div>
                  </label>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={editForm.auto_manage}
                      onChange={(e) => setEditForm({ ...editForm, auto_manage: e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Auto-Manage Stock</span>
                      <p className="text-xs text-gray-400">Automatically reduce stock when items are sold</p>
                    </div>
                  </label>
                </>
              )}

              {/* Item Tracking Tab */}
              {editTab === "tracking" && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Product Code / UPC</label>
                    <input
                      type="text"
                      value={editForm.product_code}
                      onChange={(e) => setEditForm({ ...editForm, product_code: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                      placeholder="Enter product code or UPC"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">SKU</label>
                    <input
                      type="text"
                      value={editItem.sku}
                      disabled
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 text-gray-500"
                    />
                    <p className="text-xs text-gray-400 mt-1">SKU cannot be changed after creation.</p>
                  </div>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={editForm.available}
                      onChange={(e) => setEditForm({ ...editForm, available: e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Available for Sale</span>
                      <p className="text-xs text-gray-400">Item can be sold at POS</p>
                    </div>
                  </label>
                  <label className="flex items-center gap-3 px-3 py-2.5 bg-gray-50 rounded-lg cursor-pointer">
                    <input
                      type="checkbox"
                      checked={editForm.auto_manage}
                      onChange={(e) => setEditForm({ ...editForm, auto_manage: e.target.checked })}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-700">Auto-Manage Stock</span>
                      <p className="text-xs text-gray-400">Automatically reduce stock when items are sold</p>
                    </div>
                  </label>
                </>
              )}

              {/* Change History Tab */}
              {editTab === "history" && (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-sm font-semibold text-gray-700">Stock Change History</h4>
                    <button
                      onClick={async () => {
                        if (!editItem) return;
                        setChangeHistoryLoading(true);
                        try {
                          const res = await getInventoryChanges({ sku: editItem.sku, limit: 50 });
                          setChangeHistory(res.data.changes || []);
                        } catch { setChangeHistory([]); }
                        setChangeHistoryLoading(false);
                      }}
                      className="text-xs text-green-600 hover:text-green-700 font-medium"
                    >
                      {changeHistoryLoading ? "Loading..." : "Refresh"}
                    </button>
                  </div>
                  {changeHistoryLoading ? (
                    <div className="flex justify-center py-8"><RefreshCw className="w-5 h-5 text-gray-400 animate-spin" /></div>
                  ) : changeHistory.length === 0 ? (
                    <div className="text-center py-8">
                      <Package className="w-10 h-10 text-gray-300 mx-auto mb-2" />
                      <p className="text-sm text-gray-500 font-medium">No changes recorded yet</p>
                      <p className="text-xs text-gray-400 mt-1">Stock changes will appear here after the next sync detects a difference.</p>
                    </div>
                  ) : (
                    <div className="max-h-[350px] overflow-y-auto border border-gray-200 rounded-lg">
                      <table className="w-full text-sm">
                        <thead className="bg-gray-50 sticky top-0">
                          <tr>
                            <th className="text-left px-3 py-2 text-xs font-medium text-gray-500">Date</th>
                            <th className="text-left px-3 py-2 text-xs font-medium text-gray-500">Location</th>
                            <th className="text-right px-3 py-2 text-xs font-medium text-gray-500">Old</th>
                            <th className="text-right px-3 py-2 text-xs font-medium text-gray-500">New</th>
                            <th className="text-right px-3 py-2 text-xs font-medium text-gray-500">Change</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {changeHistory.map((ch) => (
                            <tr key={ch.id} className="hover:bg-gray-50">
                              <td className="px-3 py-2 text-xs text-gray-600 whitespace-nowrap">{new Date(ch.created_at + "Z").toLocaleString()}</td>
                              <td className="px-3 py-2 text-xs text-gray-700">{ch.location_name}</td>
                              <td className="px-3 py-2 text-xs text-gray-500 text-right">{ch.old_stock}</td>
                              <td className="px-3 py-2 text-xs text-gray-700 text-right font-medium">{ch.new_stock}</td>
                              <td className={`px-3 py-2 text-xs text-right font-semibold ${ch.change_amount > 0 ? "text-green-600" : ch.change_amount < 0 ? "text-red-600" : "text-gray-500"}`}>
                                {ch.change_amount > 0 ? "+" : ""}{ch.change_amount}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}

              {/* Image Tab */}
              {editTab === "image" && (
                <>
                  {/* Primary Image */}
                  <label className="block text-sm font-medium text-gray-700 mb-2">Primary Image</label>
                  {editImagePreview && !editImageFile ? (
                    <div className="mb-4">
                      <div className="relative inline-block">
                        <img src={editImagePreview} alt={editItem?.name} className="w-48 h-48 object-cover rounded-lg border border-gray-200" />
                        <button
                          onClick={handleDeleteEditImage}
                          disabled={imageUploading}
                          className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-1 hover:bg-red-600 disabled:opacity-50"
                          title="Delete image"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                  ) : !editImageFile ? (
                    <div className="flex flex-col items-center justify-center py-4 text-center mb-4 bg-gray-50 rounded-lg border border-dashed border-gray-300">
                      <Image className="w-8 h-8 text-gray-300 mb-1" />
                      <p className="text-xs text-gray-500">No primary image</p>
                    </div>
                  ) : null}

                  {/* Upload Primary Image */}
                  <div className="mb-4">
                    <div className="flex items-center gap-3">
                      <label className="flex items-center gap-2 px-4 py-2 bg-green-50 text-green-700 rounded-lg cursor-pointer hover:bg-green-100 border border-green-200 text-sm font-medium">
                        <Upload className="w-4 h-4" />
                        {editImagePreview && !editImageFile ? "Replace Primary" : "Choose Primary Image"}
                        <input
                          type="file"
                          accept="image/*"
                          onChange={handleEditFileSelect}
                          className="hidden"
                        />
                      </label>
                      {editImageFile && (
                        <span className="text-xs text-gray-500">{editImageFile.name}</span>
                      )}
                    </div>
                    {editImageFile && editImagePreview && (
                      <div className="mt-3">
                        <img src={editImagePreview} alt="Preview" className="w-32 h-32 object-cover rounded-lg border border-gray-200" />
                        <div className="flex gap-2 mt-2">
                          <button
                            onClick={handleUploadEditImage}
                            disabled={imageUploading}
                            className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white rounded-lg text-xs hover:bg-green-700 disabled:opacity-50"
                          >
                            {imageUploading ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Upload className="w-3 h-3" />}
                            {imageUploading ? "Uploading..." : "Upload"}
                          </button>
                          <button
                            onClick={() => { setEditImageFile(null); setEditImagePreview(editItem?.has_image ? getImageUrl(editItem.sku, imageCacheBust) : null); }}
                            className="px-3 py-1.5 border border-gray-300 rounded-lg text-xs hover:bg-gray-50"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Gallery Images */}
                  <div className="border-t border-gray-200 pt-4 mt-2">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <Images className="w-5 h-5 text-indigo-600" />
                        <span className="text-sm font-medium text-gray-700">Additional Photos ({galleryImages.length})</span>
                      </div>
                      <label className="flex items-center gap-2 px-3 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg cursor-pointer hover:bg-indigo-100 border border-indigo-200 text-xs font-medium">
                        {galleryUploading ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                        {galleryUploading ? "Uploading..." : "Add Photos"}
                        <input
                          type="file"
                          accept="image/*"
                          multiple
                          onChange={handleGalleryFileSelect}
                          disabled={galleryUploading}
                          className="hidden"
                        />
                      </label>
                    </div>

                    {galleryImages.length > 0 ? (
                      <div className="grid grid-cols-3 gap-3">
                        {galleryImages.map((img) => (
                          <div key={img.id} className="relative group">
                            <img
                              src={getGalleryImageUrl(editItem!.sku, img.position, imageCacheBust)}
                              alt={`Gallery ${img.position + 1}`}
                              className="w-full h-28 object-cover rounded-lg border border-gray-200"
                            />
                            <button
                              onClick={() => handleDeleteGalleryImage(img.position)}
                              disabled={galleryUploading}
                              className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-1 hover:bg-red-600 disabled:opacity-50 opacity-0 group-hover:opacity-100 transition-opacity"
                              title="Delete gallery image"
                            >
                              <Trash2 className="w-3 h-3" />
                            </button>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center py-4 text-center bg-gray-50 rounded-lg border border-dashed border-gray-300">
                        <Images className="w-8 h-8 text-gray-300 mb-1" />
                        <p className="text-xs text-gray-500">No additional photos yet</p>
                        <p className="text-xs text-gray-400">Click &quot;Add Photos&quot; to upload multiple images</p>
                      </div>
                    )}
                  </div>

                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mt-4">
                    <p className="text-xs text-blue-700">
                      <strong>For e-commerce:</strong> The primary image shows on the product card. Additional photos appear in the product detail gallery. Images are stored in our app and won&apos;t appear in Clover POS.
                    </p>
                  </div>
                </>
              )}

              {/* Variants Tab */}
              {editTab === "variants" && (
                <>
                  {editItem?.item_group_name ? (
                    <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 mb-4">
                      <div className="flex items-center gap-2 mb-2">
                        <Layers className="w-4 h-4 text-emerald-600" />
                        <span className="text-sm font-semibold text-emerald-800">Part of variant group: {editItem.item_group_name}</span>
                      </div>
                      <p className="text-xs text-emerald-700">
                        This item is already a variant in the <strong>{editItem.item_group_name}</strong> item group. Other variants in this group:
                      </p>
                      <div className="mt-2 space-y-1">
                        {items.filter(i => i.item_group_name === editItem.item_group_name && i.sku !== editItem.sku).map(sibling => (
                          <div key={sibling.sku} className="text-xs text-emerald-700 bg-white rounded px-2 py-1 border border-emerald-100">
                            {sibling.name} <span className="text-emerald-500">({sibling.sku})</span>
                          </div>
                        ))}
                      </div>
                      <p className="text-xs text-gray-500 mt-3">To add more variants, use the form below:</p>
                    </div>
                  ) : (
                    <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4">
                      <p className="text-xs text-blue-700">
                        <strong>Add variants</strong> to this item (e.g. Size: Small, Medium, Large). This will create an item group in Clover with variant items. Run a sync after to see the new items.
                      </p>
                    </div>
                  )}
                  {editVariantAttrs.map((attr, ai) => (
                    <div key={ai} className="border border-gray-200 rounded-lg p-3 mb-3">
                      <div className="flex items-center gap-2 mb-2">
                        <input
                          type="text"
                          placeholder="Attribute (e.g. Size, Color)"
                          value={attr.attribute_name}
                          onChange={e => {
                            const updated = [...editVariantAttrs];
                            updated[ai] = { ...updated[ai], attribute_name: e.target.value };
                            setEditVariantAttrs(updated);
                          }}
                          className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                        />
                        {editVariantAttrs.length > 1 && (
                          <button onClick={() => setEditVariantAttrs(editVariantAttrs.filter((_, i) => i !== ai))}
                            className="text-red-400 hover:text-red-600"><X className="w-4 h-4" /></button>
                        )}
                      </div>
                      <div className="space-y-1.5 ml-4">
                        {attr.option_names.map((opt, oi) => (
                          <div key={oi} className="flex items-center gap-2">
                            <input
                              type="text"
                              placeholder={`Option ${oi + 1} (e.g. Small)`}
                              value={opt}
                              onChange={e => {
                                const updated = [...editVariantAttrs];
                                const opts = [...updated[ai].option_names];
                                opts[oi] = e.target.value;
                                updated[ai] = { ...updated[ai], option_names: opts };
                                setEditVariantAttrs(updated);
                              }}
                              className="flex-1 px-2 py-1.5 border border-gray-200 rounded text-sm focus:ring-1 focus:ring-green-400 outline-none"
                            />
                            {attr.option_names.length > 1 && (
                              <button onClick={() => {
                                const updated = [...editVariantAttrs];
                                updated[ai] = { ...updated[ai], option_names: attr.option_names.filter((_, i) => i !== oi) };
                                setEditVariantAttrs(updated);
                              }} className="text-red-300 hover:text-red-500"><Minus className="w-3 h-3" /></button>
                            )}
                          </div>
                        ))}
                        <button onClick={() => {
                          const updated = [...editVariantAttrs];
                          updated[ai] = { ...updated[ai], option_names: [...attr.option_names, ""] };
                          setEditVariantAttrs(updated);
                        }} className="text-xs text-green-600 hover:text-green-700 font-medium flex items-center gap-1 mt-1">
                          <Plus className="w-3 h-3" /> Add option
                        </button>
                      </div>
                    </div>
                  ))}
                  <button onClick={() => setEditVariantAttrs([...editVariantAttrs, { attribute_name: "", option_names: [""] }])}
                    className="text-sm text-green-600 hover:text-green-700 font-medium flex items-center gap-1 mb-3">
                    <Plus className="w-4 h-4" /> Add attribute
                  </button>
                  <label className="flex items-center gap-2 text-sm text-gray-600 mb-3">
                    <input type="checkbox" checked={keepOriginal} onChange={e => setKeepOriginal(e.target.checked)}
                      className="w-4 h-4 text-green-600 rounded focus:ring-green-500" />
                    Keep original item (don&apos;t delete it after creating variants)
                  </label>
                  <button
                    onClick={handleAddVariantsToExisting}
                    disabled={addingVariants}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 disabled:opacity-50 font-medium"
                  >
                    {addingVariants ? <><RefreshCw className="w-4 h-4 animate-spin" /> Creating Variants...</> : <><Layers className="w-4 h-4" /> Create Variants</>}
                  </button>
                </>
              )}
            </div>

            <div className="flex gap-3 mt-6 pt-4 border-t border-gray-100">
              <button
                onClick={() => setConfirmDelete(editItem)}
                className="flex items-center justify-center gap-1 px-3 py-2.5 border border-red-300 text-red-600 rounded-lg text-sm hover:bg-red-50 font-medium"
                title="Delete from all locations"
              >
                <Trash2 className="w-4 h-4" />
              </button>
              <button
                onClick={() => setEditItem(null)}
                className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveEdit}
                disabled={saving || !editForm.name || !editForm.price}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 disabled:opacity-50 font-medium"
              >
                {saving ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Saving...
                  </>
                ) : (
                  <>
                    <Save className="w-4 h-4" />
                    Save Changes
                  </>
                )}
              </button>
            </div>
            <p className="text-xs text-gray-400 text-center mt-2">
              Changes will be pushed to Clover POS at all locations.
            </p>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {confirmDelete && (
        <div className="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-sm w-full p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2 bg-red-100 rounded-lg">
                <Trash2 className="w-5 h-5 text-red-600" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-gray-900">Delete Item</h3>
                <p className="text-xs text-gray-400">This cannot be undone</p>
              </div>
            </div>
            <p className="text-sm text-gray-600 mb-1">
              Are you sure you want to delete <strong>{confirmDelete.name}</strong>?
            </p>
            <p className="text-xs text-gray-400 mb-5">
              This will remove it from all Clover locations and delete PAR levels.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setConfirmDelete(null)}
                className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 font-medium"
              >
                Cancel
              </button>
              <button
                onClick={() => handleDeleteItem(confirmDelete)}
                disabled={deleting === confirmDelete.sku}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 disabled:opacity-50 font-medium"
              >
                {deleting === confirmDelete.sku ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Deleting...
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    Delete Item
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Action Bar */}
      {selectedItems.size > 0 && (
        <div className="bg-green-50 border border-green-200 rounded-xl px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <CheckSquare className="w-5 h-5 text-green-600" />
            <span className="text-sm font-medium text-green-700">
              {selectedItems.size} item{selectedItems.size !== 1 ? "s" : ""} selected
            </span>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setSelectedItems(new Set())}
              className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
            >
              Clear Selection
            </button>
            <button
              onClick={handleDownloadExcel}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-green-700 text-white rounded-lg text-sm hover:bg-green-800"
            >
              <Download className="w-3.5 h-3.5" />
              Download CSV
            </button>
            <button
              onClick={() => { setShowBulkCategory(true); setBulkCategoryName(""); }}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
            >
              <Tag className="w-3.5 h-3.5" />
              Assign Category
            </button>
            <button
              onClick={() => setShowBulkConfirm(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Delete Selected
            </button>
          </div>
        </div>
      )}

      {/* Bulk Assign Category Modal */}
      {showBulkCategory && (
        <div className="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2 bg-blue-100 rounded-lg">
                <Tag className="w-5 h-5 text-blue-600" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-gray-900">Assign Category</h3>
                <p className="text-xs text-gray-400">Assign a category to {selectedItems.size} selected item{selectedItems.size !== 1 ? "s" : ""}</p>
              </div>
            </div>
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">Category Name</label>
              <input
                list="category-suggestions"
                type="text"
                value={bulkCategoryName}
                onChange={(e) => setBulkCategoryName(e.target.value)}
                placeholder="Select existing or type new category..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                autoFocus
              />
              <datalist id="category-suggestions">
                {categories.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
              <p className="text-xs text-gray-400 mt-1">Choose an existing category or type a new name to create one</p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setShowBulkCategory(false)}
                className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleBulkAssignCategory}
                disabled={assigningCategory || !bulkCategoryName.trim()}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50 font-medium"
              >
                {assigningCategory ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Assigning...
                  </>
                ) : (
                  <>
                    <Tag className="w-4 h-4" />
                    Assign
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Delete Confirmation Modal */}
      {showBulkConfirm && (
        <div className="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-sm w-full p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2 bg-red-100 rounded-lg">
                <Trash2 className="w-5 h-5 text-red-600" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-gray-900">Bulk Delete</h3>
                <p className="text-xs text-gray-400">This cannot be undone</p>
              </div>
            </div>
            <p className="text-sm text-gray-600 mb-1">
              Are you sure you want to delete <strong>{selectedItems.size} item{selectedItems.size !== 1 ? "s" : ""}</strong>?
            </p>
            <p className="text-xs text-gray-400 mb-5">
              This will remove them from all Clover locations and delete PAR levels.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowBulkConfirm(false)}
                className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleBulkDelete}
                disabled={bulkDeleting}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 disabled:opacity-50 font-medium"
              >
                {bulkDeleting ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Deleting...
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    Delete All
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Inventory Table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto max-h-[calc(100vh-280px)] overflow-y-auto">
          <table className="w-full">
            <thead className="sticky top-0 z-10">
              <tr className="bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                <th className="px-3 py-3 w-10">
                  <button
                    onClick={toggleSelectAll}
                    className="text-gray-400 hover:text-green-600 transition-colors"
                    title={selectedItems.size === filteredItems.length ? "Deselect all" : "Select all"}
                  >
                    {selectedItems.size === filteredItems.length && filteredItems.length > 0 ? (
                      <CheckSquare className="w-4 h-4 text-green-600" />
                    ) : selectedItems.size > 0 ? (
                      <Minus className="w-4 h-4 text-green-600" />
                    ) : (
                      <Square className="w-4 h-4" />
                    )}
                  </button>
                </th>
                <th
                  className="px-4 py-3 cursor-pointer hover:text-gray-700"
                  onClick={() => toggleSort("name")}
                >
                  Product <SortIcon field="name" />
                </th>
                <th
                  className="px-4 py-3 cursor-pointer hover:text-gray-700"
                  onClick={() => toggleSort("sku")}
                >
                  SKU <SortIcon field="sku" />
                </th>
                <th
                  className="px-4 py-3 cursor-pointer hover:text-gray-700"
                  onClick={() => toggleSort("price")}
                >
                  Price <SortIcon field="price" />
                </th>
                <th
                  className="px-4 py-3 cursor-pointer hover:text-gray-700"
                  onClick={() => toggleSort("category")}
                >
                  Category <SortIcon field="category" />
                </th>
                {locations.map((loc) => (
                  <th
                    key={loc.id}
                    className="px-4 py-3 cursor-pointer hover:text-gray-700 text-center"
                    onClick={() => toggleSort("stock", loc.name)}
                  >
                    {loc.name} Stock {sortField === "stock" && sortLocation === loc.name ? <SortIcon field="stock" /> : sortField === "stock" ? <span className="text-gray-300 inline"><SortIcon field="stock" /></span> : null}
                  </th>
                ))}
                {locations.map((loc) => (
                  <th
                    key={`par-${loc.id}`}
                    className="px-4 py-3 text-center cursor-pointer hover:text-gray-700"
                    onClick={() => toggleSort("par", loc.name)}
                  >
                    {loc.name} PAR {sortField === "par" && sortLocation === loc.name ? <SortIcon field="par" /> : sortField === "par" ? <span className="text-gray-300 inline"><SortIcon field="par" /></span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {paginatedItems.map((item, idx) => (
                <tr
                  key={`${item.sku}::${item.name}::${idx}`}
                  className={`hover:bg-green-50 cursor-pointer transition-colors ${selectedItems.has(item.id) ? "bg-green-50/50" : ""}`}
                  onClick={(e) => {
                    const target = e.target as HTMLElement;
                    if (target.closest("button") || target.closest("input") || target.tagName === "BUTTON" || target.tagName === "INPUT") return;
                    openEditModal(item);
                  }}
                >
                  <td className="px-3 py-3 w-10">
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleSelectItem(item.id); }}
                      className="text-gray-400 hover:text-green-600 transition-colors"
                    >
                      {selectedItems.has(item.id) ? (
                        <CheckSquare className="w-4 h-4 text-green-600" />
                      ) : (
                        <Square className="w-4 h-4" />
                      )}
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {item.has_image && (
                        <img
                          src={getImageUrl(item.sku, imageCacheBust)}
                          alt=""
                          className="w-8 h-8 rounded object-cover border border-gray-200 flex-shrink-0"
                          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                        />
                      )}
                      <div>
                        <p className="text-sm font-medium text-green-700 hover:text-green-800 underline decoration-green-200 hover:decoration-green-400" title={item.name}>
                          {item.name}
                        </p>
                        {item.item_group_name && (
                          <span className="text-[10px] text-purple-600 bg-purple-50 border border-purple-200 rounded px-1 py-0.5 mt-0.5 inline-block">
                            Variant: {item.item_group_name}
                          </span>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500 font-mono">
                    {item.sku.length > 15 ? item.sku.slice(0, 15) + "..." : item.sku}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">
                    ${(item.price / 100).toFixed(2)}
                  </td>
                  <td className="px-4 py-3">
                    {item.categories.map((c) => (
                      <span
                        key={c}
                        className="inline-block px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs mr-1"
                      >
                        {c}
                      </span>
                    ))}
                  </td>
                  {locations.map((loc) => {
                    const locData = item.locations[loc.name];
                    const changeKey = `${item.sku}::${loc.name}`;
                    const pendingChange = pendingStockChanges.get(changeKey);
                    const isEditing = !!pendingChange;
                    return (
                      <td key={loc.id} className="px-4 py-3 text-center">
                        {!locData ? (
                          <span className="text-gray-300 text-sm">—</span>
                        ) : isEditing ? (
                          <div
                            className="flex items-center gap-1 justify-center"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <input
                              type="number"
                              value={pendingChange.value}
                              onChange={(e) => {
                                const next = new Map(pendingStockChanges);
                                next.set(changeKey, { ...pendingChange, value: e.target.value });
                                setPendingStockChanges(next);
                              }}
                              className="w-16 px-2 py-1 border border-amber-400 rounded text-sm text-center focus:ring-1 focus:ring-amber-500 outline-none bg-amber-50"
                              onKeyDown={(e) => {
                                if (e.key === "Escape") {
                                  const next = new Map(pendingStockChanges);
                                  next.delete(changeKey);
                                  setPendingStockChanges(next);
                                }
                              }}
                            />
                            <button
                              onClick={() => {
                                const next = new Map(pendingStockChanges);
                                next.delete(changeKey);
                                setPendingStockChanges(next);
                              }}
                              className="text-gray-400 hover:text-red-500 text-xs"
                              title="Cancel edit"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              const next = new Map(pendingStockChanges);
                              next.set(changeKey, {
                                sku: item.sku,
                                locationId: locData.location_id,
                                locName: loc.name,
                                value: locData.stock.toString(),
                                originalValue: locData.stock,
                                itemName: item.name,
                                cloverItemId: locData.clover_item_id || "",
                              });
                              setPendingStockChanges(next);
                            }}
                            className={`inline-block px-2.5 py-1 rounded-lg text-sm font-semibold cursor-pointer hover:ring-2 hover:ring-green-300 transition-all ${stockColor(
                              locData.stock,
                              locData.par_level
                            )}`}
                            title="Click to edit stock"
                          >
                            {locData.stock}
                          </button>
                        )}
                      </td>
                    );
                  })}
                  {locations.map((loc) => {
                    const locData = item.locations[loc.name];
                    const isEditing =
                      editingPar?.sku === item.sku && editingPar?.locName === loc.name;
                    return (
                      <td key={`par-${loc.id}`} className="px-4 py-3 text-center">
                        {!locData ? (
                          <span className="text-gray-300 text-sm">—</span>
                        ) : isEditing ? (
                          <div
                            className="flex items-center gap-1 justify-center"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <input
                              type="number"
                              value={parValue}
                              onChange={(e) => setParValue(e.target.value)}
                              className="w-16 px-2 py-1 border border-green-300 rounded text-sm text-center focus:ring-1 focus:ring-green-500 outline-none"
                              autoFocus
                              onKeyDown={(e) => {
                                if (e.key === "Enter") handleSetPar(item.sku, locData.location_id);
                                if (e.key === "Escape") setEditingPar(null);
                              }}
                            />
                            <button
                              onClick={() => handleSetPar(item.sku, locData.location_id)}
                              className="text-green-600 hover:text-green-700 text-xs font-medium"
                            >
                              Save
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setEditingPar({ sku: item.sku, locName: loc.name });
                              setParValue(locData.par_level?.toString() || "");
                            }}
                            className={`text-sm ${
                              locData.par_level !== null
                                ? "font-semibold text-gray-700"
                                : "text-gray-400 hover:text-green-600"
                            }`}
                          >
                            {locData.par_level !== null ? locData.par_level : "Set"}
                          </button>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filteredItems.length === 0 && (
          <div className="text-center py-12 text-gray-500">
            No products found matching your filters.
          </div>
        )}
        {/* Pagination Controls */}
        {filteredItems.length > 0 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50 rounded-b-xl">
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <span>Show</span>
              <select
                value={itemsPerPage}
                onChange={(e) => { setItemsPerPage(Number(e.target.value)); setCurrentPage(1); }}
                className="px-2 py-1 border border-gray-300 rounded text-sm bg-white"
              >
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={250}>250</option>
              </select>
              <span>of {filteredItems.length} items</span>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setCurrentPage(1)}
                disabled={currentPage === 1}
                className="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
                title="First page"
              >
                <ChevronsLeft className="w-4 h-4" />
              </button>
              <button
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
                title="Previous page"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="px-3 py-1 text-sm font-medium text-gray-700">
                Page {currentPage} of {totalPages}
              </span>
              <button
                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                className="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
                title="Next page"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
              <button
                onClick={() => setCurrentPage(totalPages)}
                disabled={currentPage === totalPages}
                className="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed"
                title="Last page"
              >
                <ChevronsRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Transfer Stock Modal */}
      {showTransfer && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full p-6 max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <ArrowRightLeft className="w-5 h-5 text-purple-600" />
                Transfer Stock
              </h3>
              <button onClick={resetTransferModal}>
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>

            {/* Location selectors */}
            <div className="grid grid-cols-2 gap-3 mb-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">From Location</label>
                <select
                  value={transferFromId}
                  onChange={(e) => setTransferFromId(parseInt(e.target.value))}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 outline-none"
                >
                  <option value={0}>Select source...</option>
                  {locations.map((loc) => (
                    <option key={loc.id} value={loc.id}>{loc.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">To Location</label>
                <select
                  value={transferToId}
                  onChange={(e) => setTransferToId(parseInt(e.target.value))}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 outline-none"
                >
                  <option value={0}>Select destination...</option>
                  {locations.map((loc) => (
                    <option key={loc.id} value={loc.id}>{loc.name}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Search to add items */}
            <div className="mb-3">
              <label className="block text-sm font-medium text-gray-700 mb-1">Search Items to Add</label>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  value={transferSearch}
                  onChange={(e) => setTransferSearch(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 outline-none"
                  placeholder="Search by name or SKU..."
                />
              </div>
              {/* Search results dropdown */}
              {transferSearch.length >= 2 && transferSearchResults.length > 0 && (
                <div className="mt-1 border border-gray-200 rounded-lg max-h-40 overflow-y-auto bg-white shadow-sm">
                  {transferSearchResults.map((item) => {
                    const isAdded = transferItems.has(item.id);
                    return (
                      <button
                        key={item.id}
                        onClick={() => toggleTransferItem(item)}
                        className={`w-full text-left px-3 py-2 text-sm hover:bg-purple-50 flex items-center justify-between border-b border-gray-100 last:border-0 ${isAdded ? "bg-purple-50" : ""}`}
                      >
                        <div className="flex-1 min-w-0">
                          <span className="truncate block">{item.name}</span>
                          <span className="text-xs text-gray-400">{item.sku}</span>
                        </div>
                        {isAdded ? (
                          <span className="text-xs text-purple-600 font-medium ml-2 shrink-0">Added</span>
                        ) : (
                          <Plus className="w-4 h-4 text-gray-400 ml-2 shrink-0" />
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
              {transferSearch.length >= 2 && transferSearchResults.length === 0 && (
                <p className="mt-1 text-xs text-gray-400">No items found</p>
              )}
            </div>

            {/* Selected items with quantities */}
            {transferItems.size > 0 && (
              <div className="flex-1 min-h-0 overflow-y-auto mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Items to Transfer ({transferItems.size})
                </label>
                <div className="space-y-2">
                  {Array.from(transferItems.entries()).map(([id, { item, quantity }]) => (
                    <div key={id} className="flex items-center gap-2 p-2 bg-gray-50 rounded-lg">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">{item.name}</p>
                        <p className="text-xs text-gray-400">{item.sku}</p>
                      </div>
                      <input
                        type="number"
                        min="1"
                        value={quantity}
                        onChange={(e) => setTransferQuantity(id, e.target.value)}
                        className="w-20 px-2 py-1 border border-gray-300 rounded text-sm text-center focus:ring-2 focus:ring-purple-500 outline-none"
                        placeholder="Qty"
                      />
                      <button
                        onClick={() => toggleTransferItem(item)}
                        className="p-1 text-gray-400 hover:text-red-500"
                        title="Remove"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {transferItems.size === 0 && (
              <div className="flex-1 flex items-center justify-center text-sm text-gray-400 py-8">
                Search and add items above to start a transfer
              </div>
            )}

            {/* Transfer results */}
            {transferResults.length > 0 && (
              <div className="mb-4 p-3 bg-gray-50 border border-gray-200 rounded-lg max-h-32 overflow-y-auto">
                <p className="text-sm font-medium text-gray-700 mb-1">Results:</p>
                {transferResults.map((r, i) => (
                  <p key={i} className={`text-xs ${r.status.startsWith("Transferred") ? "text-green-700" : "text-red-600"}`}>
                    {r.name}: {r.status}
                  </p>
                ))}
              </div>
            )}

            <div className="flex gap-3">
              <button
                onClick={resetTransferModal}
                className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
              >
                {transferResults.length > 0 ? "Close" : "Cancel"}
              </button>
              {transferResults.length === 0 && (
                <button
                  onClick={handleTransferStock}
                  disabled={transferring || transferItems.size === 0 || !transferFromId || !transferToId}
                  className="flex-1 px-4 py-2 bg-purple-600 text-white rounded-lg text-sm hover:bg-purple-700 disabled:opacity-50"
                >
                  {transferring ? "Transferring..." : `Transfer ${transferItems.size} Item${transferItems.size !== 1 ? "s" : ""}`}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Bulk Image Assignment Modal */}
      {showBulkImage && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6 max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <Images className="w-5 h-5 text-pink-600" />
                Bulk Assign Images
              </h3>
              <button onClick={() => { setShowBulkImage(false); setBulkImageResult(null); setBulkImageMatches([]); setBulkImageSelected(new Set()); }}>
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>
            <p className="text-sm text-gray-500 mb-4">
              Upload one image and assign it to matching products. Type a keyword, click &quot;Find Matches&quot;, then check/uncheck items before assigning.
            </p>
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Keyword</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={bulkImageKeyword}
                    onChange={(e) => { setBulkImageKeyword(e.target.value); setBulkImageMatches([]); setBulkImageSelected(new Set()); setBulkImageResult(null); }}
                    onKeyDown={(e) => { if (e.key === "Enter") handleBulkImagePreview(); }}
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-pink-500 outline-none"
                    placeholder='e.g., "gummies", "flower", "vape"'
                  />
                  <button
                    onClick={handleBulkImagePreview}
                    disabled={!bulkImageKeyword || bulkImageKeyword.length < 2}
                    className="px-3 py-2 bg-gray-100 border border-gray-300 rounded-lg text-sm hover:bg-gray-200 disabled:opacity-50 whitespace-nowrap"
                  >
                    Find Matches
                  </button>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Image</label>
                <input
                  type="file"
                  accept="image/*"
                  onChange={handleBulkImageFileSelect}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                />
                {bulkImagePreview && (
                  <div className="mt-2 flex justify-center">
                    <img src={bulkImagePreview} alt="Preview" className="w-24 h-24 object-cover rounded-lg border" />
                  </div>
                )}
              </div>
            </div>
            {/* Preview matches with checkboxes */}
            {bulkImageMatches.length > 0 && !bulkImageResult && (
              <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg flex-1 min-h-0 flex flex-col">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-sm font-medium text-blue-800">
                    {bulkImageMatches.length} product{bulkImageMatches.length !== 1 ? "s" : ""} found — {bulkImageSelected.size} selected
                  </p>
                  <button
                    onClick={toggleAllBulkImageItems}
                    className="text-xs text-blue-600 hover:text-blue-800 underline"
                  >
                    {bulkImageSelected.size === bulkImageMatches.length ? "Deselect All" : "Select All"}
                  </button>
                </div>
                <div className="overflow-y-auto max-h-48 space-y-1">
                  {bulkImageMatches.map((p) => (
                    <label key={p.sku} className="flex items-center gap-2 py-1 px-2 rounded hover:bg-blue-100 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={bulkImageSelected.has(p.sku)}
                        onChange={() => toggleBulkImageItem(p.sku)}
                        className="rounded border-gray-300 text-pink-600 focus:ring-pink-500"
                      />
                      <span className="text-xs text-blue-900 truncate">{p.name}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            {bulkImageMatches.length === 0 && bulkImageKeyword.length >= 2 && !bulkImageResult && (
              <p className="mt-3 text-sm text-gray-400 text-center">Click &quot;Find Matches&quot; to preview matching products</p>
            )}
            {bulkImageResult && (
              <div className="mt-4 p-3 bg-green-50 border border-green-200 rounded-lg">
                <p className="text-sm font-medium text-green-800">
                  Assigned to {bulkImageResult.assigned} product{bulkImageResult.assigned !== 1 ? "s" : ""}:
                </p>
                <div className="mt-1 max-h-32 overflow-y-auto">
                  {bulkImageResult.products.map((p) => (
                    <p key={p.sku} className="text-xs text-green-700">{p.name}</p>
                  ))}
                </div>
              </div>
            )}
            <div className="flex gap-3 mt-5">
              <button
                onClick={() => { setShowBulkImage(false); setBulkImageResult(null); setBulkImageMatches([]); setBulkImageSelected(new Set()); }}
                className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
              >
                Close
              </button>
              <button
                onClick={handleBulkAssignImages}
                disabled={assigningImages || !bulkImageKeyword || !bulkImageFile || bulkImageSelected.size === 0}
                className="flex-1 px-4 py-2 bg-pink-600 text-white rounded-lg text-sm hover:bg-pink-700 disabled:opacity-50"
              >
                {assigningImages ? "Assigning..." : `Assign to ${bulkImageSelected.size} Product${bulkImageSelected.size !== 1 ? "s" : ""}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
