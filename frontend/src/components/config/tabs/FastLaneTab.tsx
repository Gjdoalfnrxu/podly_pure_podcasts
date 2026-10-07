import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import { lanesApi } from '../../../services/api';
import { getHttpErrorInfo } from '../../../utils/httpError';
import { Section } from '../shared';

const usd = (value: number) => `$${value.toFixed(value < 1 ? 4 : 2)}`;

export default function FastLaneTab() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ['lane-settings'], queryFn: lanesApi.getSettings });
  const statusQuery = useQuery({
    queryKey: ['lane-status'],
    queryFn: lanesApi.getStatus,
    refetchInterval: 15000,
  });

  const [form, setForm] = useState({
    enabled: false,
    base_url: '',
    model: '',
    language: '',
    usd_per_hour: '',
    monthly_cap_usd: '',
    max_episode_minutes: '',
  });
  const [apiKey, setApiKey] = useState('');
  const [keyTouched, setKeyTouched] = useState(false);

  useEffect(() => {
    const s = settingsQuery.data;
    if (!s) return;
    setForm({
      enabled: s.enabled,
      base_url: s.base_url,
      model: s.model,
      language: s.language,
      usd_per_hour: String(s.usd_per_hour),
      monthly_cap_usd: String(s.monthly_cap_usd),
      max_episode_minutes: s.max_episode_minutes ? String(s.max_episode_minutes) : '',
    });
    setApiKey('');
    setKeyTouched(false);
  }, [settingsQuery.data]);

  const mutation = useMutation({
    mutationFn: lanesApi.updateSettings,
    onSuccess: () => {
      toast.success('Fast lane settings saved');
      queryClient.invalidateQueries({ queryKey: ['lane-settings'] });
      queryClient.invalidateQueries({ queryKey: ['lane-status'] });
    },
    onError: (err: unknown) => {
      toast.error(`Failed to save: ${getHttpErrorInfo(err).message}`);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const usdPerHour = Number(form.usd_per_hour);
    const cap = Number(form.monthly_cap_usd);
    if (!Number.isFinite(usdPerHour) || usdPerHour < 0 || !Number.isFinite(cap) || cap < 0) {
      toast.error('Price and cap must be numbers of 0 or more');
      return;
    }
    const maxMinutes = form.max_episode_minutes.trim();
    mutation.mutate({
      enabled: form.enabled,
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      language: form.language.trim(),
      usd_per_hour: usdPerHour,
      monthly_cap_usd: cap,
      max_episode_minutes: maxMinutes ? Math.max(1, Math.round(Number(maxMinutes))) : null,
      ...(keyTouched ? { api_key: apiKey } : {}),
    });
  };

  if (settingsQuery.isLoading) {
    return <div className="text-sm text-gray-600">Loading fast lane settings...</div>;
  }
  if (settingsQuery.error) {
    return <div className="text-sm text-red-600">Failed to load fast lane settings</div>;
  }

  const status = statusQuery.data;
  const cap = status?.monthly_cap_usd ?? 0;
  const spent = status?.month_spent_usd ?? 0;
  const pct = cap > 0 ? Math.min(100, (spent / cap) * 100) : 0;

  return (
    <div className="space-y-6">
      <Section title="How it works">
        <ul className="text-sm text-gray-700 list-disc pl-5 space-y-1">
          <li>
            Automatic jobs (new episodes from feed refreshes) always use the free local lane (CPU
            Whisper, one at a time).
          </li>
          <li>
            Jobs you start by hand (Process / Reprocess) use the paid cloud lane when it is enabled,
            has a key, and the episode's estimated cost fits in what is left of this month's cap.
            Up to {status?.cloud_concurrency ?? 2} cloud jobs run alongside the local one.
          </li>
          <li>
            The cap is checked again with the real audio length before upload. If the cloud call
            is refused or fails, the job goes back to the local queue. Ad detection still uses your
            normal LLM either way.
          </li>
        </ul>
      </Section>

      <Section title="This month">
        {status ? (
          <div className="space-y-2 text-sm text-gray-700 max-w-xl">
            <div className="flex justify-between">
              <span>Cloud spend</span>
              <span className="font-medium">
                {usd(spent)} of {usd(cap)}
              </span>
            </div>
            <div className="h-2 rounded bg-gray-200 overflow-hidden">
              <div
                className={`h-2 ${pct >= 100 ? 'bg-red-600' : 'bg-indigo-600'}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="text-xs text-gray-500">
              {status.month_cloud_jobs} cloud transcription(s) this month. Spend is estimated from
              audio length × your price per hour (10 s minimum per request); check your provider's
              billing for the authoritative figure.
            </div>
            <div className="text-xs text-gray-600">
              {status.cloud_available
                ? 'Cloud lane is available.'
                : `Cloud lane unavailable: ${status.cloud_unavailable_reason}.`}{' '}
              Queues: local {status.queues.local?.running ?? 0} running /{' '}
              {status.queues.local?.pending ?? 0} waiting, cloud {status.queues.cloud?.running ?? 0}{' '}
              running / {status.queues.cloud?.pending ?? 0} waiting.
            </div>
          </div>
        ) : (
          <div className="text-sm text-gray-600">Loading...</div>
        )}
      </Section>

      <Section title="Settings">
        <form onSubmit={handleSubmit} className="space-y-4 max-w-xl">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600"
            />
            <span className="text-sm text-gray-700">Enable the cloud fast lane</span>
          </label>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              API key
              {settingsQuery.data?.api_key_preview && (
                <span className="ml-2 text-xs text-gray-500">
                  (Current: {settingsQuery.data.api_key_preview})
                </span>
              )}
            </label>
            <input
              type="password"
              className="input"
              value={apiKey}
              autoComplete="off"
              onChange={(e) => {
                setApiKey(e.target.value);
                setKeyTouched(true);
              }}
              placeholder={settingsQuery.data?.api_key_set ? '•••••••• (unchanged)' : 'gsk_...'}
            />
            <p className="text-xs text-gray-500 mt-1">
              Leave blank to keep the current key. Without a key the lane stays off.
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              API base URL (OpenAI-compatible)
            </label>
            <input
              type="url"
              className="input"
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
              <input
                type="text"
                className="input"
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Language</label>
              <input
                type="text"
                className="input"
                value={form.language}
                onChange={(e) => setForm({ ...form, language: e.target.value })}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Price per audio hour (USD)
              </label>
              <input
                type="number"
                min="0"
                step="0.001"
                className="input"
                value={form.usd_per_hour}
                onChange={(e) => setForm({ ...form, usd_per_hour: e.target.value })}
              />
              <p className="text-xs text-gray-500 mt-1">Groq whisper-large-v3-turbo: $0.04.</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Monthly cap (USD)
              </label>
              <input
                type="number"
                min="0"
                step="0.01"
                className="input"
                value={form.monthly_cap_usd}
                onChange={(e) => setForm({ ...form, monthly_cap_usd: e.target.value })}
              />
              <p className="text-xs text-gray-500 mt-1">$0 keeps everything local.</p>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Max episode length for cloud (minutes, optional)
            </label>
            <input
              type="number"
              min="1"
              step="1"
              className="input"
              value={form.max_episode_minutes}
              onChange={(e) => setForm({ ...form, max_episode_minutes: e.target.value })}
              placeholder="No limit"
            />
          </div>

          <div className="pt-2">
            <button
              type="submit"
              disabled={mutation.isPending}
              className="px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-60"
            >
              {mutation.isPending ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      </Section>
    </div>
  );
}
