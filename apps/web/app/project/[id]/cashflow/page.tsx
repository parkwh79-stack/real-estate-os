export default function CashflowPage() {
  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-4">Monthly Cash Flow</h1>
      <div className="border p-4 rounded">
        <p><strong>Total Months:</strong> 36</p>
        <p><strong>Max Minus:</strong> -15,000,000,000</p>
        <p><strong>PF Drawdown:</strong> 70,000,000,000</p>
        <p><strong>PF Repayment:</strong> 70,000,000,000</p>
        <table className="mt-4 w-full text-left">
          <thead>
            <tr>
              <th>Month</th>
              <th>Cash In</th>
              <th>Cash Out</th>
              <th>Cumulative</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>1</td>
              <td>0</td>
              <td>-5,000,000,000</td>
              <td>-5,000,000,000</td>
            </tr>
            {/* More mock rows can go here */}
          </tbody>
        </table>
      </div>
    </div>
  );
}
