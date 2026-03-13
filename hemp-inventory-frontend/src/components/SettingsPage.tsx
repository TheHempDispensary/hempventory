import { useEffect, useState } from "react";
import { getAlertSettings, updateAlertSettings, changePassword } from "../lib/api";
import { Settings, Mail, Lock, Save, RefreshCw } from "lucide-react";

export default function SettingsPage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);
  const [message, setMessage] = useState("");
  const [passwordMessage, setPasswordMessage] = useState("");

  const [emailSettings, setEmailSettings] = useState({
    notification_email: "",
    smtp_host: "smtp.gmail.com",
    smtp_port: "587",
    smtp_user: "",
    smtp_password: "",
  });

  const [passwordForm, setPasswordForm] = useState({
    current_password: "",
    new_password: "",
    confirm_password: "",
  });

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    setLoading(true);
    try {
      const res = await getAlertSettings();
      setEmailSettings({
        notification_email: res.data.notification_email || "",
        smtp_host: res.data.smtp_host || "smtp.gmail.com",
        smtp_port: res.data.smtp_port || "587",
        smtp_user: res.data.smtp_user || "",
        smtp_password: "",
      });
    } catch (err) {
      console.error("Error loading settings:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveEmail = async () => {
    setSaving(true);
    setMessage("");
    try {
      await updateAlertSettings({
        notification_email: emailSettings.notification_email,
        smtp_host: emailSettings.smtp_host,
        smtp_port: parseInt(emailSettings.smtp_port) || 587,
        smtp_user: emailSettings.smtp_user,
        smtp_password: emailSettings.smtp_password || undefined,
      });
      setMessage("Email settings saved successfully.");
    } catch {
      setMessage("Failed to save settings.");
    } finally {
      setSaving(false);
    }
  };

  const handleChangePassword = async () => {
    setPasswordMessage("");
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      setPasswordMessage("New passwords don't match.");
      return;
    }
    if (passwordForm.new_password.length < 6) {
      setPasswordMessage("Password must be at least 6 characters.");
      return;
    }
    setSavingPassword(true);
    try {
      await changePassword(passwordForm.current_password, passwordForm.new_password);
      setPasswordMessage("Password changed successfully.");
      setPasswordForm({ current_password: "", new_password: "", confirm_password: "" });
    } catch {
      setPasswordMessage("Current password is incorrect.");
    } finally {
      setSavingPassword(false);
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
    <div className="space-y-6 max-w-2xl">
      <div>
        <h2 className="text-2xl font-bold text-gray-900">Settings</h2>
        <p className="text-gray-500 text-sm">Configure email notifications and account settings</p>
      </div>

      {/* Email Notification Settings */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
          <Mail className="w-5 h-5 text-blue-500" />
          <h3 className="font-semibold text-gray-900">Email Notifications</h3>
        </div>
        <div className="p-5 space-y-4">
          {message && (
            <div className={`p-3 rounded-lg text-sm ${message.includes("success") ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
              {message}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Notification Email</label>
            <input
              type="email"
              value={emailSettings.notification_email}
              onChange={(e) => setEmailSettings({ ...emailSettings, notification_email: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
              placeholder="alerts@yourdomain.com"
            />
            <p className="text-xs text-gray-400 mt-1">PAR alerts will be sent to this address</p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">SMTP Host</label>
              <input
                type="text"
                value={emailSettings.smtp_host}
                onChange={(e) => setEmailSettings({ ...emailSettings, smtp_host: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">SMTP Port</label>
              <input
                type="number"
                value={emailSettings.smtp_port}
                onChange={(e) => setEmailSettings({ ...emailSettings, smtp_port: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">SMTP Username</label>
            <input
              type="text"
              value={emailSettings.smtp_user}
              onChange={(e) => setEmailSettings({ ...emailSettings, smtp_user: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
              placeholder="your-email@gmail.com"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">SMTP Password</label>
            <input
              type="password"
              value={emailSettings.smtp_password}
              onChange={(e) => setEmailSettings({ ...emailSettings, smtp_password: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
              placeholder="Leave blank to keep existing"
            />
            <p className="text-xs text-gray-400 mt-1">For Gmail, use an App Password (not your regular password)</p>
          </div>
          <button
            onClick={handleSaveEmail}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm transition-colors"
          >
            <Save className="w-4 h-4" />
            {saving ? "Saving..." : "Save Email Settings"}
          </button>
        </div>
      </div>

      {/* Change Password */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
          <Lock className="w-5 h-5 text-purple-500" />
          <h3 className="font-semibold text-gray-900">Change Password</h3>
        </div>
        <div className="p-5 space-y-4">
          {passwordMessage && (
            <div className={`p-3 rounded-lg text-sm ${passwordMessage.includes("success") ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
              {passwordMessage}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Current Password</label>
            <input
              type="password"
              value={passwordForm.current_password}
              onChange={(e) => setPasswordForm({ ...passwordForm, current_password: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">New Password</label>
            <input
              type="password"
              value={passwordForm.new_password}
              onChange={(e) => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Confirm New Password</label>
            <input
              type="password"
              value={passwordForm.confirm_password}
              onChange={(e) => setPasswordForm({ ...passwordForm, confirm_password: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 outline-none"
            />
          </div>
          <button
            onClick={handleChangePassword}
            disabled={savingPassword || !passwordForm.current_password || !passwordForm.new_password}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 text-sm transition-colors"
          >
            <Lock className="w-4 h-4" />
            {savingPassword ? "Changing..." : "Change Password"}
          </button>
        </div>
      </div>

      {/* Info */}
      <div className="bg-gray-50 rounded-xl border border-gray-200 p-5">
        <div className="flex items-center gap-2 mb-2">
          <Settings className="w-5 h-5 text-gray-400" />
          <h3 className="font-medium text-gray-700">Default Credentials</h3>
        </div>
        <p className="text-sm text-gray-500">
          Username: <span className="font-mono text-gray-700">admin</span> · 
          Default password: <span className="font-mono text-gray-700">hempdispensary2026</span>
        </p>
        <p className="text-xs text-gray-400 mt-1">Please change the default password after first login.</p>
      </div>
    </div>
  );
}
