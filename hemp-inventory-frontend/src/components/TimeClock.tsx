import { useState, useEffect, useCallback } from "react";
import {
  getEmployees,
  createEmployee,
  updateEmployee,
  deleteEmployee,
  clockIn,
  clockOut,
  getActiveClocks,
  getTimeEntries,
  deleteTimeEntry,
  getTimeclockExportUrl,
  syncEmployeesFromClover,
} from "../lib/api";
import {
  Clock,
  UserPlus,
  Trash2,
  Download,
  LogIn,
  LogOut,
  Users,
  CalendarDays,
  Search,
  X,
  ChevronLeft,
  ChevronRight,
  Edit2,
  Check,
  RefreshCw,
  Calendar,
  Save,
} from "lucide-react";
import { getSchedules, saveSchedule, deleteScheduleByDay } from "../lib/api";

interface Employee {
  id: number;
  name: string;
  pin: string | null;
  active: boolean;
  created_at: string;
}

interface ActiveClock {
  employee_id: number;
  employee_name: string;
  clock_in: string;
  entry_id: number;
  hours_elapsed: number;
}

interface TimeEntry {
  id: number;
  employee_id: number;
  employee_name: string;
  clock_in: string;
  clock_out: string | null;
  hours: number | null;
}

type Tab = "clock" | "timesheet" | "employees" | "schedule";

interface ScheduleItem {
  id: number;
  employee_id: number;
  employee_name: string;
  day_of_week: number;
  day_name: string;
  start_time: string;
  end_time: string;
  location: string | null;
  notes: string | null;
}

export default function TimeClock() {
  const [tab, setTab] = useState<Tab>("clock");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [activeClocks, setActiveClocks] = useState<ActiveClock[]>([]);
  const [entries, setEntries] = useState<TimeEntry[]>([]);
  const [loading, setLoading] = useState(true);

  // Employee form
  const [showAddEmployee, setShowAddEmployee] = useState(false);
  const [newEmpName, setNewEmpName] = useState("");
  const [newEmpPin, setNewEmpPin] = useState("");
  const [editingEmp, setEditingEmp] = useState<number | null>(null);
  const [editEmpName, setEditEmpName] = useState("");

  // Timesheet filters
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - d.getDay()); // Start of current week (Sunday)
    return d.toISOString().split("T")[0];
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [filterEmployee, setFilterEmployee] = useState<number | 0>(0);
  const [searchActive, setSearchActive] = useState("");

  // Pagination for timesheet
  const [page, setPage] = useState(1);
  const perPage = 25;

  // Sync
  const [syncing, setSyncing] = useState(false);

  // Schedule state
  const [schedules, setSchedules] = useState<ScheduleItem[]>([]);
  const [scheduleEmployee, setScheduleEmployee] = useState<number>(0);
  const [editingSchedule, setEditingSchedule] = useState<{ day: number; start: string; end: string; location: string; notes: string } | null>(null);
  const [savingSchedule, setSavingSchedule] = useState(false);

  // Toast
  const [toast, setToast] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const showToast = (type: "success" | "error", text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 4000);
  };

  const loadEmployees = useCallback(async () => {
    try {
      const res = await getEmployees();
      setEmployees(res.data);
    } catch (err) {
      console.error("Error loading employees:", err);
    }
  }, []);

  const loadActiveClocks = useCallback(async () => {
    try {
      const res = await getActiveClocks();
      setActiveClocks(res.data);
    } catch (err) {
      console.error("Error loading active clocks:", err);
    }
  }, []);

  const loadEntries = useCallback(async () => {
    try {
      const params: { start_date?: string; end_date?: string; employee_id?: number } = {};
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      if (filterEmployee) params.employee_id = filterEmployee;
      const res = await getTimeEntries(params);
      setEntries(res.data);
    } catch (err) {
      console.error("Error loading entries:", err);
    }
  }, [startDate, endDate, filterEmployee]);

  useEffect(() => {
    const init = async () => {
      await Promise.all([loadEmployees(), loadActiveClocks(), loadEntries()]);
      setLoading(false);
    };
    init();
  }, [loadEmployees, loadActiveClocks, loadEntries]);

  // Auto-refresh active clocks every 30 seconds
  useEffect(() => {
    const interval = setInterval(loadActiveClocks, 30000);
    return () => clearInterval(interval);
  }, [loadActiveClocks]);

  const handleClockIn = async (empId: number) => {
    try {
      const res = await clockIn(empId);
      showToast("success", `${res.data.employee} clocked in`);
      await Promise.all([loadActiveClocks(), loadEntries()]);
    } catch (err) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      showToast("error", axErr?.response?.data?.detail || "Clock in failed");
    }
  };

  const handleClockOut = async (empId: number) => {
    try {
      const res = await clockOut(empId);
      showToast("success", `${res.data.employee} clocked out (${res.data.hours}h)`);
      await Promise.all([loadActiveClocks(), loadEntries()]);
    } catch (err) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      showToast("error", axErr?.response?.data?.detail || "Clock out failed");
    }
  };

  const handleAddEmployee = async () => {
    if (!newEmpName.trim()) return;
    try {
      await createEmployee({ name: newEmpName.trim(), pin: newEmpPin || undefined });
      setNewEmpName("");
      setNewEmpPin("");
      setShowAddEmployee(false);
      showToast("success", "Employee added");
      await loadEmployees();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to add employee");
    }
  };

  const handleSaveEditEmp = async (id: number) => {
    if (!editEmpName.trim()) return;
    try {
      await updateEmployee(id, { name: editEmpName.trim() });
      setEditingEmp(null);
      showToast("success", "Employee updated");
      await loadEmployees();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to update employee");
    }
  };

  const handleToggleActive = async (emp: Employee) => {
    try {
      await updateEmployee(emp.id, { active: !emp.active });
      showToast("success", `${emp.name} ${emp.active ? "deactivated" : "activated"}`);
      await loadEmployees();
    } catch (err) {
      console.error(err);
    }
  };

  const handleSyncFromClover = async () => {
    setSyncing(true);
    try {
      const res = await syncEmployeesFromClover();
      const { imported, skipped, errors } = res.data;
      let msg = `Imported ${imported} employee(s)`;
      if (skipped > 0) msg += `, ${skipped} already existed`;
      if (errors.length > 0) msg += `. Errors: ${errors.map((e: { location: string; error: string }) => e.location).join(", ")}`;
      showToast(errors.length > 0 ? "error" : "success", msg);
      await loadEmployees();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to sync employees from Clover");
    } finally {
      setSyncing(false);
    }
  };

  const handleDeleteEmployee = async (emp: Employee) => {
    if (!confirm(`Delete ${emp.name} and all their time entries?`)) return;
    try {
      await deleteEmployee(emp.id);
      showToast("success", `${emp.name} deleted`);
      await Promise.all([loadEmployees(), loadActiveClocks(), loadEntries()]);
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to delete employee");
    }
  };

  const handleDeleteEntry = async (entryId: number) => {
    try {
      await deleteTimeEntry(entryId);
      showToast("success", "Entry deleted");
      await loadEntries();
    } catch (err) {
      console.error(err);
    }
  };

  const loadSchedules = useCallback(async () => {
    try {
      const res = await getSchedules(scheduleEmployee || undefined);
      setSchedules(res.data);
    } catch (err) {
      console.error("Error loading schedules:", err);
    }
  }, [scheduleEmployee]);

  useEffect(() => {
    if (tab === "schedule") loadSchedules();
  }, [tab, loadSchedules]);

  const handleSaveSchedule = async (empId: number, day: number, start: string, end: string, location: string, notes: string) => {
    setSavingSchedule(true);
    try {
      await saveSchedule({ employee_id: empId, day_of_week: day, start_time: start, end_time: end, location: location || undefined, notes: notes || undefined });
      showToast("success", "Schedule saved");
      setEditingSchedule(null);
      await loadSchedules();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to save schedule");
    } finally {
      setSavingSchedule(false);
    }
  };

  const handleDeleteSchedule = async (empId: number, day: number) => {
    try {
      await deleteScheduleByDay(empId, day);
      showToast("success", "Schedule entry removed");
      await loadSchedules();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to delete schedule");
    }
  };

  const handleExport = () => {
    const params: { start_date?: string; end_date?: string; employee_id?: number } = {};
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    if (filterEmployee) params.employee_id = filterEmployee;
    const url = getTimeclockExportUrl(params);
    const token = localStorage.getItem("token");
    // Fetch with auth header then download
    fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => r.blob())
      .then((blob) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `timesheet_${startDate}_${endDate}.csv`;
        a.click();
      })
      .catch(() => showToast("error", "Export failed"));
  };

  const formatTime = (iso: string | null) => {
    if (!iso) return "---";
    const d = new Date(iso);
    return d.toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
  };

  const formatDate = (iso: string | null) => {
    if (!iso) return "---";
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric" });
  };

  const formatHours = (h: number | null) => {
    if (h === null || h === undefined) return "---";
    const hrs = Math.floor(h);
    const mins = Math.round((h - hrs) * 60);
    return `${hrs}h ${mins}m`;
  };

  const activeSet = new Set(activeClocks.map((c) => c.employee_id));

  // Filtered active employees for clock tab
  const filteredActiveEmployees = employees
    .filter((e) => e.active)
    .filter((e) => !searchActive || e.name.toLowerCase().includes(searchActive.toLowerCase()));

  // Timesheet pagination
  const totalPages = Math.ceil(entries.length / perPage);
  const paginatedEntries = entries.slice((page - 1) * perPage, page * perPage);

  // Summary for timesheet
  const totalHours = entries.reduce((sum, e) => sum + (e.hours || 0), 0);
  const employeeSummary: Record<string, number> = {};
  entries.forEach((e) => {
    employeeSummary[e.employee_name] = (employeeSummary[e.employee_name] || 0) + (e.hours || 0);
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-green-600" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Toast */}
      {toast && (
        <div
          className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-white text-sm ${
            toast.type === "success" ? "bg-green-600" : "bg-red-600"
          }`}
        >
          {toast.text}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Time Clock</h2>
          <p className="text-sm text-gray-500">
            {activeClocks.length} employee{activeClocks.length !== 1 ? "s" : ""} clocked in
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 p-1 rounded-lg w-fit">
        {([
          { id: "clock" as Tab, label: "Clock In/Out", icon: Clock },
          { id: "timesheet" as Tab, label: "Timesheet", icon: CalendarDays },
          { id: "schedule" as Tab, label: "Schedule", icon: Calendar },
          { id: "employees" as Tab, label: "Employees", icon: Users },
        ]).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === t.id ? "bg-white text-green-700 shadow-sm" : "text-gray-600 hover:text-gray-900"
            }`}
          >
            <t.icon className="w-4 h-4" />
            {t.label}
          </button>
        ))}
      </div>

      {/* =================== CLOCK IN/OUT TAB =================== */}
      {tab === "clock" && (
        <div className="space-y-4">
          {/* Search */}
          <div className="relative w-full max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search employees..."
              value={searchActive}
              onChange={(e) => setSearchActive(e.target.value)}
              className="w-full pl-9 pr-8 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
            />
            {searchActive && (
              <button onClick={() => setSearchActive("")} className="absolute right-2 top-1/2 -translate-y-1/2">
                <X className="w-4 h-4 text-gray-400" />
              </button>
            )}
          </div>

          {/* Currently Clocked In */}
          {activeClocks.length > 0 && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
              <h3 className="text-sm font-semibold text-green-800 mb-3 flex items-center gap-2">
                <Clock className="w-4 h-4" />
                Currently On the Clock ({activeClocks.length})
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {activeClocks.map((ac) => (
                  <div
                    key={ac.employee_id}
                    className="bg-white rounded-lg border border-green-200 p-3 flex items-center justify-between"
                  >
                    <div>
                      <p className="font-medium text-gray-900 text-sm">{ac.employee_name}</p>
                      <p className="text-xs text-gray-500">
                        In: {formatTime(ac.clock_in)} &middot; {formatHours(ac.hours_elapsed)}
                      </p>
                    </div>
                    <button
                      onClick={() => handleClockOut(ac.employee_id)}
                      className="px-3 py-1.5 bg-red-100 text-red-700 text-xs font-medium rounded-lg hover:bg-red-200 transition-colors flex items-center gap-1"
                    >
                      <LogOut className="w-3 h-3" />
                      Out
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Employee Grid for Clock In */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {filteredActiveEmployees.map((emp) => {
              const isIn = activeSet.has(emp.id);
              return (
                <div
                  key={emp.id}
                  className={`rounded-lg border p-4 flex items-center justify-between transition-colors ${
                    isIn ? "bg-green-50 border-green-300" : "bg-white border-gray-200 hover:border-gray-300"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div
                      className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold ${
                        isIn ? "bg-green-200 text-green-800" : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {emp.name.charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <p className="font-medium text-gray-900 text-sm">{emp.name}</p>
                      <p className={`text-xs ${isIn ? "text-green-600" : "text-gray-400"}`}>
                        {isIn ? "Clocked In" : "Off"}
                      </p>
                    </div>
                  </div>
                  {isIn ? (
                    <button
                      onClick={() => handleClockOut(emp.id)}
                      className="px-3 py-2 bg-red-600 text-white text-xs font-medium rounded-lg hover:bg-red-700 transition-colors flex items-center gap-1"
                    >
                      <LogOut className="w-3 h-3" />
                      Clock Out
                    </button>
                  ) : (
                    <button
                      onClick={() => handleClockIn(emp.id)}
                      className="px-3 py-2 bg-green-600 text-white text-xs font-medium rounded-lg hover:bg-green-700 transition-colors flex items-center gap-1"
                    >
                      <LogIn className="w-3 h-3" />
                      Clock In
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          {filteredActiveEmployees.length === 0 && (
            <div className="text-center py-12 text-gray-500">
              <Users className="w-12 h-12 mx-auto mb-3 text-gray-300" />
              <p className="text-sm">
                {employees.length === 0
                  ? "No employees yet. Add employees in the Employees tab."
                  : "No matching employees found."}
              </p>
            </div>
          )}
        </div>
      )}

      {/* =================== TIMESHEET TAB =================== */}
      {tab === "timesheet" && (
        <div className="space-y-4">
          {/* Filters */}
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="flex flex-wrap gap-3 items-end">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Start Date</label>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => { setStartDate(e.target.value); setPage(1); }}
                  className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-green-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">End Date</label>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => { setEndDate(e.target.value); setPage(1); }}
                  className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-green-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Employee</label>
                <select
                  value={filterEmployee}
                  onChange={(e) => { setFilterEmployee(Number(e.target.value)); setPage(1); }}
                  className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-green-500"
                >
                  <option value={0}>All Employees</option>
                  {employees.map((e) => (
                    <option key={e.id} value={e.id}>{e.name}</option>
                  ))}
                </select>
              </div>
              <button
                onClick={handleExport}
                className="px-4 py-1.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors flex items-center gap-2"
              >
                <Download className="w-4 h-4" />
                Export CSV
              </button>
            </div>
          </div>

          {/* Summary Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">Total Hours</p>
              <p className="text-2xl font-bold text-gray-900">{totalHours.toFixed(1)}</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">Entries</p>
              <p className="text-2xl font-bold text-gray-900">{entries.length}</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">Employees</p>
              <p className="text-2xl font-bold text-gray-900">{Object.keys(employeeSummary).length}</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">Avg Hours/Entry</p>
              <p className="text-2xl font-bold text-gray-900">
                {entries.length > 0 ? (totalHours / entries.length).toFixed(1) : "0"}
              </p>
            </div>
          </div>

          {/* Per-Employee Summary */}
          {Object.keys(employeeSummary).length > 0 && (
            <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Employee</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Total Hours</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(employeeSummary)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([name, hrs]) => (
                      <tr key={name} className="border-b border-gray-100">
                        <td className="px-4 py-2 text-gray-900">{name}</td>
                        <td className="px-4 py-2 text-right font-medium text-gray-900">{formatHours(hrs)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Detailed Entries */}
          <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="text-left px-4 py-2 font-medium text-gray-600">Employee</th>
                  <th className="text-left px-4 py-2 font-medium text-gray-600">Date</th>
                  <th className="text-left px-4 py-2 font-medium text-gray-600">Clock In</th>
                  <th className="text-left px-4 py-2 font-medium text-gray-600">Clock Out</th>
                  <th className="text-right px-4 py-2 font-medium text-gray-600">Hours</th>
                  <th className="text-right px-4 py-2 font-medium text-gray-600">Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginatedEntries.map((e) => (
                  <tr key={e.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-900">{e.employee_name}</td>
                    <td className="px-4 py-2 text-gray-600">{formatDate(e.clock_in)}</td>
                    <td className="px-4 py-2 text-gray-600">{formatTime(e.clock_in)}</td>
                    <td className="px-4 py-2 text-gray-600">
                      {e.clock_out ? formatTime(e.clock_out) : (
                        <span className="text-green-600 font-medium">Active</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right font-medium text-gray-900">
                      {e.hours !== null ? formatHours(e.hours) : "---"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        onClick={() => handleDeleteEntry(e.id)}
                        className="text-gray-400 hover:text-red-600 transition-colors"
                        title="Delete entry"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
                {entries.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                      No time entries for this period
                    </td>
                  </tr>
                )}
              </tbody>
            </table>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50">
                <p className="text-xs text-gray-500">
                  Showing {(page - 1) * perPage + 1}–{Math.min(page * perPage, entries.length)} of {entries.length}
                </p>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                    className="p-1 rounded hover:bg-gray-200 disabled:opacity-30"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <span className="text-xs text-gray-600 px-2">
                    {page} / {totalPages}
                  </span>
                  <button
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page === totalPages}
                    className="p-1 rounded hover:bg-gray-200 disabled:opacity-30"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* =================== EMPLOYEES TAB =================== */}
      {tab === "employees" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-500">{employees.length} employee{employees.length !== 1 ? "s" : ""}</p>
            <div className="flex items-center gap-2">
              <button
                onClick={handleSyncFromClover}
                disabled={syncing}
                className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors flex items-center gap-2 disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
                {syncing ? "Syncing..." : "Sync from Clover"}
              </button>
              <button
                onClick={() => setShowAddEmployee(true)}
                className="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors flex items-center gap-2"
              >
                <UserPlus className="w-4 h-4" />
                Add Employee
              </button>
            </div>
          </div>

          {/* Add Employee Form */}
          {showAddEmployee && (
            <div className="bg-white border border-green-200 rounded-lg p-4">
              <h3 className="text-sm font-semibold text-gray-900 mb-3">New Employee</h3>
              <div className="flex gap-3 items-end">
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-600 mb-1">Name *</label>
                  <input
                    type="text"
                    value={newEmpName}
                    onChange={(e) => setNewEmpName(e.target.value)}
                    placeholder="Employee name"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500"
                    onKeyDown={(e) => e.key === "Enter" && handleAddEmployee()}
                  />
                </div>
                <div className="w-32">
                  <label className="block text-xs font-medium text-gray-600 mb-1">PIN (optional)</label>
                  <input
                    type="text"
                    value={newEmpPin}
                    onChange={(e) => setNewEmpPin(e.target.value)}
                    placeholder="1234"
                    maxLength={6}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500"
                  />
                </div>
                <button
                  onClick={handleAddEmployee}
                  className="px-4 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700"
                >
                  Add
                </button>
                <button
                  onClick={() => { setShowAddEmployee(false); setNewEmpName(""); setNewEmpPin(""); }}
                  className="px-4 py-2 bg-gray-100 text-gray-600 text-sm rounded-lg hover:bg-gray-200"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Employee List */}
          <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="text-left px-4 py-2 font-medium text-gray-600">Name</th>
                  <th className="text-left px-4 py-2 font-medium text-gray-600">PIN</th>
                  <th className="text-center px-4 py-2 font-medium text-gray-600">Status</th>
                  <th className="text-right px-4 py-2 font-medium text-gray-600">Actions</th>
                </tr>
              </thead>
              <tbody>
                {employees.map((emp) => (
                  <tr key={emp.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-3">
                      {editingEmp === emp.id ? (
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            value={editEmpName}
                            onChange={(e) => setEditEmpName(e.target.value)}
                            className="border border-gray-300 rounded px-2 py-1 text-sm w-48"
                            onKeyDown={(e) => e.key === "Enter" && handleSaveEditEmp(emp.id)}
                            autoFocus
                          />
                          <button onClick={() => handleSaveEditEmp(emp.id)} className="text-green-600 hover:text-green-800">
                            <Check className="w-4 h-4" />
                          </button>
                          <button onClick={() => setEditingEmp(null)} className="text-gray-400 hover:text-gray-600">
                            <X className="w-4 h-4" />
                          </button>
                        </div>
                      ) : (
                        <span className="text-gray-900 font-medium">{emp.name}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-500">{emp.pin || "---"}</td>
                    <td className="px-4 py-3 text-center">
                      <button
                        onClick={() => handleToggleActive(emp)}
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          emp.active
                            ? "bg-green-100 text-green-700 hover:bg-green-200"
                            : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                        }`}
                      >
                        {emp.active ? "Active" : "Inactive"}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => { setEditingEmp(emp.id); setEditEmpName(emp.name); }}
                          className="text-gray-400 hover:text-blue-600 transition-colors"
                          title="Edit name"
                        >
                          <Edit2 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDeleteEmployee(emp)}
                          className="text-gray-400 hover:text-red-600 transition-colors"
                          title="Delete employee"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {employees.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-gray-400">
                      No employees yet. Click "Add Employee" to get started.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {/* =================== SCHEDULE TAB =================== */}
      {tab === "schedule" && (
        <div className="space-y-4">
          {/* Employee filter */}
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="flex flex-wrap gap-3 items-end">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Employee</label>
                <select
                  value={scheduleEmployee}
                  onChange={(e) => setScheduleEmployee(Number(e.target.value))}
                  className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500"
                >
                  <option value={0}>All Employees</option>
                  {employees.filter((e) => e.active).map((e) => (
                    <option key={e.id} value={e.id}>{e.name}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Schedule Grid */}
          {scheduleEmployee > 0 ? (
            <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
              <div className="p-4 border-b border-gray-200">
                <h3 className="font-semibold text-gray-900">Weekly Schedule — {employees.find(e => e.id === scheduleEmployee)?.name}</h3>
              </div>
              <table className="w-full">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Day</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Start</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">End</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Location</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Notes</th>
                    <th className="text-right px-4 py-3 text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {[0, 1, 2, 3, 4, 5, 6].map((day) => {
                    const dayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
                    const existing = schedules.find(s => s.employee_id === scheduleEmployee && s.day_of_week === day);
                    const isEditing = editingSchedule?.day === day;
                    return (
                      <tr key={day} className="hover:bg-gray-50">
                        <td className="px-4 py-3 text-sm font-medium text-gray-900">{dayNames[day]}</td>
                        {isEditing ? (
                          <>
                            <td className="px-4 py-3">
                              <input type="time" value={editingSchedule.start} onChange={(e) => setEditingSchedule({ ...editingSchedule, start: e.target.value })} className="px-2 py-1 border border-gray-300 rounded text-sm" />
                            </td>
                            <td className="px-4 py-3">
                              <input type="time" value={editingSchedule.end} onChange={(e) => setEditingSchedule({ ...editingSchedule, end: e.target.value })} className="px-2 py-1 border border-gray-300 rounded text-sm" />
                            </td>
                            <td className="px-4 py-3">
                              <input type="text" value={editingSchedule.location} onChange={(e) => setEditingSchedule({ ...editingSchedule, location: e.target.value })} placeholder="Location" className="px-2 py-1 border border-gray-300 rounded text-sm w-full" />
                            </td>
                            <td className="px-4 py-3">
                              <input type="text" value={editingSchedule.notes} onChange={(e) => setEditingSchedule({ ...editingSchedule, notes: e.target.value })} placeholder="Notes" className="px-2 py-1 border border-gray-300 rounded text-sm w-full" />
                            </td>
                            <td className="px-4 py-3 text-right">
                              <div className="flex items-center justify-end gap-1">
                                <button
                                  disabled={savingSchedule || !editingSchedule.start || !editingSchedule.end}
                                  onClick={() => handleSaveSchedule(scheduleEmployee, day, editingSchedule.start, editingSchedule.end, editingSchedule.location, editingSchedule.notes)}
                                  className="p-1.5 text-green-600 hover:bg-green-50 rounded-lg disabled:opacity-50"
                                  title="Save"
                                >
                                  <Save className="w-4 h-4" />
                                </button>
                                <button onClick={() => setEditingSchedule(null)} className="p-1.5 text-gray-400 hover:bg-gray-100 rounded-lg" title="Cancel">
                                  <X className="w-4 h-4" />
                                </button>
                              </div>
                            </td>
                          </>
                        ) : existing ? (
                          <>
                            <td className="px-4 py-3 text-sm text-gray-700">{existing.start_time}</td>
                            <td className="px-4 py-3 text-sm text-gray-700">{existing.end_time}</td>
                            <td className="px-4 py-3 text-sm text-gray-500">{existing.location || "—"}</td>
                            <td className="px-4 py-3 text-sm text-gray-500">{existing.notes || "—"}</td>
                            <td className="px-4 py-3 text-right">
                              <div className="flex items-center justify-end gap-1">
                                <button
                                  onClick={() => setEditingSchedule({ day, start: existing.start_time, end: existing.end_time, location: existing.location || "", notes: existing.notes || "" })}
                                  className="p-1.5 text-blue-600 hover:bg-blue-50 rounded-lg" title="Edit"
                                >
                                  <Edit2 className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => handleDeleteSchedule(scheduleEmployee, day)}
                                  className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg" title="Remove"
                                >
                                  <Trash2 className="w-4 h-4" />
                                </button>
                              </div>
                            </td>
                          </>
                        ) : (
                          <>
                            <td colSpan={4} className="px-4 py-3 text-sm text-gray-400 italic">Off</td>
                            <td className="px-4 py-3 text-right">
                              <button
                                onClick={() => setEditingSchedule({ day, start: "09:00", end: "17:00", location: "", notes: "" })}
                                className="text-xs text-green-600 hover:text-green-700 font-medium"
                              >
                                + Add Shift
                              </button>
                            </td>
                          </>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            /* All employees overview */
            <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
              <div className="p-4 border-b border-gray-200">
                <h3 className="font-semibold text-gray-900">All Employee Schedules</h3>
              </div>
              {schedules.length === 0 ? (
                <div className="p-8 text-center text-gray-400">
                  <Calendar className="w-12 h-12 mx-auto mb-3 text-gray-300" />
                  <p>No schedules set yet. Select an employee above to create their schedule.</p>
                </div>
              ) : (
                <table className="w-full">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Employee</th>
                      <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Day</th>
                      <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Start</th>
                      <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">End</th>
                      <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Location</th>
                      <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Notes</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {schedules.map((s) => (
                      <tr key={s.id} className="hover:bg-gray-50">
                        <td className="px-4 py-3 text-sm font-medium text-gray-900">{s.employee_name}</td>
                        <td className="px-4 py-3 text-sm text-gray-700">{s.day_name}</td>
                        <td className="px-4 py-3 text-sm text-gray-700">{s.start_time}</td>
                        <td className="px-4 py-3 text-sm text-gray-700">{s.end_time}</td>
                        <td className="px-4 py-3 text-sm text-gray-500">{s.location || "—"}</td>
                        <td className="px-4 py-3 text-sm text-gray-500">{s.notes || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
