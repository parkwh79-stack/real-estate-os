export default function RiskReviewPage() {
  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-4">Risk Review</h1>
      <div className="border p-4 rounded">
        <h2 className="text-xl font-bold mb-2">Overall Risk Grade: Moderate</h2>
        <ul className="list-disc ml-6 mt-4">
          <li><strong>Sales Risk:</strong> Low presale rate (Score: 12 - High)</li>
          <li><strong>Permit Risk:</strong> Delay in hotel permit (Score: 6 - Moderate)</li>
        </ul>
        <h3 className="text-lg font-bold mt-6 mb-2">Comprehensive Opinion</h3>
        <p>본 프로젝트는 전반적으로 양호하나 일부 리스크에 대한 보완 장치가 필요함. 조건부 승인을 권고함.</p>
      </div>
    </div>
  );
}
