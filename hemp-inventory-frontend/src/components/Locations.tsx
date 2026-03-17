import { useEffect, useState } from "react";
import { getLocations, addLocation, deleteLocation } from "../lib/api";
import { MapPin, Plus, Trash2, RefreshCw, X } from "lucide-react";

interface Location {
  id: number;
  name: string;
  merchant_id: string;
  created_at: string;
}

export default function Locations() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ name: "", merchant_id: "", api_token: "", is_virtual: false });
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");

  const loadLocations = async () => {
    setLoading(true);
    try {
      const res = await getLocations();
      setLocations(res.data);
    } catch (err) {
      console.error("Error loading locations:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLocations();
  }, []);

  const handleAdd = async () => {
    if (!form.name || (!form.is_virtual && (!form.merchant_id || !form.api_token))) return;
    setAdding(true);
    setError("");
    try {
      await addLocation(form);
      setShowAdd(false);
      setForm({ name: "", merchant_id: "", api_token: "", is_virtual: false });
      await loadLocations();
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      setError(error.response?.data?.detail || "Failed to add location");
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete location "${name}"? This will also remove all PAR levels for this location.`)) return;
    try {
      await deleteLocation(id);
      await loadLocations();
    } catch (err) {
      console.error("Error deleting location:", err);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 text-green-600 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Locations</h2>
          <p className="text-gray-500 text-sm">Manage your Clover POS locations</p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 text-sm transition-colors"
        >
          <Plus className="w-4 h-4" /> Add Location
        </button>
      </div>

      {/* Add Location Modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">Add New Location</h3>
              <button onClick={() => { setShowAdd(false); setError(""); }}>
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>
            {error && (
              <div className="mb-4 p-3 bg-red-50 text-red-600 rounded-lg text-sm">{error}</div>
            )}
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Location Name</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
                  placeholder="e.g., HQ"
                />
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_virtual"
                  checked={form.is_virtual}
                  onChange={(e) => setForm({ ...form, is_virtual: e.target.checked, merchant_id: e.target.checked ? '' : form.merchant_id, api_token: e.target.checked ? '' : form.api_token })}
                  className="w-4 h-4 text-green-600 rounded border-gray-300 focus:ring-green-500"
                />
                <label htmlFor="is_virtual" className="text-sm text-gray-700">Virtual location (no Clover sync yet)</label>
              </div>
              {!form.is_virtual && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Merchant ID</label>
                    <input
                      type="text"
                      value={form.merchant_id}
                      onChange={(e) => setForm({ ...form, merchant_id: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none font-mono"
                      placeholder="From Clover dashboard"
                    />
                    <p className="text-xs text-gray-400 mt-1">Found in Account & Setup → Business Information</p>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">API Token</label>
                    <input
                      type="password"
                      value={form.api_token}
                      onChange={(e) => setForm({ ...form, api_token: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none font-mono"
                      placeholder="From Clover API Tokens"
                    />
                    <p className="text-xs text-gray-400 mt-1">Found in Account & Setup → API Tokens</p>
                  </div>
                </>
              )}
              {form.is_virtual && (
                <p className="text-xs text-amber-600 bg-amber-50 p-2 rounded-lg">This location will be created as a placeholder. You can connect Clover credentials later when they're ready.</p>
              )}
            </div>
            <div className="flex gap-3 mt-5">
              <button
                onClick={() => { setShowAdd(false); setError(""); }}
                className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleAdd}
                disabled={adding || !form.name || (!form.is_virtual && (!form.merchant_id || !form.api_token))}
                className="flex-1 px-4 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 disabled:opacity-50"
              >
                {adding ? (form.is_virtual ? "Adding..." : "Verifying...") : "Add Location"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Location Cards */}
      {locations.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-gray-200">
          <MapPin className="w-12 h-12 mx-auto text-gray-300 mb-3" />
          <p className="text-gray-500">No locations configured</p>
          <p className="text-sm text-gray-400 mt-1">Add your Clover locations to start syncing inventory</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {locations.map((loc) => (
            <div key={loc.id} className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center">
                    <MapPin className="w-5 h-5 text-green-600" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-gray-900">{loc.name}</h3>
                    <p className="text-xs text-gray-400 font-mono">{loc.merchant_id.startsWith('virtual-') ? <span className="text-amber-500">Pending Clover sync</span> : loc.merchant_id}</p>
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(loc.id, loc.name)}
                  className="text-gray-400 hover:text-red-500 transition-colors"
                  title="Delete location"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              <div className="mt-3 pt-3 border-t border-gray-100">
                <p className="text-xs text-gray-400">
                  Added {new Date(loc.created_at + "Z").toLocaleDateString("en-US", { timeZone: "America/New_York" })}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
