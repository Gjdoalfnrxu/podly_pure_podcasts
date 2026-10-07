import { useState } from 'react';
import { feedsApi } from '../services/api';
import type { OpmlImportResult } from '../types';
import { diagnostics, emitDiagnosticError } from '../utils/diagnostics';
import { getHttpErrorInfo } from '../utils/httpError';

interface ImportOpmlPanelProps {
  onImported: () => void;
  onDone: () => void;
  disabled?: boolean;
}

function UrlList({ title, urls, tone }: { title: string; urls: string[]; tone: string }) {
  if (urls.length === 0) return null;
  return (
    <div>
      <h4 className={`text-sm font-medium ${tone}`}>
        {title} ({urls.length})
      </h4>
      <ul className="mt-1 text-xs text-gray-600 break-all list-disc pl-5 space-y-0.5">
        {urls.map((u) => (
          <li key={u}>{u}</li>
        ))}
      </ul>
    </div>
  );
}

export default function ImportOpmlPanel({ onImported, onDone, disabled }: ImportOpmlPanelProps) {
  const [file, setFile] = useState<File | null>(null);
  const [processLatest, setProcessLatest] = useState(true);
  const [isImporting, setIsImporting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<OpmlImportResult | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setIsImporting(true);
    setError('');
    setResult(null);
    try {
      diagnostics.add('info', 'OPML import request', { size: file.size, processLatest });
      const summary = await feedsApi.importOpml(file, processLatest);
      setResult(summary);
      diagnostics.add('info', 'OPML import done', {
        added: summary.added.length,
        skipped: summary.skipped_existing.length,
        failed: summary.failed.length,
      });
      if (summary.added.length > 0) {
        onImported();
      }
    } catch (err) {
      const { status, data, message } = getHttpErrorInfo(err);
      emitDiagnosticError({
        title: 'OPML import failed',
        message,
        kind: status ? 'http' : 'network',
        details: { status, response: data },
      });
      setError(message || 'Failed to import OPML file.');
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <div className="space-y-4">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="opml-file" className="block text-sm font-medium text-gray-700 mb-1">
            OPML file
          </label>
          <input
            type="file"
            id="opml-file"
            accept=".opml,.xml,text/xml,application/xml,text/x-opml"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setResult(null);
              setError('');
            }}
            className="block w-full text-sm text-gray-700 file:mr-3 file:px-3 file:py-2 file:rounded-md file:border-0 file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
            disabled={disabled}
          />
          <p className="mt-1 text-xs text-gray-500">
            Export subscriptions from your podcast app as OPML. Feeds you already follow are skipped.
          </p>
        </div>

        <label className="flex items-start gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={processLatest}
            onChange={(e) => setProcessLatest(e.target.checked)}
            className="mt-0.5"
            disabled={disabled}
          />
          <span>
            Process the latest episode of each feed (same as adding one feed).
            <span className="block text-xs text-gray-500">
              Untick to import without queueing any episodes; new episodes released later still
              follow your auto-process settings.
            </span>
          </span>
        </label>

        {error && <div className="text-red-600 text-sm">{error}</div>}

        <div className="flex flex-col sm:flex-row sm:justify-end gap-3">
          <button
            type="submit"
            disabled={isImporting || !file || !!disabled}
            className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white px-4 py-2 rounded-md font-medium transition-colors sm:w-auto w-full"
          >
            {isImporting ? 'Importing...' : 'Import'}
          </button>
        </div>
      </form>

      {result && (
        <div className="space-y-3 p-3 border border-gray-200 rounded-md bg-gray-50" data-testid="opml-import-result">
          <p className="text-sm text-gray-800">
            Added {result.added.length}, already subscribed {result.skipped_existing.length}, failed{' '}
            {result.failed.length}.
          </p>
          <UrlList title="Added" urls={result.added} tone="text-green-700" />
          <UrlList title="Already subscribed" urls={result.skipped_existing} tone="text-gray-700" />
          {result.failed.length > 0 && (
            <div>
              <h4 className="text-sm font-medium text-red-700">Failed ({result.failed.length})</h4>
              <ul className="mt-1 text-xs text-gray-600 break-all list-disc pl-5 space-y-0.5">
                {result.failed.map((f) => (
                  <li key={f.url}>
                    {f.url}: <span className="text-red-600">{f.error}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex justify-end">
            <button
              type="button"
              onClick={onDone}
              className="px-3 py-2 rounded-md border border-gray-200 text-sm text-gray-700 hover:bg-gray-100"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
