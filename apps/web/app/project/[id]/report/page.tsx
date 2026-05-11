export default async function ReportPreviewPage({ params }: { params: Promise<{ id: string }> | { id: string }}) {
  const resolvedParams = await params;
  let reportMarkdown = "Failed to load report.";
  try {
    const res = await fetch(`http://127.0.0.1:8000/projects/${resolvedParams.id}`, { cache: "no-store" });
    const data = await res.json();
    
    const reportRes = await fetch(`http://127.0.0.1:8000/projects/${resolvedParams.id}/reports/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });
    
    if (reportRes.ok) {
        const reportData = await reportRes.json();
        reportMarkdown = reportData.report_markdown;
    }
  } catch (err) {
    console.error(err);
  }

  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-4">Report Preview</h1>
      <div className="border p-4 rounded bg-gray-50 min-h-[400px]">
        <pre className="whitespace-pre-wrap font-sans">
          {reportMarkdown}
        </pre>
      </div>
    </div>
  );
}
