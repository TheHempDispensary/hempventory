import { useState, useEffect } from "react";
import { getMyProfile } from "../lib/api";

interface Profile {
  id: number;
  name: string;
  nickname: string | null;
  phone: string | null;
  email: string | null;
  role: string | null;
  pay_type: string | null;
  pay_rate: number | null;
  username: string | null;
  custom_id: string | null;
  created_at: string | null;
}

export default function EmployeeAccount() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchProfile = async () => {
      try {
        const res = await getMyProfile();
        setProfile(res.data);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    fetchProfile();
  }, []);

  if (loading) {
    return (
      <div className="max-w-2xl mx-auto p-8 text-center text-gray-400">
        Loading profile...
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="max-w-2xl mx-auto p-8 text-center text-red-500">
        Failed to load profile
      </div>
    );
  }

  const fields = [
    { label: "Full Name", value: profile.name },
    { label: "Nickname", value: profile.nickname },
    { label: "Username", value: profile.username },
    { label: "Email", value: profile.email },
    { label: "Phone", value: profile.phone },
    { label: "Role", value: profile.role },
    { label: "Position", value: profile.custom_id },
    { label: "Pay Type", value: profile.pay_type },
    { label: "Pay Rate", value: profile.pay_rate ? `$${profile.pay_rate.toFixed(2)}/hr` : null },
  ];

  return (
    <div className="max-w-2xl mx-auto">
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        {/* Header */}
        <div className="bg-gradient-to-r from-green-600 to-emerald-600 p-6 text-white">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-white/20 flex items-center justify-center text-2xl font-bold">
              {profile.name.split(" ").map(n => n[0]).join("")}
            </div>
            <div>
              <h2 className="text-xl font-bold">{profile.name}</h2>
              <p className="text-green-100">{profile.role || "Employee"}{profile.custom_id ? ` - ${profile.custom_id}` : ""}</p>
            </div>
          </div>
        </div>

        {/* Profile Fields */}
        <div className="divide-y divide-gray-100">
          {fields.map((field) => (
            field.value && (
              <div key={field.label} className="px-6 py-4 flex items-center justify-between">
                <span className="text-sm text-gray-500">{field.label}</span>
                <span className="text-sm font-medium text-gray-900">{field.value}</span>
              </div>
            )
          ))}
        </div>
      </div>
    </div>
  );
}
