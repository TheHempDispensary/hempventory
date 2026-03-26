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
  updateTimeEntry,
  deleteTimeEntry,
  getTimeclockExportUrl,
  syncEmployeesFromClover,
  getSchedules,
  saveSchedule,
  deleteScheduleByDate,
  getTimeOffRequests,
  createTimeOffRequest,
  updateTimeOffRequest,
  deleteTimeOffRequest,
  getScheduleNotes,
  createScheduleNote,
  deleteScheduleNote,
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
  Plus,
  MessageSquare,
  CalendarOff,
} from "lucide-react";

interface Employee {
  id: number;
  name: string;
  pin: string | null;
  active: boolean;
  created_at: string;
  pay_rate: number | null;
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
  date: string;
  start_time: string;
  end_time: string;
  location: string | null;
  notes: string | null;
}

interface TimeOffItem {
  id: number;
  employee_id: number;
  employee_name: string;
  date: string;
  reason: string | null;
  status: string;
  reviewed_by: string | null;
  created_at: string;
}

interface ScheduleNote {
  id: number;
  date: string;
  note: string;
  created_by: string | null;
  created_at: string;
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
  const [editEmpPayRate, setEditEmpPayRate] = useState("");

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

  // Timesheet edit
  const [editingEntry, setEditingEntry] = useState<number | null>(null);
  const [editClockIn, setEditClockIn] = useState("");
  const [editClockOut, setEditClockOut] = useState("");

  // Sync
  const [syncing, setSyncing] = useState(false);

  // Schedule state
  const [schedules, setSchedules] = useState<ScheduleItem[]>([]);
  const [editingSchedule, setEditingSchedule] = useState<{ empId: number; date: string; start: string; end: string; location: string } | null>(null);
  const [savingSchedule, setSavingSchedule] = useState(false);

  // Monthly calendar state
  const [calMonth, setCalMonth] = useState(() => new Date().getMonth());
  const [calYear, setCalYear] = useState(() => new Date().getFullYear());
  const [monthsToShow, setMonthsToShow] = useState(1);
  const [timeOffRequests, setTimeOffRequests] = useState<TimeOffItem[]>([]);
  const [scheduleNotes, setScheduleNotes] = useState<ScheduleNote[]>([]);
  const [showTimeOffModal, setShowTimeOffModal] = useState(false);
  const [showNoteModal, setShowNoteModal] = useState(false);
  const [selectedDate, setSelectedDate] = useState("");
  const [timeOffEmpId, setTimeOffEmpId] = useState(0);
  const [timeOffReason, setTimeOffReason] = useState("");
  const [noteText, setNoteText] = useState("");
  const [scheduleSubTab, setScheduleSubTab] = useState<"calendar" | "requests">("calendar");

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
      const data: { name: string; pay_rate?: number } = { name: editEmpName.trim() };
      if (editEmpPayRate !== "") data.pay_rate = parseFloat(editEmpPayRate);
      await updateEmployee(id, data);
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

  const handleStartEditEntry = (entry: TimeEntry) => {
    setEditingEntry(entry.id);
    // Convert ISO to datetime-local format in EST
    const toLocal = (iso: string) => {
      const d = new Date(iso);
      const est = new Date(d.toLocaleString("en-US", { timeZone: "America/New_York" }));
      const y = est.getFullYear();
      const mo = String(est.getMonth() + 1).padStart(2, "0");
      const da = String(est.getDate()).padStart(2, "0");
      const h = String(est.getHours()).padStart(2, "0");
      const mi = String(est.getMinutes()).padStart(2, "0");
      return `${y}-${mo}-${da}T${h}:${mi}`;
    };
    setEditClockIn(toLocal(entry.clock_in));
    setEditClockOut(entry.clock_out ? toLocal(entry.clock_out) : "");
  };

  const handleSaveEditEntry = async (entryId: number) => {
    try {
      // Convert datetime-local (EST) back to UTC ISO
      const toUTC = (local: string) => {
        // Parse as EST then convert to UTC
        const d = new Date(local + ":00");
        // Create date in EST by interpreting the input as EST
        const estStr = d.toLocaleString("en-US", { timeZone: "America/New_York" });
        const estDate = new Date(estStr);
        const diff = d.getTime() - estDate.getTime();
        const utc = new Date(d.getTime() + diff);
        return utc.toISOString();
      };
      const data: { clock_in?: string; clock_out?: string } = {};
      if (editClockIn) data.clock_in = toUTC(editClockIn);
      if (editClockOut) data.clock_out = toUTC(editClockOut);
      await updateTimeEntry(entryId, data);
      showToast("success", "Entry updated");
      setEditingEntry(null);
      await loadEntries();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to update entry");
    }
  };

  // Compute date range for all visible months
  const getVisibleDateRange = useCallback(() => {
    const start = new Date(calYear, calMonth, 1);
    const end = new Date(calYear, calMonth + monthsToShow, 0);
    return {
      start_date: start.toISOString().split("T")[0],
      end_date: end.toISOString().split("T")[0],
    };
  }, [calYear, calMonth, monthsToShow]);

  const loadSchedules = useCallback(async () => {
    try {
      const range = getVisibleDateRange();
      const res = await getSchedules({ start_date: range.start_date, end_date: range.end_date });
      setSchedules(res.data);
    } catch (err) {
      console.error("Error loading schedules:", err);
    }
  }, [getVisibleDateRange]);

  const loadTimeOff = useCallback(async () => {
    try {
      const range = getVisibleDateRange();
      const res = await getTimeOffRequests({ start_date: range.start_date, end_date: range.end_date });
      setTimeOffRequests(res.data);
    } catch (err) {
      console.error("Error loading time-off requests:", err);
    }
  }, [getVisibleDateRange]);

  const loadNotes = useCallback(async () => {
    try {
      const range = getVisibleDateRange();
      const res = await getScheduleNotes({ start_date: range.start_date, end_date: range.end_date });
      setScheduleNotes(res.data);
    } catch (err) {
      console.error("Error loading schedule notes:", err);
    }
  }, [getVisibleDateRange]);

  useEffect(() => {
    if (tab === "schedule") {
      loadSchedules();
      loadTimeOff();
      loadNotes();
    }
  }, [tab, loadSchedules, loadTimeOff, loadNotes]);

  const handleSaveSchedule = async (empId: number, date: string, start: string, end: string, location: string) => {
    setSavingSchedule(true);
    try {
      await saveSchedule({ employee_id: empId, date, start_time: start, end_time: end, location: location || undefined });
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

  const handleDeleteSchedule = async (empId: number, date: string) => {
    try {
      await deleteScheduleByDate(empId, date);
      showToast("success", "Schedule entry removed");
      setEditingSchedule(null);
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
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Pay Rate</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Total Hours</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Est. Pay</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(employeeSummary)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([name, hrs]) => {
                      const emp = employees.find((e) => e.name === name);
                      const rate = emp?.pay_rate ?? null;
                      return (
                        <tr key={name} className="border-b border-gray-100">
                          <td className="px-4 py-2 text-gray-900">{name}</td>
                          <td className="px-4 py-2 text-right text-gray-600">
                            {rate !== null ? `$${rate.toFixed(2)}/hr` : "—"}
                          </td>
                          <td className="px-4 py-2 text-right font-medium text-gray-900">{formatHours(hrs)}</td>
                          <td className="px-4 py-2 text-right font-medium text-green-700">
                            {rate !== null ? `$${(hrs * rate).toFixed(2)}` : "—"}
                          </td>
                        </tr>
                      );
                    })}
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
                    <td className="px-4 py-2 text-gray-600">
                      {editingEntry === e.id ? (
                        <input
                          type="datetime-local"
                          value={editClockIn}
                          onChange={(ev) => setEditClockIn(ev.target.value)}
                          className="border border-gray-300 rounded px-2 py-1 text-xs w-44 focus:ring-2 focus:ring-green-500"
                        />
                      ) : (
                        formatTime(e.clock_in)
                      )}
                    </td>
                    <td className="px-4 py-2 text-gray-600">
                      {editingEntry === e.id ? (
                        <input
                          type="datetime-local"
                          value={editClockOut}
                          onChange={(ev) => setEditClockOut(ev.target.value)}
                          className="border border-gray-300 rounded px-2 py-1 text-xs w-44 focus:ring-2 focus:ring-green-500"
                        />
                      ) : e.clock_out ? formatTime(e.clock_out) : (
                        <span className="text-green-600 font-medium">Active</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right font-medium text-gray-900">
                      {e.hours !== null ? formatHours(e.hours) : "---"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {editingEntry === e.id ? (
                          <>
                            <button
                              onClick={() => handleSaveEditEntry(e.id)}
                              className="text-green-600 hover:text-green-800 transition-colors"
                              title="Save changes"
                            >
                              <Check className="w-4 h-4" />
                            </button>
                            <button
                              onClick={() => setEditingEntry(null)}
                              className="text-gray-400 hover:text-gray-600 transition-colors"
                              title="Cancel"
                            >
                              <X className="w-4 h-4" />
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              onClick={() => handleStartEditEntry(e)}
                              className="text-gray-400 hover:text-blue-600 transition-colors"
                              title="Edit entry"
                            >
                              <Edit2 className="w-4 h-4" />
                            </button>
                            <button
                              onClick={() => handleDeleteEntry(e.id)}
                              className="text-gray-400 hover:text-red-600 transition-colors"
                              title="Delete entry"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </>
                        )}
                      </div>
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
                  <th className="text-right px-4 py-2 font-medium text-gray-600">Pay Rate</th>
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
                    <td className="px-4 py-3 text-right">
                      {editingEmp === emp.id ? (
                        <input
                          type="number"
                          step="0.25"
                          min="0"
                          value={editEmpPayRate}
                          onChange={(e) => setEditEmpPayRate(e.target.value)}
                          className="border border-gray-300 rounded px-2 py-1 text-sm w-24 text-right"
                          placeholder="0.00"
                        />
                      ) : (
                        <span className="text-gray-600">
                          {emp.pay_rate !== null ? `$${emp.pay_rate.toFixed(2)}/hr` : "—"}
                        </span>
                      )}
                    </td>
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
                          onClick={() => { setEditingEmp(emp.id); setEditEmpName(emp.name); setEditEmpPayRate(emp.pay_rate !== null ? String(emp.pay_rate) : ""); }}
                          className="text-gray-400 hover:text-blue-600 transition-colors"
                          title="Edit employee"
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
                    <td colSpan={5} className="px-4 py-8 text-center text-gray-400">
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
      {tab === "schedule" && (() => {
        const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
        const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
        const activeEmps = employees.filter(e => e.active);

        // Build month data for rendering
        const renderMonths: Array<{ month: number; year: number; days: Date[] }> = [];
        for (let m = 0; m < monthsToShow; m++) {
          let mo = calMonth + m;
          let yr = calYear;
          if (mo > 11) { mo -= 12; yr += 1; }
          const daysInMonth = new Date(yr, mo + 1, 0).getDate();
          const days: Date[] = [];
          for (let d = 1; d <= daysInMonth; d++) days.push(new Date(yr, mo, d));
          renderMonths.push({ month: mo, year: yr, days });
        }

        // Build lookup maps — keyed by employee_id + date (YYYY-MM-DD)
        const scheduleMap: Record<string, ScheduleItem> = {};
        schedules.forEach(s => { scheduleMap[`${s.employee_id}-${s.date}`] = s; });
        const timeOffMap: Record<string, TimeOffItem> = {};
        timeOffRequests.forEach(t => { timeOffMap[`${t.employee_id}-${t.date}`] = t; });
        const notesMap: Record<string, ScheduleNote[]> = {};
        scheduleNotes.forEach(n => { notesMap[n.date] = notesMap[n.date] || []; notesMap[n.date].push(n); });

        const fmtTime12 = (t: string) => {
          const [h, mi] = t.split(":").map(Number);
          const ampm = h >= 12 ? "p" : "a";
          const hr = h === 0 ? 12 : h > 12 ? h - 12 : h;
          return mi === 0 ? `${hr}${ampm}` : `${hr}:${mi.toString().padStart(2, "0")}${ampm}`;
        };

        const handleAddTimeOff = async () => {
          if (!timeOffEmpId || !selectedDate) return;
          try {
            await createTimeOffRequest({ employee_id: timeOffEmpId, date: selectedDate, reason: timeOffReason || undefined });
            showToast("success", "Time-off request created");
            setShowTimeOffModal(false);
            setTimeOffEmpId(0);
            setTimeOffReason("");
            setSelectedDate("");
            await loadTimeOff();
          } catch { showToast("error", "Failed to create time-off request"); }
        };

        const handleApproveTimeOff = async (id: number) => {
          try {
            await updateTimeOffRequest(id, "approved");
            showToast("success", "Time-off approved");
            await loadTimeOff();
          } catch { showToast("error", "Failed to update"); }
        };

        const handleDenyTimeOff = async (id: number) => {
          try {
            await updateTimeOffRequest(id, "denied");
            showToast("success", "Time-off denied");
            await loadTimeOff();
          } catch { showToast("error", "Failed to update"); }
        };

        const handleRemoveTimeOff = async (id: number) => {
          try {
            await deleteTimeOffRequest(id);
            showToast("success", "Time-off removed");
            await loadTimeOff();
          } catch { showToast("error", "Failed to delete"); }
        };

        const handleAddNote = async () => {
          if (!selectedDate || !noteText.trim()) return;
          try {
            await createScheduleNote({ date: selectedDate, note: noteText.trim() });
            showToast("success", "Note added");
            setShowNoteModal(false);
            setNoteText("");
            setSelectedDate("");
            await loadNotes();
          } catch { showToast("error", "Failed to add note"); }
        };

        const handleRemoveNote = async (id: number) => {
          try {
            await deleteScheduleNote(id);
            showToast("success", "Note removed");
            await loadNotes();
          } catch { showToast("error", "Failed to delete note"); }
        };

        const prevMonth = () => {
          if (calMonth === 0) { setCalMonth(11); setCalYear(calYear - 1); }
          else setCalMonth(calMonth - 1);
        };
        const nextMonth = () => {
          if (calMonth === 11) { setCalMonth(0); setCalYear(calYear + 1); }
          else setCalMonth(calMonth + 1);
        };
        const goToToday = () => {
          const now = new Date();
          setCalMonth(now.getMonth());
          setCalYear(now.getFullYear());
        };

        return (
        <div className="space-y-4">
          {/* Sub-tabs */}
          <div className="flex flex-wrap gap-2 items-center justify-between">
            <div className="flex gap-1 bg-gray-100 p-1 rounded-lg">
              {([
                { id: "calendar" as const, label: "Monthly View", icon: Calendar },
                { id: "requests" as const, label: "Time-Off Requests", icon: CalendarOff },
              ]).map(st => (
                <button key={st.id} onClick={() => setScheduleSubTab(st.id)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${scheduleSubTab === st.id ? "bg-white text-green-700 shadow-sm" : "text-gray-600 hover:text-gray-900"}`}>
                  <st.icon className="w-3.5 h-3.5" />{st.label}
                </button>
              ))}
            </div>
          </div>

          {/* =================== CALENDAR VIEW =================== */}
          {scheduleSubTab === "calendar" && (
            <div className="space-y-4">
              {/* Controls */}
              <div className="bg-white border border-gray-200 rounded-lg p-3 flex flex-wrap gap-3 items-center justify-between">
                <div className="flex items-center gap-2">
                  <button onClick={prevMonth} className="p-1.5 hover:bg-gray-100 rounded-lg"><ChevronLeft className="w-5 h-5" /></button>
                  <span className="font-semibold text-gray-900 min-w-48 text-center">
                    {monthNames[calMonth]} {calYear}
                    {monthsToShow > 1 && ` — ${monthNames[(calMonth + monthsToShow - 1) % 12]} ${calMonth + monthsToShow - 1 > 11 ? calYear + 1 : calYear}`}
                  </span>
                  <button onClick={nextMonth} className="p-1.5 hover:bg-gray-100 rounded-lg"><ChevronRight className="w-5 h-5" /></button>
                  <button onClick={goToToday} className="ml-2 px-3 py-1 text-xs font-medium text-green-700 bg-green-50 rounded-lg hover:bg-green-100">Today</button>
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-500 font-medium">Months:</label>
                  {[1, 2, 3].map(n => (
                    <button key={n} onClick={() => setMonthsToShow(n)}
                      className={`px-2.5 py-1 text-xs font-medium rounded-lg transition-colors ${monthsToShow === n ? "bg-green-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
                      {n}
                    </button>
                  ))}
                  <button onClick={() => { setShowNoteModal(true); setSelectedDate(new Date().toISOString().split("T")[0]); }}
                    className="ml-3 flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-blue-700 bg-blue-50 rounded-lg hover:bg-blue-100">
                    <MessageSquare className="w-3 h-3" />Add Note
                  </button>
                  <button onClick={() => { setShowTimeOffModal(true); setSelectedDate(new Date().toISOString().split("T")[0]); }}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-amber-700 bg-amber-50 rounded-lg hover:bg-amber-100">
                    <CalendarOff className="w-3 h-3" />Request Off
                  </button>
                </div>
              </div>

              {/* Month calendars */}
              {renderMonths.map(({ month: mo, year: yr, days }) => {
                return (
                  <div key={`${yr}-${mo}`} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                    <div className="p-3 border-b border-gray-200 bg-gray-50">
                      <h3 className="font-semibold text-gray-900 text-center">{monthNames[mo]} {yr}</h3>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full border-collapse text-xs">
                        <thead>
                          <tr className="bg-gray-50">
                            <th className="px-2 py-2 text-left text-xs font-semibold text-gray-600 border-b border-r border-gray-200 sticky left-0 bg-gray-50 z-10 min-w-28">Employee</th>
                            {days.map((d, i) => {
                              const dateStr = d.toISOString().split("T")[0];
                              const isToday = dateStr === new Date().toISOString().split("T")[0];
                              const isWeekend = d.getDay() === 0 || d.getDay() === 6;
                              const dayNotes = notesMap[dateStr];
                              return (
                                <th key={i} className={`px-1 py-1 text-center border-b border-r border-gray-200 min-w-16 ${isToday ? "bg-green-100" : isWeekend ? "bg-gray-100" : ""}`}
                                  title={dayNotes ? dayNotes.map(n => n.note).join("; ") : undefined}>
                                  <div className={`text-xs font-medium ${isToday ? "text-green-800" : "text-gray-500"}`}>{dayNames[d.getDay()]}</div>
                                  <div className={`text-sm font-bold ${isToday ? "text-green-700" : "text-gray-900"}`}>{d.getDate()}</div>
                                  {dayNotes && <MessageSquare className="w-3 h-3 text-blue-500 mx-auto" />}
                                </th>
                              );
                            })}
                          </tr>
                        </thead>
                        <tbody>
                          {activeEmps.map(emp => (
                            <tr key={emp.id} className="hover:bg-gray-50/50">
                              <td className="px-2 py-1.5 text-sm font-medium text-gray-900 border-r border-b border-gray-200 sticky left-0 bg-white z-10 whitespace-nowrap">
                                {emp.name.split(" ")[0]}
                              </td>
                              {days.map((d, i) => {
                                const dow = d.getDay();
                                const dateStr = d.toISOString().split("T")[0];
                                const sched = scheduleMap[`${emp.id}-${dateStr}`];
                                const timeOff = timeOffMap[`${emp.id}-${dateStr}`];
                                const isToday = dateStr === new Date().toISOString().split("T")[0];
                                const isWeekend = dow === 0 || dow === 6;
                                const isEditingThis = editingSchedule?.empId === emp.id && editingSchedule?.date === dateStr;

                                if (timeOff && timeOff.status === "approved") {
                                  return (
                                    <td key={i} className={`px-1 py-1 text-center border-r border-b border-gray-200 ${isToday ? "bg-green-50" : ""}`}>
                                      <span className="inline-block px-1 py-0.5 rounded text-xs font-bold bg-red-100 text-red-700" title={timeOff.reason || "Time Off"}>OFF</span>
                                    </td>
                                  );
                                }

                                if (isEditingThis) {
                                  return (
                                    <td key={i} className="px-1 py-1 border-r border-b border-gray-200 bg-yellow-50 min-w-32">
                                      <div className="space-y-0.5">
                                        <input type="time" value={editingSchedule.start} onChange={e => setEditingSchedule({ ...editingSchedule, start: e.target.value })}
                                          className="w-full px-1 py-0.5 border border-gray-300 rounded text-xs" />
                                        <input type="time" value={editingSchedule.end} onChange={e => setEditingSchedule({ ...editingSchedule, end: e.target.value })}
                                          className="w-full px-1 py-0.5 border border-gray-300 rounded text-xs" />
                                        <input type="text" value={editingSchedule.location} onChange={e => setEditingSchedule({ ...editingSchedule, location: e.target.value })}
                                          placeholder="Loc" className="w-full px-1 py-0.5 border border-gray-300 rounded text-xs" />
                                        <div className="flex gap-0.5 justify-center pt-0.5">
                                          <button disabled={savingSchedule || !editingSchedule.start || !editingSchedule.end}
                                            onClick={() => handleSaveSchedule(emp.id, dateStr, editingSchedule.start, editingSchedule.end, editingSchedule.location)}
                                            className="p-0.5 text-green-600 hover:bg-green-100 rounded disabled:opacity-50" title="Save"><Save className="w-3.5 h-3.5" /></button>
                                          {sched && <button onClick={() => handleDeleteSchedule(emp.id, dateStr)}
                                            className="p-0.5 text-red-600 hover:bg-red-100 rounded" title="Remove"><Trash2 className="w-3.5 h-3.5" /></button>}
                                          <button onClick={() => setEditingSchedule(null)}
                                            className="p-0.5 text-gray-400 hover:bg-gray-100 rounded" title="Cancel"><X className="w-3.5 h-3.5" /></button>
                                        </div>
                                      </div>
                                    </td>
                                  );
                                }

                                if (sched) {
                                  return (
                                    <td key={i} className={`px-1 py-1 text-center border-r border-b border-gray-200 cursor-pointer hover:bg-blue-50 ${isToday ? "bg-green-50" : ""}`}
                                      onClick={() => setEditingSchedule({ empId: emp.id, date: dateStr, start: sched.start_time, end: sched.end_time, location: sched.location || "" })}>
                                      <div className="text-xs text-gray-800 leading-tight font-medium">{fmtTime12(sched.start_time)}</div>
                                      <div className="text-xs text-gray-500 leading-tight">{fmtTime12(sched.end_time)}</div>
                                      {sched.location && <div className="text-xs text-blue-600 leading-tight truncate" title={sched.location}>{sched.location}</div>}
                                      {timeOff && timeOff.status === "pending" && (
                                        <span className="inline-block w-2 h-2 rounded-full bg-amber-400 mt-0.5" title="Pending time-off request" />
                                      )}
                                    </td>
                                  );
                                }

                                return (
                                  <td key={i} className={`px-1 py-1 text-center border-r border-b border-gray-200 cursor-pointer hover:bg-blue-50 ${isToday ? "bg-green-50" : isWeekend ? "bg-gray-50" : ""}`}
                                    onClick={() => setEditingSchedule({ empId: emp.id, date: dateStr, start: "09:00", end: "17:00", location: "" })}>
                                    <span className="text-gray-300 text-xs">—</span>
                                    {timeOff && timeOff.status === "pending" && (
                                      <span className="inline-block w-2 h-2 rounded-full bg-amber-400 mt-0.5" title="Pending time-off request" />
                                    )}
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                          {/* Notes row */}
                          <tr className="bg-blue-50/50">
                            <td className="px-2 py-1.5 text-xs font-medium text-blue-700 border-r border-b border-gray-200 sticky left-0 bg-blue-50 z-10">
                              <MessageSquare className="w-3 h-3 inline mr-1" />Notes
                            </td>
                            {days.map((d, i) => {
                              const dateStr = d.toISOString().split("T")[0];
                              const dayNotes = notesMap[dateStr];
                              return (
                                <td key={i} className="px-1 py-1 text-center border-r border-b border-gray-200 text-xs text-blue-700">
                                  {dayNotes ? dayNotes.map(n => (
                                    <div key={n.id} className="flex items-center gap-0.5 justify-center" title={n.note}>
                                      <span className="truncate max-w-14">{n.note}</span>
                                      <button onClick={() => handleRemoveNote(n.id)} className="text-red-400 hover:text-red-600 flex-shrink-0"><X className="w-2.5 h-2.5" /></button>
                                    </div>
                                  )) : (
                                    <button onClick={() => { setSelectedDate(dateStr); setShowNoteModal(true); }}
                                      className="text-gray-300 hover:text-blue-500 transition-colors"><Plus className="w-3 h-3 mx-auto" /></button>
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    {/* Legend */}
                    <div className="p-2 border-t border-gray-200 flex flex-wrap gap-4 text-xs text-gray-500">
                      <span className="flex items-center gap-1"><span className="w-3 h-3 rounded bg-green-100 border border-green-300" /> Today</span>
                      <span className="flex items-center gap-1"><span className="inline-block px-1 rounded text-xs font-bold bg-red-100 text-red-700">OFF</span> Approved Time Off</span>
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-400" /> Pending Request</span>
                      <span className="flex items-center gap-1"><MessageSquare className="w-3 h-3 text-blue-500" /> Has Notes</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* =================== TIME-OFF REQUESTS =================== */}
          {scheduleSubTab === "requests" && (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <h3 className="font-semibold text-gray-900">Time-Off Requests</h3>
                <button onClick={() => { setShowTimeOffModal(true); setSelectedDate(new Date().toISOString().split("T")[0]); }}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium bg-amber-50 text-amber-700 rounded-lg hover:bg-amber-100">
                  <Plus className="w-4 h-4" />New Request
                </button>
              </div>
              {timeOffRequests.length === 0 ? (
                <div className="bg-white border border-gray-200 rounded-lg p-8 text-center text-gray-400">
                  <CalendarOff className="w-12 h-12 mx-auto mb-3 text-gray-300" />
                  <p>No time-off requests for this period.</p>
                </div>
              ) : (
                <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                  <table className="w-full">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Employee</th>
                        <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Date</th>
                        <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Reason</th>
                        <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th className="text-right px-4 py-3 text-xs font-medium text-gray-500 uppercase">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {timeOffRequests.map(req => (
                        <tr key={req.id} className="hover:bg-gray-50">
                          <td className="px-4 py-3 text-sm font-medium text-gray-900">{req.employee_name}</td>
                          <td className="px-4 py-3 text-sm text-gray-700">{new Date(req.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}</td>
                          <td className="px-4 py-3 text-sm text-gray-500">{req.reason || "—"}</td>
                          <td className="px-4 py-3">
                            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                              req.status === "approved" ? "bg-green-100 text-green-700" :
                              req.status === "denied" ? "bg-red-100 text-red-700" :
                              "bg-amber-100 text-amber-700"
                            }`}>{req.status}</span>
                          </td>
                          <td className="px-4 py-3 text-right">
                            <div className="flex items-center justify-end gap-1">
                              {req.status === "pending" && (
                                <>
                                  <button onClick={() => handleApproveTimeOff(req.id)} className="p-1.5 text-green-600 hover:bg-green-50 rounded-lg" title="Approve"><Check className="w-4 h-4" /></button>
                                  <button onClick={() => handleDenyTimeOff(req.id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg" title="Deny"><X className="w-4 h-4" /></button>
                                </>
                              )}
                              <button onClick={() => handleRemoveTimeOff(req.id)} className="p-1.5 text-gray-400 hover:bg-gray-100 rounded-lg" title="Delete"><Trash2 className="w-4 h-4" /></button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Time-Off Modal */}
          {showTimeOffModal && (
            <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setShowTimeOffModal(false)}>
              <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
                <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2"><CalendarOff className="w-5 h-5 text-amber-600" />Request Time Off</h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Employee</label>
                    <select value={timeOffEmpId} onChange={e => setTimeOffEmpId(Number(e.target.value))}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500">
                      <option value={0}>Select employee...</option>
                      {activeEmps.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Date</label>
                    <input type="date" value={selectedDate} onChange={e => setSelectedDate(e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Reason (optional)</label>
                    <input type="text" value={timeOffReason} onChange={e => setTimeOffReason(e.target.value)} placeholder="Vacation, appointment, etc."
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button onClick={handleAddTimeOff} disabled={!timeOffEmpId || !selectedDate}
                      className="flex-1 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 font-medium text-sm">Submit Request</button>
                    <button onClick={() => setShowTimeOffModal(false)} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium text-sm">Cancel</button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Note Modal */}
          {showNoteModal && (
            <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setShowNoteModal(false)}>
              <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
                <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2"><MessageSquare className="w-5 h-5 text-blue-600" />Add Schedule Note</h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Date</label>
                    <input type="date" value={selectedDate} onChange={e => setSelectedDate(e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Note</label>
                    <textarea value={noteText} onChange={e => setNoteText(e.target.value)} placeholder="Staff meeting, holiday, special event..."
                      rows={3} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 resize-y" />
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button onClick={handleAddNote} disabled={!selectedDate || !noteText.trim()}
                      className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium text-sm">Add Note</button>
                    <button onClick={() => setShowNoteModal(false)} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium text-sm">Cancel</button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
        );
      })()}
    </div>
  );
}
