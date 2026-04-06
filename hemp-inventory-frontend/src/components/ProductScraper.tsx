import { useState } from "react";
import { Search, Loader2, ExternalLink, Image, Copy, Check, AlertCircle, Plus, Package } from "lucide-react";
import { scrapeProduct, createItem, uploadImage } from "../lib/api";

interface ScrapedProduct {
  manufacturer: string;
  model_number: string;
  product_name: string | null;
  description: string | null;
  image_urls: string[];
  specifications: Record<string, string>;
  source_url: string | null;
  error: string | null;
}

interface ProductScraperProps {
  onNavigate?: (page: string) => void;
}

export default function ProductScraper({ onNavigate }: ProductScraperProps) {
  const [manufacturer, setManufacturer] = useState("");
  const [modelNumber, setModelNumber] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ScrapedProduct | null>(null);
  const [error, setError] = useState("");
  const [copiedField, setCopiedField] = useState<string | null>(null);
  const [selectedImage, setSelectedImage] = useState<string | null>(null);

  // Create product state
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createSuccess, setCreateSuccess] = useState(false);
  const [createError, setCreateError] = useState("");
  const [productName, setProductName] = useState("");
  const [productPrice, setProductPrice] = useState("");
  const [productSku, setProductSku] = useState("");
  const [productCategory, setProductCategory] = useState("Packaging");
  const [productDescription, setProductDescription] = useState("");
  const [selectedImageForUpload, setSelectedImageForUpload] = useState<string | null>(null);

  const handleScrape = async () => {
    if (!manufacturer.trim() || !modelNumber.trim()) {
      setError("Both manufacturer and model number are required");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    setSelectedImage(null);
    setShowCreateForm(false);
    setCreateSuccess(false);
    setCreateError("");
    try {
      const resp = await scrapeProduct({
        manufacturer: manufacturer.trim(),
        model_number: modelNumber.trim(),
      });
      setResult(resp.data);
      if (resp.data.error) {
        setError(resp.data.error);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to scrape product";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = (text: string, field: string) => {
    navigator.clipboard.writeText(text);
    setCopiedField(field);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const handleOpenCreateForm = () => {
    if (!result) return;
    setProductName(result.product_name || "");
    setProductDescription(result.description || "");
    setProductSku("");
    setProductCategory("Packaging");
    setProductPrice("");
    setSelectedImageForUpload(result.image_urls.length > 0 ? result.image_urls[0] : null);
    setCreateError("");
    setCreateSuccess(false);
    setShowCreateForm(true);
  };

  const handleCreateProduct = async () => {
    if (!productName.trim()) {
      setCreateError("Product name is required");
      return;
    }
    if (!productPrice || isNaN(parseFloat(productPrice)) || parseFloat(productPrice) <= 0) {
      setCreateError("A valid price is required");
      return;
    }
    setCreating(true);
    setCreateError("");
    try {
      const priceInCents = Math.round(parseFloat(productPrice) * 100);
      const response = await createItem({
        name: productName.trim(),
        price: priceInCents,
        sku: productSku.trim() || undefined,
        category: productCategory.trim() || undefined,
        description: productDescription.trim() || undefined,
        available: true,
        auto_manage: true,
        default_tax_rates: true,
        is_revenue: true,
      });

      // Upload image if one was selected
      const itemSku = productSku.trim() || response.data?.sku;
      if (itemSku && selectedImageForUpload) {
        try {
          const imgResponse = await fetch(selectedImageForUpload);
          const blob = await imgResponse.blob();
          const base64 = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
              const dataUrl = reader.result as string;
              resolve(dataUrl.split(",")[1]);
            };
            reader.onerror = reject;
            reader.readAsDataURL(blob);
          });
          await uploadImage(itemSku, base64, blob.type, productName.trim());
        } catch (imgErr) {
          console.error("Image upload failed (product was still created):", imgErr);
        }
      }

      setCreateSuccess(true);
      setShowCreateForm(false);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setCreateError(axiosErr?.response?.data?.detail || "Failed to create product");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Product Scraper</h1>
        <p className="text-sm text-gray-500 mt-1">
          Pull product images and descriptions from manufacturer websites
        </p>
      </div>

      {/* Search Form */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Manufacturer
            </label>
            <input
              type="text"
              value={manufacturer}
              onChange={(e) => setManufacturer(e.target.value)}
              placeholder="e.g. Chubby Gorilla"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
              onKeyDown={(e) => e.key === "Enter" && handleScrape()}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Model / Product Name
            </label>
            <input
              type="text"
              value={modelNumber}
              onChange={(e) => setModelNumber(e.target.value)}
              placeholder="e.g. 4 oz black container"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
              onKeyDown={(e) => e.key === "Enter" && handleScrape()}
            />
          </div>
        </div>
        <div className="mt-4">
          <button
            onClick={handleScrape}
            disabled={loading}
            className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Scraping...
              </>
            ) : (
              <>
                <Search className="w-4 h-4" />
                Search Product
              </>
            )}
          </button>
        </div>
        <p className="mt-2 text-xs text-gray-400">
          Supported: Chubby Gorilla, Calyx Containers, CRC, Loud Lock, Dispensary Supply, Kush Supply, Sana Packaging, N2 Packaging. Other manufacturers searched by domain.
        </p>
      </div>

      {/* Error */}
      {error && !result?.product_name && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-medium text-red-800">Scraping Error</p>
            <p className="text-sm text-red-600 mt-1">{error}</p>
          </div>
        </div>
      )}

      {/* Success message after product creation */}
      {createSuccess && (
        <div className="bg-green-50 border border-green-200 rounded-xl p-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Check className="w-5 h-5 text-green-600" />
            <p className="text-sm font-medium text-green-800">
              Product created successfully! It will appear in your inventory after a sync.
            </p>
          </div>
          {onNavigate && (
            <button
              onClick={() => onNavigate("inventory")}
              className="inline-flex items-center gap-1 text-sm text-green-700 hover:text-green-800 font-medium"
            >
              <Package className="w-4 h-4" />
              Go to Inventory
            </button>
          )}
        </div>
      )}

      {/* Results */}
      {result && result.product_name && (
        <div className="space-y-4">
          {/* Product Info Card */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <div className="flex items-start justify-between mb-4">
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold text-gray-900">
                    {result.product_name}
                  </h2>
                  <button
                    onClick={() => copyToClipboard(result.product_name || "", "name")}
                    className="text-gray-400 hover:text-gray-600"
                    title="Copy name"
                  >
                    {copiedField === "name" ? (
                      <Check className="w-4 h-4 text-green-500" />
                    ) : (
                      <Copy className="w-4 h-4" />
                    )}
                  </button>
                </div>
                <p className="text-sm text-gray-500 mt-1">
                  {result.manufacturer} &middot; {result.model_number}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {!showCreateForm && !createSuccess && (
                  <button
                    onClick={handleOpenCreateForm}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 transition-colors"
                  >
                    <Plus className="w-4 h-4" />
                    Create Product
                  </button>
                )}
                {result.source_url && (
                  <a
                    href={result.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-sm text-green-600 hover:text-green-700"
                  >
                    <ExternalLink className="w-4 h-4" />
                    Source
                  </a>
                )}
              </div>
            </div>

            {/* Description */}
            {result.description && (
              <div className="mb-4">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="text-sm font-medium text-gray-700">Description</h3>
                  <button
                    onClick={() => copyToClipboard(result.description || "", "desc")}
                    className="text-gray-400 hover:text-gray-600"
                    title="Copy description"
                  >
                    {copiedField === "desc" ? (
                      <Check className="w-3 h-3 text-green-500" />
                    ) : (
                      <Copy className="w-3 h-3" />
                    )}
                  </button>
                </div>
                <p className="text-sm text-gray-600 leading-relaxed">
                  {result.description}
                </p>
              </div>
            )}

            {/* Specifications */}
            {Object.keys(result.specifications).length > 0 && (
              <div className="mb-4">
                <h3 className="text-sm font-medium text-gray-700 mb-2">Specifications</h3>
                <div className="bg-gray-50 rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <tbody>
                      {Object.entries(result.specifications).map(([key, val]) => (
                        <tr key={key} className="border-b border-gray-100 last:border-0">
                          <td className="px-3 py-2 font-medium text-gray-600 w-1/3">{key}</td>
                          <td className="px-3 py-2 text-gray-900">{val}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          {/* Create Product Form */}
          {showCreateForm && (
            <div className="bg-white rounded-xl border-2 border-green-200 p-6">
              <h3 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Plus className="w-5 h-5 text-green-600" />
                Create Product in Inventory
              </h3>

              {createError && (
                <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
                  <p className="text-sm text-red-600">{createError}</p>
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Product Name *
                  </label>
                  <input
                    type="text"
                    value={productName}
                    onChange={(e) => setProductName(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Price *
                  </label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      value={productPrice}
                      onChange={(e) => setProductPrice(e.target.value)}
                      placeholder="0.00"
                      className="w-full pl-7 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    SKU (optional)
                  </label>
                  <input
                    type="text"
                    value={productSku}
                    onChange={(e) => setProductSku(e.target.value)}
                    placeholder="Auto-generated if blank"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Category
                  </label>
                  <input
                    type="text"
                    value={productCategory}
                    onChange={(e) => setProductCategory(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Description
                  </label>
                  <textarea
                    value={productDescription}
                    onChange={(e) => setProductDescription(e.target.value)}
                    rows={3}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500 resize-none"
                  />
                </div>

                {/* Image selection for upload */}
                {result.image_urls.length > 0 && (
                  <div className="md:col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Product Image
                    </label>
                    <div className="flex gap-2 flex-wrap">
                      {result.image_urls.map((url, idx) => (
                        <button
                          key={idx}
                          onClick={() => setSelectedImageForUpload(url === selectedImageForUpload ? null : url)}
                          className={`w-16 h-16 rounded-lg overflow-hidden border-2 transition-colors ${
                            selectedImageForUpload === url
                              ? "border-green-500"
                              : "border-gray-200 hover:border-gray-300"
                          }`}
                        >
                          <img
                            src={url}
                            alt={`Option ${idx + 1}`}
                            className="w-full h-full object-contain p-1"
                          />
                        </button>
                      ))}
                      <button
                        onClick={() => setSelectedImageForUpload(null)}
                        className={`w-16 h-16 rounded-lg border-2 flex items-center justify-center text-xs text-gray-400 transition-colors ${
                          !selectedImageForUpload
                            ? "border-green-500 bg-green-50 text-green-600"
                            : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        None
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <div className="mt-6 flex items-center gap-3">
                <button
                  onClick={handleCreateProduct}
                  disabled={creating}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {creating ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Creating...
                    </>
                  ) : (
                    <>
                      <Plus className="w-4 h-4" />
                      Create Product
                    </>
                  )}
                </button>
                <button
                  onClick={() => setShowCreateForm(false)}
                  className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Images */}
          {result.image_urls.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <div className="flex items-center gap-2 mb-4">
                <Image className="w-5 h-5 text-gray-400" />
                <h3 className="text-sm font-medium text-gray-700">
                  Product Images ({result.image_urls.length})
                </h3>
              </div>

              {/* Selected/Large Image */}
              {selectedImage && (
                <div className="mb-4 bg-gray-50 rounded-lg p-4 flex flex-col items-center">
                  <img
                    src={selectedImage}
                    alt="Selected product"
                    className="max-h-80 object-contain rounded"
                  />
                  <div className="mt-2 flex items-center gap-2">
                    <button
                      onClick={() => copyToClipboard(selectedImage, "img")}
                      className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700"
                    >
                      {copiedField === "img" ? (
                        <Check className="w-3 h-3 text-green-500" />
                      ) : (
                        <Copy className="w-3 h-3" />
                      )}
                      Copy URL
                    </button>
                    <a
                      href={selectedImage}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-green-600 hover:text-green-700"
                    >
                      <ExternalLink className="w-3 h-3" />
                      Open Full Size
                    </a>
                  </div>
                </div>
              )}

              {/* Image Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                {result.image_urls.map((url, idx) => (
                  <button
                    key={idx}
                    onClick={() => setSelectedImage(url === selectedImage ? null : url)}
                    className={`relative aspect-square bg-gray-50 rounded-lg overflow-hidden border-2 transition-colors ${
                      selectedImage === url
                        ? "border-green-500"
                        : "border-transparent hover:border-gray-300"
                    }`}
                  >
                    <img
                      src={url}
                      alt={`Product ${idx + 1}`}
                      className="w-full h-full object-contain p-2"
                      onError={(e) => {
                        (e.target as HTMLImageElement).src = "";
                        (e.target as HTMLImageElement).alt = "Failed to load";
                      }}
                    />
                  </button>
                ))}
              </div>

              {/* Copy All URLs */}
              <div className="mt-3 flex justify-end">
                <button
                  onClick={() => copyToClipboard(result.image_urls.join("\n"), "allimgs")}
                  className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700"
                >
                  {copiedField === "allimgs" ? (
                    <Check className="w-3 h-3 text-green-500" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                  Copy All Image URLs
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
