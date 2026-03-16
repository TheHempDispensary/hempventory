import { useState, useEffect, useCallback } from "react";
import { getMyClockStatus, myClockIn, myClockOut, getMyEntries } from "../lib/api";

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

export default function EmployeeTimeClock() {
  const [status, setStatus] = useState<ClockStatus>({ clocked_in: false });
  const [entries, setEntries] = useState<TimeEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [tab, setTab] = useState<"clock" | "timesheet">("clock");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

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

  useEffect(() => {
    fetchStatus();
    fetchEntries();
  }, [fetchStatus, fetchEntries]);

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
      month: "short", day: "numeric", year: "numeric",
      hour: "numeric", minute: "2-digit", hour12: true,
    });
  };

  // Calculate total hours for visible entries
  const totalHours = entries.reduce((sum, e) => sum + (e.hours || 0), 0);

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
              <h2 className="text-xl font-bold text-gray-900 mb-2">You're On the Clock</h2>
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
              <h2 className="text-xl font-bold text-gray-900 mb-2">Not Clocked In</h2>
              <p className="text-gray-500 mb-6">Tap the button below to start your shift</p>
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
    </div>
  );
}
