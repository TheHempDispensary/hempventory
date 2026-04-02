import { useState, useEffect, useCallback } from "react";
import { getMyClockStatus, myClockIn, myClockOut, getMyEntries, getMySchedule, getMyTimeOff, submitMyTimeOff, cancelMyTimeOff, getMyScheduleNotes, getMyProfile, getShiftRequests, createShiftPickupRequest, createShiftTradeRequest, deleteShiftRequest, getSchedules } from "../lib/api";
import { ChevronLeft, ChevronRight, CalendarOff, MessageSquare, Plus, Trash2, RefreshCw } from "lucide-react";

interface ClockStatus {
  clocked_in: boolean;
  clock_in?: string;
  hours_elapsed?: number;
}

interface TimeEntry {
  id: number;
  employee_name: string;
  clock_in: string;
  clock_out: string | null;
  hours: number | null;
}

interface ScheduleItem {
  id: number;
  date: string;
  start_time: string;
  end_time: string;
  location: string | null;
  notes: string | null;
}

interface TimeOffItem {
  id: number;
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

export default function EmployeeTimeClock() {
  const [status, setStatus] = useState<ClockStatus>({ clocked_in: false });
  const [entries, setEntries] = useState<TimeEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [tab, setTab] = useState<"clock" | "timesheet" | "schedule">("clock");
  const [schedule, setSchedule] = useState<ScheduleItem[]>([]);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [employeeName, setEmployeeName] = useState("");

  // Monthly calendar state
  const [calMonth, setCalMonth] = useState(() => new Date().getMonth());
  const [calYear, setCalYear] = useState(() => new Date().getFullYear());
  const [myTimeOff, setMyTimeOff] = useState<TimeOffItem[]>([]);
  const [scheduleNotes, setScheduleNotes] = useState<ScheduleNote[]>([]);
  const [showRequestModal, setShowRequestModal] = useState(false);
  const [requestDate, setRequestDate] = useState("");
  const [requestReason, setRequestReason] = useState("");

  // Shift requests state
  interface ShiftRequestItem {
    id: number;
    request_type: string;
    requester_id: number;
    requester_name: string;
    schedule_id: number;
    target_schedule_id: number | null;
    message: string | null;
    status: string;
    shift_date: string;
    shift_start: string;
    shift_end: string;
    shift_location: string | null;
    shift_employee_name: string;
    target_date: string | null;
    target_start: string | null;
    target_end: string | null;
    target_employee_name: string | null;
  }
  interface AllScheduleItem {
    id: number;
    employee_id: number;
    employee_name: string;
    date: string;
    start_time: string;
    end_time: string;
    location: string | null;
  }
  const [myShiftRequests, setMyShiftRequests] = useState<ShiftRequestItem[]>([]);
  const [allSchedules, setAllSchedules] = useState<AllScheduleItem[]>([]);
  const [showPickupModal, setShowPickupModal] = useState(false);
  const [showTradeModal, setShowTradeModal] = useState(false);
  const [pickupScheduleId, setPickupScheduleId] = useState(0);
  const [pickupMessage, setPickupMessage] = useState("");
  const [tradeMyScheduleId, setTradeMyScheduleId] = useState(0);
  const [tradeTargetScheduleId, setTradeTargetScheduleId] = useState(0);
  const [tradeMessage, setTradeMessage] = useState("");

  const fetchProfile = useCallback(async () => {
    try {
      const res = await getMyProfile();
      setEmployeeName(res.data.name || "");
    } catch {
      // ignore
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await getMyClockStatus();
      setStatus(res.data);
    } catch {
      // ignore
    }
  }, []);

  const fetchEntries = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getMyEntries();
      setEntries(res.data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSchedule = useCallback(async () => {
    setScheduleLoading(true);
    try {
      const start = new Date(calYear, calMonth, 1);
      const end = new Date(calYear, calMonth + 1, 0);
      const res = await getMySchedule({
        start_date: start.toISOString().split("T")[0],
        end_date: end.toISOString().split("T")[0],
      });
      setSchedule(res.data);
    } catch {
      // ignore
    } finally {
      setScheduleLoading(false);
    }
  }, [calYear, calMonth]);

  useEffect(() => {
    fetchProfile();
    fetchStatus();
    fetchEntries();
  }, [fetchProfile, fetchStatus, fetchEntries]);

  const fetchTimeOff = useCallback(async () => {
    try {
      const res = await getMyTimeOff();
      setMyTimeOff(res.data);
    } catch {
      // ignore
    }
  }, []);

  const fetchNotes = useCallback(async () => {
    try {
      const start = new Date(calYear, calMonth, 1);
      const end = new Date(calYear, calMonth + 1, 0);
      const res = await getMyScheduleNotes({
        start_date: start.toISOString().split("T")[0],
        end_date: end.toISOString().split("T")[0],
      });
      setScheduleNotes(res.data);
    } catch {
      // ignore
    }
  }, [calYear, calMonth]);

  useEffect(() => {
    if (tab === "schedule") {
      fetchSchedule();
      fetchTimeOff();
      fetchNotes();
      fetchMyShiftRequests();
      fetchAllSchedules();
    }
  }, [tab, fetchSchedule, fetchTimeOff, fetchNotes, fetchMyShiftRequests, fetchAllSchedules]);

  // Live timer for elapsed time when clocked in
  useEffect(() => {
    if (!status.clocked_in || !status.clock_in) {
      setElapsedSeconds(0);
      return;
    }
    const clockInTime = new Date(status.clock_in).getTime();
    const updateElapsed = () => {
      const now = Date.now();
      setElapsedSeconds(Math.floor((now - clockInTime) / 1000));
    };
    updateElapsed();
    const interval = setInterval(updateElapsed, 1000);
    return () => clearInterval(interval);
  }, [status.clocked_in, status.clock_in]);

  const formatElapsed = (totalSeconds: number) => {
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    return `${h}h ${m.toString().padStart(2, "0")}m ${s.toString().padStart(2, "0")}s`;
  };

  const handleClockIn = async () => {
    setActionLoading(true);
    setError("");
    setSuccess("");
    try {
      await myClockIn();
      setSuccess("Clocked in successfully!");
      await fetchStatus();
      await fetchEntries();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Failed to clock in";
      setError(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleClockOut = async () => {
    setActionLoading(true);
    setError("");
    setSuccess("");
    try {
      const res = await myClockOut();
      setSuccess(`Clocked out! Worked ${res.data.hours} hours.`);
      await fetchStatus();
      await fetchEntries();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Failed to clock out";
      setError(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const formatTime = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short", day: "numeric", year: "numeric",
      hour: "numeric", minute: "2-digit", hour12: true,
    });
  };

  const handleSubmitTimeOff = async () => {
    if (!requestDate) return;
    try {
      await submitMyTimeOff({ date: requestDate, reason: requestReason || undefined });
      setSuccess("Time-off request submitted!");
      setShowRequestModal(false);
      setRequestDate("");
      setRequestReason("");
      await fetchTimeOff();
    } catch {
      setError("Failed to submit time-off request");
    }
  };

  const handleCancelTimeOff = async (id: number) => {
    try {
      await cancelMyTimeOff(id);
      setSuccess("Time-off request cancelled");
      await fetchTimeOff();
    } catch {
      setError("Failed to cancel request");
    }
  };

  const fetchMyShiftRequests = useCallback(async () => {
    try {
      const res = await getShiftRequests();
      setMyShiftRequests(res.data);
    } catch {
      // ignore
    }
  }, []);

  const fetchAllSchedules = useCallback(async () => {
    try {
      const start = new Date(calYear, calMonth, 1);
      const end = new Date(calYear, calMonth + 1, 0);
      const res = await getSchedules({
        start_date: start.toISOString().split("T")[0],
        end_date: end.toISOString().split("T")[0],
      });
      setAllSchedules(res.data);
    } catch {
      // ignore
    }
  }, [calYear, calMonth]);

  const handlePickupRequest = async () => {
    if (!pickupScheduleId) return;
    try {
      await createShiftPickupRequest({ schedule_id: pickupScheduleId, message: pickupMessage || undefined });
      setSuccess("Shift pickup request submitted!");
      setShowPickupModal(false);
      setPickupScheduleId(0);
      setPickupMessage("");
      await fetchMyShiftRequests();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Failed to submit pickup request";
      setError(msg);
    }
  };

  const handleTradeRequest = async () => {
    if (!tradeMyScheduleId || !tradeTargetScheduleId) return;
    try {
      await createShiftTradeRequest({
        requester_schedule_id: tradeMyScheduleId,
        target_schedule_id: tradeTargetScheduleId,
        message: tradeMessage || undefined,
      });
      setSuccess("Shift trade request submitted!");
      setShowTradeModal(false);
      setTradeMyScheduleId(0);
      setTradeTargetScheduleId(0);
      setTradeMessage("");
      await fetchMyShiftRequests();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Failed to submit trade request";
      setError(msg);
    }
  };

  const handleCancelShiftRequest = async (id: number) => {
    try {
      await deleteShiftRequest(id);
      setSuccess("Shift request cancelled");
      await fetchMyShiftRequests();
    } catch {
      setError("Failed to cancel shift request");
    }
  };

  // Calculate total hours for visible entries — truncate to 2 decimal places (no rounding up)
  const totalHoursRaw = entries.reduce((sum, e) => sum + (e.hours || 0), 0);
  const totalHours = Math.floor(totalHoursRaw * 100) / 100;

  // Calendar helpers
  const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const dayNamesShort = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  const prevMonth = () => {
    if (calMonth === 0) { setCalMonth(11); setCalYear(calYear - 1); }
    else setCalMonth(calMonth - 1);
  };
  const nextMonth = () => {
    if (calMonth === 11) { setCalMonth(0); setCalYear(calYear + 1); }
    else setCalMonth(calMonth + 1);
  };

  const fmtTime12 = (t: string) => {
    const [h, mi] = t.split(":").map(Number);
    const ampm = h >= 12 ? "p" : "a";
    const hr = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return mi === 0 ? `${hr}${ampm}` : `${hr}:${mi.toString().padStart(2, "0")}${ampm}`;
  };

  // Build schedule lookup — keyed by date string (YYYY-MM-DD)
  const scheduleMap: Record<string, ScheduleItem> = {};
  schedule.forEach(s => { scheduleMap[s.date] = s; });
  const timeOffMap: Record<string, TimeOffItem> = {};
  myTimeOff.forEach(t => { timeOffMap[t.date] = t; });
  const notesMap: Record<string, ScheduleNote[]> = {};
  scheduleNotes.forEach(n => { notesMap[n.date] = notesMap[n.date] || []; notesMap[n.date].push(n); });

  // Build calendar days
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  const firstDow = new Date(calYear, calMonth, 1).getDay();
  const calendarDays: (Date | null)[] = [];
  for (let i = 0; i < firstDow; i++) calendarDays.push(null);
  for (let d = 1; d <= daysInMonth; d++) calendarDays.push(new Date(calYear, calMonth, d));

  return (
    <div className="max-w-2xl mx-auto">
      {/* Tab Navigation */}
      <div className="flex gap-1 mb-6 bg-gray-100 rounded-lg p-1">
        <button
          onClick={() => setTab("clock")}
          className={`flex-1 py-2 px-4 rounded-md text-sm font-medium transition-colors ${
            tab === "clock" ? "bg-white shadow text-green-700" : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Clock In / Out
        </button>
        <button
          onClick={() => setTab("timesheet")}
          className={`flex-1 py-2 px-4 rounded-md text-sm font-medium transition-colors ${
            tab === "timesheet" ? "bg-white shadow text-green-700" : "text-gray-500 hover:text-gray-700"
          }`}
        >
          My Timesheet
        </button>
        <button
          onClick={() => setTab("schedule")}
          className={`flex-1 py-2 px-4 rounded-md text-sm font-medium transition-colors ${
            tab === "schedule" ? "bg-white shadow text-green-700" : "text-gray-500 hover:text-gray-700"
          }`}
        >
          My Schedule
        </button>
      </div>

      {/* Messages */}
      {error && (
        <div className="mb-4 bg-red-50 text-red-600 p-3 rounded-lg text-sm">
          {error}
        </div>
      )}
      {success && (
        <div className="mb-4 bg-green-50 text-green-600 p-3 rounded-lg text-sm">
          {success}
        </div>
      )}

      {tab === "clock" && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
          {status.clocked_in ? (
            <>
              <div className="inline-flex items-center justify-center w-24 h-24 rounded-full bg-green-100 mb-6">
                <svg className="w-12 h-12 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h2 className="text-xl font-bold text-gray-900 mb-2">{employeeName ? `${employeeName}, You're On the Clock` : "You're On the Clock"}</h2>
              <p className="text-gray-500 mb-1">Clocked in at {formatTime(status.clock_in!)}</p>
              <p className="text-3xl font-mono font-bold text-green-600 mb-6">
                {formatElapsed(elapsedSeconds)}
              </p>
              <button
                onClick={handleClockOut}
                disabled={actionLoading}
                className="px-8 py-3 bg-red-600 text-white rounded-lg font-medium hover:bg-red-700 disabled:opacity-50 transition-colors text-lg"
              >
                {actionLoading ? "Clocking Out..." : "Clock Out"}
              </button>
            </>
          ) : (
            <>
              <div className="inline-flex items-center justify-center w-24 h-24 rounded-full bg-gray-100 mb-6">
                <svg className="w-12 h-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h2 className="text-xl font-bold text-gray-900 mb-2">{employeeName ? `Hi ${employeeName}` : "Not Clocked In"}</h2>
              <p className="text-gray-500 mb-6">{employeeName ? "Tap the button below to start your shift" : "Tap the button below to start your shift"}</p>
              <button
                onClick={handleClockIn}
                disabled={actionLoading}
                className="px-8 py-3 bg-green-600 text-white rounded-lg font-medium hover:bg-green-700 disabled:opacity-50 transition-colors text-lg"
              >
                {actionLoading ? "Clocking In..." : "Clock In"}
              </button>
            </>
          )}
        </div>
      )}

      {tab === "timesheet" && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="p-4 border-b border-gray-200 flex items-center justify-between">
            <h3 className="font-semibold text-gray-900">My Time Entries</h3>
            <span className="text-sm text-gray-500">
              Total: <span className="font-semibold text-green-600">{totalHours.toFixed(2)} hrs</span>
            </span>
          </div>
          {loading ? (
            <div className="p-8 text-center text-gray-400">Loading...</div>
          ) : entries.length === 0 ? (
            <div className="p-8 text-center text-gray-400">No time entries yet</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Clock In</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">Clock Out</th>
                    <th className="text-right px-4 py-3 text-xs font-medium text-gray-500 uppercase">Hours</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {entries.map((entry) => (
                    <tr key={entry.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 text-sm">{formatTime(entry.clock_in)}</td>
                      <td className="px-4 py-3 text-sm">
                        {entry.clock_out ? formatTime(entry.clock_out) : (
                          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                            Active
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-right font-medium">
                        {entry.hours != null ? `${entry.hours.toFixed(2)}` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      {tab === "schedule" && (
        <div className="space-y-4">
          {scheduleLoading ? (
            <div className="p-8 text-center text-gray-400">Loading...</div>
          ) : (
            <>
              {/* Month Navigation */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-3 flex items-center justify-between">
                <button onClick={prevMonth} className="p-1.5 hover:bg-gray-100 rounded-lg"><ChevronLeft className="w-5 h-5" /></button>
                <span className="font-semibold text-gray-900">{monthNames[calMonth]} {calYear}</span>
                <button onClick={nextMonth} className="p-1.5 hover:bg-gray-100 rounded-lg"><ChevronRight className="w-5 h-5" /></button>
              </div>

              {/* Monthly Calendar */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                <div className="grid grid-cols-7 bg-gray-50 border-b border-gray-200">
                  {dayNamesShort.map(d => (
                    <div key={d} className="text-center py-2 text-xs font-semibold text-gray-500">{d}</div>
                  ))}
                </div>
                <div className="grid grid-cols-7">
                  {calendarDays.map((day, i) => {
                    if (!day) return <div key={`empty-${i}`} className="border-b border-r border-gray-100 p-2 min-h-[5rem] bg-gray-50/50" />;
                    const dateStr = day.toISOString().split("T")[0];
                    const dow = day.getDay();
                    const sched = scheduleMap[dateStr];
                    const timeOff = timeOffMap[dateStr];
                    const dayNotes = notesMap[dateStr];
                    const isToday = dateStr === new Date().toISOString().split("T")[0];
                    const isWeekend = dow === 0 || dow === 6;

                    return (
                      <div key={dateStr} className={`border-b border-r border-gray-100 p-1.5 min-h-[5rem] ${isToday ? "bg-green-50 ring-1 ring-inset ring-green-300" : isWeekend ? "bg-gray-50/50" : ""}`}>
                        <div className={`text-xs font-bold mb-0.5 ${isToday ? "text-green-700" : "text-gray-900"}`}>{day.getDate()}</div>
                        {timeOff && timeOff.status === "approved" ? (
                          <div className="bg-red-100 text-red-700 rounded px-1 py-0.5 text-xs font-bold text-center">OFF</div>
                        ) : sched ? (
                          <div className="space-y-0.5">
                            <div className="text-xs text-gray-800 font-medium">{fmtTime12(sched.start_time)}-{fmtTime12(sched.end_time)}</div>
                            {sched.location && <div className="text-xs text-blue-600 truncate">{sched.location}</div>}
                          </div>
                        ) : (
                          <div className="text-xs text-gray-300">Off</div>
                        )}
                        {timeOff && timeOff.status === "pending" && (
                          <div className="bg-amber-100 text-amber-700 rounded px-1 py-0.5 text-xs text-center mt-0.5">Pending</div>
                        )}
                        {timeOff && timeOff.status === "denied" && (
                          <div className="bg-red-50 text-red-500 rounded px-1 py-0.5 text-xs text-center mt-0.5 line-through">Denied</div>
                        )}
                        {dayNotes && dayNotes.map(n => (
                          <div key={n.id} className="bg-blue-50 text-blue-600 rounded px-1 py-0.5 text-xs mt-0.5 truncate" title={n.note}>
                            <MessageSquare className="w-2.5 h-2.5 inline mr-0.5" />{n.note}
                          </div>
                        ))}
                      </div>
                    );
                  })}
                </div>
                {/* Legend */}
                <div className="p-2 border-t border-gray-200 flex flex-wrap gap-3 text-xs text-gray-500">
                  <span className="flex items-center gap-1"><span className="w-3 h-3 rounded bg-green-100 border border-green-300" /> Today</span>
                  <span className="flex items-center gap-1"><span className="inline-block px-1 rounded text-xs font-bold bg-red-100 text-red-700">OFF</span> Approved Off</span>
                  <span className="flex items-center gap-1"><span className="inline-block px-1 rounded text-xs bg-amber-100 text-amber-700">Pending</span> Awaiting Approval</span>
                </div>
              </div>

              {/* Request Time Off */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold text-gray-900 flex items-center gap-2">
                    <CalendarOff className="w-4 h-4 text-amber-600" />My Time-Off Requests
                  </h3>
                  <button onClick={() => { setShowRequestModal(true); setRequestDate(new Date().toISOString().split("T")[0]); }}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium bg-amber-50 text-amber-700 rounded-lg hover:bg-amber-100">
                    <Plus className="w-3.5 h-3.5" />Request Off
                  </button>
                </div>
                {myTimeOff.length === 0 ? (
                  <p className="text-sm text-gray-400">No time-off requests submitted.</p>
                ) : (
                  <div className="space-y-2">
                    {myTimeOff.map(req => (
                      <div key={req.id} className="flex items-center justify-between bg-gray-50 rounded-lg p-2.5">
                        <div>
                          <span className="text-sm font-medium text-gray-900">
                            {new Date(req.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
                          </span>
                          {req.reason && <span className="text-sm text-gray-500 ml-2">&mdash; {req.reason}</span>}
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                            req.status === "approved" ? "bg-green-100 text-green-700" :
                            req.status === "denied" ? "bg-red-100 text-red-700" :
                            "bg-amber-100 text-amber-700"
                          }`}>{req.status}</span>
                          {req.status === "pending" && (
                            <button onClick={() => handleCancelTimeOff(req.id)} className="p-1 text-gray-400 hover:text-red-500 rounded" title="Cancel request">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Shift Pickup & Trade Requests */}
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold text-gray-900 flex items-center gap-2">
                    <RefreshCw className="w-4 h-4 text-indigo-600" />Shift Requests
                  </h3>
                  <div className="flex gap-2">
                    <button onClick={() => setShowPickupModal(true)}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100">
                      <Plus className="w-3.5 h-3.5" />Pick Up Shift
                    </button>
                    <button onClick={() => setShowTradeModal(true)}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium bg-purple-50 text-purple-700 rounded-lg hover:bg-purple-100">
                      <RefreshCw className="w-3.5 h-3.5" />Trade Shift
                    </button>
                  </div>
                </div>
                {myShiftRequests.length === 0 ? (
                  <p className="text-sm text-gray-400">No shift requests submitted.</p>
                ) : (
                  <div className="space-y-2">
                    {myShiftRequests.map(sr => (
                      <div key={sr.id} className="flex items-center justify-between bg-gray-50 rounded-lg p-2.5">
                        <div>
                          <span className={`px-2 py-0.5 rounded-full text-xs font-medium mr-2 ${sr.request_type === "pickup" ? "bg-blue-100 text-blue-700" : "bg-purple-100 text-purple-700"}`}>
                            {sr.request_type === "pickup" ? "Pickup" : "Trade"}
                          </span>
                          <span className="text-sm font-medium text-gray-900">
                            {sr.shift_date && new Date(sr.shift_date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
                            {" "}{sr.shift_start}–{sr.shift_end}
                          </span>
                          {sr.request_type === "trade" && sr.target_date && (
                            <span className="text-sm text-gray-500 ml-1">
                              for {new Date(sr.target_date + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })} {sr.target_start}–{sr.target_end}
                            </span>
                          )}
                          {sr.message && <span className="text-xs text-gray-400 ml-2">({sr.message})</span>}
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                            sr.status === "approved" ? "bg-green-100 text-green-700" :
                            sr.status === "denied" ? "bg-red-100 text-red-700" :
                            "bg-amber-100 text-amber-700"
                          }`}>{sr.status}</span>
                          {sr.status === "pending" && (
                            <button onClick={() => handleCancelShiftRequest(sr.id)} className="p-1 text-gray-400 hover:text-red-500 rounded" title="Cancel request">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {/* Request Modal */}
          {showRequestModal && (
            <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setShowRequestModal(false)}>
              <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm" onClick={e => e.stopPropagation()}>
                <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                  <CalendarOff className="w-5 h-5 text-amber-600" />Request Time Off
                </h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Date</label>
                    <input type="date" value={requestDate} onChange={e => setRequestDate(e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Reason (optional)</label>
                    <input type="text" value={requestReason} onChange={e => setRequestReason(e.target.value)}
                      placeholder="Vacation, appointment, etc."
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500" />
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button onClick={handleSubmitTimeOff} disabled={!requestDate}
                      className="flex-1 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 font-medium text-sm">
                      Submit Request
                    </button>
                    <button onClick={() => setShowRequestModal(false)}
                      className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium text-sm">
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Pickup Shift Modal */}
          {showPickupModal && (
            <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setShowPickupModal(false)}>
              <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm" onClick={e => e.stopPropagation()}>
                <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                  <Plus className="w-5 h-5 text-blue-600" />Pick Up a Shift
                </h3>
                <p className="text-xs text-gray-500 mb-3">Select a shift from another employee that you want to pick up.</p>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Available Shifts</label>
                    <select value={pickupScheduleId} onChange={e => setPickupScheduleId(Number(e.target.value))}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500">
                      <option value={0}>Select a shift...</option>
                      {allSchedules
                        .filter(s => new Date(s.date) >= new Date(new Date().toISOString().split("T")[0]))
                        .map(s => (
                          <option key={s.id} value={s.id}>
                            {new Date(s.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })} {s.start_time}–{s.end_time} ({s.employee_name}){s.location ? ` @ ${s.location}` : ""}
                          </option>
                        ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Message (optional)</label>
                    <input type="text" value={pickupMessage} onChange={e => setPickupMessage(e.target.value)}
                      placeholder="I can cover this shift"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500" />
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button onClick={handlePickupRequest} disabled={!pickupScheduleId}
                      className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium text-sm">
                      Request Pickup
                    </button>
                    <button onClick={() => setShowPickupModal(false)}
                      className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium text-sm">
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Trade Shift Modal */}
          {showTradeModal && (
            <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setShowTradeModal(false)}>
              <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm" onClick={e => e.stopPropagation()}>
                <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                  <RefreshCw className="w-5 h-5 text-purple-600" />Trade a Shift
                </h3>
                <p className="text-xs text-gray-500 mb-3">Select your shift to give away and the shift you want in return.</p>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Your Shift (to give away)</label>
                    <select value={tradeMyScheduleId} onChange={e => setTradeMyScheduleId(Number(e.target.value))}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500">
                      <option value={0}>Select your shift...</option>
                      {schedule
                        .filter(s => new Date(s.date) >= new Date(new Date().toISOString().split("T")[0]))
                        .map(s => (
                          <option key={s.id} value={s.id}>
                            {new Date(s.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })} {s.start_time}–{s.end_time}{s.location ? ` @ ${s.location}` : ""}
                          </option>
                        ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Shift You Want (from another employee)</label>
                    <select value={tradeTargetScheduleId} onChange={e => setTradeTargetScheduleId(Number(e.target.value))}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500">
                      <option value={0}>Select target shift...</option>
                      {allSchedules
                        .filter(s => new Date(s.date) >= new Date(new Date().toISOString().split("T")[0]))
                        .map(s => (
                          <option key={s.id} value={s.id}>
                            {new Date(s.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })} {s.start_time}–{s.end_time} ({s.employee_name}){s.location ? ` @ ${s.location}` : ""}
                          </option>
                        ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Message (optional)</label>
                    <input type="text" value={tradeMessage} onChange={e => setTradeMessage(e.target.value)}
                      placeholder="Would like to swap shifts"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500" />
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button onClick={handleTradeRequest} disabled={!tradeMyScheduleId || !tradeTargetScheduleId}
                      className="flex-1 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 font-medium text-sm">
                      Request Trade
                    </button>
                    <button onClick={() => setShowTradeModal(false)}
                      className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium text-sm">
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
