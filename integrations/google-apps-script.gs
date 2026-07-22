/*
  Google Sheets lead receiver for AI Grow Lab.

  1. Create a Google Sheet named "AI Grow Lab Leads".
  2. Open Extensions > Apps Script and paste this file.
  3. Replace SHEET_ID and SHARED_SECRET.
  4. Deploy as a Web app. Execute as: Me. Who has access: Anyone.
  5. Copy the deployed URL into GOOGLE_SHEETS_WEBHOOK_URL in .env.
*/

const SHEET_ID = 'REPLACE_WITH_YOUR_GOOGLE_SHEET_ID';
const SHEET_NAME = 'Leads';
const SHARED_SECRET = 'REPLACE_WITH_A_LONG_RANDOM_VALUE';

function doPost(event) {
  try {
    const payload = JSON.parse(event.postData.contents);
    if (payload.token !== SHARED_SECRET) {
      return jsonResponse({ ok: false, error: 'Unauthorized' });
    }

    const spreadsheet = SpreadsheetApp.openById(SHEET_ID);
    const sheet = spreadsheet.getSheetByName(SHEET_NAME) || spreadsheet.insertSheet(SHEET_NAME);
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(['Received at', 'Name', 'Company', 'Email', 'Goal', 'Context']);
      sheet.getRange(1, 1, 1, 6).setFontWeight('bold');
      sheet.setFrozenRows(1);
    }
    sheet.appendRow([
      payload.created_at || new Date().toISOString(),
      payload.name || '',
      payload.company || '',
      payload.email || '',
      payload.goal || '',
      payload.message || ''
    ]);
    return jsonResponse({ ok: true });
  } catch (error) {
    return jsonResponse({ ok: false, error: 'Unable to save the lead' });
  }
}

function jsonResponse(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}
