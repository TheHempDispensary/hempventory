import { useState, useEffect } from "react";
import { getChatSessions, getChatSession } from "../lib/api";
import { Search, ArrowLeft, MessageCircle, User, Mail, ShoppingCart, Eye, Calendar, Filter } from "lucide-react";

interface ChatSession {
  session_id: string;
  customer_name: string | null;
  customer_email: string | null;
  page_url: string | null;
  device_type: string | null;
  intent: string | null;
  message_count: number;
  first_message: string | null;
  created_at: string;
  updated_at: string;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

interface SessionDetail {
  session: {
    session_id: string;
    customer_name: string | null;
    customer_email: string | null;
    page_url: string | null;
    device_type: string | null;
    intent: string | null;
    created_at: string;
    updated_at: string;
  };
  messages: ChatMessage[];
}

function formatDate(dateStr: string): string {
  if (!dateStr) return "";
  const d = new Date(dateStr + (dateStr.includes("Z") || dateStr.includes("+") ? "" : "Z"));
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "America/New_York",
  });
}

function formatTime(dateStr: string): string {
  if (!dateStr) return "";
  const d = new Date(dateStr + (dateStr.includes("Z") || dateStr.includes("+") ? "" : "Z"));
  return d.toLocaleString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "America/New_York",
  });
}

export default function Conversations() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [intentFilter, setIntentFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [selectedSession, setSelectedSession] = useState<SessionDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 25;

  const fetchSessions = async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
      if (search) params.search = search;
      if (intentFilter) params.intent = intentFilter;
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const resp = await getChatSessions(params);
      setSessions(resp.data.sessions);
      setTotal(resp.data.total);
    } catch (err) {
      console.error("Failed to fetch chat sessions:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSessions();
  }, [page, intentFilter, dateFrom, dateTo]);

  const handleSearch = () => {
    setPage(0);
    fetchSessions();
  };

  const viewSession = async (sessionId: string) => {
    setLoadingDetail(true);
    try {
      const resp = await getChatSession(sessionId);
      setSelectedSession(resp.data);
    } catch (err) {
      console.error("Failed to fetch session detail:", err);
    } finally {
      setLoadingDetail(false);
    }
  };

  // Session detail view (transcript)
  if (selectedSession) {
    const s = selectedSession.session;
    return (
      <div className="space-y-4">
        {/* Back button + header */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setSelectedSession(null)}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-gray-600" />
          </button>
          <div className="flex-1">
            <h2 className="text-lg font-semibold text-gray-900">Conversation Transcript</h2>
            <p className="text-sm text-gray-500">{formatDate(s.created_at)}</p>
          </div>
          {s.intent && (
            <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${
              s.intent === "purchase"
                ? "bg-green-100 text-green-700"
                : "bg-gray-100 text-gray-600"
            }`}>
              {s.intent === "purchase" ? "Purchase Intent" : "Browsing"}
            </span>
          )}
        </div>

        {/* Session metadata */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-gray-500">Customer</p>
              <p className="font-medium text-gray-900">{s.customer_name || "Anonymous"}</p>
            </div>
            <div>
              <p className="text-gray-500">Email</p>
              <p className="font-medium text-gray-900">{s.customer_email || "—"}</p>
            </div>
            <div>
              <p className="text-gray-500">Device</p>
              <p className="font-medium text-gray-900 capitalize">{s.device_type || "—"}</p>
            </div>
            <div>
              <p className="text-gray-500">Page</p>
              <p className="font-medium text-gray-900 truncate" title={s.page_url || ""}>
                {s.page_url ? new URL(s.page_url).pathname : "—"}
              </p>
            </div>
          </div>
        </div>

        {/* Chat transcript */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="space-y-3 max-h-[600px] overflow-y-auto">
            {selectedSession.messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[75%] ${
                  msg.role === "user"
                    ? "bg-green-100 text-green-900 rounded-2xl rounded-br-md"
                    : "bg-gray-100 text-gray-900 rounded-2xl rounded-bl-md"
                } px-4 py-2.5`}>
                  <p className="text-sm whitespace-pre-line">{msg.content}</p>
                  <p className="text-xs text-gray-400 mt-1">{formatTime(msg.created_at)}</p>
                </div>
              </div>
            ))}
            {selectedSession.messages.length === 0 && (
              <p className="text-center text-gray-400 py-8">No messages in this conversation.</p>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Session list view
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Conversations</h1>
          <p className="text-sm text-gray-500">Bud AI chat sessions with customers</p>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <MessageCircle className="w-4 h-4" />
          <span>{total} total</span>
        </div>
      </div>

      {/* Search & Filters */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search by name, email, or keyword..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleSearch(); }}
              className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-green-500"
            />
          </div>
          <button
            onClick={handleSearch}
            className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 transition-colors"
          >
            Search
          </button>
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`px-3 py-2 border rounded-lg text-sm font-medium transition-colors ${
              showFilters ? "bg-green-50 border-green-300 text-green-700" : "border-gray-300 text-gray-600 hover:bg-gray-50"
            }`}
          >
            <Filter className="w-4 h-4" />
          </button>
        </div>

        {showFilters && (
          <div className="flex flex-wrap gap-3 pt-2 border-t border-gray-100">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Intent</label>
              <select
                value={intentFilter}
                onChange={e => { setIntentFilter(e.target.value); setPage(0); }}
                className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              >
                <option value="">All</option>
                <option value="purchase">Purchase</option>
                <option value="browsing">Browsing</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">From</label>
              <input
                type="date"
                value={dateFrom}
                onChange={e => { setDateFrom(e.target.value); setPage(0); }}
                className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">To</label>
              <input
                type="date"
                value={dateTo}
                onChange={e => { setDateTo(e.target.value); setPage(0); }}
                className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              />
            </div>
          </div>
        )}
      </div>

      {/* Sessions list */}
      <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
        {loading ? (
          <div className="p-8 text-center text-gray-400">Loading conversations...</div>
        ) : sessions.length === 0 ? (
          <div className="p-8 text-center text-gray-400">
            <MessageCircle className="w-8 h-8 mx-auto mb-2 text-gray-300" />
            <p>No conversations found</p>
          </div>
        ) : (
          sessions.map(session => (
            <button
              key={session.session_id}
              onClick={() => viewSession(session.session_id)}
              className="w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors flex items-start gap-3"
            >
              <div className="w-9 h-9 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                {session.customer_name ? (
                  <User className="w-4 h-4 text-green-600" />
                ) : (
                  <MessageCircle className="w-4 h-4 text-green-600" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-gray-900 text-sm">
                    {session.customer_name || "Anonymous"}
                  </span>
                  {session.customer_email && (
                    <span className="text-xs text-gray-400 flex items-center gap-1">
                      <Mail className="w-3 h-3" />
                      {session.customer_email}
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-500 truncate mt-0.5">
                  {session.first_message || "No messages"}
                </p>
                <div className="flex items-center gap-3 mt-1">
                  <span className="text-xs text-gray-400 flex items-center gap-1">
                    <Calendar className="w-3 h-3" />
                    {formatDate(session.created_at)}
                  </span>
                  <span className="text-xs text-gray-400">
                    {session.message_count} messages
                  </span>
                  {session.device_type && (
                    <span className="text-xs text-gray-400 capitalize">
                      {session.device_type}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-col items-end gap-1 flex-shrink-0">
                {session.intent && (
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    session.intent === "purchase"
                      ? "bg-green-100 text-green-700"
                      : "bg-gray-100 text-gray-500"
                  }`}>
                    {session.intent === "purchase" ? (
                      <span className="flex items-center gap-1"><ShoppingCart className="w-3 h-3" /> Purchase</span>
                    ) : (
                      <span className="flex items-center gap-1"><Eye className="w-3 h-3" /> Browsing</span>
                    )}
                  </span>
                )}
              </div>
            </button>
          ))
        )}
      </div>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-500">
            Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm disabled:opacity-50 hover:bg-gray-50"
            >
              Previous
            </button>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={(page + 1) * PAGE_SIZE >= total}
              className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm disabled:opacity-50 hover:bg-gray-50"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {loadingDetail && (
        <div className="fixed inset-0 bg-black/20 z-50 flex items-center justify-center">
          <div className="bg-white rounded-lg px-6 py-4 shadow-xl text-sm text-gray-600">
            Loading transcript...
          </div>
        </div>
      )}
    </div>
  );
}
