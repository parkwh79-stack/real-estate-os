export default function SettingsPage() {
  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-4">Settings & Data Sources</h1>
      <div className="border p-4 rounded">
        <h2 className="text-lg font-bold mb-2">API Integrations</h2>
        <div className="flex flex-col gap-2">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked readOnly /> ValueMap API
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked readOnly /> VWORLD API
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked readOnly /> MOLEG DRF API
          </label>
        </div>
        <h2 className="text-lg font-bold mt-6 mb-2">Report Templates</h2>
        <p className="text-sm text-gray-500">Manage Markdown and DOCX templates here.</p>
        <button className="mt-4 px-4 py-2 bg-blue-600 text-white rounded">Save Settings</button>
      </div>
    </div>
  );
}
