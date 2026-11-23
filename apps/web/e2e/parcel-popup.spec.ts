import { expect, test } from "@playwright/test";
import { createParcelPopup } from "../lib/parcel-popup";

test("provider parcel strings render as text without creating executable HTML", async ({ page }) => {
  const result = await page.evaluate((functionSource) => {
    const create = new Function(`return (${functionSource})`)() as typeof createParcelPopup;
    const malicious = '<img src=x onerror="window.compromised=true">';
    const popup = create({addressFragment: malicious, pnu: '<script>bad()</script>', landCategory: '<b>대</b>', areaSqm: 0}, document);
    document.body.append(popup);
    return {text: popup.textContent, executableElements: popup.querySelectorAll('script,img,b').length};
  }, createParcelPopup.toString());
  expect(result.executableElements).toBe(0);
  expect(result.text).toContain('<img src=x');
  expect(result.text).toContain('면적 0㎡');
});
