/** Local calendar day as YYYY-MM-DD (what <input type=date> and the API's date fields use). */
export const today = () => {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
};
