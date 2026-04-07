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
  updateScheduleNote,
  deleteScheduleNote,
  createManualEntry,
  getScheduleHours,
  saveBulkSchedule,
  getShiftRequests,
  updateShiftRequest,
  deleteShiftRequest,
  syncTipsFromClover,
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
  tips: number;
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
  note_type?: string;
  employee_id?: number | null;
  employee_name?: string;
}

interface ScheduleHours {
  employee_id: number;
  employee_name: string;
  total_hours: number;
  shift_count: number;
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

  // Timesheet filters (datetime-local values for time-precise filtering)
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - d.getDay()); // Start of current week (Sunday)
    return d.toISOString().split("T")[0];
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [filterEmployee, setFilterEmployee] = useState<number | 0>(0);
  const [searchActive, setSearchActive] = useState("");

  // Pagination for timesheet
  const [page, setPage] = useState(1);
  const perPage = 25;

  // Timesheet edit
  const [editingEntry, setEditingEntry] = useState<number | null>(null);
  const [editClockIn, setEditClockIn] = useState("");
  const [editClockOut, setEditClockOut] = useState("");
  const [editTips, setEditTips] = useState("");

  // Sync
  const [syncing, setSyncing] = useState(false);

  // Manual entry form
  const [showManualEntry, setShowManualEntry] = useState(false);
  const [manualEmpId, setManualEmpId] = useState(0);
  const [manualClockIn, setManualClockIn] = useState("");
  const [manualClockOut, setManualClockOut] = useState("");
  const [savingManual, setSavingManual] = useState(false);

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
  const [noteType, setNoteType] = useState<"shared" | "admin_only" | "employee_private">("shared");
  const [noteEmpId, setNoteEmpId] = useState(0);
  const [scheduleSubTab, setScheduleSubTab] = useState<"calendar" | "requests" | "shift_requests">("calendar");
  const [hoursView, setHoursView] = useState<"week" | "month">("week");
  const [weeklyHours, setWeeklyHours] = useState<ScheduleHours[]>([]);
  const [monthlyHours, setMonthlyHours] = useState<ScheduleHours[]>([]);
  const [editingNoteId, setEditingNoteId] = useState<number | null>(null);
  const [editNoteText, setEditNoteText] = useState("");

  // Multi-day scheduling modal
  const [showBulkScheduleModal, setShowBulkScheduleModal] = useState(false);
  const [bulkEmpId, setBulkEmpId] = useState(0);
  const [bulkStartTime, setBulkStartTime] = useState("09:00");
  const [bulkEndTime, setBulkEndTime] = useState("17:00");
  const [bulkLocation, setBulkLocation] = useState("");
  const [bulkDates, setBulkDates] = useState<string[]>([]);
  const [savingBulk, setSavingBulk] = useState(false);

  // Shift requests state
  interface ShiftRequest {
    id: number;
    request_type: string;
    requester_id: number;
    requester_name: string;
    schedule_id: number;
    target_schedule_id: number | null;
    message: string | null;
    status: string;
    reviewed_by: string | null;
    created_at: string;
    shift_date: string;
    shift_start: string;
    shift_end: string;
    shift_location: string | null;
    shift_employee_id: number;
    shift_employee_name: string;
    target_date: string | null;
    target_start: string | null;
    target_end: string | null;
    target_location: string | null;
    target_employee_id: number | null;
    target_employee_name: string | null;
  }
  const [shiftRequests, setShiftRequests] = useState<ShiftRequest[]>([]);

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
      if (startDate) {
        // If time is set, combine date+time into ISO format for precise filtering
        params.start_date = startTime ? `${startDate}T${startTime}:00` : startDate;
      }
      if (endDate) {
        params.end_date = endTime ? `${endDate}T${endTime}:00` : endDate;
      }
      if (filterEmployee) params.employee_id = filterEmployee;
      const res = await getTimeEntries(params);
      setEntries(res.data);
    } catch (err) {
      console.error("Error loading entries:", err);
    }
  }, [startDate, startTime, endDate, endTime, filterEmployee]);

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

  const [syncingTips, setSyncingTips] = useState(false);

  const handleSyncTips = async () => {
    setSyncingTips(true);
    try {
      const res = await syncTipsFromClover({
        start_date: startDate || undefined,
        end_date: endDate || undefined,
      });
      const { updated_entries, total_tips_synced, errors } = res.data;
      let msg = `Synced $${total_tips_synced.toFixed(2)} in tips across ${updated_entries} entries`;
      if (errors && errors.length > 0) {
        msg += `. Errors: ${errors.map((e: { location: string; error: string }) => e.location).join(", ")}`;
      }
      showToast(errors && errors.length > 0 ? "error" : "success", msg);
      await loadEntries();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to sync tips from Clover");
    } finally {
      setSyncingTips(false);
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
    setEditTips(entry.tips ? String(entry.tips) : "");
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
      const data: { clock_in?: string; clock_out?: string; tips?: number } = {};
      if (editClockIn) data.clock_in = toUTC(editClockIn);
      if (editClockOut) data.clock_out = toUTC(editClockOut);
      if (editTips !== "") data.tips = parseFloat(editTips) || 0;
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

  const loadHours = useCallback(async () => {
    try {
      // Load monthly hours (full visible range)
      const range = getVisibleDateRange();
      const monthRes = await getScheduleHours({ start_date: range.start_date, end_date: range.end_date });
      setMonthlyHours(monthRes.data);

      // Load weekly hours (current week: Sunday to Saturday)
      const now = new Date();
      const weekStart = new Date(now);
      weekStart.setDate(now.getDate() - now.getDay());
      const weekEnd = new Date(weekStart);
      weekEnd.setDate(weekStart.getDate() + 6);
      const weekRes = await getScheduleHours({
        start_date: weekStart.toISOString().split("T")[0],
        end_date: weekEnd.toISOString().split("T")[0],
      });
      setWeeklyHours(weekRes.data);
    } catch (err) {
      console.error("Error loading schedule hours:", err);
    }
  }, [getVisibleDateRange]);

  const loadShiftRequests = useCallback(async () => {
    try {
      const res = await getShiftRequests();
      setShiftRequests(res.data);
    } catch (err) {
      console.error("Error loading shift requests:", err);
    }
  }, []);

  useEffect(() => {
    if (tab === "schedule") {
      loadSchedules();
      loadTimeOff();
      loadNotes();
      loadHours();
      loadShiftRequests();
    }
  }, [tab, loadSchedules, loadTimeOff, loadNotes, loadHours, loadShiftRequests]);

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

  const handleSaveBulkSchedule = async () => {
    if (!bulkEmpId || bulkDates.length === 0 || !bulkStartTime || !bulkEndTime) {
      showToast("error", "Please select an employee and at least one date");
      return;
    }
    setSavingBulk(true);
    try {
      await saveBulkSchedule({
        employee_id: bulkEmpId,
        dates: bulkDates,
        start_time: bulkStartTime,
        end_time: bulkEndTime,
        location: bulkLocation || undefined,
      });
      showToast("success", `Scheduled ${bulkDates.length} day${bulkDates.length > 1 ? "s" : ""}`);
      setShowBulkScheduleModal(false);
      setBulkDates([]);
      setBulkEmpId(0);
      setBulkStartTime("09:00");
      setBulkEndTime("17:00");
      setBulkLocation("");
      await loadSchedules();
      await loadHours();
    } catch (err) {
      console.error(err);
      showToast("error", "Failed to save bulk schedule");
    } finally {
      setSavingBulk(false);
    }
  };

  const toggleBulkDate = (dateStr: string) => {
    setBulkDates(prev => prev.includes(dateStr) ? prev.filter(d => d !== dateStr) : [...prev, dateStr].sort());
  };

  const handleApproveShiftRequest = async (id: number) => {
    try {
      await updateShiftRequest(id, "approved");
      showToast("success", "Shift request approved");
      await loadShiftRequests();
      await loadSchedules();
    } catch { showToast("error", "Failed to approve"); }
  };

  const handleDenyShiftRequest = async (id: number) => {
    try {
      await updateShiftRequest(id, "denied");
      showToast("success", "Shift request denied");
      await loadShiftRequests();
    } catch { showToast("error", "Failed to deny"); }
  };

  const handleDeleteShiftRequest = async (id: number) => {
    try {
      await deleteShiftRequest(id);
      showToast("success", "Shift request removed");
      await loadShiftRequests();
    } catch { showToast("error", "Failed to delete"); }
  };

  const handleExport = () => {
    const params: { start_date?: string; end_date?: string; employee_id?: number } = {};
    if (startDate) params.start_date = startTime ? `${startDate}T${startTime}:00` : startDate;
    if (endDate) params.end_date = endTime ? `${endDate}T${endTime}:00` : endDate;
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
    const mins = Math.floor((h - hrs) * 60);
    return `${hrs}h ${mins}m`;
  };

  const handleAddManualEntry = async () => {
    if (!manualEmpId || !manualClockIn || !manualClockOut) {
      showToast("error", "Please fill in all fields");
      return;
    }
    setSavingManual(true);
    try {
      // Convert datetime-local (EST) to UTC ISO
      const toUTC = (local: string) => {
        const d = new Date(local + ":00");
        const estStr = d.toLocaleString("en-US", { timeZone: "America/New_York" });
        const estDate = new Date(estStr);
        const diff = d.getTime() - estDate.getTime();
        const utc = new Date(d.getTime() + diff);
        return utc.toISOString();
      };
      const res = await createManualEntry({
        employee_id: manualEmpId,
        clock_in: toUTC(manualClockIn),
        clock_out: toUTC(manualClockOut),
      });
      showToast("success", `Manual entry added for ${res.data.employee} (${formatHours(res.data.hours)})`);
      setShowManualEntry(false);
      setManualEmpId(0);
      setManualClockIn("");
      setManualClockOut("");
      await loadEntries();
    } catch (err) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      showToast("error", axErr?.response?.data?.detail || "Failed to add manual entry");
    } finally {
      setSavingManual(false);
    }
  };

  const activeSet = new Set(activeClocks.map((c) => c.employee_id));

  // Filtered active employees for clock tab
  const filteredActiveEmployees = employees
    .filter((e) => e.active)
    .filter((e) => !searchActive || e.name.toLowerCase().includes(searchActive.toLowerCase()));

  // Timesheet pagination
  const totalPages = Math.ceil(entries.length / perPage);
  const paginatedEntries = entries.slice((page - 1) * perPage, page * perPage);

  // Summary for timesheet — truncate to 2 decimal places (no rounding up)
  const totalHoursRaw = entries.reduce((sum, e) => sum + (e.hours || 0), 0);
  const totalHours = Math.floor(totalHoursRaw * 100) / 100;
  const totalTips = entries.reduce((sum, e) => sum + (e.tips || 0), 0);
  const employeeSummary: Record<string, { hours: number; tips: number }> = {};
  entries.forEach((e) => {
    if (!employeeSummary[e.employee_name]) employeeSummary[e.employee_name] = { hours: 0, tips: 0 };
    employeeSummary[e.employee_name].hours += (e.hours || 0);
    employeeSummary[e.employee_name].tips += (e.tips || 0);
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
                <div className="flex gap-1">
                  <input
                    type="date"
                    value={startDate}
                    onChange={(e) => { setStartDate(e.target.value); setPage(1); }}
                    className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-green-500"
                  />
                  <input
                    type="time"
                    value={startTime}
                    onChange={(e) => { setStartTime(e.target.value); setPage(1); }}
                    className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:ring-2 focus:ring-green-500 w-28"
                    placeholder="Time"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">End Date</label>
                <div className="flex gap-1">
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => { setEndDate(e.target.value); setPage(1); }}
                    className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-green-500"
                  />
                  <input
                    type="time"
                    value={endTime}
                    onChange={(e) => { setEndTime(e.target.value); setPage(1); }}
                    className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:ring-2 focus:ring-green-500 w-28"
                    placeholder="Time"
                  />
                </div>
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
                onClick={handleSyncTips}
                disabled={syncingTips}
                className="px-4 py-1.5 bg-purple-600 text-white text-sm font-medium rounded-lg hover:bg-purple-700 transition-colors flex items-center gap-2 disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${syncingTips ? "animate-spin" : ""}`} />
                {syncingTips ? "Syncing..." : "Sync Tips"}
              </button>
              <button
                onClick={handleExport}
                className="px-4 py-1.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors flex items-center gap-2"
              >
                <Download className="w-4 h-4" />
                Export CSV
              </button>
              <button
                onClick={() => setShowManualEntry(!showManualEntry)}
                className="px-4 py-1.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Add Entry
              </button>
            </div>
          </div>

          {/* Manual Entry Form */}
          {showManualEntry && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
              <h4 className="text-sm font-semibold text-green-800 mb-3">Add Manual Time Entry</h4>
              <div className="flex flex-wrap gap-3 items-end">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Employee</label>
                  <select
                    value={manualEmpId}
                    onChange={(e) => setManualEmpId(Number(e.target.value))}
                    className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-green-500"
                  >
                    <option value={0}>Select Employee</option>
                    {employees.filter(e => e.active).map((e) => (
                      <option key={e.id} value={e.id}>{e.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Clock In</label>
                  <input
                    type="datetime-local"
                    value={manualClockIn}
                    onChange={(e) => setManualClockIn(e.target.value)}
                    className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-green-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Clock Out</label>
                  <input
                    type="datetime-local"
                    value={manualClockOut}
                    onChange={(e) => setManualClockOut(e.target.value)}
                    className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-green-500"
                  />
                </div>
                <button
                  onClick={handleAddManualEntry}
                  disabled={savingManual}
                  className="px-4 py-1.5 bg-green-700 text-white text-sm font-medium rounded-lg hover:bg-green-800 disabled:opacity-50 transition-colors"
                >
                  {savingManual ? "Saving..." : "Save Entry"}
                </button>
                <button
                  onClick={() => setShowManualEntry(false)}
                  className="px-4 py-1.5 bg-gray-200 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-300 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Summary Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">Total Hours</p>
              <p className="text-2xl font-bold text-gray-900">{formatHours(totalHours)}</p>
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
              <p className="text-xs text-gray-500 mb-1">Total Tips</p>
              <p className="text-2xl font-bold text-green-700">${totalTips.toFixed(2)}</p>
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
                      <th className="text-right px-4 py-2 font-medium text-gray-600">Tips</th>
                      <th className="text-right px-4 py-2 font-medium text-gray-600">Est. Pay</th>
                    </tr>
                </thead>
                <tbody>
                  {Object.entries(employeeSummary)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([name, data]) => {
                      const emp = employees.find((e) => e.name === name);
                      const rate = emp?.pay_rate ?? null;
                      return (
                        <tr key={name} className="border-b border-gray-100">
                          <td className="px-4 py-2 text-gray-900">{name}</td>
                          <td className="px-4 py-2 text-right text-gray-600">
                            {rate !== null ? `$${rate.toFixed(2)}/hr` : "—"}
                          </td>
                          <td className="px-4 py-2 text-right font-medium text-gray-900">{formatHours(data.hours)}</td>
                          <td className="px-4 py-2 text-right font-medium text-green-700">
                            ${data.tips.toFixed(2)}
                          </td>
                          <td className="px-4 py-2 text-right font-medium text-green-700">
                            {rate !== null ? `$${(data.hours * rate + data.tips).toFixed(2)}` : "—"}
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
                  <th className="text-right px-4 py-2 font-medium text-gray-600">Tips</th>
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
                    <td className="px-4 py-2 text-right text-green-700">
                      {editingEntry === e.id ? (
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          value={editTips}
                          onChange={(ev) => setEditTips(ev.target.value)}
                          className="border border-gray-300 rounded px-2 py-1 text-xs w-20 text-right focus:ring-2 focus:ring-green-500"
                          placeholder="0.00"
                        />
                      ) : (
                        e.tips > 0 ? `$${e.tips.toFixed(2)}` : "—"
                      )}
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
                    <td colSpan={7} className="px-4 py-8 text-center text-gray-400">
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
          if (noteType === "employee_private" && !noteEmpId) {
            showToast("error", "Please select an employee for private notes");
            return;
          }
          try {
            await createScheduleNote({
              date: selectedDate,
              note: noteText.trim(),
              note_type: noteType,
              employee_id: noteType === "employee_private" ? noteEmpId : undefined,
            });
            showToast("success", "Note added");
            setShowNoteModal(false);
            setNoteText("");
            setNoteType("shared");
            setNoteEmpId(0);
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

        const handleEditNote = async (id: number) => {
          if (!editNoteText.trim()) return;
          try {
            await updateScheduleNote(id, { note: editNoteText.trim() });
            showToast("success", "Note updated");
            setEditingNoteId(null);
            setEditNoteText("");
            await loadNotes();
          } catch { showToast("error", "Failed to update note"); }
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
                { id: "shift_requests" as const, label: `Shift Requests${shiftRequests.filter(r => r.status === "pending").length ? ` (${shiftRequests.filter(r => r.status === "pending").length})` : ""}`, icon: RefreshCw },
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
                  <button onClick={() => setShowBulkScheduleModal(true)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-purple-700 bg-purple-50 rounded-lg hover:bg-purple-100">
                    <CalendarDays className="w-3 h-3" />Multi-Day Schedule
                  </button>
                </div>
              </div>

              {/* Month calendars */}
              {/* Employee Hours Summary */}
              {(weeklyHours.length > 0 || monthlyHours.length > 0) && (
                <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                  <div className="p-3 border-b border-gray-200 bg-emerald-50 flex items-center justify-between">
                    <h3 className="font-semibold text-emerald-800 text-sm flex items-center gap-2"><Clock className="w-4 h-4" />Scheduled Hours Summary</h3>
                    <div className="flex items-center gap-1 bg-emerald-100 rounded-lg p-0.5">
                      <button onClick={() => setHoursView("week")}
                        className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${hoursView === "week" ? "bg-white text-emerald-800 shadow-sm" : "text-emerald-600 hover:text-emerald-800"}`}>
                        This Week
                      </button>
                      <button onClick={() => setHoursView("month")}
                        className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${hoursView === "month" ? "bg-white text-emerald-800 shadow-sm" : "text-emerald-600 hover:text-emerald-800"}`}>
                        This Month
                      </button>
                    </div>
                  </div>
                  <div className="p-3">
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
                      {(hoursView === "week" ? weeklyHours : monthlyHours).map(h => (
                        <div key={h.employee_id} className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-center">
                          <div className="text-sm font-medium text-gray-900">{h.employee_name}</div>
                          <div className="text-2xl font-bold text-emerald-700 mt-1">{h.total_hours}h</div>
                          <div className="text-xs text-gray-500 mt-0.5">{h.shift_count} shift{h.shift_count !== 1 ? "s" : ""}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

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
                                  {dayNotes ? dayNotes.map(n => {
                                    const typeColor = n.note_type === "admin_only" ? "text-purple-700 bg-purple-50" : n.note_type === "employee_private" ? "text-orange-700 bg-orange-50" : "text-blue-700";
                                    const typeLabel = n.note_type === "admin_only" ? "Admin" : n.note_type === "employee_private" ? (n.employee_name || "Emp") : "";
                                    if (editingNoteId === n.id) {
                                      return (
                                        <div key={n.id} className="flex items-center gap-0.5">
                                          <input type="text" value={editNoteText} onChange={e => setEditNoteText(e.target.value)}
                                            className="w-20 px-1 py-0.5 border border-blue-300 rounded text-xs" autoFocus
                                            onKeyDown={e => { if (e.key === "Enter") handleEditNote(n.id); if (e.key === "Escape") { setEditingNoteId(null); setEditNoteText(""); } }} />
                                          <button onClick={() => handleEditNote(n.id)} className="text-green-600 hover:text-green-800 flex-shrink-0"><Check className="w-2.5 h-2.5" /></button>
                                          <button onClick={() => { setEditingNoteId(null); setEditNoteText(""); }} className="text-gray-400 hover:text-gray-600 flex-shrink-0"><X className="w-2.5 h-2.5" /></button>
                                        </div>
                                      );
                                    }
                                    return (
                                      <div key={n.id} className={`flex items-center gap-0.5 justify-center ${typeColor}`} title={`${typeLabel ? `[${typeLabel}] ` : ""}${n.note}`}>
                                        {typeLabel && <span className="text-[9px] font-bold">{typeLabel[0]}</span>}
                                        <span className="truncate max-w-14 cursor-pointer hover:underline" onClick={() => { setEditingNoteId(n.id); setEditNoteText(n.note); }}>{n.note}</span>
                                        <button onClick={() => handleRemoveNote(n.id)} className="text-red-400 hover:text-red-600 flex-shrink-0"><X className="w-2.5 h-2.5" /></button>
                                      </div>
                                    );
                                  }) : (
                                    <button onClick={() => { setSelectedDate(dateStr); setShowNoteModal(true); }}
                                      className="text-gray-300 hover:text-blue-500 transition-colors"><Plus className="w-3 h-3 mx-auto" /></button>
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                          {/* Hours totals row */}
                          <tr className="bg-emerald-50/50">
                            <td className="px-2 py-1.5 text-xs font-medium text-emerald-700 border-r border-b border-gray-200 sticky left-0 bg-emerald-50 z-10">
                              <Clock className="w-3 h-3 inline mr-1" />Total Hrs
                            </td>
                            {days.map((d, i) => {
                              const dateStr = d.toISOString().split("T")[0];
                              // Sum hours for all employees on this date
                              let dayTotal = 0;
                              activeEmps.forEach(emp => {
                                const sched = scheduleMap[`${emp.id}-${dateStr}`];
                                if (sched) {
                                  const [sh, sm] = sched.start_time.split(":").map(Number);
                                  const [eh, em] = sched.end_time.split(":").map(Number);
                                  let hrs = (eh + em / 60) - (sh + sm / 60);
                                  if (hrs < 0) hrs += 24; // handle overnight shifts
                                  dayTotal += hrs;
                                }
                              });
                              return (
                                <td key={i} className="px-1 py-1 text-center border-r border-b border-gray-200 text-xs font-medium text-emerald-700">
                                  {dayTotal > 0 ? dayTotal.toFixed(1) : ""}
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
                      <span className="flex items-center gap-1"><MessageSquare className="w-3 h-3 text-blue-500" /> Shared Note</span>
                      <span className="flex items-center gap-1"><span className="text-[9px] font-bold text-purple-700 bg-purple-50 px-1 rounded">A</span> Admin Only</span>
                      <span className="flex items-center gap-1"><span className="text-[9px] font-bold text-orange-700 bg-orange-50 px-1 rounded">E</span> Employee Private</span>
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
                    <label className="block text-xs font-medium text-gray-600 mb-1">Note Type</label>
                    <div className="flex gap-2">
                      <button onClick={() => { setNoteType("shared"); setNoteEmpId(0); }}
                        className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium border transition-colors ${noteType === "shared" ? "bg-blue-100 border-blue-400 text-blue-700" : "bg-white border-gray-300 text-gray-600 hover:bg-gray-50"}`}>
                        Shared (All)
                      </button>
                      <button onClick={() => { setNoteType("admin_only"); setNoteEmpId(0); }}
                        className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium border transition-colors ${noteType === "admin_only" ? "bg-purple-100 border-purple-400 text-purple-700" : "bg-white border-gray-300 text-gray-600 hover:bg-gray-50"}`}>
                        Admin Only
                      </button>
                      <button onClick={() => setNoteType("employee_private")}
                        className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium border transition-colors ${noteType === "employee_private" ? "bg-orange-100 border-orange-400 text-orange-700" : "bg-white border-gray-300 text-gray-600 hover:bg-gray-50"}`}>
                        Private (Emp)
                      </button>
                    </div>
                    <p className="text-[10px] text-gray-400 mt-1">
                      {noteType === "shared" ? "Visible to everyone." : noteType === "admin_only" ? "Only visible to admin, hidden from employees." : "Only visible to the selected employee."}
                    </p>
                  </div>
                  {noteType === "employee_private" && (
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Employee</label>
                      <select value={noteEmpId} onChange={e => setNoteEmpId(Number(e.target.value))}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500">
                        <option value={0}>Select employee...</option>
                        {activeEmps.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
                      </select>
                    </div>
                  )}
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Note</label>
                    <textarea value={noteText} onChange={e => setNoteText(e.target.value)} placeholder="Staff meeting, holiday, special event..."
                      rows={3} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 resize-y" />
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button onClick={handleAddNote} disabled={!selectedDate || !noteText.trim() || (noteType === "employee_private" && !noteEmpId)}
                      className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium text-sm">Add Note</button>
                    <button onClick={() => { setShowNoteModal(false); setNoteType("shared"); setNoteEmpId(0); }} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium text-sm">Cancel</button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Bulk (Multi-Day) Schedule Modal */}
          {showBulkScheduleModal && (() => {
            const bulkMonth = calMonth;
            const bulkYear = calYear;
            const daysInMonth = new Date(bulkYear, bulkMonth + 1, 0).getDate();
            const firstDow = new Date(bulkYear, bulkMonth, 1).getDay();
            const bulkCalDays: (number | null)[] = [];
            for (let i = 0; i < firstDow; i++) bulkCalDays.push(null);
            for (let d = 1; d <= daysInMonth; d++) bulkCalDays.push(d);
            const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
            const monthNames2 = ["January","February","March","April","May","June","July","August","September","October","November","December"];

            return (
              <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setShowBulkScheduleModal(false)}>
                <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-lg" onClick={e => e.stopPropagation()}>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                    <CalendarDays className="w-5 h-5 text-purple-600" />Multi-Day Schedule
                  </h3>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Employee</label>
                      <select value={bulkEmpId} onChange={e => setBulkEmpId(Number(e.target.value))}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500">
                        <option value={0}>Select employee...</option>
                        {activeEmps.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
                      </select>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Start Time</label>
                        <input type="time" value={bulkStartTime} onChange={e => setBulkStartTime(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">End Time</label>
                        <input type="time" value={bulkEndTime} onChange={e => setBulkEndTime(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500" />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Location (optional)</label>
                      <input type="text" value={bulkLocation} onChange={e => setBulkLocation(e.target.value)} placeholder="e.g. Main Store"
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500" />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">
                        Select Dates ({bulkDates.length} selected)
                      </label>
                      <div className="text-center font-semibold text-gray-800 text-sm mb-2">
                        {monthNames2[bulkMonth]} {bulkYear}
                      </div>
                      <div className="grid grid-cols-7 gap-1 text-center">
                        {dayNames.map(d => (
                          <div key={d} className="text-[10px] font-medium text-gray-500 py-1">{d}</div>
                        ))}
                        {bulkCalDays.map((day, idx) => {
                          if (day === null) return <div key={`empty-${idx}`} />;
                          const dateStr = `${bulkYear}-${String(bulkMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
                          const isSelected = bulkDates.includes(dateStr);
                          const isPast = new Date(dateStr) < new Date(new Date().toISOString().split("T")[0]);
                          return (
                            <button key={dateStr} onClick={() => !isPast && toggleBulkDate(dateStr)}
                              disabled={isPast}
                              className={`p-1.5 text-xs rounded-lg transition-colors ${
                                isSelected ? "bg-purple-600 text-white font-bold" :
                                isPast ? "text-gray-300 cursor-not-allowed" :
                                "text-gray-700 hover:bg-purple-50"
                              }`}>
                              {day}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    <div className="flex gap-2 pt-2">
                      <button onClick={handleSaveBulkSchedule} disabled={savingBulk || !bulkEmpId || bulkDates.length === 0}
                        className="flex-1 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 font-medium text-sm">
                        {savingBulk ? "Saving..." : `Schedule ${bulkDates.length} Day${bulkDates.length !== 1 ? "s" : ""}`}
                      </button>
                      <button onClick={() => { setShowBulkScheduleModal(false); setBulkDates([]); }}
                        className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium text-sm">Cancel</button>
                    </div>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* =================== SHIFT REQUESTS TAB =================== */}
          {scheduleSubTab === "shift_requests" && (
            <div className="space-y-4">
              <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                <div className="p-3 border-b border-gray-200 bg-indigo-50">
                  <h3 className="font-semibold text-indigo-800 text-sm flex items-center gap-2">
                    <RefreshCw className="w-4 h-4" />Shift Pickup & Trade Requests
                  </h3>
                  <p className="text-xs text-indigo-600 mt-1">Employees can request to pick up open shifts or trade shifts with each other. Approve or deny below.</p>
                </div>
                {shiftRequests.length === 0 ? (
                  <div className="p-8 text-center text-gray-400 text-sm">No shift requests yet.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left">
                      <thead className="bg-gray-50 border-b border-gray-200">
                        <tr>
                          <th className="px-4 py-2 text-xs font-semibold text-gray-600">Type</th>
                          <th className="px-4 py-2 text-xs font-semibold text-gray-600">Requester</th>
                          <th className="px-4 py-2 text-xs font-semibold text-gray-600">Shift</th>
                          <th className="px-4 py-2 text-xs font-semibold text-gray-600">Target Shift</th>
                          <th className="px-4 py-2 text-xs font-semibold text-gray-600">Message</th>
                          <th className="px-4 py-2 text-xs font-semibold text-gray-600">Status</th>
                          <th className="px-4 py-2 text-xs font-semibold text-gray-600 text-right">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {shiftRequests.map(sr => (
                          <tr key={sr.id} className="hover:bg-gray-50">
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${sr.request_type === "pickup" ? "bg-blue-100 text-blue-700" : "bg-purple-100 text-purple-700"}`}>
                                {sr.request_type === "pickup" ? "Pickup" : "Trade"}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-sm font-medium text-gray-900">{sr.requester_name}</td>
                            <td className="px-4 py-3 text-sm text-gray-700">
                              {sr.shift_date && new Date(sr.shift_date + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                              {" "}{sr.shift_start}–{sr.shift_end}
                              {sr.shift_employee_name && <span className="text-xs text-gray-400 ml-1">({sr.shift_employee_name})</span>}
                            </td>
                            <td className="px-4 py-3 text-sm text-gray-700">
                              {sr.request_type === "trade" && sr.target_date ? (
                                <>
                                  {new Date(sr.target_date + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                                  {" "}{sr.target_start}–{sr.target_end}
                                  {sr.target_employee_name && <span className="text-xs text-gray-400 ml-1">({sr.target_employee_name})</span>}
                                </>
                              ) : "—"}
                            </td>
                            <td className="px-4 py-3 text-sm text-gray-500">{sr.message || "—"}</td>
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                sr.status === "approved" ? "bg-green-100 text-green-700" :
                                sr.status === "denied" ? "bg-red-100 text-red-700" :
                                "bg-amber-100 text-amber-700"
                              }`}>{sr.status}</span>
                            </td>
                            <td className="px-4 py-3 text-right">
                              <div className="flex items-center justify-end gap-1">
                                {sr.status === "pending" && (
                                  <>
                                    <button onClick={() => handleApproveShiftRequest(sr.id)} className="p-1.5 text-green-600 hover:bg-green-50 rounded-lg" title="Approve"><Check className="w-4 h-4" /></button>
                                    <button onClick={() => handleDenyShiftRequest(sr.id)} className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg" title="Deny"><X className="w-4 h-4" /></button>
                                  </>
                                )}
                                <button onClick={() => handleDeleteShiftRequest(sr.id)} className="p-1.5 text-gray-400 hover:bg-gray-100 rounded-lg" title="Delete"><Trash2 className="w-4 h-4" /></button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        );
      })()}
    </div>
  );
}
