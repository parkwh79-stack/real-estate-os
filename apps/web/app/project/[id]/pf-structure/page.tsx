export default function PfStructurePage() {
  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-4">PF Structure</h1>
      <div className="border p-4 rounded">
        <table className="w-full text-left">
          <thead>
            <tr>
              <th>Tranche</th>
              <th>Amount</th>
              <th>Interest Rate</th>
              <th>Priority</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Tranche A</td>
              <td>50,000,000,000</td>
              <td>5.0%</td>
              <td>1</td>
            </tr>
            <tr>
              <td>Tranche B</td>
              <td>20,000,000,000</td>
              <td>8.0%</td>
              <td>2</td>
            </tr>
          </tbody>
        </table>
        <div className="mt-4">
          <p><strong>LTV:</strong> 46.6%</p>
          <p><strong>LTC:</strong> 58.3%</p>
          <p><strong>DSCR:</strong> 1.5</p>
          <p><strong>Exit Sale Rate:</strong> 46.6%</p>
        </div>
      </div>
    </div>
  );
}
